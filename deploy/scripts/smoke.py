"""Smoke test against a running API: sign in, ask a question, check the build's self-check score.

    python deploy/scripts/smoke.py http://127.0.0.1:8020 <demo-password> [--min-score 0.75]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request


def call(url: str, data: dict | None = None, token: str | None = None) -> tuple[int, str]:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.read().decode()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("base")
    p.add_argument("password")
    p.add_argument("--min-score", type=float, default=0.75)
    a = p.parse_args()
    token = json.loads(call(f"{a.base}/v1/auth/login", {"username": "asha", "password": a.password})[1])["token"]
    status, stream = call(f"{a.base}/v1/ask", {"message": "what counts as a SEV1?"}, token)
    assert status == 200 and "event: answer" in stream and "event: error" not in stream, stream[:800]
    for _ in range(30):  # the self-check finishes a few seconds after start
        result = json.loads(call(f"{a.base}/v1/selfcheck")[1])
        if "score" in result:
            break
        time.sleep(2)
    print(json.dumps(result))
    if result.get("score", 0) < a.min_score:
        print(f"self-check score {result.get('score')} below {a.min_score}")
        return 1
    print("smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
