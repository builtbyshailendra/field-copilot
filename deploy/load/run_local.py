"""The same load as deploy/load/ask.js, with only the standard library, for a laptop without k6.

    python deploy/load/run_local.py http://127.0.0.1:8022 <demo-password> --users 12 --seconds 60

Closed loop: N simulated people each ask a question from questions.json, wait for the full
streamed reply, and ask again. Prints latency percentiles, throughput, errors, how many
replies came from cache, and the model calls the server made (from its /metrics).
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import threading
import time
import urllib.request
from pathlib import Path

MIX = json.loads((Path(__file__).with_name("questions.json")).read_text(encoding="utf-8"))["questions"]


def post(url: str, body: dict, token: str | None = None) -> str:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode()


def answered_from(stream: str) -> str:
    """The question a cached answer was originally produced for (result.question)."""
    lines = stream.splitlines()
    for i, line in enumerate(lines):
        if line == "event: answer" and i + 1 < len(lines):
            return (json.loads(lines[i + 1][5:]).get("result") or {}).get("question", "")
    return ""


def model_calls(base: str) -> dict[str, float]:
    text = urllib.request.urlopen(f"{base}/metrics", timeout=30).read().decode()
    calls: dict[str, float] = {}
    for m in re.finditer(r'fieldcopilot_llm_calls_total\{([^}]*)\} ([0-9.e+]+)', text):
        purpose = re.search(r'purpose="([^"]+)"', m.group(1)).group(1)
        calls[purpose] = calls.get(purpose, 0) + float(m.group(2))
    return calls


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("base")
    p.add_argument("password")
    p.add_argument("--users", type=int, default=12)
    p.add_argument("--seconds", type=float, default=60)
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    for _ in range(60):  # let the startup self-check finish (it builds the indexes) before measuring
        if "score" in urllib.request.urlopen(f"{a.base}/v1/selfcheck", timeout=30).read().decode():
            break
        time.sleep(2)
    tokens = {u: json.loads(post(f"{a.base}/v1/auth/login", {"username": u, "password": a.password}))["token"]
              for u in {q["user"] for q in MIX}}
    before = model_calls(a.base)
    latencies: list[float] = []
    reused_pairs: set[tuple[str, str]] = set()
    errors = cached = near_miss_cached = 0
    lock = threading.Lock()
    stop = time.monotonic() + a.seconds

    def person(seed: int) -> None:
        nonlocal errors, cached, near_miss_cached
        rng = random.Random(seed)
        while time.monotonic() < stop:
            q = rng.choice(MIX)
            started = time.perf_counter()
            try:
                body = post(f"{a.base}/v1/ask", {"message": q["message"]}, tokens[q["user"]])
                ok = "event: answer" in body and "event: error" not in body
            except Exception:  # noqa: BLE001 - counted as an error
                body, ok = "", False
            elapsed = (time.perf_counter() - started) * 1000
            with lock:
                latencies.append(elapsed)
                errors += not ok
                if '"cache:hit"' in body:
                    cached += 1
                    source = answered_from(body)
                    if source != q["message"]:  # reused from a different question
                        reused_pairs.add((q["message"], source))
                        near_miss_cached += q["near_miss"]

    threads = [threading.Thread(target=person, args=(a.seed + i,)) for i in range(a.users)]
    wall = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - wall
    after = model_calls(a.base)
    calls = {k: int(after.get(k, 0) - before.get(k, 0)) for k in sorted(set(after) | set(before))}
    q = statistics.quantiles(latencies, n=100)
    result = {"requests": len(latencies), "errors": errors, "throughput_rps": round(len(latencies) / wall, 2),
              "p50_ms": round(q[49]), "p95_ms": round(q[94]), "p99_ms": round(q[98]),
              "from_cache": cached, "near_miss_answered_from_another_question": near_miss_cached,
              "reused_across_questions": sorted(f"{asked}  <=  {source}" for asked, source in reused_pairs),
              "model_calls": {k: v for k, v in calls.items() if v}}
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
