"""Approval requests: the only path from a model's proposal to a real action.

An approval is tied to a hash of the exact tool and arguments, expires, and can only be
decided by a user holding the tenant's approver role. Approving runs the paired "acts"
tool once; the outcome and every decision go to the audit log.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

from fieldcopilot.agent.tools import ToolContext, ToolRegistry, ToolResult, run_tool

Status = Literal["pending", "approved", "rejected", "expired", "executed", "failed"]


class ApprovalError(Exception):
    pass


def content_hash(tool: str, args: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({"tool": tool, "args": args}, sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class Approval:
    id: str
    tenant: str
    run_id: str
    tool: str          # the "acts" tool that will run
    args: dict[str, Any]
    summary: str
    hash: str
    created_at: datetime
    expires_at: datetime
    status: Status = "pending"
    decided_by: str | None = None
    decided_at: datetime | None = None
    reason: str | None = None
    outcome: str | None = None


@dataclass
class User:
    name: str
    tenant: str
    roles: frozenset[str] = field(default_factory=frozenset)


AuditSink = Callable[[str, dict[str, Any]], None]


class ApprovalService:
    def __init__(self, registry: ToolRegistry, approver_roles: dict[str, str],
                 ttl_minutes: dict[str, int] | None = None, now: Callable[[], datetime] | None = None,
                 audit: AuditSink | None = None):
        self.registry = registry
        self.approver_roles = approver_roles
        self.ttl = ttl_minutes or {}
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.audit = audit or (lambda event, data: None)
        self._items: dict[str, Approval] = {}
        self._ids = itertools.count(1)

    def request(self, tenant: str, run_id: str, tool: str, args: dict[str, Any], summary: str) -> Approval:
        self.registry.get(tool)  # must exist
        now = self.now()
        approval = Approval(
            id=f"APR-{next(self._ids):04d}", tenant=tenant, run_id=run_id, tool=tool, args=args, summary=summary,
            hash=content_hash(tool, args), created_at=now, expires_at=now + timedelta(minutes=self.ttl.get(tenant, 15)),
        )
        self._items[approval.id] = approval
        self.audit("approval.requested", {"id": approval.id, "tenant": tenant, "tool": tool, "args": args, "run": run_id})
        return approval

    def get(self, approval_id: str, tenant: str) -> Approval:
        approval = self._items.get(approval_id)
        if approval is None or approval.tenant != tenant:  # never reveal another tenant's approvals
            raise ApprovalError(f"no approval {approval_id}")
        if approval.status == "pending" and self.now() >= approval.expires_at:
            approval.status = "expired"
            self.audit("approval.expired", {"id": approval.id, "tenant": tenant})
        return approval

    def pending(self, tenant: str) -> list[Approval]:
        return [a for a in self._items.values() if a.tenant == tenant and self.get(a.id, tenant).status == "pending"]

    def _check(self, approval: Approval, user: User, seen_hash: str) -> None:
        if user.tenant != approval.tenant:
            raise ApprovalError("user belongs to another tenant")
        if self.approver_roles.get(approval.tenant) not in user.roles:
            raise ApprovalError(f"{user.name} lacks the {self.approver_roles.get(approval.tenant)} role")
        if approval.status != "pending":
            raise ApprovalError(f"{approval.id} is {approval.status}")
        if seen_hash != approval.hash:
            raise ApprovalError("the request changed after it was shown; review it again")

    def reject(self, approval_id: str, user: User, seen_hash: str, reason: str) -> Approval:
        approval = self.get(approval_id, user.tenant)
        self._check(approval, user, seen_hash)
        approval.status, approval.decided_by, approval.decided_at, approval.reason = "rejected", user.name, self.now(), reason
        self.audit("approval.rejected", {"id": approval.id, "by": user.name, "reason": reason})
        return approval

    def approve(self, approval_id: str, user: User, seen_hash: str, reason: str = "") -> tuple[Approval, ToolResult]:
        approval = self.get(approval_id, user.tenant)
        self._check(approval, user, seen_hash)
        approval.status, approval.decided_by, approval.decided_at, approval.reason = "approved", user.name, self.now(), reason
        self.audit("approval.approved", {"id": approval.id, "by": user.name, "reason": reason})
        tool = self.registry.get(approval.tool)
        if tool.side_effect != "acts":
            raise ApprovalError(f"{tool.name} is not an executable action")
        result, _ = run_tool(tool, ToolContext(approval.tenant, approval.run_id, user.name), approval.args)
        approval.status = "failed" if result.is_error else "executed"
        approval.outcome = result.as_text()[:2000]
        self.audit("approval.executed", {"id": approval.id, "status": approval.status, "outcome": approval.outcome[:500]})
        return approval, result
