"""Output policy: what each customer's copilot must never say, checked before anyone sees it.

- Tallowmere: no coverage decisions or recommendations ("approved", "should be denied").
- Corvane: no claim that an action ran unless an approval actually executed it.
- Everyone: no secrets in an answer.

A violating answer is replaced with a safe message and the violation is recorded; the
original is kept in the (redacted) trace for review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from fieldcopilot.guard.pii import contains_secret

RULES: dict[str, list[tuple[str, re.Pattern]]] = {
    "tallowmere-health": [
        ("coverage_decision", re.compile(
            r"(?i)\b(is|are|be|been|should be|will be|would be|recommend(ing)?|i)\s+(approved|denied|approve|deny|approving|denying)\b")),
        ("coverage_decision", re.compile(r"(?i)\b(meets|doesn't meet|does not meet) medical necessity\b")),
    ],
    "corvane-freight": [
        ("claimed_action", re.compile(
            r"(?i)\bI (have )?(rolled back|restarted|scaled|deleted|deployed|changed|set|enabled|disabled)\b")),
        ("claimed_action", re.compile(r"(?i)\b(has|have) been (rolled back|restarted|scaled|deleted)\b")),
    ],
}

SAFE = {
    "coverage_decision": "I can't make or suggest coverage decisions. Here is what the record and the policy say; the decision is the reviewer's.",
    "claimed_action": "No action has run. Any change is a proposal waiting for approval.",
    "secret": "The answer contained something that looked like a credential, so it was withheld.",
}


@dataclass
class PolicyResult:
    text: str
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def check(tenant: str, text: str, executed_actions: int = 0) -> PolicyResult:
    violations = [name for name, pattern in RULES.get(tenant, []) if pattern.search(text)]
    if executed_actions:  # an approved action really ran; saying so is true
        violations = [v for v in violations if v != "claimed_action"]
    if contains_secret(text):
        violations.append("secret")
    if not violations:
        return PolicyResult(text)
    return PolicyResult(SAFE[violations[0]], sorted(set(violations)))
