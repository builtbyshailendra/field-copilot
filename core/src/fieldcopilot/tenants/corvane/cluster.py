"""Where Corvane's tools get their data: a frozen scenario, or a live cluster through kubectl.

Both backends return the same plain dicts, so the tools, the rules baseline and the
model see identical shapes in tests, in the evaluation and in the kind lab.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import yaml

from fieldcopilot.packs import load_pack


class Cluster(Protocol):
    actions: list[dict[str, Any]]

    def deploy_history(self, service: str) -> dict: ...
    def config_changes(self, service: str) -> dict: ...
    def pods(self, service: str) -> dict: ...
    def events(self, service: str) -> dict: ...
    def logs(self, pod: str, previous: bool, tail: int) -> dict: ...
    def metrics(self, service: str) -> dict: ...
    def rollback(self, service: str, to_revision: int) -> str: ...
    def scale(self, service: str, replicas: int) -> str: ...
    def restart(self, service: str) -> str: ...
    def set_flag(self, flag: str, value: str) -> str: ...


def scenarios_dir() -> Path:
    return load_pack("corvane-freight").root / "scenarios"


def load_scenario(scenario_id: str) -> dict[str, Any]:
    matches = sorted(scenarios_dir().glob(f"{scenario_id}*.yaml"))
    if not matches:
        raise KeyError(f"no scenario {scenario_id!r}")
    return yaml.safe_load(matches[0].read_text(encoding="utf-8"))


def all_scenarios() -> list[dict[str, Any]]:
    return [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(scenarios_dir().glob("s*.yaml"))]


@dataclass
class FixtureCluster:
    scenario: dict[str, Any]
    actions: list[dict[str, Any]] = field(default_factory=list)

    def deploy_history(self, service: str) -> dict:
        revs = [d for d in self.scenario.get("deploys", []) if d["service"] == service]
        return {"service": service, "revisions": sorted(revs, key=lambda d: d["revision"])}

    def config_changes(self, service: str) -> dict:
        changes = [c for c in self.scenario.get("config_changes", []) if service in c["object"]]
        return {"service": service, "changes": changes}

    def pods(self, service: str) -> dict:
        return {"service": service, "pods": [p for p in self.scenario.get("pods", []) if p["service"] == service]}

    def events(self, service: str) -> dict:
        keep = [e for e in self.scenario.get("events", [])
                if service in e["object"] or e["object"].split("/")[0] in ("node", "certificate")]
        return {"service": service, "events": sorted(keep, key=lambda e: -e["minutes_ago"])}

    def logs(self, pod: str, previous: bool, tail: int) -> dict:
        entry = self.scenario.get("logs", {}).get(pod)
        if entry is None:
            raise ValueError(f"pod {pod} not found")
        lines = entry.get("previous" if previous else "current", [])
        return {"pod": pod, "previous": previous, "lines": lines[-tail:]}

    def metrics(self, service: str) -> dict:
        raw = self.scenario.get("metrics", {}).get(service, {})
        series = {name: {"values": vals, "first": vals[0], "last": vals[-1], "max": max(vals), "min": min(vals)}
                  for name, vals in raw.items()}
        return {"service": service, "interval_minutes": 5, "series": series}

    def _act(self, kind: str, **details: Any) -> str:
        self.actions.append({"kind": kind, **details})
        return f"{kind} recorded: {details}"

    def rollback(self, service: str, to_revision: int) -> str:
        return self._act("rollback", service=service, to_revision=to_revision)

    def scale(self, service: str, replicas: int) -> str:
        return self._act("scale", service=service, replicas=replicas)

    def restart(self, service: str) -> str:
        return self._act("restart", service=service)

    def set_flag(self, flag: str, value: str) -> str:
        return self._act("flag", flag=flag, value=value)


def _minutes_ago(timestamp: str | None) -> float:
    if not timestamp:
        return 0.0
    when = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return round((datetime.now(timezone.utc) - when).total_seconds() / 60, 2)


class KubeCluster:
    """Live cluster through kubectl (the kind lab in CI). Services are selected by the `app` label."""

    def __init__(self, namespace: str = "freight", kubectl: str = "kubectl"):
        self.namespace, self.kubectl = namespace, kubectl
        self.actions: list[dict[str, Any]] = []

    def _run(self, *args: str) -> str:
        out = subprocess.run([self.kubectl, "-n", self.namespace, *args], capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip()[:400])
        return out.stdout

    def _json(self, *args: str) -> dict:
        return json.loads(self._run(*args, "-o", "json"))

    def _revisions(self, service: str) -> dict[str, int]:
        sets = self._json("get", "replicasets", "-l", f"app={service}")["items"]
        return {rs["metadata"]["name"]: int(rs["metadata"].get("annotations", {}).get("deployment.kubernetes.io/revision", 0))
                for rs in sets}

    def deploy_history(self, service: str) -> dict:
        sets = self._json("get", "replicasets", "-l", f"app={service}")["items"]
        revs = [{"service": service,
                 "revision": int(rs["metadata"].get("annotations", {}).get("deployment.kubernetes.io/revision", 0)),
                 "image": rs["spec"]["template"]["spec"]["containers"][0]["image"],
                 "minutes_ago": _minutes_ago(rs["metadata"].get("creationTimestamp")), "by": "kubectl"} for rs in sets]
        return {"service": service, "revisions": sorted(revs, key=lambda d: d["revision"])}

    def config_changes(self, service: str) -> dict:
        maps = self._json("get", "configmaps", "-l", f"app={service}")["items"]
        changes = [{"object": f"configmap/{m['metadata']['name']}", "change": m["metadata"].get("annotations", {}).get("lab/change", ""),
                    "minutes_ago": _minutes_ago(m["metadata"].get("creationTimestamp"))}
                   for m in maps if m["metadata"].get("annotations", {}).get("lab/change")]
        return {"service": service, "changes": changes}

    def pods(self, service: str) -> dict:
        revisions = self._revisions(service)
        pods = []
        for item in self._json("get", "pods", "-l", f"app={service}")["items"]:
            statuses = item.get("status", {}).get("containerStatuses") or [{}]
            status = statuses[0]
            waiting = status.get("state", {}).get("waiting", {}).get("reason")
            terminated = status.get("lastState", {}).get("terminated") or {}
            owner = (item["metadata"].get("ownerReferences") or [{}])[0].get("name", "")
            pod = {"name": item["metadata"]["name"], "service": service, "revision": revisions.get(owner, 0),
                   "status": waiting or item.get("status", {}).get("phase", "Unknown"), "ready": bool(status.get("ready")),
                   "restarts": int(status.get("restartCount", 0))}
            if terminated:
                pod["last_state"] = {"reason": terminated.get("reason"), "exit_code": terminated.get("exitCode")}
            pods.append(pod)
        return {"service": service, "pods": pods}

    def events(self, service: str) -> dict:
        items = self._json("get", "events")["items"]
        events = [{"minutes_ago": _minutes_ago(e.get("lastTimestamp") or e.get("eventTime")),
                   "object": f"{e['involvedObject']['kind'].lower()}/{e['involvedObject']['name']}",
                   "reason": e.get("reason", ""), "message": e.get("message", "")}
                  for e in items if e["involvedObject"]["name"].startswith(service)]
        return {"service": service, "events": sorted(events, key=lambda e: -e["minutes_ago"])[-30:]}

    def logs(self, pod: str, previous: bool, tail: int) -> dict:
        args = ["logs", pod, f"--tail={tail}"] + (["--previous"] if previous else [])
        try:
            lines = self._run(*args).splitlines()
        except RuntimeError as error:
            lines = [f"(no logs: {error})"]
        return {"pod": pod, "previous": previous, "lines": lines}

    def metrics(self, service: str) -> dict:
        return {"service": service, "interval_minutes": 5, "series": {}, "note": "no Prometheus in the lab cluster"}

    def rollback(self, service: str, to_revision: int) -> str:
        self.actions.append({"kind": "rollback", "service": service, "to_revision": to_revision})
        return self._run("rollout", "undo", f"deployment/{service}", f"--to-revision={to_revision}")

    def scale(self, service: str, replicas: int) -> str:
        self.actions.append({"kind": "scale", "service": service, "replicas": replicas})
        return self._run("scale", f"deployment/{service}", f"--replicas={replicas}")

    def restart(self, service: str) -> str:
        self.actions.append({"kind": "restart", "service": service})
        return self._run("rollout", "restart", f"deployment/{service}")

    def set_flag(self, flag: str, value: str) -> str:
        self.actions.append({"kind": "flag", "flag": flag, "value": value})
        return f"flag {flag}={value} (feature flags are not part of the lab)"
