"""A small golden-set check every build runs at startup, exported as a metric.

Score (0-1) = mean of: routing accuracy of the deployed router on the hand-labelled set,
retrieval MRR for each tenant on its labelled questions, and the output-policy red-team
cases. It needs no model and finishes in a few seconds, so it can run in every pod.

The canary analysis (Argo Rollouts) reads `fieldcopilot_canary_quality_score` and stops a
rollout whose score drops below the stable version's. FIELD_FAULT lets the CI drill ship
a deliberately broken build (for example `empty_retrieval`) to prove the gate works.
"""
from __future__ import annotations

import os

from fieldcopilot.guard.policy import check as policy_check


def score() -> dict:
    import yaml

    from fieldcopilot.guard.redteam import DATASET
    from fieldcopilot.ml.router import SklearnRouter
    from fieldcopilot.packs import all_packs
    from fieldcopilot.prompts.lab import load_examples
    from fieldcopilot.rag.evaluate import evaluate, load_questions
    from fieldcopilot.wiring import retriever_for

    router = SklearnRouter()
    examples = load_examples("route")
    routing = sum(router.predict(e.inputs["message"])[0] == e.expected for e in examples) / len(examples)
    retrieval = {p.id: evaluate(retriever_for(p.id), load_questions(p), p.id, "deployed", "hybrid").mrr for p in all_packs()}
    cases = yaml.safe_load(DATASET.read_text(encoding="utf-8"))["output"]
    policy = sum((not policy_check(c["tenant"], c["text"]).ok) == c["bad"] for c in cases) / len(cases)
    parts = [routing, *retrieval.values(), policy]
    return {"score": round(sum(parts) / len(parts), 4), "routing": round(routing, 3), "retrieval_mrr": retrieval,
            "policy": round(policy, 3), "fault": os.environ.get("FIELD_FAULT", "")}


def publish() -> dict:
    from fieldcopilot.obs.metrics import CANARY_QUALITY

    result = score()
    CANARY_QUALITY.set(result["score"])
    return result
