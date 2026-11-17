"""How safe is the semantic cache? Paraphrases that should reuse an answer, and near-misses
that must not, measured for each embedder and threshold, with and without the evidence
check (cached citations must come back for the new question too).

    python -m fieldcopilot.evals.cache_eval [--embedders hashing,fastembed]

Writes evals/results/cache/<embedder>.json and a summary table. Uses the fake model, so
"answered" means the scripted answerer produced a verified answer for the base question.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import yaml

from fieldcopilot.answer.grounded import GroundedAnswerer
from fieldcopilot.api.assembly import default_llm
from fieldcopilot.cache import SemanticCache, key_terms
from fieldcopilot.packs import load_pack
from fieldcopilot.rag.chunking import chunk_corpus
from fieldcopilot.rag.documents import load_corpus
from fieldcopilot.rag.embeddings import build_embedder
from fieldcopilot.rag.retriever import Retriever
from fieldcopilot.rag.stores import MemoryIndex

CORE = Path(__file__).resolve().parents[3]
DATASET = CORE / "evals" / "datasets" / "cache_pairs.yaml"
OUT = CORE / "evals" / "results" / "cache"
THRESHOLDS = [0.80, 0.85, 0.90, 0.92, 0.94, 0.96, 0.97, 0.98]
GUARDS = {"none": (), "evidence": ("sources_ok",), "terms + evidence": ("terms_ok", "sources_ok")}


def measure(embedder_kind: str) -> dict:
    embedder = build_embedder(embedder_kind)
    pairs = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    rows = []
    for tenant, items in pairs.items():
        pack = load_pack(tenant)
        index = MemoryIndex(embedder, tenant)
        index.add(chunk_corpus(load_corpus(pack.corpus_dir, tenant)))
        answerer = GroundedAnswerer(default_llm(), Retriever(index, "hybrid"), tenant)
        for item in items:
            cache = SemanticCache(embedder, threshold=0.0)
            base = answerer.answer(item["base"])
            if not cache.store(item["base"], base, answerer):
                rows.append({**item, "tenant": tenant, "skipped": f"base not answered ({base.status})"})
                continue
            entry = cache._entries[cache.version(answerer)][0]
            similarity = float(np.dot(embedder.embed_query(item["variant"]), entry.vector))
            found = {h.chunk.id for h in answerer.retriever.search(item["variant"], k=answerer.k)}
            rows.append({**item, "tenant": tenant, "similarity": round(similarity, 4),
                         "terms_ok": key_terms(item["variant"]) == key_terms(item["base"]),
                         "sources_ok": entry.cited <= found, "cited": sorted(entry.cited)})
    scored = [r for r in rows if "similarity" in r]
    same = [r for r in scored if r["same"]]
    diff = [r for r in scored if not r["same"]]
    sweep = []
    for t in THRESHOLDS:
        for guard, checks in GUARDS.items():
            def hit(r, t=t, checks=checks):
                return r["similarity"] >= t and all(r[c] for c in checks)
            sweep.append({"threshold": t, "guards": guard,
                          "paraphrase_hit_rate": round(sum(map(hit, same)) / max(len(same), 1), 3),
                          "wrong_hits": sum(map(hit, diff)), "near_misses": len(diff)})
    return {"embedder": embedder.name if hasattr(embedder, "name") else embedder_kind, "pairs": rows,
            "scored": len(scored), "skipped": len(rows) - len(scored), "sweep": sweep}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedders", default="hashing,fastembed")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    lines = ["# Semantic cache safety (fake model)", ""]
    for kind in args.embedders.split(","):
        result = measure(kind)
        (OUT / f"{kind}.json").write_text(json.dumps(result, indent=2), encoding="utf-8", newline="
")
        lines += [f"## {kind} ({result['scored']} pairs scored, {result['skipped']} skipped: base not answered)", "",
                  "| threshold | guards | paraphrases reused | wrong answers served |", "|---|---|---|---|"]
        for s in result["sweep"]:
            lines.append(f"| {s['threshold']:.2f} | {s['guards']} | "
                         f"{s['paraphrase_hit_rate']:.0%} | {s['wrong_hits']} of {s['near_misses']} |")
        lines.append("")
    (OUT / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
