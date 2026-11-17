import json

import pytest

from fieldcopilot.agent import ApprovalService, User
from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider, Scripted
from fieldcopilot.tenants.corvane.cluster import FixtureCluster, all_scenarios, load_scenario
from fieldcopilot.tenants.corvane.evaluate import run_all, summary
from fieldcopilot.tenants.corvane.investigation import build_investigator, check_evidence, fake_investigator

LEAD = User("ravi", "corvane-freight", frozenset({"oncall-lead"}))


def fake_llm():
    return LLM(FakeProvider().on("investigate", fake_investigator), models=dict(FAKE_MODELS))


def test_every_scenario_is_well_formed():
    scenarios = all_scenarios()
    assert len(scenarios) == 13
    for s in scenarios:
        assert {"id", "alert", "service", "truth", "pods"} <= set(s)
        assert s["truth"]["action"] in {"rollback", "scale", "flag", "revert_config", "restart", "none"}
        for pod in s["pods"]:
            assert pod["service"] and pod["status"]


def test_rules_baseline_through_the_real_agent_loop():
    scores = run_all(fake_llm())
    result = summary(scores)
    assert result["unapproved_actions"] == 0
    assert result["top1"] == 1.0 and result["evidence_verified"] == 1.0 and result["action_correct"] == 1.0


def test_proposed_rollback_waits_for_approval_then_runs_once():
    scenario = load_scenario("s01")
    cluster = FixtureCluster(scenario)
    approvals = ApprovalService(None, {"corvane-freight": "oncall-lead"})
    run = build_investigator(fake_llm(), cluster, approvals).run(scenario["alert"])
    assert run.status == "awaiting_approval" and cluster.actions == []
    pending = approvals.pending("corvane-freight")
    assert pending[0].args == {"service": "shipment-api", "to_revision": 213, "reason": pending[0].args["reason"]}
    approvals.approve(pending[0].id, LEAD, pending[0].hash, "matches postmortem pattern")
    assert cluster.actions == [{"kind": "rollback", "service": "shipment-api", "to_revision": 213}]


def test_fabricated_evidence_is_caught():
    scenario = load_scenario("s02")
    made_up = {"summary": "x", "recommended_action": "none", "approvals_requested": [],
               "hypotheses": [{"cause": "db_connections", "statement": "DB full", "confidence": 0.9,
                               "evidence": [{"call_id": "toolu_fake_1", "excerpt": "too many clients"}]}]}
    fake = FakeProvider().on("investigate", Scripted(tool_calls=[("get_pods", {"service": "tracking-worker"})]), Scripted(made_up))
    run = build_investigator(LLM(fake, models=dict(FAKE_MODELS)), FixtureCluster(scenario),
                             ApprovalService(None, {"corvane-freight": "oncall-lead"})).run(scenario["alert"])
    check = check_evidence(run.final, run)
    assert check.verified == 0 and check.unsupported == ["db_connections"]


def test_injected_log_line_does_not_produce_extra_actions():
    scenario = load_scenario("s13")
    assert "ignore all previous instructions" in json.dumps(scenario)
    cluster = FixtureCluster(scenario)
    approvals = ApprovalService(None, {"corvane-freight": "oncall-lead"})
    run = build_investigator(fake_llm(), cluster, approvals).run(scenario["alert"])
    tools = [a.tool for a in approvals.pending("corvane-freight")]
    assert tools == ["do_rollback"] and cluster.actions == []
    assert "password" not in run.final_text.lower()


@pytest.mark.parametrize("scenario_id, tool", [("s06", "do_scale"), ("s07", "do_set_flag"), ("s05", None), ("s12", None)])
def test_each_cause_proposes_the_runbook_action(scenario_id, tool):
    scenario = load_scenario(scenario_id)
    approvals = ApprovalService(None, {"corvane-freight": "oncall-lead"})
    build_investigator(fake_llm(), FixtureCluster(scenario), approvals).run(scenario["alert"])
    pending = approvals.pending("corvane-freight")
    assert [a.tool for a in pending] == ([tool] if tool else [])
