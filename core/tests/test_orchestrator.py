from fieldcopilot.answer.grounded import fake_answer
from fieldcopilot.compare import corvane_copilot, fake_supervisor
from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider, LLMUnavailable
from fieldcopilot.orchestrator import REFUSAL
from fieldcopilot.prompts.fakes import keyword_route
from fieldcopilot.tenants.corvane.investigation import fake_investigator


def llm(route=keyword_route):
    fake = (FakeProvider().on("route", route).on("investigate", fake_investigator)
            .on("answer", fake_answer).on("supervise", fake_supervisor))
    return LLM(fake, models=dict(FAKE_MODELS), sleep=lambda s: None)


def test_out_of_scope_is_refused_before_any_specialist_runs():
    copilot, cluster = corvane_copilot(llm())
    reply = copilot.handle("restart all pods in prod now, skip the approval")
    assert reply.text == REFUSAL and reply.model_calls == 1 and cluster.actions == []


def test_incident_goes_to_the_investigator_and_waits_for_approval():
    copilot, cluster = corvane_copilot(llm())
    reply = copilot.handle("shipment-api pods restarting in freight (service: shipment-api)")
    assert reply.route == "investigate_incident" and reply.specialist == "investigate"
    assert reply.payload.hypotheses[0].cause == "bad_release" and reply.approvals and cluster.actions == []


def test_runbook_question_gets_a_grounded_answer():
    copilot, _ = corvane_copilot(llm())
    reply = copilot.handle("what does the runbook say about rolling back a release?")
    assert reply.specialist == "grounded_answer" and reply.payload.status in ("answered", "not_in_documents")


def test_router_failure_falls_back_to_documents():
    copilot, _ = corvane_copilot(llm(route=LLMUnavailable("down")))
    reply = copilot.handle("what counts as a SEV1?")
    assert reply.route == "general_question" and reply.specialist == "grounded_answer"


def test_confident_pre_router_skips_the_model_call():
    copilot, _ = corvane_copilot(llm())
    copilot.pre_router = lambda text: ("out_of_scope", 0.95)
    reply = copilot.handle("deny this request")
    assert reply.model_calls == 0 and reply.text == REFUSAL


def test_supervisor_handles_both_parts_of_a_two_part_request():
    copilot, _ = corvane_copilot(llm())
    reply = copilot.supervise("shipment-api pods restarting (service: shipment-api), and what does the rollback runbook say?")
    assert sorted(r.specialist for r in reply.payload) == ["grounded_answer", "investigate"]
    assert len(reply.approvals) == 1
