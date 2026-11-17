"""Hybrid retrieval: keyword + vector, merged with Reciprocal Rank Fusion, optionally reranked.

RRF uses only ranks, not scores, so it doesn't care that BM25 scores and cosine
similarities live on different scales: score(chunk) = sum over lists of 1 / (k + rank).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from fieldcopilot.rag.chunking import Chunk
from fieldcopilot.rag.stores import Index

Mode = Literal["keyword", "vector", "hybrid"]


@dataclass
class Hit:
    chunk: Chunk
    score: float
    sources: dict[str, int] = field(default_factory=dict)  # list name -> 1-based rank


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, hits: list[Hit]) -> list[Hit]: ...


def rrf(ranked_lists: dict[str, list[Chunk]], k: int = 60) -> list[Hit]:
    hits: dict[str, Hit] = {}
    for name, chunks in ranked_lists.items():
        for rank, chunk in enumerate(chunks, start=1):
            hit = hits.setdefault(chunk.id, Hit(chunk, 0.0))
            hit.score += 1.0 / (k + rank)
            hit.sources[name] = rank
    return sorted(hits.values(), key=lambda h: -h.score)


class Retriever:
    def __init__(self, index: Index, mode: Mode = "hybrid", candidates: int = 20,
                 reranker: Reranker | None = None, rrf_k: int = 60):
        self.index, self.mode, self.candidates, self.reranker, self.rrf_k = index, mode, candidates, reranker, rrf_k

    def search(self, query: str, k: int = 5) -> list[Hit]:
        lists: dict[str, list[Chunk]] = {}
        if self.mode in ("keyword", "hybrid"):
            lists["keyword"] = [c for c, _ in self.index.keyword_search(query, self.candidates)]
        if self.mode in ("vector", "hybrid"):
            lists["vector"] = [c for c, _ in self.index.vector_search(query, self.candidates)]
        hits = rrf(lists, self.rrf_k)
        if self.reranker:
            hits = self.reranker.rerank(query, hits[: self.candidates])
        return hits[:k]

    @property
    def name(self) -> str:
        return self.mode + (f"+{self.reranker.name}" if self.reranker else "")
