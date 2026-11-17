"""From thumbs-down to test case.

Every rating is stored. A thumbs-down becomes a *candidate* eval case (deduplicated by
the normalised question). A person reviews candidates, writes the expected facts and
citations, and promotes them into the pack's `qa_from_feedback.yaml`, which the QA
evaluation loads. So production mistakes become regression tests, with a human in the
middle deciding what "right" is.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from fieldcopilot.guard.pii import redact
from fieldcopilot.packs import load_pack


@dataclass
class Feedback:
    tenant: str
    question: str
    answer: str
    rating: str  # "up" | "down"
    comment: str = ""
    trace_id: str = ""
    at: str = ""


def _key(question: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", question.lower()).strip()


class FeedbackStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _file(self, tenant: str, kind: str) -> Path:
        return self.root / f"{tenant}.{kind}.jsonl"

    def record(self, fb: Feedback) -> bool:
        """Store a rating (redacted). Returns True if it created a new candidate case."""
        fb.at = fb.at or datetime.now(timezone.utc).isoformat()
        fb.question, _ = redact(fb.question)
        fb.answer, _ = redact(fb.answer)
        fb.comment, _ = redact(fb.comment)
        with self._file(fb.tenant, "ratings").open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(fb)) + "\n")
        if fb.rating != "down":
            return False
        if any(_key(c["question"]) == _key(fb.question) for c in self.candidates(fb.tenant)):
            return False
        with self._file(fb.tenant, "candidates").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": f"fb-{len(self.candidates(fb.tenant)) + 1:03d}", "question": fb.question,
                                "bad_answer": fb.answer, "comment": fb.comment, "trace_id": fb.trace_id}) + "\n")
        return True

    def candidates(self, tenant: str) -> list[dict]:
        path = self._file(tenant, "candidates")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line] if path.exists() else []

    def promote(self, tenant: str, candidate_id: str, facts: list[str] | None = None, cite: list[str] | None = None,
                not_in_documents: bool = False, target: Path | None = None) -> dict:
        candidate = next(c for c in self.candidates(tenant) if c["id"] == candidate_id)
        case = {"id": candidate_id, "question": candidate["question"]}
        case.update({"expect": "not_in_documents"} if not_in_documents else {"facts": facts or [], "cite": cite or []})
        path = target or (load_pack(tenant).evals_dir / "qa_from_feedback.yaml")
        rows = (yaml.safe_load(path.read_text(encoding="utf-8")) or []) if path.exists() else []
        rows.append(case)
        path.write_text(yaml.safe_dump(rows, sort_keys=False), encoding="utf-8")
        return case
