"""Retrieval evaluation: which search setup finds the right chunk for each labelled question?

    python -m fieldcopilot.rag.evaluate --embedder fastembed --rerank --contextual fake

Metrics, for each question with its list of relevant chunks:
- hit@1: the first result is relevant
- recall@5: share of the relevant chunks found in the top 5
- MRR: 1 / rank of the first relevant result (0 if none in the top 10)
- nDCG@5: rewards relevant results ranked higher

Contextual retrieval needs a model to write chunk context. With --contextual fake the
context is a deterministic "document + section" sentence, which measures what headings
add, not what Claude's context adds; that run waits for an API key.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider, build_llm
from fieldcopilot.packs import Pack, all_packs
from fieldcopilot.rag.chunking import chunk_document
from fieldcopilot.rag.contextual import Contextualizer, fake_context
from fieldcopilot.rag.documents import load_corpus
from fieldcopilot.rag.embeddings import Embedder, build_embedder
from fieldcopilot.rag.retriever import Retriever
from fieldcopilot.rag.stores import MemoryIndex

RESULTS = Path(__file__).resolve().parents[3] / "evals" / "results" / "retrieval"


@dataclass
class Score:
    tenant: str
    setup: str
    embedder: str
    n: int
    hit_at_1: float
    recall_at_5: float
    mrr: float
    ndcg_at_5: float


def load_questions(pack: Pack) -> list[dict]:
    return yaml.safe_load((pack.evals_dir / "retrieval.yaml").read_text(encoding="utf-8"))


def score_ranking(ranked_ids: list[str], relevant: set[str]) -> tuple[float, float, float, float]:
    hit1 = float(bool(ranked_ids) and ranked_ids[0] in relevant)
    recall5 = len(relevant & set(ranked_ids[:5])) / len(relevant)
    mrr = next((1.0 / (i + 1) for i, cid in enumerate(ranked_ids[:10]) if cid in relevant), 0.0)
    dcg = sum(1.0 / math.log2(i + 2) for i, cid in enumerate(ranked_ids[:5]) if cid in relevant)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), 5)))
    return hit1, recall5, mrr, (dcg / ideal if ideal else 0.0)


def evaluate(retriever: Retriever, questions: list[dict], tenant: str, embedder_name: str, setup: str) -> Score:
    totals = [0.0, 0.0, 0.0, 0.0]
    for q in questions:
        ranked = [h.chunk.id for h in retriever.search(q["query"], k=10)]
        for i, value in enumerate(score_ranking(ranked, set(q["relevant"]))):
            totals[i] += value
    n = len(questions)
    return Score(tenant, setup, embedder_name, n, *(round(t / n, 3) for t in totals))


def build_index(pack: Pack, embedder: Embedder, contextualizer: Contextualizer | None) -> MemoryIndex:
    index = MemoryIndex(embedder, pack.id)
    for doc in load_corpus(pack.corpus_dir, pack.id):
        chunks = chunk_document(doc)
        if contextualizer:
            contextualizer.contextualize(doc, chunks)
        index.add(chunks)
    return index


def run(embedder: Embedder, rerank: bool, contextual: str | None) -> list[Score]:
    reranker = None
    if rerank:
        from fieldcopilot.rag.rerank import CrossEncoderReranker

        reranker = CrossEncoderReranker()
    scores: list[Score] = []
    for pack in all_packs():
        questions = load_questions(pack)
        variants = [("plain", None)]
        if contextual:
            llm = LLM(FakeProvider().on("contextualize", fake_context), models=dict(FAKE_MODELS)) \
                if contextual == "fake" else build_llm(contextual)
            variants.append((f"contextual-{contextual}", Contextualizer(llm)))
        for label, ctx in variants:
            index = build_index(pack, embedder, ctx)
            setups = [Retriever(index, "keyword"), Retriever(index, "vector"), Retriever(index, "hybrid")]
            if reranker:
                setups.append(Retriever(index, "hybrid", reranker=reranker))
            for retriever in setups:
                setup = retriever.name if label == "plain" else f"{retriever.name} ({label})"
                scores.append(evaluate(retriever, questions, pack.id, embedder.name, setup))
    return scores


def to_markdown(scores: list[Score]) -> str:
    lines = ["| Tenant | Setup | Embedder | hit@1 | recall@5 | MRR | nDCG@5 |", "|---|---|---|---|---|---|---|"]
    for s in scores:
        lines.append(f"| {s.tenant} | {s.setup} | {s.embedder} | {s.hit_at_1:.2f} | {s.recall_at_5:.2f} | {s.mrr:.2f} | {s.ndcg_at_5:.2f} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedder", default="hashing")
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--contextual", choices=["fake", "anthropic", "recorded"], default=None)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    scores = run(build_embedder(args.embedder), args.rerank, args.contextual)
    table = to_markdown(scores)
    print(table)
    if args.save:
        RESULTS.mkdir(parents=True, exist_ok=True)
        stem = f"{args.embedder}{'-rerank' if args.rerank else ''}{'-ctx-' + args.contextual if args.contextual else ''}"
        (RESULTS / f"{stem}.json").write_text(json.dumps([asdict(s) for s in scores], indent=2), encoding="utf-8")
        (RESULTS / f"{stem}.md").write_text(table + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
