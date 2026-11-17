"""Background worker: claims jobs from the PostgreSQL queue, one tenant at a time.

    python -m fieldcopilot.api.worker

It sets the tenant on every connection like the API does, so row-level security applies
to the worker too. Failed jobs are retried with a delay up to three attempts.
"""
from __future__ import annotations

import os
import time

from fieldcopilot.api.db import Database
from fieldcopilot.api.stores import Audit, Jobs
from fieldcopilot.packs import all_packs


def handle(job: dict) -> dict:
    if job["kind"] == "reindex":
        from fieldcopilot.wiring import retriever_for

        retriever_for.cache_clear()
        retriever = retriever_for(job["tenant"])
        return {"chunks": len(retriever.index.chunks)}
    raise ValueError(f"unknown job kind {job['kind']!r}")


def run_once(db: Database) -> int:
    """Process at most one ready job per tenant. Returns how many jobs ran."""
    jobs = Jobs(db)
    done = 0
    for pack in all_packs():
        job = jobs.claim(pack.id)
        if job is None:
            continue
        try:
            result = handle(job)
            jobs.finish(pack.id, job["id"], result, ok=True)
            Audit(db, pack.id)("job.done", {"id": job["id"], "kind": job["kind"], "result": result})
        except Exception as error:  # noqa: BLE001 - recorded on the job and retried
            jobs.finish(pack.id, job["id"], {"error": f"{type(error).__name__}: {error}"}, ok=False)
        done += 1
    return done


def main() -> None:  # pragma: no cover - long-running loop
    db = Database(os.environ.get("FIELD_APP_DSN", "postgresql://copilot_app:copilot-app-dev@127.0.0.1:5433/fieldcopilot"))
    while True:
        if run_once(db) == 0:
            time.sleep(2.0)


if __name__ == "__main__":
    main()
