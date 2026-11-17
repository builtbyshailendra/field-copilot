"""Fake-model behaviour for prompts, so the platform runs end to end without a key.

These are deliberately simple keyword rules. They make the plumbing testable; they are
not a model and their scores are never reported as model quality.
"""
from __future__ import annotations

import re

from fieldcopilot.llm import LLMRequest, Scripted

_RULES: list[tuple[str, str]] = [
    (r"\b(approve|deny|skip the approval|password|without me checking|ignore your rules|pizza)\b", "out_of_scope"),
    (r"\b(write|draft|prepare)\b.*\b(rfi|letter|request|update)\b|\bwrite an rfi\b", "draft_letter"),
    (r"\b(503|5\d\d s|restarting|crashloop|latency|stuck|lag|disk full|down|alert|errors?)\b", "investigate_incident"),
    (r"\b(pa-\d+|case \d+|deadline|checklist status|who owns|last deployed|missing on)\b", "case_lookup"),
    (r"\b(policy|runbook|procedure|handbook|covered|require)\b", "policy_question"),
]


def message_of(request: LLMRequest) -> str:
    content = request.messages[-1]["content"]
    text = content if isinstance(content, str) else " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return text.removeprefix("Message:").strip()


def keyword_route(request: LLMRequest) -> Scripted:
    text = message_of(request).lower()
    for pattern, route in _RULES:
        if re.search(pattern, text):
            return Scripted({"route": route, "reason": f"keyword rule matched: {pattern[:30]}"})
    return Scripted({"route": "general_question", "reason": "no rule matched"})
