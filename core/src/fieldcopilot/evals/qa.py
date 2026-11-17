"""Grounded-answer evaluation: right fact, right citation, and refusing when it should."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from fieldcopilot.answer.grounded import GroundedAnswerer
from fieldcopilot.llm import LLM, LLMError
from fieldcopilot.packs import load_pack
from fieldcopilot.rag.retriever import Retriever


@dataclass
class QAScore:
    tenant: str
    n: int
    correct: float            # answerable questions: verified claim states the fact and cites an expected chunk
    refusal_correct: float    # unanswerable questions: said the documents don't cover it
    false_answers: int        # unanswerable questions that got an answer anyway
    unverified: int           # answers dropped because nothing verified
    errors: int


def load_qa(tenant: str) -> list[dict]:
    root = load_pack(tenant).evals_dir
    rows = yaml.safe_load((root / "qa.yaml").read_text(encoding="utf-8"))
    extra = root / "qa_from_feedback.yaml"
    if extra.exists():
        rows += yaml.safe_load(extra.read_text(encoding="utf-8")) or []
    return rows


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def evaluate(llm: LLM, retriever: Retriever, tenant: str) -> QAScore:
    answerer = GroundedAnswerer(llm, retriever, tenant)
    rows = load_qa(tenant)
    answerable = [r for r in rows if r.get("expect") != "not_in_documents"]
    unanswerable = [r for r in rows if r.get("expect") == "not_in_documents"]
    correct = refused = false_answers = unverified = errors = 0
    for row in rows:
        try:
            result = answerer.answer(row["question"])
        except LLMError:
            errors += 1
            continue
        if result.status == "unverified":
            unverified += 1
        if row.get("expect") == "not_in_documents":
            if result.status == "not_in_documents":
                refused += 1
            elif result.status == "answered":
                false_answers += 1
            continue
        text = _norm(" ".join(c.text + " " + " ".join(ci.quote for ci in c.citations) for c in result.claims))
        cited = {ci.chunk_id for c in result.claims for ci in c.citations}
        has_fact = all(any(_norm(alt) in text for alt in group.split("|")) for group in row["facts"])
        if result.status == "answered" and has_fact and cited & set(row["cite"]):
            correct += 1
    return QAScore(tenant, len(rows), round(correct / max(1, len(answerable)), 3),
                   round(refused / max(1, len(unanswerable)), 3), false_answers, unverified, errors)
