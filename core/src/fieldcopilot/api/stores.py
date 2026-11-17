"""PostgreSQL-backed stores used by the API: approvals, runs, audit, feedback, jobs."""
from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from fieldcopilot.agent.approvals import Approval
from fieldcopilot.api.db import Database
from fieldcopilot.guard.pii import redact, redact_obj


class PgApprovalStore:
    def __init__(self, db: Database, tenant: str):
        self.db, self.tenant = db, tenant

    def next_id(self) -> str:
        with self.db.tenant(self.tenant) as conn:
            return f"APR-{conn.execute('SELECT nextval(%s)', ('approval_seq',)).fetchone()['nextval']:04d}"

    def save(self, a: Approval) -> None:
        with self.db.tenant(self.tenant) as conn:
            conn.execute(
                """INSERT INTO approvals (id, tenant, run_id, tool, args, summary, hash, status, created_at, expires_at,
                                          decided_by, decided_at, reason, outcome)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, decided_by = EXCLUDED.decided_by,
                     decided_at = EXCLUDED.decided_at, reason = EXCLUDED.reason, outcome = EXCLUDED.outcome""",
                (a.id, a.tenant, a.run_id, a.tool, Jsonb(a.args), a.summary, a.hash, a.status, a.created_at, a.expires_at,
                 a.decided_by, a.decided_at, a.reason, a.outcome))

    @staticmethod
    def _row(r: dict) -> Approval:
        return Approval(id=r["id"], tenant=r["tenant"], run_id=r["run_id"], tool=r["tool"], args=r["args"],
                        summary=r["summary"], hash=r["hash"], created_at=r["created_at"], expires_at=r["expires_at"],
                        status=r["status"], decided_by=r["decided_by"], decided_at=r["decided_at"], reason=r["reason"],
                        outcome=r["outcome"])

    def load(self, approval_id: str) -> Approval | None:
        with self.db.tenant(self.tenant) as conn:
            row = conn.execute("SELECT * FROM approvals WHERE id = %s", (approval_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, tenant: str) -> list[Approval]:
        with self.db.tenant(self.tenant) as conn:
            return [self._row(r) for r in conn.execute("SELECT * FROM approvals ORDER BY created_at DESC LIMIT 200")]


class Audit:
    """Append-only, redacted. Hooks into approvals, MCP calls and API actions."""

    def __init__(self, db: Database, tenant: str):
        self.db, self.tenant = db, tenant

    def __call__(self, event: str, data: dict[str, Any]) -> None:
        clean = redact_obj(json.loads(json.dumps(data, default=str)))
        with self.db.tenant(self.tenant) as conn:
            conn.execute("INSERT INTO audit_log (tenant, event, data) VALUES (%s, %s, %s)",
                         (self.tenant, event, Jsonb(clean)))


class Runs:
    def __init__(self, db: Database):
        self.db = db

    def save(self, tenant: str, run: dict[str, Any]) -> None:
        with self.db.tenant(tenant) as conn:
            conn.execute(
                """INSERT INTO runs (id, tenant, username, kind, question, route, status, answer, result, trace, cost_usd,
                                     idempotency_key, flags)
                   VALUES (%(id)s, %(tenant)s, %(username)s, %(kind)s, %(question)s, %(route)s, %(status)s, %(answer)s,
                           %(result)s, %(trace)s, %(cost_usd)s, %(idempotency_key)s, %(flags)s)""",
                {**run, "tenant": tenant, "result": Jsonb(run.get("result")), "trace": Jsonb(run.get("trace", [])),
                 "flags": Jsonb(run.get("flags", []))})

    def get(self, tenant: str, run_id: str) -> dict | None:
        with self.db.tenant(tenant) as conn:
            return conn.execute("SELECT * FROM runs WHERE id = %s", (run_id,)).fetchone()

    def by_key(self, tenant: str, username: str, key: str) -> dict | None:
        with self.db.tenant(tenant) as conn:
            return conn.execute("SELECT * FROM runs WHERE username = %s AND idempotency_key = %s", (username, key)).fetchone()

    def recent(self, tenant: str, limit: int = 50) -> list[dict]:
        with self.db.tenant(tenant) as conn:
            return list(conn.execute(
                "SELECT id, username, kind, question, route, status, cost_usd, created_at FROM runs ORDER BY created_at DESC LIMIT %s",
                (limit,)))


class Feedbacks:
    def __init__(self, db: Database):
        self.db = db

    def add(self, tenant: str, run_id: str, username: str, rating: str, comment: str) -> None:
        clean, _ = redact(comment)
        with self.db.tenant(tenant) as conn:
            conn.execute("INSERT INTO feedback (tenant, run_id, username, rating, comment) VALUES (%s,%s,%s,%s,%s)",
                         (tenant, run_id, username, rating, clean))


class Jobs:
    """A job queue in PostgreSQL. Workers claim with FOR UPDATE SKIP LOCKED, so two workers
    never take the same job and neither blocks on the other."""

    def __init__(self, db: Database, worker: str | None = None):
        self.db, self.worker = db, worker or socket.gethostname()

    def enqueue(self, tenant: str, kind: str, payload: dict | None = None) -> int:
        with self.db.tenant(tenant) as conn:
            return conn.execute("INSERT INTO jobs (tenant, kind, payload) VALUES (%s,%s,%s) RETURNING id",
                                (tenant, kind, Jsonb(payload or {}))).fetchone()["id"]

    def claim(self, tenant: str) -> dict | None:
        with self.db.tenant(tenant) as conn:
            return conn.execute(
                """UPDATE jobs SET status = 'running', attempts = attempts + 1, locked_by = %s, locked_at = now()
                   WHERE id = (SELECT id FROM jobs WHERE status = 'queued' AND run_after <= now()
                               ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
                   RETURNING *""", (self.worker,)).fetchone()

    def finish(self, tenant: str, job_id: int, result: dict, ok: bool = True, retry_in_seconds: int = 30,
               max_attempts: int = 3) -> None:
        with self.db.tenant(tenant) as conn:
            row = conn.execute("SELECT attempts FROM jobs WHERE id = %s", (job_id,)).fetchone()
            status = "done" if ok else ("failed" if row["attempts"] >= max_attempts else "queued")
            conn.execute(
                "UPDATE jobs SET status = %s, result = %s, locked_by = NULL, run_after = now() + make_interval(secs => %s) "
                "WHERE id = %s", (status, Jsonb(result), 0 if ok else retry_in_seconds, job_id))

    def get(self, tenant: str, job_id: int) -> dict | None:
        with self.db.tenant(tenant) as conn:
            return conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
