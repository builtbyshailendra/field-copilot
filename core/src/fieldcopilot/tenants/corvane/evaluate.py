"""Run every Corvane scenario through the investigator and score it against the known cause.

    python -m fieldcopilot.tenants.corvane.evaluate               # fake model = rules baseline
    python -m fieldcopilot.tenants.corvane.evaluate --mode anthropic

Scores per scenario: cause ranked first, cause in the top three, evidence that checks
out against the run's real tool outputs, the right first action, unapproved actions
executed (must be 0), steps and cost.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from fieldcopilot.agent import ApprovalService, Limits
from fieldcopilot.answer.grounded import locate
from fieldcopilot.agent.runtime import OutputFilter
from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider, build_llm
from fieldcopilot.tenants.corvane.cluster import FixtureCluster, all_scenarios
from fieldcopilot.tenants.corvane.investigation import Investigation, build_investigator, check_evidence, fake_investigator

RESULTS = Path(__file__).resolve().parents[4] / "evals" / "results" / "investigations"


@dataclass
class ScenarioScore:
    scenario: str
    truth: str
    predicted: str
    top1: bool
    top3: bool
    evidence_ratio: float
    key_evidence_found: bool
    action_ok: bool
    unapproved_actions: int
    steps: int
    cost_usd: float
    status: str


def score(scenario: dict, llm: LLM, output_filter: OutputFilter | None = None) -> ScenarioScore:
    cluster = FixtureCluster(scenario)
    approvals = ApprovalService(None, {"corvane-freight": "oncall-lead"})  # registry set by build_investigator
    agent = build_investigator(llm, cluster, approvals, limits=Limits(max_steps=8), output_filter=output_filter)
    run = agent.run(scenario["alert"] + f" (service: {scenario['service']})")
    truth = scenario["truth"]
    report: Investigation | None = run.final
    if report is None:
        return ScenarioScore(scenario["id"], truth["cause"], "none", False, False, 0.0, False, False,
                             len(cluster.actions), len(run.steps), run.cost_usd, run.status)
    causes = [h.cause for h in report.hypotheses]
    check = check_evidence(report, run)
    verified_text = " ".join(e.excerpt for h in report.hypotheses[:1] for e in h.evidence
                             if e.call_id in run.evidence and locate(e.excerpt, run.evidence[e.call_id])).lower()
    key_found = not truth["evidence"] or any(k.lower() in verified_text for k in truth["evidence"])
    return ScenarioScore(
        scenario["id"], truth["cause"], causes[0] if causes else "none", bool(causes) and causes[0] == truth["cause"],
        truth["cause"] in causes[:3], round(check.ratio, 2), key_found, report.recommended_action == truth["action"],
        len(cluster.actions), len(run.steps), run.cost_usd, run.status)


def run_all(llm: LLM, output_filter: OutputFilter | None = None) -> list[ScenarioScore]:
    return [score(s, llm, output_filter) for s in all_scenarios()]


def summary(scores: list[ScenarioScore]) -> dict:
    n = len(scores)
    return {
        "scenarios": n,
        "top1": round(sum(s.top1 for s in scores) / n, 3),
        "top3": round(sum(s.top3 for s in scores) / n, 3),
        "evidence_verified": round(sum(s.evidence_ratio for s in scores) / n, 3),
        "key_evidence_found": round(sum(s.key_evidence_found for s in scores) / n, 3),
        "action_correct": round(sum(s.action_ok for s in scores) / n, 3),
        "unapproved_actions": sum(s.unapproved_actions for s in scores),
        "mean_steps": round(sum(s.steps for s in scores) / n, 1),
        "cost_usd": round(sum(s.cost_usd for s in scores), 4),
    }


def to_markdown(scores: list[ScenarioScore]) -> str:
    rows = ["| Scenario | Truth | Ranked first | Top 1 | Evidence checks | Action | Unapproved actions | Steps |",
            "|---|---|---|---|---|---|---|---|"]
    for s in scores:
        rows.append(f"| {s.scenario} | {s.truth} | {s.predicted} | {'✅' if s.top1 else '❌'} | {s.evidence_ratio:.0%} "
                    f"| {'✅' if s.action_ok else '❌'} | {s.unapproved_actions} | {s.steps} |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="fake")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    llm = LLM(FakeProvider().on("investigate", fake_investigator), models=dict(FAKE_MODELS)) if args.mode == "fake" \
        else build_llm(args.mode)
    scores = run_all(llm)
    print(to_markdown(scores))
    print(json.dumps(summary(scores), indent=1))
    if args.save:
        RESULTS.mkdir(parents=True, exist_ok=True)
        label = "rules-baseline" if args.mode == "fake" else args.mode
        (RESULTS / f"{label}.json").write_text(json.dumps({"summary": summary(scores), "scenarios": [asdict(s) for s in scores]},
                                                          indent=2), encoding="utf-8")
        (RESULTS / f"{label}.md").write_text(to_markdown(scores) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
