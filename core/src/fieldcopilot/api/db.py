"""PostgreSQL schema, row-level security, and tenant-scoped connections.

Every table carries a tenant column and has a policy: rows are visible and writable only
when `tenant = current_setting('app.tenant')`. The API connects as `copilot_app`, a
non-superuser, with FORCE ROW LEVEL SECURITY on, so even a bug that forgets a WHERE
clause can't read another customer's rows. A test proves it with a hand-written query.
The audit log is append-only, enforced by a trigger.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

TABLES = ["runs", "approvals", "audit_log", "feedback", "jobs"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id text PRIMARY KEY, tenant text NOT NULL, username text NOT NULL, kind text NOT NULL,
  question text NOT NULL, route text, status text NOT NULL, answer text, result jsonb, trace jsonb,
  cost_usd numeric NOT NULL DEFAULT 0, idempotency_key text, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant, username, idempotency_key)
);
CREATE TABLE IF NOT EXISTS approvals (
  id text PRIMARY KEY, tenant text NOT NULL, run_id text, tool text NOT NULL, args jsonb NOT NULL,
  summary text NOT NULL, hash text NOT NULL, status text NOT NULL, created_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL, decided_by text, decided_at timestamptz, reason text, outcome text
);
CREATE SEQUENCE IF NOT EXISTS approval_seq;
CREATE TABLE IF NOT EXISTS audit_log (
  id bigserial PRIMARY KEY, tenant text NOT NULL, event text NOT NULL, data jsonb NOT NULL,
  at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS feedback (
  id bigserial PRIMARY KEY, tenant text NOT NULL, run_id text NOT NULL, username text NOT NULL,
  rating text NOT NULL CHECK (rating IN ('up', 'down')), comment text NOT NULL DEFAULT '',
  at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS jobs (
  id bigserial PRIMARY KEY, tenant text NOT NULL, kind text NOT NULL, payload jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'queued', attempts int NOT NULL DEFAULT 0, run_after timestamptz NOT NULL DEFAULT now(),
  locked_by text, locked_at timestamptz, result jsonb, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs (tenant, status, run_after);
-- Guardrail flags (possible injection, secrets redacted, cache outcome) kept with each run for audit.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS flags jsonb NOT NULL DEFAULT '[]';

CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_no_change ON audit_log;
CREATE TRIGGER audit_no_change BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_append_only();
"""

RLS = """
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON {t};
CREATE POLICY tenant_isolation ON {t}
  USING (tenant = current_setting('app.tenant', true))
  WITH CHECK (tenant = current_setting('app.tenant', true));
"""

GRANTS = """
GRANT SELECT, INSERT, UPDATE ON runs, approvals, feedback, jobs TO {role};
GRANT SELECT, INSERT ON audit_log TO {role};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role};
"""


MIGRATE_LOCK = 0x66636F70  # "fcop": one advisory-lock key for every copy of the app


def migrate(admin_dsn: str, app_role: str = "copilot_app", app_password: str | None = None) -> None:
    """Create or update the schema. Safe to run from many replicas at once.

    Without the lock, replicas starting together race on CREATE TABLE IF NOT EXISTS and
    lose with "duplicate key value violates unique constraint pg_type_typname_nsp_index"
    (seen on the first kind deploy: API and worker pods restarted 2-3 times each).
    The session-level lock makes them take turns; closing the connection releases it.
    """
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATE_LOCK,))
        conn.execute(SCHEMA)
        for table in TABLES:
            conn.execute(RLS.format(t=table))
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app_role,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
                sql.Identifier(app_role), sql.Literal(app_password or "copilot-app-dev")))
        conn.execute(sql.SQL(GRANTS.replace("{role}", "{}")).format(*[sql.Identifier(app_role)] * 3))


class Database:
    def __init__(self, app_dsn: str):
        self.app_dsn = app_dsn

    @contextmanager
    def tenant(self, tenant: str) -> Iterator[psycopg.Connection]:
        """A connection whose transaction can only see this tenant's rows."""
        with psycopg.connect(self.app_dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                conn.execute("SELECT set_config('app.tenant', %s, true)", (tenant,))
                yield conn
