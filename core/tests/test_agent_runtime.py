from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ConfigDict

from fieldcopilot.agent import (
    Agent, ApprovalError, ApprovalService, Limits, SessionMemory, Tool, ToolRegistry, ToolResult, User, content_hash,
)
from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider, LLMUnavailable, Scripted


class Svc(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str


class Rollback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    to_revision: int


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cause: str


executed: list[dict] = []


def registry():
    executed.clear()
    return ToolRegistry([
        Tool("get_pods", "List pods", Svc, lambda ctx, a: {"pods": [f"{a.service}-1 CrashLoopBackOff"]}),
        Tool("broken_tool", "Always fails", Svc, lambda ctx, a: 1 / 0),
        Tool("propose_rollback", "Propose a rollback", Rollback, lambda ctx, a: None, side_effect="proposes",
             executes="do_rollback", summarize=lambda a: f"roll back {a.service} to revision {a.to_revision}"),
        Tool("do_rollback", "Run a rollback", Rollback,
             lambda ctx, a: executed.append(a.model_dump()) or ToolResult(f"rolled back {a.service}"), side_effect="acts"),
    ])


def make_agent(fake, **kw):
    reg = registry()
    approvals = ApprovalService(reg, {"corvane-freight": "oncall-lead"})
    llm = LLM(fake, models=dict(FAKE_MODELS), sleep=lambda s: None)
    agent = Agent(llm, reg, approvals, "You investigate incidents.", "corvane-freight", **kw)
    return agent, approvals


def test_acts_tools_are_never_offered_to_the_model():
    fake = FakeProvider().on("agent", Scripted("nothing to do"))
    agent, _ = make_agent(fake)
    agent.run("check shipment-api")
    offered = [t.name for t in fake.calls[0][0].tools]
    assert "do_rollback" not in offered and "propose_rollback" in offered


def test_tool_loop_records_evidence_and_wraps_outputs_as_data():
    fake = FakeProvider().on("agent", Scripted(tool_calls=[("get_pods", {"service": "shipment-api"})]), Scripted("Pods crash-loop."))
    agent, _ = make_agent(fake)
    run = agent.run("why is shipment-api failing?")
    assert run.status == "completed" and run.tool_calls == ["get_pods"] and run.final_text == "Pods crash-loop."
    tool_message = fake.calls[1][0].messages[-1]["content"][0]
    assert tool_message["type"] == "tool_result" and tool_message["content"].startswith('<tool_output tool="get_pods"')
    assert "CrashLoopBackOff" in next(iter(run.evidence.values()))


def test_bad_arguments_and_failing_tools_go_back_to_the_model_as_errors():
    fake = FakeProvider().on("agent",
                             Scripted(tool_calls=[("get_pods", {"svc": "x"}), ("broken_tool", {"service": "x"}), ("nope", {})]),
                             Scripted("done"))
    agent, _ = make_agent(fake)
    run = agent.run("go")
    results = fake.calls[1][0].messages[-1]["content"]
    assert len(results) == 3 and all(r.get("is_error") for r in results)  # all results in one user message
    assert "Invalid arguments" in results[0]["content"] and "ZeroDivisionError" in results[1]["content"]
    assert "not available" in results[2]["content"] and run.status == "completed"


def test_proposal_creates_an_approval_and_nothing_runs():
    fake = FakeProvider().on("agent",
                             Scripted(tool_calls=[("propose_rollback", {"service": "shipment-api", "to_revision": 213})]),
                             Scripted("Proposed a rollback to 213; waiting for approval."))
    agent, approvals = make_agent(fake)
    run = agent.run("fix it")
    assert run.status == "awaiting_approval" and run.approvals == ["APR-0001"] and executed == []
    assert "has NOT run" in run.evidence[next(iter(run.evidence))]
    pending = approvals.pending("corvane-freight")
    assert pending[0].summary == "roll back shipment-api to revision 213" and pending[0].tool == "do_rollback"


def test_only_an_approver_of_the_same_tenant_can_approve_the_exact_request():
    fake = FakeProvider().on("agent", Scripted(tool_calls=[("propose_rollback", {"service": "shipment-api", "to_revision": 213})]), Scripted("ok"))
    agent, approvals = make_agent(fake)
    agent.run("fix it")
    approval = approvals.pending("corvane-freight")[0]
    engineer = User("asha", "corvane-freight", frozenset({"engineer"}))
    lead = User("ravi", "corvane-freight", frozenset({"oncall-lead"}))
    outsider = User("nina", "tallowmere-health", frozenset({"oncall-lead"}))
    with pytest.raises(ApprovalError, match="lacks"):
        approvals.approve(approval.id, engineer, approval.hash)
    with pytest.raises(ApprovalError, match="no approval"):
        approvals.approve(approval.id, outsider, approval.hash)
    with pytest.raises(ApprovalError, match="changed"):
        approvals.approve(approval.id, lead, content_hash("do_rollback", {"service": "shipment-api", "to_revision": 1}))
    done, result = approvals.approve(approval.id, lead, approval.hash, "error rate up since release 214")
    assert done.status == "executed" and executed == [{"service": "shipment-api", "to_revision": 213}]
    with pytest.raises(ApprovalError, match="executed"):
        approvals.approve(approval.id, lead, approval.hash)


def test_approvals_expire():
    clock = [datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)]
    reg = registry()
    service = ApprovalService(reg, {"corvane-freight": "oncall-lead"}, {"corvane-freight": 15}, now=lambda: clock[0])
    approval = service.request("corvane-freight", "run-1", "do_rollback", {"service": "a", "to_revision": 1}, "roll back a")
    clock[0] += timedelta(minutes=16)
    assert service.get(approval.id, "corvane-freight").status == "expired"
    with pytest.raises(ApprovalError, match="expired"):
        service.approve(approval.id, User("ravi", "corvane-freight", frozenset({"oncall-lead"})), approval.hash)


def test_step_limit_stops_a_looping_agent():
    fake = FakeProvider().on("agent", Scripted(tool_calls=[("get_pods", {"service": "x"})]))
    agent, _ = make_agent(fake, limits=Limits(max_steps=3))
    run = agent.run("loop forever")
    assert run.status == "step_limit" and run.tool_calls == ["get_pods"] * 3


def test_budget_and_time_limits():
    fake = FakeProvider().on("agent", Scripted(tool_calls=[("get_pods", {"service": "x" * 2000})]))
    agent, _ = make_agent(fake, limits=Limits(max_steps=50, max_cost_usd=0.02))
    assert agent.run("spend").status == "budget"
    ticks = iter([0, 0, 200])
    agent2, _ = make_agent(FakeProvider().on("agent", Scripted(tool_calls=[("get_pods", {"service": "x"})])),
                           clock=lambda: next(ticks, 999))
    assert agent2.run("slow").status == "time_limit"


def test_model_outage_ends_the_run_with_a_clear_error():
    agent, _ = make_agent(FakeProvider().on("agent", LLMUnavailable("down")))
    run = agent.run("anything")
    assert run.status == "error" and "LLMUnavailable" in run.error


def test_structured_final_answer_and_output_filter():
    fake = FakeProvider().on("agent", Scripted(tool_calls=[("get_pods", {"service": "x"})]), Scripted({"cause": "bad_release"}))
    flagged = lambda text: (text + "\n[note: suspicious text]", ["looks_like_instructions"])
    agent, _ = make_agent(fake, final_schema=Report, output_filter=flagged)
    run = agent.run("investigate")
    assert run.final.cause == "bad_release" and run.flags == ["get_pods:looks_like_instructions"]
    assert fake.calls[0][0].output_schema["additionalProperties"] is False


def test_session_memory_carries_conclusions_forward():
    memory = SessionMemory()
    fake = FakeProvider().on("agent", Scripted("The cause was release 214."))
    agent, _ = make_agent(fake, memory=memory)
    agent.run("why did shipment-api fail?", session="inc-7")
    agent.run("what should we tell support?", session="inc-7")
    second_prompt = fake.calls[1][0].messages[0]["content"]
    assert "release 214" in second_prompt and second_prompt.endswith("what should we tell support?")
