"""Load a pack's documents: Markdown with a small YAML front matter block."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Document:
    id: str
    tenant: str
    title: str
    type: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)


def parse(raw: str, tenant: str, fallback_id: str) -> Document:
    meta: dict[str, Any] = {}
    body = raw
    if raw.startswith("---"):
        _, front, body = raw.split("---", 2)
        meta = yaml.safe_load(front) or {}
        body = body.lstrip("\n")
    return Document(
        id=str(meta.get("id", fallback_id)), tenant=tenant, title=str(meta.get("title", fallback_id)),
        type=str(meta.get("type", "document")), text=body.replace("\r\n", "\n"), meta=meta,
    )


def load_corpus(directory: Path, tenant: str) -> list[Document]:
    docs = [parse(p.read_text(encoding="utf-8"), tenant, p.stem) for p in sorted(directory.glob("*.md"))]
    ids = [d.id for d in docs]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate document ids in {directory}")
    return docs
