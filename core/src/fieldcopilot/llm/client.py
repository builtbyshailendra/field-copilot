"""The reliability layer every caller uses.

`LLM.complete()` does, in order:
1. Picks the model chain for the request's role (for example strong: Opus, then Sonnet).
2. Checks the budget before the call.
3. Calls the provider; on a retryable error waits with exponential backoff and jitter
   (or the server's retry-after) and tries again, up to `max_attempts` per model.
4. If a model keeps failing, or refuses, moves to the next model in the chain.
5. Prices the call, charges the budget, and reports a record to every hook
   (metrics, tracing and the audit log subscribe here).
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, TypeVar

from pydantic import BaseModel, ValidationError

from fieldcopilot.llm.errors import BudgetExceeded, LLMBadOutput, LLMError, LLMRateLimited, LLMRefused
from fieldcopilot.llm.pricing import cost_usd
from fieldcopilot.llm.providers.base import Provider
from fieldcopilot.llm.types import LLMRequest, LLMResponse, ModelRole, StreamEvent

T = TypeVar("T", bound=BaseModel)

REAL_MODELS: dict[ModelRole, list[str]] = {
    "strong": ["claude-opus-5-5", "claude-sonnet-5-5"],
    "fast": ["claude-haiku-5-5", "claude-sonnet-5-5"],
}
# Claude in Amazon Bedrock uses the same models under `anthropic.`-prefixed IDs.
BEDROCK_MODELS: dict[ModelRole, list[str]] = {
    role: [f"anthropic.{model}" for model in chain] for role, chain in REAL_MODELS.items()
}
FAKE_MODELS: dict[ModelRole, list[str]] = {"strong": ["fake-strong"], "fast": ["fake-fast"]}


@dataclass
class Budget:
    """A spending limit for one run (an investigation, an eval suite, a tenant's day)."""

    max_cost_usd: float = float("inf")
    max_output_tokens: int = 10**9
    spent_usd: float = 0.0
    output_tokens: int = 0

    def check(self) -> None:
        if self.spent_usd >= self.max_cost_usd:
            raise BudgetExceeded(f"cost budget of ${self.max_cost_usd:.2f} used up")
        if self.output_tokens >= self.max_output_tokens:
            raise BudgetExceeded(f"output token budget of {self.max_output_tokens} used up")

    def charge(self, response: LLMResponse) -> None:
        self.spent_usd = round(self.spent_usd + response.cost_usd, 6)
        self.output_tokens += response.usage.output_tokens


@dataclass
class CallRecord:
    purpose: str
    tenant: str | None
    provider: str
    model: str
    outcome: str  # ok | error
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    attempts: int = 0
    fell_back: bool = False


Hook = Callable[[CallRecord], None]


@dataclass
class LLM:
    provider: Provider
    models: dict[ModelRole, list[str]] = field(default_factory=lambda: dict(REAL_MODELS))
    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    sleep: Callable[[float], None] = time.sleep
    hooks: list[Hook] = field(default_factory=list)

    # ---- plain calls ----------------------------------------------------------
    def complete(self, request: LLMRequest, budget: Budget | None = None) -> LLMResponse:
        from fieldcopilot.obs.tracing import tracer

        requested = self.models[request.role][0]
        with tracer.start_as_current_span(f"chat {requested}") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.provider.name", getattr(self.provider, "name", "unknown"))
            span.set_attribute("gen_ai.request.model", requested)
            span.set_attribute("fieldcopilot.purpose", request.purpose)
            span.set_attribute("fieldcopilot.tenant", request.tenant or "none")
            try:
                response = self._complete(request, budget)
            except Exception as error:
                span.set_attribute("error.type", type(error).__name__)
                raise
            span.set_attribute("gen_ai.response.model", response.model)
            span.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens)
            span.set_attribute("gen_ai.response.finish_reasons", [response.stop_reason])
            span.set_attribute("fieldcopilot.cost_usd", response.cost_usd)
            span.set_attribute("fieldcopilot.attempts", response.attempts)
            span.set_attribute("fieldcopilot.fell_back", response.fell_back)
            return response

    def _complete(self, request: LLMRequest, budget: Budget | None = None) -> LLMResponse:
        if budget:
            budget.check()
        chain = self.models[request.role]
        last_error: Exception | None = None
        attempts = 0
        started = time.perf_counter()
        for index, model in enumerate(chain):
            for attempt in range(1, self.max_attempts + 1):
                attempts += 1
                try:
                    response = self.provider.complete(request, model)
                except LLMRefused as error:
                    last_error = error
                    break  # a refusal won't change on retry; try the next model
                except LLMError as error:
                    last_error = error
                    if not error.retryable:
                        self._report(request, model, attempts, started, error=error, fell_back=index > 0)
                        raise
                    if attempt < self.max_attempts:
                        self.sleep(self._delay(attempt, error))
                    continue
                response.attempts = attempts
                response.fell_back = index > 0
                response.cost_usd = cost_usd(response.model, response.usage)
                if not response.latency_ms:
                    response.latency_ms = (time.perf_counter() - started) * 1000
                if budget:
                    budget.charge(response)
                self._report(request, response.model, attempts, started, response=response)
                return response
        assert last_error is not None
        self._report(request, chain[-1], attempts, started, error=last_error, fell_back=len(chain) > 1)
        raise last_error

    def stream(self, request: LLMRequest, budget: Budget | None = None) -> Iterator[StreamEvent]:
        """Streams from the first model in the chain. Before any text arrives, a failure
        falls back to complete() on the rest of the chain; after text has been shown,
        the error is raised, because a person has already seen part of an answer."""
        if budget:
            budget.check()
        model = self.models[request.role][0]
        started = time.perf_counter()
        shown_text = False
        try:
            for event in self.provider.stream(request, model):
                if event.type == "text":
                    shown_text = True
                    yield event
                elif event.response is not None:
                    response = event.response
                    response.cost_usd = cost_usd(response.model, response.usage)
                    if budget:
                        budget.charge(response)
                    self._report(request, response.model, 1, started, response=response)
                    yield event
        except LLMError as error:
            bad_request = not error.retryable and not isinstance(error, LLMRefused)
            if shown_text or bad_request:
                self._report(request, model, 1, started, error=error)
                raise
            response = self.complete(request, budget)
            yield StreamEvent("text", text=response.text)
            yield StreamEvent("final", response=response)

    # ---- structured output ------------------------------------------------------
    def structured(self, request: LLMRequest, schema: type[T], budget: Budget | None = None) -> tuple[T, LLMResponse]:
        """Ask for JSON matching a Pydantic model; validate; one repair attempt on failure."""
        request.output_schema = json_schema_for(schema)
        response = self.complete(request, budget)
        try:
            parsed = schema.model_validate_json(response.text)
        except ValidationError as error:
            repair = LLMRequest(
                purpose=request.purpose, system=request.system, role=request.role,
                messages=request.messages + [
                    {"role": "assistant", "content": response.assistant_content or response.text},
                    {"role": "user", "content": f"That reply didn't match the schema: {error.errors()[:3]}. Reply again with valid JSON only."},
                ],
                output_schema=request.output_schema, max_tokens=request.max_tokens,
                effort=request.effort, tenant=request.tenant,
            )
            response = self.complete(repair, budget)
            try:
                parsed = schema.model_validate_json(response.text)
            except ValidationError as second:
                raise LLMBadOutput(f"reply didn't match {schema.__name__}: {second.errors()[:3]}") from second
        response.parsed = parsed
        return parsed, response

    # ---- internals ----------------------------------------------------------------
    def _delay(self, attempt: int, error: LLMError) -> float:
        if isinstance(error, LLMRateLimited) and error.retry_after:
            return min(error.retry_after, self.max_delay)
        backoff = min(self.base_delay * 2 ** (attempt - 1), self.max_delay)
        return backoff * (0.5 + random.random() / 2)

    def _report(self, request: LLMRequest, model: str, attempts: int, started: float,
                response: LLMResponse | None = None, error: Exception | None = None, fell_back: bool = False) -> None:
        record = CallRecord(
            purpose=request.purpose, tenant=request.tenant,
            provider=response.provider if response else getattr(self.provider, "name", "?"),
            model=model, outcome="ok" if response else "error",
            error=type(error).__name__ if error else None, attempts=attempts,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            fell_back=response.fell_back if response else fell_back,
        )
        if response:
            record.input_tokens = response.usage.input_tokens
            record.output_tokens = response.usage.output_tokens
            record.cache_read_tokens = response.usage.cache_read_tokens
            record.cost_usd = response.cost_usd
        for hook in self.hooks:
            hook(record)


def json_schema_for(schema: type[BaseModel]) -> dict[str, Any]:
    """Pydantic's schema, checked for what structured outputs require."""
    data = schema.model_json_schema()
    if data.get("additionalProperties") is not False:
        raise ValueError(f"{schema.__name__} must set model_config = ConfigDict(extra='forbid')")
    return json.loads(json.dumps(data))
