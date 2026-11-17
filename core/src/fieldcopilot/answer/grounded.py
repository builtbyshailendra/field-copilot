"""Grounded answers: retrieve, answer with citations, then check every citation in code.

The model's answer is a list of claims. Each claim names the passages it relies on and
copies a quote from one of them. Nothing is trusted:
- a citation to a passage that wasn't provided is dropped;
- a quote that doesn't appear in the cited passage (after normalising whitespace and
  quote marks) is dropped;
- a number in the claim that isn't in its quote is dropped ("12 weeks" claimed from a
  quote that says "6 weeks").
Dropped claims are reported, never silently shown. If nothing survives, the user is
told the answer couldn't be verified. What this can't catch: a real sentence read the
wrong way. That's why the quote sits next to the claim in the UI.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from fieldcopilot.llm import LLM, Budget, LLMRequest, Scripted
from fieldcopilot.prompts import PromptRegistry
from fieldcopilot.rag.chunking import Chunk
from fieldcopilot.rag.retriever import Retriever

NUMBER = re.compile(r"\d+(?:\.\d+)?")

TENANT_RULES = {
    "tallowmere-health": "- Never say whether a request should be approved or denied. Policy questions only.",
    "corvane-freight": "- Never present an action as done. Actions are proposals that need approval.",
}
AUDIENCE = {"tallowmere-health": "utilization-review nurses", "corvane-freight": "on-call engineers"}


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    citations: list[str]
    quote: str


class ModelAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["answered", "not_in_documents"]
    claims: list[Claim]


@dataclass
class Citation:
    passage: str
    chunk_id: str
    doc_id: str
    heading: str
    quote: str
    start: int  # offset of the quote inside the chunk text
    end: int


@dataclass
class VerifiedClaim:
    text: str
    citations: list[Citation]


@dataclass
class Dropped:
    text: str
    reason: str


@dataclass
class AnswerResult:
    question: str
    status: Literal["answered", "not_in_documents", "unverified"]
    claims: list[VerifiedClaim] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)
    passages: dict[str, Chunk] = field(default_factory=dict)
    prompt_version: str = ""
    cost_usd: float = 0.0
    latency_ms: float = 0.0


def normalise(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[*_`]", "", text)  # markdown emphasis in the source doesn't count
    return re.sub(r"\s+", " ", text).strip().lower()


def locate(quote: str, passage: str) -> tuple[int, int] | None:
    """Find the quote in the passage, tolerant of whitespace and quote-mark differences."""
    target = normalise(quote)
    if not target:
        return None
    norm_chars: list[str] = []
    mapping: list[int] = []
    prev_space = False
    for i, ch in enumerate(passage):
        c = {"’": "'", "‘": "'", "“": '"', "”": '"'}.get(ch, ch)
        if c in "*_`":
            continue
        if c.isspace():
            if prev_space or not norm_chars:
                continue
            c, prev_space = " ", True
        else:
            prev_space = False
        norm_chars.append(c.lower())
        mapping.append(i)
    haystack = "".join(norm_chars)
    at = haystack.find(target)
    if at < 0:
        return None
    return mapping[at], mapping[at + len(target) - 1] + 1


def verify(answer: ModelAnswer, passages: dict[str, Chunk]) -> tuple[list[VerifiedClaim], list[Dropped]]:
    kept: list[VerifiedClaim] = []
    dropped: list[Dropped] = []
    for claim in answer.claims:
        unknown = [p for p in claim.citations if p not in passages]
        cited = [p for p in claim.citations if p in passages]
        if not cited:
            dropped.append(Dropped(claim.text, f"cites passages that weren't provided: {unknown or 'none'}"))
            continue
        found = next(((p, span) for p in cited if (span := locate(claim.quote, passages[p].text))), None)
        if not found:
            dropped.append(Dropped(claim.text, "quote not found in the cited passage"))
            continue
        missing_numbers = [n for n in NUMBER.findall(claim.text) if n not in NUMBER.findall(claim.quote)]
        if missing_numbers:
            dropped.append(Dropped(claim.text, f"numbers not in the quote: {missing_numbers}"))
            continue
        passage_id, (start, end) = found
        chunk = passages[passage_id]
        kept.append(VerifiedClaim(claim.text, [Citation(passage_id, chunk.id, chunk.doc_id, chunk.heading,
                                                         chunk.text[start:end], start, end)]))
    return kept, dropped


def format_passages(chunks: list[Chunk]) -> tuple[str, dict[str, Chunk]]:
    passages = {f"P{i + 1}": c for i, c in enumerate(chunks)}
    body = "\n\n".join(f'<passage id="{pid}" source="{c.title} > {" > ".join(c.heading_path[1:])}">\n{c.text}\n</passage>'
                       for pid, c in passages.items())
    return body, passages


class GroundedAnswerer:
    def __init__(self, llm: LLM, retriever: Retriever, tenant: str, registry: PromptRegistry | None = None, k: int = 5):
        self.llm, self.retriever, self.tenant, self.k = llm, retriever, tenant, k
        self.prompt = (registry or PromptRegistry()).get("answer", tenant)

    def answer(self, question: str, budget: Budget | None = None) -> AnswerResult:
        started = time.perf_counter()
        hits = self.retriever.search(question, k=self.k)
        body, passages = format_passages([h.chunk for h in hits])
        request = self.prompt.request(
            tenant=self.tenant, question=question, passages=body,
            audience=AUDIENCE.get(self.tenant, "a customer team"), tenant_rules=TENANT_RULES.get(self.tenant, ""))
        model_answer, response = self.llm.structured(request, ModelAnswer, budget)
        result = AnswerResult(question, model_answer.status, passages=passages, prompt_version=self.prompt.version,
                              cost_usd=response.cost_usd)
        if model_answer.status == "answered":
            result.claims, result.dropped = verify(model_answer, passages)
            if not result.claims:
                result.status = "unverified"
        result.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return result


def fake_answer(request: LLMRequest) -> Scripted:
    """Fake model: extractive. Quotes the passage sentence sharing most words with the
    question; refuses when nothing overlaps enough. Plumbing, not a model."""
    from fieldcopilot.rag.bm25 import tokenize

    content = request.messages[-1]["content"]
    question = content.split("\n", 1)[0].removeprefix("Question:").strip()
    q_terms = set(tokenize(question))
    best = (0, None, None)
    for pid, text in re.findall(r'<passage id="(P\d+)" source="[^"]*">\n(.*?)\n</passage>', content, flags=re.S):
        for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
            overlap = len(q_terms & set(tokenize(sentence)))
            if overlap > best[0] and len(sentence.split()) >= 4:
                best = (overlap, pid, sentence.strip())
    if best[0] < 2:
        return Scripted({"status": "not_in_documents", "claims": []})
    _, pid, sentence = best
    return Scripted({"status": "answered", "claims": [{"text": sentence.lstrip("-0123456789. "), "citations": [pid], "quote": sentence}]})
