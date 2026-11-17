"""The real provider, against a stub client: request shape, reply parsing, error mapping.

No network and no key. What these tests can't prove is that Claude answers well;
that's what the recorded evaluations are for once a key exists.
"""
from types import SimpleNamespace as NS

import anthropic
import httpx
import pytest

from fieldcopilot.llm import LLMBadRequest, LLMRateLimited, LLMRefused, LLMRequest, LLMTimeout, LLMUnavailable, ToolSpec
from fieldcopilot.llm.providers.anthropic import AnthropicProvider, build_params

TOOL = ToolSpec("get_pods", "List pods", {"type": "object", "properties": {"namespace": {"type": "string"}},
                                          "required": ["namespace"], "additionalProperties": False})


class Block(NS):
    def to_dict(self):
        return dict(self.__dict__)


def message(content, stop="end_turn", model="claude-opus-5-5", details=None):
    return NS(content=content, stop_reason=stop, model=model, stop_details=details,
              usage=NS(input_tokens=120, output_tokens=40, cache_read_input_tokens=900, cache_creation_input_tokens=0))


class StubMessages:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.params = reply, error, None

    def create(self, **params):
        self.params = params
        if self.error:
            raise self.error
        return self.reply


def provider(reply=None, error=None):
    stub = StubMessages(reply, error)
    return AnthropicProvider(client=NS(beta=NS(messages=stub))), stub


def req(**kw):
    return LLMRequest(purpose="answer", system="You help.", messages=[{"role": "user", "content": "hi"}], **kw)


def test_request_shape_for_opus():
    params = build_params(req(tools=[TOOL], output_schema={"type": "object"}, effort="low"), "claude-opus-5-5")
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert params["tools"][0]["strict"] is True
    assert params["betas"] == ["server-side-fallback-2026-07-01"] and params["fallbacks"] == "default"
    assert "thinking" not in params  # Opus 5.5 runs adaptive thinking by default; disabling it is a 400


def test_haiku_gets_no_server_side_fallback():
    params = build_params(req(), "claude-haiku-5-5")
    assert "fallbacks" not in params and "betas" not in params


def test_parses_text_tool_use_usage_and_keeps_the_whole_turn():
    reply = message([
        Block(type="thinking", thinking="", signature="sig"),
        Block(type="text", text="Checking pods."),
        Block(type="tool_use", id="toolu_1", name="get_pods", input={"namespace": "shop"}),
    ], stop="tool_use")
    prov, stub = provider(reply)
    response = prov.complete(req(tools=[TOOL]), "claude-opus-5-5")
    assert response.text == "Checking pods." and response.tool_calls[0].input == {"namespace": "shop"}
    assert response.usage.cache_read_tokens == 900 and response.provider == "anthropic"
    assert [b["type"] for b in response.assistant_content] == ["thinking", "text", "tool_use"]
    assert stub.params["model"] == "claude-opus-5-5"


def test_refusal_raises():
    prov, _ = provider(message([], stop="refusal", details=NS(category="cyber")))
    with pytest.raises(LLMRefused, match="cyber"):
        prov.complete(req(), "claude-opus-5-5")


def _status_error(cls, status, headers=None):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, headers=headers or {})
    return cls("boom", response=response, body=None)


@pytest.mark.parametrize("error, expected", [
    (lambda: anthropic.APITimeoutError(request=httpx.Request("POST", "https://x")), LLMTimeout),
    (lambda: _status_error(anthropic.RateLimitError, 429, {"retry-after": "3"}), LLMRateLimited),
    (lambda: _status_error(anthropic.InternalServerError, 529), LLMUnavailable),
    (lambda: _status_error(anthropic.BadRequestError, 400), LLMBadRequest),
    (lambda: anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")), LLMUnavailable),
])
def test_errors_map_to_platform_errors(error, expected):
    prov, _ = provider(error=error())
    with pytest.raises(expected) as caught:
        prov.complete(req(), "claude-opus-5-5")
    if expected is LLMRateLimited:
        assert caught.value.retry_after == 3.0


def test_streaming_with_tools_is_refused_up_front():
    prov, _ = provider()
    with pytest.raises(LLMBadRequest):
        list(prov.stream(req(tools=[TOOL]), "claude-opus-5-5"))
