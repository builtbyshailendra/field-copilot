"""Heading-aware chunking.

Chunks never cross a heading, so a chunk is always about one thing, and each one keeps
its heading path ("MP-114 > Criteria > B. Persistent radiculopathy"). Long sections
are split at paragraph boundaries. Every chunk records its exact character offsets in
the document, which is what lets a citation be checked against the source later.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from fieldcopilot.rag.documents import Document

HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


@dataclass
class Chunk:
    id: str
    doc_id: str
    tenant: str
    title: str
    heading_path: tuple[str, ...]
    text: str
    start: int
    end: int
    context: str = ""

    @property
    def heading(self) -> str:
        return " > ".join(self.heading_path)

    @property
    def index_text(self) -> str:
        """What gets embedded and keyword-indexed: context, headings, then the text."""
        parts = [self.context] if self.context else []
        parts += [self.heading, self.text]
        return "\n".join(parts)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "section"


def _sections(doc: Document):
    """Yield (heading_path, body_start, body_end) for each heading's body."""
    stack: list[tuple[int, str]] = []
    lines = doc.text.split("\n")
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    current_start = 0
    current_path: tuple[str, ...] = ()
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if not m:
            continue
        yield current_path, current_start, offsets[i]
        level, title = len(m.group(1)), m.group(2)
        stack = [(lvl, t) for lvl, t in stack if lvl < level] + [(level, title)]
        current_path = tuple(t for _, t in stack)
        current_start = offsets[i] + len(line) + 1
    yield current_path, current_start, len(doc.text)


def chunk_document(doc: Document, max_words: int = 160, min_words: int = 4) -> list[Chunk]:
    chunks: list[Chunk] = []
    used_ids: dict[str, int] = {}
    for path, start, end in _sections(doc):
        body = doc.text[start:end]
        paragraphs = [(m.start() + start, m.end() + start) for m in re.finditer(r"\S(?:.*\S)?(?:\n(?!\n).*\S)*", body)]
        group: list[tuple[int, int]] = []
        words = 0

        def emit() -> None:
            nonlocal group, words
            if not group:
                return
            s, e = group[0][0], group[-1][1]
            text = doc.text[s:e]
            if len(text.split()) >= min_words:
                base = f"{doc.id}#{_slug(path[-1] if path else doc.title)}"
                n = used_ids.get(base, 0)
                used_ids[base] = n + 1
                chunks.append(Chunk(
                    id=base if n == 0 else f"{base}-{n + 1}", doc_id=doc.id, tenant=doc.tenant, title=doc.title,
                    heading_path=path or (doc.title,), text=text, start=s, end=e,
                ))
            group, words = [], 0

        for p_start, p_end in paragraphs:
            p_words = len(doc.text[p_start:p_end].split())
            if group and words + p_words > max_words:
                emit()
            group.append((p_start, p_end))
            words += p_words
        emit()
    return chunks


def chunk_corpus(docs: list[Document], max_words: int = 160) -> list[Chunk]:
    return [c for d in docs for c in chunk_document(d, max_words)]
