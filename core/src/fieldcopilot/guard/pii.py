"""Redact PII and secrets from anything that will be logged, traced or audited.

Pattern-based, so it is fast and predictable, and it has known limits: it catches
structured identifiers (emails, phone numbers, SSNs, card numbers that pass the Luhn
check, member IDs, NPIs, API keys, tokens, passwords in key=value form, private keys).
It does not catch free-text names; minimum-necessary views keep names out of prompts
in the first place, and that is the main defence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


def luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{16,}=*")),
    ("password", re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)\b\s*[:=]\s*\S+")),
    ("connection_string", re.compile(r"\b[a-z+]+://[^\s:/@]+:[^\s@]+@[^\s]+")),
]
PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("member_id", re.compile(r"\bTM-\d{5}\b")),
    ("phone", re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]\d{3}[\s.\-]\d{4}(?!\w)")),
]
CARD = re.compile(r"\b(?:\d[ \-]?){13,19}\b")
NPI = re.compile(r"\b\d{10}\b")


@dataclass
class Finding:
    kind: str
    start: int
    end: int


def find(text: str, include_pii: bool = True) -> list[Finding]:
    findings = [Finding(kind, m.start(), m.end()) for kind, p in SECRET_PATTERNS for m in p.finditer(text)]
    if include_pii:
        findings += [Finding(kind, m.start(), m.end()) for kind, p in PII_PATTERNS for m in p.finditer(text)]
        for m in CARD.finditer(text):
            digits = re.sub(r"\D", "", m.group(0))
            if 13 <= len(digits) <= 19 and luhn_ok(digits):
                findings.append(Finding("card_number", m.start(), m.end()))
        for m in NPI.finditer(text):
            if luhn_ok("80840" + m.group(0)):
                findings.append(Finding("npi", m.start(), m.end()))
    # keep the outermost finding when they overlap
    findings.sort(key=lambda f: (f.start, -(f.end - f.start)))
    kept: list[Finding] = []
    for f in findings:
        if not kept or f.start >= kept[-1].end:
            kept.append(f)
    return kept


def redact(text: str, include_pii: bool = True) -> tuple[str, list[str]]:
    findings = find(text, include_pii)
    out, last = [], 0
    for f in findings:
        out.append(text[last:f.start])
        out.append(f"[{f.kind.upper()}]")
        last = f.end
    out.append(text[last:])
    return "".join(out), [f.kind for f in findings]


def contains_secret(text: str) -> bool:
    return bool(find(text, include_pii=False))
