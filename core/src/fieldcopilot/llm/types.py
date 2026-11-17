"""Provider-neutral request and response types.

Messages use the Anthropic Messages format (role + content blocks) because it is the
documented shape the real provider sends, and it is simple enough for the fake and
recorded providers to read. Nothing outside `llm/providers/anthropic.py` imports the SDK.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ModelRole = Literal["fast", "strong"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]
StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "pause_turn", "stop_sequence"]


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. `input_schema` must set additionalProperties: false."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def as_api(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema, "strict": True}


@dataclass
class LLMRequest:
    """One call to a model.

    `purpose` names the job (for example "answer", "route", "contextualize"). The fake
    provider scripts responses by purpose, metrics are labelled by it, and the router
    reads it to pick a model.
    """

    purpose: str
    system: str
    messages: list[dict[str, Any]]
    role: ModelRole = "strong"
    tools: list[ToolSpec] = field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    max_tokens: int = 16000
    effort: Effort = "medium"
    cache_system: bool = True
    tenant: str | None = None


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass
class LLMResponse:
    text: str
    stop_reason: StopReason
    model: str
    provider: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    # The assistant turn exactly as it must be sent back (thinking blocks included).
    assistant_content: list[dict[str, Any]] = field(default_factory=list)
    parsed: Any = None
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    attempts: int = 1
    fell_back: bool = False


@dataclass(frozen=True)
class StreamEvent:
    """What a streaming call yields: text as it arrives, then one final event."""

    type: Literal["text", "final"]
    text: str = ""
    response: LLMResponse | None = None
