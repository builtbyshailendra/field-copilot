"""Reading cases from prior-auth-workbench, with only the information a task needs.

The copilot has its own read-only token. Minimum necessary: member names and other
identifiers the model doesn't need are removed before anything reaches a prompt.

Integration note (product feedback to the workbench team): the workbench today issues
service tokens only to its connectors and extraction worker. A read-only `copilot`
service principal, and an endpoint to queue an approved RFI letter for the nurse to
send, are needed before this runs against a live workbench. The client below targets
the existing GET /api/cases/{id}; tests use a fixture with the same response shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
import yaml

from fieldcopilot.packs import load_pack


class Workbench(Protocol):
    sent_letters: list[dict[str, Any]]

    def case(self, case_id: int) -> dict[str, Any]: ...

    def queue_letter(self, case_id: int, letter: str, approved_by: str) -> str: ...


def minimum_necessary(case: dict[str, Any]) -> dict[str, Any]:
    """What a prompt may see: no member name, no provider NPI, facts with their quotes."""
    return {
        "case_id": case["id"], "state": case["state"], "priority": case["priority"],
        "member_id": case["member"]["id"], "service": case["service"], "due_at": case["due_at"],
        "documents_present": case.get("documents_present", [d.get("doc_type") for d in case.get("documents", [])]),
        "facts": [{k: f.get(k) for k in ("field", "label", "value", "status", "quote")} for f in case.get("facts", [])],
    }


@dataclass
class FixtureWorkbench:
    cases: dict[int, dict[str, Any]] = field(default_factory=dict)
    sent_letters: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_pack(cls) -> "FixtureWorkbench":
        path = load_pack("tallowmere-health").root / "cases" / "cases.yaml"
        rows = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls({int(r["id"]): r for r in rows})

    def case(self, case_id: int) -> dict[str, Any]:
        if case_id not in self.cases:
            raise KeyError("No such case")  # same answer whether missing or not visible
        return self.cases[case_id]

    def queue_letter(self, case_id: int, letter: str, approved_by: str) -> str:
        self.sent_letters.append({"case_id": case_id, "letter": letter, "approved_by": approved_by})
        return f"letter for case {case_id} queued for sending by {approved_by}"


class WorkbenchClient:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport,
                                  headers={"Authorization": f"Bearer {token}", "X-Requested-With": "field-copilot"})
        self.sent_letters: list[dict[str, Any]] = []

    def case(self, case_id: int) -> dict[str, Any]:
        response = self._http.get(f"/api/cases/{case_id}")
        if response.status_code == 404:
            raise KeyError("No such case")
        response.raise_for_status()
        return response.json()

    def queue_letter(self, case_id: int, letter: str, approved_by: str) -> str:
        response = self._http.post(f"/api/cases/{case_id}/rfi-letters", json={"letter": letter, "approved_by": approved_by})
        response.raise_for_status()
        self.sent_letters.append({"case_id": case_id, "approved_by": approved_by})
        return f"queued (workbench answered {response.status_code})"


def load_requirements() -> dict[str, Any]:
    return yaml.safe_load((load_pack("tallowmere-health").root / "requirements.yaml").read_text(encoding="utf-8"))
