"""The real Claude API.

Design choices (each one is tested against a stub client in tests/test_anthropic_provider.py):
- The SDK's own retries are off (max_retries=0). The reliability layer in llm/client.py
  retries with backoff and then falls back to another model, and two retry loops stacked
  on each other would multiply the wait.
- Structured output uses output_config.format with a JSON schema, so replies always parse.
- Effort is set on every request. Claude Opus 5.5 defaults to "medium"; routing and
  extraction jobs ask for "low", investigations for "high".
- The system prompt carries a cache breakpoint: it is the stable prefix of every call
  for a tenant, so repeat calls read it from cache.
- Server-side refusal fallback is on for the models that support it; a final refusal
  raises LLMRefused.
- Tools are sent with strict: true so arguments always match their schema.
- The whole assistant turn is kept (thinking and fallback blocks included) so it can be
  sent back unchanged on the next turn of a tool loop.
"""
from __future__ import annotations

import time
from typing import Any, Iterator

import anthropic

from fieldcopilot.llm.errors import LLMBadRequest, LLMRateLimited, LLMRefused, LLMTimeout, LLMUnavailable
from fieldcopilot.llm.types import LLMRequest, LLMResponse, StreamEvent, ToolCall, Usage

SERVER_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def build_params(request: LLMRequest, model: str) -> dict[str, Any]:
    system_block: dict[str, Any] = {"type": "text", "text": request.system}
    if request.cache_system:
        system_block["cache_control"] = {"type": "ephemeral"}
    output_config: dict[str, Any] = {"effort": request.effort}
    if request.output_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": request.output_schema}
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": request.max_tokens,
        "system": [system_block],
        "messages": request.messages,
        "output_config": output_config,
    }
    if request.tools:
        params["tools"] = [tool.as_api() for tool in request.tools]
    if model in SERVER_FALLBACK_MODELS:
        params["betas"] = [FALLBACK_BETA]
        params["fallbacks"] = "default"
    return params


def _block_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    return block.to_dict()


def parse_message(message: Any, requested_model: str) -> LLMResponse:
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise LLMRefused(f"Claude declined the request (category: {category})")
    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in message.content:
        kind = getattr(block, "type", None)
        if kind == "text":
            texts.append(block.text)
        elif kind == "tool_use":
            calls.append(ToolCall(block.id, block.name, dict(block.input)))
    usage = message.usage
    return LLMResponse(
        text="".join(texts),
        stop_reason=message.stop_reason,
        model=getattr(message, "model", None) or requested_model,
        provider="anthropic",
        tool_calls=calls,
        usage=Usage(
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        ),
        assistant_content=[_block_dict(b) for b in message.content],
    )


def map_error(error: Exception) -> Exception:
    """Most specific first: a timeout is also a connection error in the SDK."""
    if isinstance(error, anthropic.APITimeoutError):
        return LLMTimeout("Timed out waiting for Claude")
    if isinstance(error, anthropic.RateLimitError):
        retry_after = error.response.headers.get("retry-after") if error.response is not None else None
        return LLMRateLimited("Rate limited by the Claude API", float(retry_after) if retry_after else None)
    if isinstance(error, anthropic.APIStatusError):
        if error.status_code >= 500:
            return LLMUnavailable(f"Claude API answered {error.status_code}")
        return LLMBadRequest(f"Claude API answered {error.status_code}: {error.message}")
    if isinstance(error, anthropic.APIConnectionError):
        return LLMUnavailable("Can't reach the Claude API")
    return error


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: Any | None = None, timeout: float = 60.0):
        self.client = client or anthropic.Anthropic(timeout=timeout, max_retries=0)

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        params = build_params(request, model)
        started = time.perf_counter()
        try:
            message = self.client.beta.messages.create(**params)
        except Exception as error:  # mapped to our error types, re-raised below
            raise map_error(error) from error
        response = parse_message(message, model)
        response.latency_ms = (time.perf_counter() - started) * 1000
        return response

    def stream(self, request: LLMRequest, model: str) -> Iterator[StreamEvent]:
        if request.tools:
            # Tool loops use complete(); streaming is for final answers shown to a person.
            raise LLMBadRequest("streaming with tools is not supported here; use complete()")
        params = build_params(request, model)
        started = time.perf_counter()
        try:
            with self.client.beta.messages.stream(**params) as stream:
                for event in stream:
                    if getattr(event, "type", None) == "text":
                        yield StreamEvent("text", text=event.text)
                message = stream.get_final_message()
        except Exception as error:
            raise map_error(error) from error
        response = parse_message(message, model)
        response.latency_ms = (time.perf_counter() - started) * 1000
        yield StreamEvent("final", response=response)
