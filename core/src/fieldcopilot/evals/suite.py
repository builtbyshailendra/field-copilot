"""One command for every evaluation, with quality gates for CI.

    python -m fieldcopilot.evals.suite            # report
    python -m fieldcopilot.evals.suite --gate     # exit 1 if any gate fails (CI)

Runs on the fake model and the hashing embedder by default, so CI needs no key and no
downloads. In that mode the gates catch regressions in retrieval (a real metric even
with hashing embeddings), routing rules, evidence checking, approvals, guardrails and
the letter logic. With FIELD_LLM_MODE=recorded and real recordings, the same gates
catch prompt regressions too. Thresholds live in core/evals/gates.yaml.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

from fieldcopilot.agent import ApprovalService
from fieldcopilot.answer.grounded import fake_answer
from fieldcopilot.evals import judge as judge_mod
from fieldcopilot.evals.qa import evaluate as qa_evaluate
from fieldcopilot.guard.redteam import run_all as redteam_all
from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider, build_llm
from fieldcopilot.packs import all_packs, load_pack
from fieldcopilot.prompts import PromptRegistry
from fieldcopilot.prompts import lab
from fieldcopilot.prompts.fakes import keyword_route
from fieldcopilot.rag.embeddings import build_embedder
from fieldcopilot.rag.evaluate import build_index, evaluate as retrieval_evaluate, load_questions
from fieldcopilot.rag.retriever import Retriever
from fieldcopilot.tenants.corvane.evaluate import run_all as investigations, summary as inv_summary
from fieldcopilot.tenants.corvane.investigation import fake_investigator
from fieldcopilot.tenants.tallowmere.tools import build_reviewer, fake_reviewer
from fieldcopilot.tenants.tallowmere.workbench import FixtureWorkbench

CORE = Path(__file__).resolve().parents[3]
GATES = CORE / "evals" / "gates.yaml"
REPORTS = CORE / "evals" / "results" / "suite"


def make_llm(mode: str) -> LLM:
    if mode == "fake":
        fake = (FakeProvider().on("route", keyword_route).on("answer", fake_answer).on("investigate", fake_investigator)
                .on("review_case", fake_reviewer).on("judge_faithfulness", judge_mod.heuristic_judge))
        return LLM(fake, models=dict(FAKE_MODELS), sleep=lambda s: None)
    return build_llm(mode)


def case_reviews(llm: LLM) -> dict:
    rows = yaml.safe_load((load_pack("tallowmere-health").root / "cases" / "cases.yaml").read_text(encoding="utf-8"))
    correct = letters_ok = 0
    for row in rows:
        approvals = ApprovalService(None, {"tallowmere-health": "review-nurse"})
        run = build_reviewer(llm, FixtureWorkbench.from_pack(), approvals).run(f"Review case {row['id']} against its policy.")
        missing = run.final.missing_documents if run.final else None
        correct += missing == row["truth"]["missing"]
        letters_ok += bool(approvals.pending("tallowmere-health")) == bool(row["truth"]["missing"])
    return {"cases": len(rows), "missing_docs_correct": round(correct / len(rows), 3),
            "letter_decision_correct": round(letters_ok / len(rows), 3)}


def run(mode: str = "fake", embedder_kind: str = "hashing") -> dict:
    llm = make_llm(mode)
    embedder = build_embedder(embedder_kind)
    metrics: dict = {"mode": mode, "embedder": embedder.name, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    routing = lab.run(llm, PromptRegistry().get("route"), lab.load_examples("route"), lab.RouteDecision, lambda d: d.route)
    metrics["routing"] = {"accuracy": routing.score, "tricky": routing.by_tag.get("tricky", 0.0)}

    for pack in all_packs():
        index = build_index(pack, embedder, None)
        retriever = Retriever(index, "hybrid")
        r = retrieval_evaluate(retriever, load_questions(pack), pack.id, embedder.name, "hybrid")
        metrics[f"retrieval.{pack.id}"] = {"mrr": r.mrr, "recall_at_5": r.recall_at_5, "hit_at_1": r.hit_at_1}
        qa = qa_evaluate(llm, retriever, pack.id)
        metrics[f"qa.{pack.id}"] = {"correct": qa.correct, "refusal_correct": qa.refusal_correct,
                                    "false_answers": qa.false_answers, "errors": qa.errors}

    metrics["investigations"] = inv_summary(investigations(llm))
    metrics["case_reviews"] = case_reviews(llm)
    cases = redteam_all()
    metrics["redteam"] = {"passed": sum(c.passed for c in cases), "total": len(cases)}
    cal = judge_mod.calibrate(llm)
    metrics["judge"] = {"accuracy": cal.accuracy, "kappa": cal.kappa, "disagreements": cal.disagreements}
    return metrics


def check_gates(metrics: dict, gates: dict) -> list[str]:
    failures = []
    for path, rules in gates.items():
        section = metrics.get(path, {})
        for key, rule in rules.items():
            value = section.get(key)
            if value is None:
                failures.append(f"{path}.{key}: missing")
            elif "min" in rule and value < rule["min"]:
                failures.append(f"{path}.{key} = {value} < {rule['min']}")
            elif "max" in rule and value > rule["max"]:
                failures.append(f"{path}.{key} = {value} > {rule['max']}")
    return failures


def to_markdown(metrics: dict, failures: list[str]) -> str:
    lines = [f"# Evaluation suite ({metrics['mode']} model, {metrics['embedder']} embeddings, {metrics['at']})", ""]
    for key, value in metrics.items():
        if isinstance(value, dict):
            lines.append(f"- **{key}**: " + ", ".join(f"{k}={v}" for k, v in value.items()))
    lines += ["", "Gates: " + ("all passed" if not failures else f"{len(failures)} failed"), *[f"- {f}" for f in failures]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default=os.environ.get("FIELD_LLM_MODE", "fake"))
    parser.add_argument("--embedder", default=os.environ.get("FIELD_EMBEDDER", "hashing"))
    parser.add_argument("--gate", action="store_true")
    args = parser.parse_args(argv)
    metrics = run(args.mode, args.embedder)
    gates = yaml.safe_load(GATES.read_text(encoding="utf-8")).get(f"{args.mode}+{args.embedder}", {})
    failures = check_gates(metrics, gates)
    report = to_markdown(metrics, failures)
    print(report)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{args.mode}-{args.embedder}.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (REPORTS / f"{args.mode}-{args.embedder}.md").write_text(report + "\n", encoding="utf-8")
    return 1 if (args.gate and failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
