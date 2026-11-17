from __future__ import annotations

from typing import Iterator, Protocol

from fieldcopilot.llm.types import LLMRequest, LLMResponse, StreamEvent


class Provider(Protocol):
    """Talks to one model backend. Maps every failure onto `fieldcopilot.llm.errors`."""

    name: str

    def complete(self, request: LLMRequest, model: str) -> LLMResponse: ...

    def stream(self, request: LLMRequest, model: str) -> Iterator[StreamEvent]: ...


def estimate_tokens(text: str) -> int:
    """About four characters per token. Only the fake provider uses this."""
    return max(1, len(text) // 4)
