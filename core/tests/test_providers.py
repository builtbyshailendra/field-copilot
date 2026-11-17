import json

import pytest

from fieldcopilot.llm import (
    FakeProvider, LLMBadRequest, LLMRequest, RecordedProvider, RecordingProvider, Scripted, ToolSpec, build_llm,
)
from fieldcopilot.llm.providers.recorded import request_key

TOOL = ToolSpec("get_pods", "List pods in a namespace", {
    "type": "object", "properties": {"namespace": {"type": "string"}},
    "required": ["namespace"], "additionalProperties": False,
})


def req(**kw):
    base = dict(purpose="investigate", system="sys", messages=[{"role": "user", "content": "checkout errors"}], tools=[TOOL])
    base.update(kw)
    return LLMRequest(**base)


def test_fake_tool_call_has_replayable_assistant_content():
    fake = FakeProvider().on("investigate", Scripted(tool_calls=[("get_pods", {"namespace": "shop"})]))
    response = fake.complete(req(), "fake-strong")
    assert response.stop_reason == "tool_use"
    call = response.tool_calls[0]
    assert call.name == "get_pods" and call.input == {"namespace": "shop"}
    assert response.assistant_content[0] == {"type": "tool_use", "id": call.id, "name": "get_pods", "input": {"namespace": "shop"}}


def test_fake_queue_without_repeat_runs_out():
    fake = FakeProvider().on("investigate", Scripted("once"), repeat_last=False)
    fake.complete(req(), "m")
    with pytest.raises(LLMBadRequest, match="ran out"):
        fake.complete(req(), "m")


def test_fake_function_matcher():
    fake = FakeProvider().on(lambda r: "checkout" in json.dumps(r.messages), Scripted("matched"))
    assert fake.complete(req(), "m").text == "matched"


def test_recording_then_replay_round_trip(tmp_path):
    real = FakeProvider().on("investigate", Scripted("live answer", tool_calls=[("get_pods", {"namespace": "shop"})]))
    recorder = RecordingProvider(real, tmp_path)
    live = recorder.complete(req(), "claude-opus-5-5")
    replay = RecordedProvider(tmp_path).complete(req(), "claude-opus-5-5")
    assert replay.text == live.text and replay.tool_calls == live.tool_calls and replay.provider == "recorded"
    assert replay.usage == live.usage


def test_changed_prompt_means_no_recording(tmp_path):
    RecordingProvider(FakeProvider().on("investigate", Scripted("a")), tmp_path).complete(req(), "m")
    with pytest.raises(LLMBadRequest, match="no recording"):
        RecordedProvider(tmp_path).complete(req(system="a different system prompt"), "m")


def test_request_key_ignores_nothing_that_matters():
    assert request_key(req()) == request_key(req())
    assert request_key(req()) != request_key(req(effort="high"))
    assert request_key(req()) != request_key(req(tools=[]))


def test_build_llm_modes(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELD_LLM_MODE", "fake")
    assert build_llm().models["strong"] == ["fake-strong"]
    llm = build_llm("recorded", recordings=tmp_path)
    assert llm.provider.name == "recorded" and llm.max_attempts == 1
    with pytest.raises(ValueError):
        build_llm("openai")
