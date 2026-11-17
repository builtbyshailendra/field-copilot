"""Two index backends with the same interface (ADR 0004).

- MemoryIndex: NumPy vectors + BM25. Local development on a machine without pgvector,
  and fast unit tests.
- PgIndex: PostgreSQL with pgvector (HNSW, cosine) for vectors and built-in full-text
  search (GIN on a generated tsvector) for keywords. Every row carries its tenant, and
  every query filters on it.

Both return (chunk, score) lists, best first. The store tests run against both.
"""
from __future__ import annotations

import json
from typing import Protocol

import numpy as np

from fieldcopilot.rag.bm25 import BM25
from fieldcopilot.rag.chunking import Chunk
from fieldcopilot.rag.embeddings import Embedder


class Index(Protocol):
    embedder: Embedder

    def add(self, chunks: list[Chunk]) -> None: ...

    def vector_search(self, query: str, k: int) -> list[tuple[Chunk, float]]: ...

    def keyword_search(self, query: str, k: int) -> list[tuple[Chunk, float]]: ...

    def get(self, chunk_id: str) -> Chunk | None: ...


class MemoryIndex:
    def __init__(self, embedder: Embedder, tenant: str):
        self.embedder = embedder
        self.tenant = tenant
        self.chunks: list[Chunk] = []
        self.vectors = np.zeros((0, embedder.dim), dtype=np.float32)
        self._bm25: BM25 | None = None
        self._by_id: dict[str, Chunk] = {}

    def add(self, chunks: list[Chunk]) -> None:
        chunks = [c for c in chunks if c.tenant == self.tenant]
        if not chunks:
            return
        self.vectors = np.vstack([self.vectors, self.embedder.embed_documents([c.index_text for c in chunks])])
        self.chunks += chunks
        self._by_id.update({c.id: c for c in chunks})
        self._bm25 = BM25([c.index_text for c in self.chunks])

    def vector_search(self, query: str, k: int) -> list[tuple[Chunk, float]]:
        if not self.chunks:
            return []
        scores = self.vectors @ self.embedder.embed_query(query)
        top = np.argsort(-scores)[:k]
        return [(self.chunks[i], float(scores[i])) for i in top]

    def keyword_search(self, query: str, k: int) -> list[tuple[Chunk, float]]:
        if not self._bm25:
            return []
        scores = self._bm25.scores(query)
        ranked = sorted(range(len(scores)), key=lambda i: -scores[i])
        return [(self.chunks[i], scores[i]) for i in ranked[:k] if scores[i] > 0]

    def get(self, chunk_id: str) -> Chunk | None:
        return self._by_id.get(chunk_id)


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS rag_chunks (
  id text NOT NULL,
  tenant text NOT NULL,
  doc_id text NOT NULL,
  title text NOT NULL,
  heading_path jsonb NOT NULL,
  body text NOT NULL,
  context text NOT NULL DEFAULT '',
  start_offset int NOT NULL,
  end_offset int NOT NULL,
  embedding vector({dim}) NOT NULL,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', context || ' ' || title || ' ' || body)) STORED,
  PRIMARY KEY (tenant, id)
);
CREATE INDEX IF NOT EXISTS rag_chunks_embedding_hnsw ON rag_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS rag_chunks_tsv ON rag_chunks USING gin (tsv);
"""


def _vec(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v.tolist()) + "]"


class PgIndex:
    """pgvector + full-text search. `conn` is a psycopg connection (autocommit or managed by caller)."""

    def __init__(self, conn, embedder: Embedder, tenant: str):
        self.conn = conn
        self.embedder = embedder
        self.tenant = tenant
        with conn.cursor() as cur:
            cur.execute(SCHEMA.format(dim=embedder.dim))

    def add(self, chunks: list[Chunk]) -> None:
        chunks = [c for c in chunks if c.tenant == self.tenant]
        if not chunks:
            return
        vectors = self.embedder.embed_documents([c.index_text for c in chunks])
        with self.conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO rag_chunks (id, tenant, doc_id, title, heading_path, body, context, start_offset, end_offset, embedding)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
                   ON CONFLICT (tenant, id) DO UPDATE SET body = EXCLUDED.body, context = EXCLUDED.context,
                     heading_path = EXCLUDED.heading_path, start_offset = EXCLUDED.start_offset,
                     end_offset = EXCLUDED.end_offset, embedding = EXCLUDED.embedding""",
                [(c.id, c.tenant, c.doc_id, c.title, json.dumps(list(c.heading_path)), c.text, c.context,
                  c.start, c.end, _vec(v)) for c, v in zip(chunks, vectors)],
            )

    def _rows(self, rows) -> list[tuple[Chunk, float]]:
        out = []
        for (cid, doc_id, title, heading, body, context, start, end, score) in rows:
            path = heading if isinstance(heading, list) else json.loads(heading)
            out.append((Chunk(cid, doc_id, self.tenant, title, tuple(path), body, start, end, context), float(score)))
        return out

    COLUMNS = "id, doc_id, title, heading_path, body, context, start_offset, end_offset"

    def vector_search(self, query: str, k: int) -> list[tuple[Chunk, float]]:
        q = _vec(self.embedder.embed_query(query))
        with self.conn.cursor() as cur:
            cur.execute(
                f"SELECT {self.COLUMNS}, 1 - (embedding <=> %s::vector) FROM rag_chunks "
                "WHERE tenant = %s ORDER BY embedding <=> %s::vector LIMIT %s", (q, self.tenant, q, k))
            return self._rows(cur.fetchall())

    def keyword_search(self, query: str, k: int) -> list[tuple[Chunk, float]]:
        with self.conn.cursor() as cur:
            cur.execute(
                f"SELECT {self.COLUMNS}, ts_rank_cd(tsv, q) FROM rag_chunks, "
                "websearch_to_tsquery('english', %s) q WHERE tenant = %s AND tsv @@ q "
                "ORDER BY ts_rank_cd(tsv, q) DESC LIMIT %s", (_or_query(query), self.tenant, k))
            return self._rows(cur.fetchall())

    def get(self, chunk_id: str) -> Chunk | None:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT {self.COLUMNS}, 0 FROM rag_chunks WHERE tenant = %s AND id = %s", (self.tenant, chunk_id))
            rows = self._rows(cur.fetchall())
        return rows[0][0] if rows else None


def _or_query(text: str) -> str:
    """websearch_to_tsquery ANDs words; questions match better as OR of their content words."""
    from fieldcopilot.rag.bm25 import tokenize

    return " or ".join(dict.fromkeys(tokenize(text))) or text
