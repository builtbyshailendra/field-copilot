"""A scripted model for tests, CI and local development without an API key.

Scripts are rules: "when the request looks like this, reply like that". A rule can
match on the request's purpose or on a function, and reply with a fixed `Scripted`
value, a function of the request, or a queue of replies (to simulate several turns or
failures in order). A request no rule matches is an error, so a test never passes by
accident on an unscripted call.
"""
from __future__ import annotations

import copy
import itertools
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from fieldcopilot.llm.errors import LLMBadRequest, LLMError
from fieldcopilot.llm.providers.base import estimate_tokens
from fieldcopilot.llm.types import LLMRequest, LLMResponse, StreamEvent, ToolCall, Usage


@dataclass
class Scripted:
    text: str | dict | list = ""
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    stop_reason: str | None = None


Reply = Scripted | LLMError | Callable[[LLMRequest], "Scripted | LLMError"]
Matcher = str | Callable[[LLMRequest], bool]


@dataclass
class _Rule:
    matcher: Matcher
    replies: deque | Reply
    repeat_last: bool

    def matches(self, request: LLMRequest) -> bool:
        if isinstance(self.matcher, str):
            return request.purpose == self.matcher
        return bool(self.matcher(request))


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self._rules: list[_Rule] = []
        self.calls: list[tuple[LLMRequest, str]] = []
        self._ids = itertools.count(1)

    def on(self, matcher: Matcher, *replies: Reply, repeat_last: bool = True) -> "FakeProvider":
        """Add a rule. Several replies are used in order; the last repeats unless told not to."""
        if not replies:
            raise ValueError("a rule needs at least one reply")
        queued: deque | Reply = deque(replies) if len(replies) > 1 or not repeat_last else replies[0]
        self._rules.append(_Rule(matcher, queued, repeat_last))
        return self

    # ---- Provider interface -------------------------------------------------
    delay_s: float = 0.0  # simulated model latency, for load tests only

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        # A snapshot: callers keep appending to the same message list after the call.
        self.calls.append((copy.deepcopy(request), model))
        if self.delay_s:
            time.sleep(self.delay_s)
        reply = self._next_reply(request)
        if isinstance(reply, LLMError):
            raise reply
        return self._to_response(request, model, reply)

    def stream(self, request: LLMRequest, model: str) -> Iterator[StreamEvent]:
        response = self.complete(request, model)
        for i, word in enumerate(response.text.split(" ")):
            yield StreamEvent("text", text=word if i == 0 else " " + word)
        yield StreamEvent("final", response=response)

    # ---- internals ------------------------------------------------------------
    def _next_reply(self, request: LLMRequest) -> Scripted | LLMError:
        for rule in self._rules:
            if not rule.matches(request):
                continue
            if isinstance(rule.replies, deque):
                if not rule.replies:
                    raise LLMBadRequest(f"fake script for {request.purpose!r} ran out of replies")
                reply = rule.replies[0] if (len(rule.replies) == 1 and rule.repeat_last) else rule.replies.popleft()
            else:
                reply = rule.replies
            if callable(reply) and not isinstance(reply, (Scripted, LLMError)):
                reply = reply(request)
            return reply
        raise LLMBadRequest(f"no fake script for purpose {request.purpose!r}")

    def _to_response(self, request: LLMRequest, model: str, reply: Scripted) -> LLMResponse:
        text = reply.text if isinstance(reply.text, str) else json.dumps(reply.text)
        calls = [ToolCall(f"toolu_fake_{next(self._ids)}", name, args) for name, args in reply.tool_calls]
        stop = reply.stop_reason or ("tool_use" if calls else "end_turn")
        content: list[dict[str, Any]] = []
        if text:
            content.append({"type": "text", "text": text})
        content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls]
        prompt = request.system + json.dumps(request.messages, default=str)
        usage = Usage(input_tokens=estimate_tokens(prompt), output_tokens=estimate_tokens(text + json.dumps(reply.tool_calls)))
        return LLMResponse(
            text=text, stop_reason=stop, model=model, provider=self.name,
            tool_calls=calls, usage=usage, assistant_content=content,
        )
