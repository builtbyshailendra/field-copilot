import json
import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fieldcopilot.agent import ApprovalService
from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider
from fieldcopilot.obs import metrics
from fieldcopilot.obs.logs import JsonFormatter
from fieldcopilot.tenants.corvane.cluster import FixtureCluster, load_scenario
from fieldcopilot.tenants.corvane.investigation import build_investigator, fake_investigator

EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
trace.set_tracer_provider(_provider)


def run_investigation():
    llm = LLM(FakeProvider().on("investigate", fake_investigator), models=dict(FAKE_MODELS), hooks=[metrics.llm_hook])
    scenario = load_scenario("s01")
    return build_investigator(llm, FixtureCluster(scenario), ApprovalService(None, {"corvane-freight": "oncall-lead"})).run(scenario["alert"])


def test_spans_follow_the_genai_conventions_and_nest():
    EXPORTER.clear()
    run_investigation()
    spans = EXPORTER.get_finished_spans()
    by_name = {s.name: s for s in spans}
    agent = by_name["invoke_agent investigate"]
    chat = next(s for s in spans if s.name.startswith("chat "))
    tool = by_name["execute_tool get_pods"]
    assert agent.attributes["gen_ai.operation.name"] == "invoke_agent" and agent.attributes["fieldcopilot.approvals"] == 1
    assert chat.attributes["gen_ai.operation.name"] == "chat" and chat.attributes["gen_ai.usage.input_tokens"] > 0
    assert chat.attributes["gen_ai.provider.name"] == "fake" and "gen_ai.response.model" in chat.attributes
    assert tool.attributes["gen_ai.tool.name"] == "get_pods" and tool.attributes["fieldcopilot.tool.side_effect"] == "none"
    assert chat.parent.span_id == agent.context.span_id and tool.parent.span_id == agent.context.span_id
    # prompts and outputs never go on spans
    assert not any("LABEL_SERVICE_TOKEN" in json.dumps(dict(s.attributes), default=str) for s in spans)


def test_metrics_count_calls_tokens_and_cost():
    before = metrics.REGISTRY.get_sample_value("fieldcopilot_llm_calls_total",
                                               {"tenant": "corvane-freight", "purpose": "investigate", "model": "fake-strong", "outcome": "ok"}) or 0
    run_investigation()
    after = metrics.REGISTRY.get_sample_value("fieldcopilot_llm_calls_total",
                                              {"tenant": "corvane-freight", "purpose": "investigate", "model": "fake-strong", "outcome": "ok"})
    assert after - before >= 3
    assert metrics.REGISTRY.get_sample_value("fieldcopilot_llm_cost_usd_total", {"tenant": "corvane-freight"}) > 0


def test_selfcheck_score_drops_for_a_broken_build(monkeypatch):
    from fieldcopilot.selfcheck import score
    from fieldcopilot.wiring import retriever_for

    retriever_for.cache_clear()
    healthy = score()
    monkeypatch.setenv("FIELD_FAULT", "empty_retrieval")
    retriever_for.cache_clear()
    broken = score()
    monkeypatch.delenv("FIELD_FAULT")
    retriever_for.cache_clear()
    assert healthy["score"] > 0.8 and broken["score"] < healthy["score"] - 0.3
    assert all(v == 0 for v in broken["retrieval_mrr"].values())


def test_json_logs_are_redacted_and_carry_the_trace_id():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "member TM-55012 called from 555-201-3344", None, None)
    tracer = trace.get_tracer("t")
    with tracer.start_as_current_span("s"):
        line = json.loads(JsonFormatter().format(record))
    assert "TM-55012" not in line["msg"] and line["redacted"] == ["member_id", "phone"] and len(line["trace_id"]) == 32
