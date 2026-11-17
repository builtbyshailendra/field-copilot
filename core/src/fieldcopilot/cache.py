"""A semantic cache for document answers.

On-call engineers and review nurses ask the same handful of questions again and again
("who approves a rollback at night?", "what AHI qualifies for CPAP?"). A verified answer
to one can be reused for a question that means the same thing, saving the model call,
which is the slow and costly part.

The danger is a near-miss: "how often do we post updates in a SEV1?" and "... in a SEV3?"
look almost identical to an embedding model and have different answers. Measured on
evals/datasets/cache_pairs.yaml (evals/results/cache/summary.md), bge-small at a 0.90
threshold would have served a wrong answer to 5 of 13 near-miss questions. So a cached
answer is reused only if all of these hold:

1. **Same tenant**, and the same documents and answer prompt (the key includes a
   fingerprint of the indexed chunks and the prompt's content hash, so editing either
   empties the cache in effect).
2. **The question is very close:** cosine similarity at or above the threshold, which is
   set from that measurement, not from intuition.
3. **The specifics match:** the same numbers and identifiers (SEV1, 214, orders-consumer,
   SIGKILL, AHI...). Most dangerous near-misses differ in exactly one of these.
4. **The evidence agrees:** retrieval for the new question still returns every passage
   the cached answer cited.
5. **Not stale:** within the time-to-live.

Only document answers whose citations were verified are stored. Investigations and case
reviews never go through here: they depend on live cluster or case state.

It lives in process memory, one per tenant per replica. That keeps it simple and keeps
tenants apart; the cost is a lower hit rate with many replicas.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from fieldcopilot.answer.grounded import AnswerResult, GroundedAnswerer
from fieldcopilot.rag.embeddings import Embedder


_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_IDENTIFIER = re.compile(
    r"\b(?:[A-Z]{2,}[A-Z0-9]*"                          # SIGKILL, AHI, MRI, SEV1
    r"|[A-Za-z]+[a-z][A-Z][A-Za-z0-9]*"                  # OrdersQueueLagHigh, PostgreSQL
    r"|[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z][A-Za-z0-9]*)+)\b")  # orders-consumer, max_connections


def key_terms(question: str) -> frozenset[str]:
    """Numbers and identifiers in a question: the parts a near-miss usually changes."""
    terms = {n for n in _NUMBER.findall(question)}
    terms |= {m.lower() for m in _IDENTIFIER.findall(question)}
    return frozenset(terms)


@dataclass
class Entry:
    question: str
    vector: np.ndarray
    result: AnswerResult
    cited: frozenset[str]
    created: float
    hits: int = 0


@dataclass
class Lookup:
    outcome: str  # hit | miss_empty | miss_not_similar | miss_terms_differ | miss_sources_differ | miss_expired
    similarity: float = 0.0
    result: AnswerResult | None = None
    matched_question: str = ""


@dataclass
class SemanticCache:
    """mode "on" serves hits; mode "shadow" only records what it would have served and
    whether a fresh answer agreed, so a customer can see the agreement rate on real
    traffic before turning it on. The 0.98 default sits above the closest near-miss in
    the evaluation set (0.967, "main" vs "reporting" PostgreSQL cluster)."""

    embedder: Embedder
    threshold: float = 0.98
    mode: str = "on"  # on | shadow
    ttl_seconds: float = 24 * 3600
    max_entries: int = 2000
    check_terms: bool = True
    check_sources: bool = True
    clock: Callable[[], float] = time.time
    _entries: dict[str, list[Entry]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _versions: dict[tuple, str] = field(default_factory=dict)

    def version(self, answerer: GroundedAnswerer) -> str:
        """Documents + prompt fingerprint: a change to either starts a fresh cache."""
        chunks = getattr(answerer.retriever.index, "chunks", [])
        memo = (id(answerer.retriever.index), len(chunks), answerer.prompt.version)
        if memo in self._versions:
            return self._versions[memo]
        digest = hashlib.sha256()
        for chunk in chunks:
            digest.update(chunk.id.encode())
            digest.update(hashlib.sha256(chunk.text.encode()).digest())
        digest.update(answerer.prompt.version.encode())
        self._versions[memo] = f"{answerer.tenant}:{digest.hexdigest()[:16]}"
        return self._versions[memo]

    def lookup(self, question: str, answerer: GroundedAnswerer) -> Lookup:
        key = self.version(answerer)
        with self._lock:
            entries = list(self._entries.get(key, []))
        if not entries:
            return Lookup("miss_empty")
        vector = self.embedder.embed_query(question)
        sims = np.array([float(np.dot(vector, e.vector)) for e in entries])
        best = int(np.argmax(sims))
        entry, similarity = entries[best], float(sims[best])
        if similarity < self.threshold:
            return Lookup("miss_not_similar", similarity)
        if self.clock() - entry.created > self.ttl_seconds:
            return Lookup("miss_expired", similarity)
        if self.check_terms and key_terms(question) != key_terms(entry.question):
            return Lookup("miss_terms_differ", similarity, matched_question=entry.question)
        if self.check_sources:
            found = {hit.chunk.id for hit in answerer.retriever.search(question, k=answerer.k)}
            if not entry.cited <= found:
                return Lookup("miss_sources_differ", similarity, matched_question=entry.question)
        entry.hits += 1
        return Lookup("hit", similarity, entry.result, entry.question)

    def store(self, question: str, result: AnswerResult, answerer: GroundedAnswerer) -> bool:
        if result.status != "answered" or not result.claims:
            return False  # only verified answers are worth reusing
        cited = frozenset(c.chunk_id for claim in result.claims for c in claim.citations)
        entry = Entry(question, self.embedder.embed_query(question), result, cited, self.clock())
        key = self.version(answerer)
        with self._lock:
            bucket = self._entries.setdefault(key, [])
            bucket.append(entry)
            if len(bucket) > self.max_entries:  # drop the least used, oldest first
                bucket.sort(key=lambda e: (e.hits, e.created))
                del bucket[0]
        return True

    def size(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._entries.values())
