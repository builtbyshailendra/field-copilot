"""The copilot's HTTP API.

    uvicorn fieldcopilot.api.app:app --port 8020

Endpoints (all under /v1, all scoped to the caller's tenant):
- POST /auth/login, POST /auth/logout, GET /me
- POST /ask: streams Server-Sent Events: route, step (agent progress), answer, done.
  Idempotency-Key header: the same key returns the stored run instead of re-running.
- GET /runs, GET /runs/{id} (the trace viewer reads this)
- GET /approvals, POST /approvals/{id}/approve, POST /approvals/{id}/reject
- POST /feedback
- POST /jobs/reindex, GET /jobs/{id}
- GET /admin/prompts, GET /evals/latest
- GET /healthz (no DB), GET /readyz (DB)
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from fieldcopilot.agent import ApprovalError, ApprovalService, User
from fieldcopilot.api import auth
from fieldcopilot.api.db import Database, migrate
from fieldcopilot.api.stores import Audit, Feedbacks, Jobs, PgApprovalStore, Runs
from fieldcopilot.llm import LLM
from fieldcopilot.packs import load_pack
from fieldcopilot.prompts import PromptRegistry

COOKIE = "fc_token"
CORE = Path(__file__).resolve().parents[3]


class Settings(BaseModel):
    admin_dsn: str = os.environ.get("FIELD_ADMIN_DSN", "postgresql://postgres@127.0.0.1:5433/fieldcopilot")
    app_dsn: str = os.environ.get("FIELD_APP_DSN", "postgresql://copilot_app:copilot-app-dev@127.0.0.1:5433/fieldcopilot")
    demo_password: str = os.environ.get("FIELD_DEMO_PASSWORD", "demo-password")
    jwt_secret: str = Field(default_factory=auth.secret_from_env)
    migrate_on_start: bool = os.environ.get("FIELD_MIGRATE", "1") == "1"
    rate_per_minute: int = int(os.environ.get("FIELD_RATE_PER_MINUTE", "30"))
    cookie_secure: bool = os.environ.get("APP_ENV", "dev") != "dev"


class RateLimiter:
    """Token bucket per user, per process. Behind a load balancer the limit is per replica;
    the ingress also rate-limits per client IP."""

    def __init__(self, per_minute: int):
        self.rate, self.capacity = per_minute / 60.0, float(per_minute)
        self.buckets: dict[str, tuple[float, float]] = defaultdict(lambda: (self.capacity, time.monotonic()))

    def allow(self, key: str) -> bool:
        tokens, last = self.buckets[key]
        now = time.monotonic()
        tokens = min(self.capacity, tokens + (now - last) * self.rate)
        if tokens < 1:
            self.buckets[key] = (tokens, now)
            return False
        self.buckets[key] = (tokens - 1, now)
        return True


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if is_dataclass(value):
        return json.loads(json.dumps(asdict(value), default=str))
    return value


class LoginIn(BaseModel):
    username: str = Field(max_length=60)
    password: str = Field(max_length=200)


class AskIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=80)
    mode: Literal["auto", "supervise"] = "auto"


class DecisionIn(BaseModel):
    hash: str = Field(max_length=64)
    reason: str = Field(default="", max_length=1000)


class FeedbackIn(BaseModel):
    run_id: str = Field(max_length=40)
    rating: Literal["up", "down"]
    comment: str = Field(default="", max_length=2000)


def create_app(settings: Settings | None = None, llm_factory=None, copilot_factory=None) -> FastAPI:
    """`llm_factory()` and `copilot_factory(tenant, llm, approvals)` are injectable for tests."""
    settings = settings or Settings()
    from fieldcopilot.api.assembly import build_copilot, default_llm

    llm_factory = llm_factory or default_llm
    copilot_factory = copilot_factory or build_copilot
    app = FastAPI(title="field-copilot", version="0.1.0")
    cfg = auth.AuthConfig(settings.jwt_secret)
    users = auth.UserDirectory(settings.demo_password)
    db = Database(settings.app_dsn)
    runs, feedbacks, jobs = Runs(db), Feedbacks(db), Jobs(db)
    limiter = RateLimiter(settings.rate_per_minute)
    if settings.migrate_on_start:
        migrate(settings.admin_dsn)

    def approvals_for(tenant: str, registry=None) -> ApprovalService:
        pack = load_pack(tenant)
        return ApprovalService(registry, {tenant: pack.approver_role},
                               {tenant: int(pack.config.get("approvals", {}).get("expires_minutes", 15))},
                               audit=Audit(db, tenant), store=PgApprovalStore(db, tenant))

    def current_user(request: Request, authorization: str | None = Header(default=None)) -> User:
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:]
        elif COOKIE in request.cookies:
            if request.method != "GET" and request.headers.get("x-requested-with") != "field-copilot":
                raise HTTPException(403, "Missing X-Requested-With header")
            token = request.cookies[COOKIE]
        user = auth.verify(cfg, token) if token else None
        if user is None:
            raise HTTPException(401, "Sign in first")
        return user

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        try:
            with db.tenant("health") as conn:
                conn.execute("SELECT 1")
        except Exception:  # noqa: BLE001 - any DB failure means not ready
            return JSONResponse({"status": "not ready"}, status_code=503)
        return {"status": "ready"}

    @app.post("/v1/auth/login")
    def login(body: LoginIn, response: Response) -> dict:
        user = users.sign_in(body.username, body.password)
        if user is None:
            raise HTTPException(401, "Wrong username or password")
        token = auth.issue(cfg, user)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=settings.cookie_secure, max_age=cfg.ttl_seconds)
        Audit(db, user.tenant)("auth.login", {"user": user.name})
        return {"token": token, "user": user.name, "tenant": user.tenant, "roles": sorted(user.roles)}

    @app.post("/v1/auth/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie(COOKIE)
        return {"status": "signed out"}

    @app.get("/v1/me")
    def me(user: User = Depends(current_user)) -> dict:
        pack = load_pack(user.tenant)
        return {"user": user.name, "tenant": user.tenant, "tenant_name": pack.name, "roles": sorted(user.roles),
                "approver_role": pack.approver_role}

    @app.post("/v1/ask")
    def ask(body: AskIn, user: User = Depends(current_user), idempotency_key: str | None = Header(default=None)):
        if idempotency_key:
            stored = runs.by_key(user.tenant, user.name, idempotency_key[:80])
            if stored:
                return StreamingResponse(iter([_sse("done", {"run_id": stored["id"], "replayed": True,
                                                              "answer": stored["answer"]})]), media_type="text/event-stream")
        if not limiter.allow(user.name):
            raise HTTPException(429, "Too many questions in a minute; try again shortly")
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        events: queue.Queue = queue.Queue()

        def work() -> None:
            try:
                llm: LLM = llm_factory()
                approvals = approvals_for(user.tenant)
                copilot = copilot_factory(user.tenant, llm, approvals)
                copilot.s.agent.on_step = lambda step: events.put(("step", {
                    "index": step.index, "kind": step.kind, "name": step.name,
                    "summary": step.detail.get("text") or json.dumps(step.detail.get("args", {}))[:200]}))
                route = copilot.route(body.message) if body.mode == "auto" else "supervised"
                events.put(("route", {"route": route}))
                reply = copilot._dispatch(route, body.message, body.session_id, _budget()) if body.mode == "auto" \
                    else copilot.supervise(body.message, body.session_id)
                record = {"id": run_id, "username": user.name, "kind": reply.specialist, "question": body.message,
                          "route": reply.route, "status": reply.status, "answer": reply.text,
                          "result": to_jsonable(reply.payload) if not isinstance(reply.payload, list) else
                          [to_jsonable(p) for p in reply.payload],
                          "trace": reply.trace, "cost_usd": reply.cost_usd, "idempotency_key": (idempotency_key or None)}
                runs.save(user.tenant, record)
                events.put(("answer", {"run_id": run_id, "route": reply.route, "specialist": reply.specialist,
                                       "text": reply.text, "result": record["result"], "approvals": reply.approvals,
                                       "violations": reply.violations, "flags": reply.flags, "cost_usd": reply.cost_usd}))
            except Exception as error:  # noqa: BLE001 - reported to the client as an event
                events.put(("error", {"message": f"{type(error).__name__}: {error}"[:300]}))
            finally:
                events.put(("done", {"run_id": run_id}))

        threading.Thread(target=work, daemon=True).start()

        def stream():
            while True:
                kind, data = events.get()
                yield _sse(kind, data)
                if kind == "done":
                    return

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    @app.get("/v1/runs")
    def list_runs(user: User = Depends(current_user)) -> list[dict]:
        return [{**r, "cost_usd": float(r["cost_usd"]), "created_at": r["created_at"].isoformat()} for r in runs.recent(user.tenant)]

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str, user: User = Depends(current_user)) -> dict:
        row = runs.get(user.tenant, run_id)
        if row is None:
            raise HTTPException(404, "No such run")
        return {**row, "cost_usd": float(row["cost_usd"]), "created_at": row["created_at"].isoformat()}

    def _approval_view(a) -> dict:
        return {"id": a.id, "tool": a.tool, "summary": a.summary, "args": a.args, "hash": a.hash, "status": a.status,
                "created_at": a.created_at.isoformat(), "expires_at": a.expires_at.isoformat(), "decided_by": a.decided_by,
                "reason": a.reason, "outcome": a.outcome, "run_id": a.run_id}

    @app.get("/v1/approvals")
    def list_approvals(user: User = Depends(current_user)) -> list[dict]:
        return [_approval_view(a) for a in approvals_for(user.tenant).all(user.tenant)]

    def _decide(approval_id: str, body: DecisionIn, user: User, approve: bool) -> dict:
        llm = llm_factory()
        approvals = approvals_for(user.tenant)
        copilot = copilot_factory(user.tenant, llm, approvals)  # binds the tenant's tools to the approval service
        try:
            if approve:
                approval, result = approvals.approve(approval_id, user, body.hash, body.reason)
            else:
                approval = approvals.reject(approval_id, user, body.hash, body.reason)
        except ApprovalError as error:
            status = 404 if "no approval" in str(error) else 403 if "lacks" in str(error) or "tenant" in str(error) else 409
            raise HTTPException(status, str(error)) from None
        del copilot
        return _approval_view(approval)

    @app.post("/v1/approvals/{approval_id}/approve")
    def approve(approval_id: str, body: DecisionIn, user: User = Depends(current_user)) -> dict:
        return _decide(approval_id, body, user, True)

    @app.post("/v1/approvals/{approval_id}/reject")
    def reject(approval_id: str, body: DecisionIn, user: User = Depends(current_user)) -> dict:
        return _decide(approval_id, body, user, False)

    @app.post("/v1/feedback")
    def feedback(body: FeedbackIn, user: User = Depends(current_user)) -> dict:
        if runs.get(user.tenant, body.run_id) is None:
            raise HTTPException(404, "No such run")
        feedbacks.add(user.tenant, body.run_id, user.name, body.rating, body.comment)
        return {"status": "recorded"}

    @app.post("/v1/jobs/reindex")
    def reindex(user: User = Depends(current_user)) -> dict:
        return {"job_id": jobs.enqueue(user.tenant, "reindex")}

    @app.get("/v1/jobs/{job_id}")
    def job(job_id: int, user: User = Depends(current_user)) -> dict:
        row = jobs.get(user.tenant, job_id)
        if row is None:
            raise HTTPException(404, "No such job")
        return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}

    @app.get("/v1/admin/prompts")
    def prompts(user: User = Depends(current_user)) -> list[dict]:
        return PromptRegistry(pack_dirs={user.tenant: load_pack(user.tenant).prompts_dir}).catalogue()

    @app.get("/v1/evals/latest")
    def evals_latest(user: User = Depends(current_user)) -> dict:
        path = CORE / "evals" / "results" / "suite" / "fake-hashing.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    app.state.runs, app.state.jobs, app.state.db = runs, jobs, db
    return app


def _budget():
    from fieldcopilot.llm import Budget

    return Budget(max_cost_usd=1.0)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def __getattr__(name: str):
    """`uvicorn fieldcopilot.api.app:app` builds the app on first use, not at import (tests import freely)."""
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
