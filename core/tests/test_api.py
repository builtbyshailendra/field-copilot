"""API tests against a real PostgreSQL (local 5433, or the CI service). Skipped if none is reachable."""
import json
import os

import psycopg
import pytest

ADMIN_SERVER = os.environ.get("FIELD_TEST_ADMIN_SERVER_DSN", "postgresql://postgres@127.0.0.1:5433/postgres")
DB_NAME = "fieldcopilot_test"


def _server_up() -> bool:
    try:
        with psycopg.connect(ADMIN_SERVER, connect_timeout=2):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _server_up(), reason="needs PostgreSQL")


@pytest.fixture(scope="module")
def dsns():
    with psycopg.connect(ADMIN_SERVER, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {DB_NAME}")
    admin = ADMIN_SERVER.rsplit("/", 1)[0] + f"/{DB_NAME}"
    host = admin.split("@", 1)[1]
    return admin, f"postgresql://copilot_app:copilot-app-dev@{host}"


@pytest.fixture()
def client(dsns):
    from fastapi.testclient import TestClient

    from fieldcopilot.api.app import Settings, create_app

    admin, app_dsn = dsns
    app = create_app(Settings(admin_dsn=admin, app_dsn=app_dsn, demo_password="pw", jwt_secret="t" * 40, rate_per_minute=1000))
    return TestClient(app)


def login(client, user):
    r = client.post("/v1/auth/login", json={"username": user, "password": "pw"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_login_rejects_bad_passwords_and_requires_auth(client):
    assert client.post("/v1/auth/login", json={"username": "ravi", "password": "nope"}).status_code == 401
    assert client.get("/v1/me").status_code == 401
    me = client.get("/v1/me", headers=login(client, "ravi")).json()
    assert me["tenant"] == "corvane-freight" and "oncall-lead" in me["roles"]


def test_cookie_auth_needs_the_csrf_header_on_writes(client):
    login(client, "asha")  # sets the httpOnly cookie on the test client
    assert client.get("/v1/me").status_code == 200
    assert client.post("/v1/jobs/reindex").status_code == 403
    assert client.post("/v1/jobs/reindex", headers={"X-Requested-With": "field-copilot"}).status_code == 200


def test_investigation_streams_steps_then_answer_and_persists_the_run(client):
    headers = login(client, "asha")
    r = client.post("/v1/ask", json={"message": "shipment-api pods restarting in freight (service: shipment-api)"}, headers=headers)
    evs = events(r.text)
    kinds = [k for k, _ in evs]
    assert kinds[0] == "route" and "step" in kinds and kinds[-2:] == ["answer", "done"]
    answer = dict(evs)["answer"]
    assert answer["specialist"] == "investigate" and answer["approvals"]
    run = client.get(f"/v1/runs/{answer['run_id']}", headers=headers).json()
    assert run["route"] == "investigate_incident" and run["trace"] and run["result"]["hypotheses"][0]["cause"] == "bad_release"
    assert run["flags"] == answer["flags"]  # guardrail flags are kept with the run for audit


def test_approval_needs_the_right_role_and_runs_once(client):
    asha, ravi = login(client, "asha"), login(client, "ravi")
    client.post("/v1/ask", json={"message": "shipment-api pods restarting (service: shipment-api)"}, headers=asha)
    pending = [a for a in client.get("/v1/approvals", headers=ravi).json() if a["status"] == "pending"][-1]
    assert client.post(f"/v1/approvals/{pending['id']}/approve", json={"hash": pending["hash"]}, headers=asha).status_code == 403
    assert client.post(f"/v1/approvals/{pending['id']}/approve", json={"hash": "0" * 16}, headers=ravi).status_code == 409
    done = client.post(f"/v1/approvals/{pending['id']}/approve", json={"hash": pending["hash"], "reason": "drill"}, headers=ravi)
    assert done.status_code == 200 and done.json()["status"] == "executed" and done.json()["decided_by"] == "ravi"
    assert client.post(f"/v1/approvals/{pending['id']}/approve", json={"hash": pending["hash"]}, headers=ravi).status_code == 409


def test_tenants_cannot_see_each_others_runs_or_approvals(client):
    nurse = login(client, "j.patel")
    asha = login(client, "asha")
    run_id = dict(events(client.post("/v1/ask", json={"message": "what counts as a SEV1?"}, headers=asha).text))["done"]["run_id"]
    assert client.get(f"/v1/runs/{run_id}", headers=nurse).status_code == 404
    assert all(a["tool"] != "do_rollback" for a in client.get("/v1/approvals", headers=nurse).json())


def test_row_level_security_holds_even_without_a_where_clause(dsns):
    from fieldcopilot.api.db import Database

    db = Database(dsns[1])
    with db.tenant("corvane-freight") as conn:
        conn.execute("INSERT INTO feedback (tenant, run_id, username, rating) VALUES ('corvane-freight', 'r1', 'x', 'up')")
    with db.tenant("tallowmere-health") as conn:
        assert conn.execute("SELECT count(*) AS n FROM feedback").fetchone()["n"] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO feedback (tenant, run_id, username, rating) VALUES ('corvane-freight', 'r2', 'x', 'up')")


def test_audit_log_is_append_only_and_redacted(dsns, client):
    from fieldcopilot.api.db import Database
    from fieldcopilot.api.stores import Audit

    db = Database(dsns[1])
    Audit(db, "tallowmere-health")("test.event", {"member": "TM-55012", "note": "password: hunter2"})
    with db.tenant("tallowmere-health") as conn:
        row = conn.execute("SELECT data FROM audit_log WHERE event = 'test.event'").fetchone()
        assert "TM-55012" not in json.dumps(row["data"]) and "hunter2" not in json.dumps(row["data"])
    with db.tenant("tallowmere-health") as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("DELETE FROM audit_log")


def test_idempotency_key_replays_instead_of_rerunning(client):
    headers = {**login(client, "asha"), "Idempotency-Key": "ask-123"}
    first = dict(events(client.post("/v1/ask", json={"message": "what counts as a SEV1?"}, headers=headers).text))
    second = dict(events(client.post("/v1/ask", json={"message": "what counts as a SEV1?"}, headers=headers).text))
    assert second["done"]["replayed"] is True and second["done"]["run_id"] == first["done"]["run_id"]


def test_feedback_and_jobs(client, dsns):
    from fieldcopilot.api.db import Database
    from fieldcopilot.api.worker import run_once

    headers = {**login(client, "j.patel")}
    run_id = dict(events(client.post("/v1/ask", json={"message": "how many PT visits need no authorization?"}, headers=headers).text))["done"]["run_id"]
    assert client.post("/v1/feedback", json={"run_id": run_id, "rating": "down", "comment": "wrong"}, headers=headers).status_code == 200
    assert client.post("/v1/feedback", json={"run_id": "run-nope", "rating": "up"}, headers=headers).status_code == 404
    job_id = client.post("/v1/jobs/reindex", headers=headers).json()["job_id"]
    assert run_once(Database(dsns[1])) >= 1
    job = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
    assert job["status"] == "done" and job["result"]["chunks"] > 20


def test_metrics_endpoint_exposes_request_and_model_metrics(client):
    headers = login(client, "asha")
    client.post("/v1/ask", json={"message": "what counts as a SEV1?"}, headers=headers)
    text = client.get("/metrics").text
    assert 'fieldcopilot_http_requests_total{code="200",method="POST"}' in text
    assert "fieldcopilot_pending_approvals" in text and "fieldcopilot_canary_quality_score" in text


def test_rate_limit(dsns):
    from fastapi.testclient import TestClient

    from fieldcopilot.api.app import Settings, create_app

    app = create_app(Settings(admin_dsn=dsns[0], app_dsn=dsns[1], demo_password="pw", jwt_secret="t" * 40,
                              rate_per_minute=1, migrate_on_start=False))
    c = TestClient(app)
    headers = login(c, "asha")
    assert c.post("/v1/ask", json={"message": "what counts as a SEV1?"}, headers=headers).status_code == 200
    assert c.post("/v1/ask", json={"message": "what counts as a SEV1?"}, headers=headers).status_code == 429
