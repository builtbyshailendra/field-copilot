"""Rerankers: read the query and each candidate together, which embeddings can't.

- CrossEncoderReranker: a local cross-encoder (ms-marco MiniLM) through fastembed. CPU,
  no data leaves the machine.
- LLMReranker: asks the fast model to order candidates. Costs tokens; useful when no
  local model is allowed or for hard, domain-specific queries.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from fieldcopilot.llm import LLM, LLMError, LLMRequest, Scripted
from fieldcopilot.rag.retriever import Hit


class CrossEncoderReranker:
    name = "cross-encoder"

    def __init__(self, model: str = "Xenova/ms-marco-MiniLM-L-6-v2", cache_dir: str | None = None):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        cache = cache_dir or os.environ.get("FIELD_MODEL_CACHE", str(Path("D:/cache/fastembed")))
        self._model = TextCrossEncoder(model_name=model, cache_dir=cache)

    def rerank(self, query: str, hits: list[Hit]) -> list[Hit]:
        if not hits:
            return hits
        scores = list(self._model.rerank(query, [h.chunk.index_text for h in hits]))
        for hit, score in zip(hits, scores):
            hit.score = float(score)
        return sorted(hits, key=lambda h: -h.score)


class Ranking(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: list[int]


class LLMReranker:
    name = "llm-rerank"

    def __init__(self, llm: LLM):
        self.llm = llm

    def rerank(self, query: str, hits: list[Hit]) -> list[Hit]:
        listing = "\n".join(f"[{i}] {h.chunk.heading}: {h.chunk.text[:400]}" for i, h in enumerate(hits))
        request = LLMRequest(
            purpose="rerank", role="fast", effort="low", max_tokens=300,
            system="Order the passages by how directly they answer the question. Return JSON {\"order\": [indexes, best first]}.",
            messages=[{"role": "user", "content": f"Question: {query}\n\nPassages:\n{listing}"}],
        )
        try:
            ranking, _ = self.llm.structured(request, Ranking)
        except LLMError:
            return hits  # keep the fused order rather than fail the search
        seen = [i for i in dict.fromkeys(ranking.order) if 0 <= i < len(hits)]
        rest = [i for i in range(len(hits)) if i not in seen]
        return [hits[i] for i in seen + rest]


def fake_rerank(request: LLMRequest) -> Scripted:
    """Fake model: keeps the fused order (identity). Plumbing only."""
    count = request.messages[-1]["content"].count("\n[") + 1
    return Scripted({"order": list(range(count))})
