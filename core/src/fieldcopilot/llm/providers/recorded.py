"""Record real responses once, replay them for free.

A recording is keyed by everything that changes the model's answer: purpose, model
role, system prompt, messages, tool schemas, output schema and effort. Change a prompt
and the key changes, so a stale recording is never replayed silently: the replay fails
with "no recording" and the evaluation has to be re-recorded against the real model.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterator

from fieldcopilot.llm.errors import LLMBadRequest
from fieldcopilot.llm.providers.base import Provider
from fieldcopilot.llm.types import LLMRequest, LLMResponse, StreamEvent, ToolCall, Usage


def request_key(request: LLMRequest) -> str:
    canonical = {
        "purpose": request.purpose,
        "role": request.role,
        "system": request.system,
        "messages": request.messages,
        "tools": [{"name": t.name, "schema": t.input_schema} for t in request.tools],
        "output_schema": request.output_schema,
        "effort": request.effort,
    }
    blob = json.dumps(canonical, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def _path(root: Path, request: LLMRequest) -> Path:
    return root / request.purpose / f"{request_key(request)}.json"


def _dump(response: LLMResponse) -> dict:
    data = asdict(response)
    data.pop("parsed", None)
    return data


def _load(data: dict) -> LLMResponse:
    data = dict(data)
    data["tool_calls"] = [ToolCall(**c) for c in data.get("tool_calls", [])]
    data["usage"] = Usage(**data.get("usage", {}))
    return LLMResponse(**data)


class RecordedProvider:
    name = "recorded"

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        path = _path(self.root, request)
        if not path.exists():
            raise LLMBadRequest(f"no recording for {request.purpose!r} ({path.name}); record it against the real model")
        response = _load(json.loads(path.read_text(encoding="utf-8")))
        response.provider = self.name
        return response

    def stream(self, request: LLMRequest, model: str) -> Iterator[StreamEvent]:
        response = self.complete(request, model)
        yield StreamEvent("text", text=response.text)
        yield StreamEvent("final", response=response)


class RecordingProvider:
    """Wraps a real provider and saves every successful response."""

    def __init__(self, inner: Provider, root: str | Path):
        self.inner = inner
        self.root = Path(root)
        self.name = f"{inner.name}+recording"

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        response = self.inner.complete(request, model)
        self._save(request, response)
        return response

    def stream(self, request: LLMRequest, model: str) -> Iterator[StreamEvent]:
        for event in self.inner.stream(request, model):
            if event.type == "final" and event.response is not None:
                self._save(request, event.response)
            yield event

    def _save(self, request: LLMRequest, response: LLMResponse) -> None:
        path = _path(self.root, request)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_dump(response), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
