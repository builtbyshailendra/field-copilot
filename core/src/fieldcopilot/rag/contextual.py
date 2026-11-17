"""Contextual retrieval: prepend a short, model-written context to each chunk before indexing.

A chunk like "at least 6 weeks of conservative therapy" means little on its own; with
"From MP-114, the lumbar spine MRI policy, criterion B for radiculopathy" in front of
it, both keyword and vector search find it for the right questions.

Cost control:
- The whole document is in the system prompt, which carries a cache breakpoint, so
  every chunk after the first in a document reads the document from cache.
- Results are cached on disk by prompt version + chunk content. Re-indexing an
  unchanged corpus makes no model calls.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fieldcopilot.llm import LLM, LLMError, LLMRequest, Scripted
from fieldcopilot.prompts import PromptRegistry
from fieldcopilot.rag.chunking import Chunk
from fieldcopilot.rag.documents import Document


class Contextualizer:
    def __init__(self, llm: LLM, registry: PromptRegistry | None = None, cache_file: Path | None = None):
        self.llm = llm
        self.prompt = (registry or PromptRegistry()).get("contextualize")
        self.cache_file = cache_file
        self.cache: dict[str, str] = {}
        if cache_file and cache_file.exists():
            self.cache = json.loads(cache_file.read_text(encoding="utf-8"))
        self.calls = 0
        self.failures = 0

    def _key(self, chunk: Chunk) -> str:
        return hashlib.sha256(f"{self.prompt.version}|{chunk.doc_id}|{chunk.heading}|{chunk.text}".encode()).hexdigest()[:20]

    def contextualize(self, doc: Document, chunks: list[Chunk]) -> list[Chunk]:
        for chunk in chunks:
            key = self._key(chunk)
            if key not in self.cache:
                request = self.prompt.request(
                    tenant=doc.tenant, title=doc.title, document=doc.text, heading=chunk.heading, chunk=chunk.text)
                try:
                    self.cache[key] = self.llm.complete(request).text.strip()
                    self.calls += 1
                except LLMError:
                    # Retrieval still works without context; the chunk is indexed as is.
                    self.failures += 1
                    continue
            chunk.context = self.cache[key]
        self._save()
        return chunks

    def _save(self) -> None:
        if self.cache_file:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(self.cache, indent=1, sort_keys=True), encoding="utf-8")


def fake_context(request: LLMRequest) -> Scripted:
    """Fake-model stand-in: names the document and the section. Deterministic, no model."""
    title = request.system.split('<document title="', 1)[1].split('"', 1)[0]
    content = request.messages[-1]["content"]
    heading = content.split("\n", 1)[0].removeprefix("Section:").strip()
    return Scripted(f"This section is from \"{title}\" and covers {heading}.")
