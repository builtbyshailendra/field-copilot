"""The LLM layer. Everything else in the platform calls models through `LLM`.

Pick the backend with FIELD_LLM_MODE:
- fake: a scripted FakeProvider (tests, CI, local dev)
- recorded: replay saved responses from FIELD_RECORDINGS (free, deterministic)
- record: call Claude and save every response
- anthropic: call Claude
"""
from __future__ import annotations

import os
from pathlib import Path

from fieldcopilot.llm.client import FAKE_MODELS, REAL_MODELS, LLM, Budget, CallRecord
from fieldcopilot.llm.errors import (
    BudgetExceeded, LLMBadOutput, LLMBadRequest, LLMError, LLMRateLimited, LLMRefused, LLMTimeout, LLMUnavailable,
)
from fieldcopilot.llm.providers.fake import FakeProvider, Scripted
from fieldcopilot.llm.providers.recorded import RecordedProvider, RecordingProvider
from fieldcopilot.llm.types import LLMRequest, LLMResponse, StreamEvent, ToolCall, ToolSpec, Usage

DEFAULT_RECORDINGS = Path(__file__).resolve().parents[3] / "recordings"


def build_llm(mode: str | None = None, fake: FakeProvider | None = None, recordings: str | Path | None = None) -> LLM:
    mode = (mode or os.environ.get("FIELD_LLM_MODE", "fake")).lower()
    root = Path(recordings or os.environ.get("FIELD_RECORDINGS", DEFAULT_RECORDINGS))
    if mode == "fake":
        return LLM(fake or FakeProvider(), models=dict(FAKE_MODELS))
    if mode == "recorded":
        return LLM(RecordedProvider(root), models=dict(REAL_MODELS), max_attempts=1)
    from fieldcopilot.llm.providers.anthropic import AnthropicProvider

    if mode == "record":
        return LLM(RecordingProvider(AnthropicProvider(), root), models=dict(REAL_MODELS))
    if mode == "anthropic":
        return LLM(AnthropicProvider(), models=dict(REAL_MODELS))
    raise ValueError(f"unknown FIELD_LLM_MODE {mode!r}")


__all__ = [
    "LLM", "Budget", "CallRecord", "build_llm", "FakeProvider", "Scripted", "RecordedProvider", "RecordingProvider",
    "LLMRequest", "LLMResponse", "StreamEvent", "ToolCall", "ToolSpec", "Usage",
    "LLMError", "LLMTimeout", "LLMRateLimited", "LLMUnavailable", "LLMRefused", "LLMBadRequest", "LLMBadOutput",
    "BudgetExceeded", "FAKE_MODELS", "REAL_MODELS",
]
