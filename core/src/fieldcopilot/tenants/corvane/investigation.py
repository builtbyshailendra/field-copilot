"""Investigations: the report format, how its evidence is checked, and a rules baseline.

The report ranks hypotheses. Each cites evidence as (tool call ID, excerpt), and the
excerpt must appear in what that tool actually returned during this run, the same idea
as quote checking in grounded answers. Unsupported hypotheses are visible as such.

The rules baseline (`diagnose`) applies the runbooks' checks in a fixed order. It needs
no model, so it gives every scenario a measured baseline today, and it doubles as the
fake model's behaviour: `fake_investigator` drives the real agent loop and tools with it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from fieldcopilot.agent import Agent, AgentRun, ApprovalService, Limits
from fieldcopilot.answer.grounded import locate
from fieldcopilot.llm import LLM, LLMRequest, Scripted
from fieldcopilot.prompts import PromptRegistry
from fieldcopilot.rag.retriever import Retriever
from fieldcopilot.tenants.corvane.cluster import Cluster
from fieldcopilot.tenants.corvane.tools import build_tools

Cause = Literal["bad_release", "config_change", "memory_leak", "probe_misconfigured", "readiness_failure", "image_pull",
                "db_connections", "dependency_outage", "disk_pressure", "cert_expired", "poison_message", "no_issue"]
Action = Literal["rollback", "scale", "flag", "revert_config", "restart", "none"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: str
    excerpt: str


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cause: Cause
    statement: str
    confidence: float
    evidence: list[Evidence]


class Investigation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    hypotheses: list[Hypothesis]
    recommended_action: Action
    approvals_requested: list[str]


@dataclass
class EvidenceCheck:
    verified: int
    total: int
    unsupported: list[str]  # hypotheses (by cause) citing evidence that didn't check out

    @property
    def ratio(self) -> float:
        return self.verified / self.total if self.total else 1.0


def check_evidence(report: Investigation, run: AgentRun) -> EvidenceCheck:
    verified = total = 0
    unsupported = []
    for hyp in report.hypotheses:
        ok = 0
        for ev in hyp.evidence:
            total += 1
            if ev.call_id in run.evidence and locate(ev.excerpt, run.evidence[ev.call_id]):
                ok += 1
        verified += ok
        if hyp.evidence and ok == 0:
            unsupported.append(hyp.cause)
    return EvidenceCheck(verified, total, unsupported)


def build_investigator(llm: LLM, cluster: Cluster, approvals: ApprovalService, retriever: Retriever | None = None,
                       limits: Limits | None = None, output_filter=None, registry: PromptRegistry | None = None) -> Agent:
    prompt = (registry or PromptRegistry()).get("investigate", "corvane-freight")
    tools = build_tools(cluster, retriever)
    approvals.registry = tools  # approvals execute against this cluster's "acts" tools
    return Agent(llm, tools, approvals, prompt.system, "corvane-freight",
                 allowlist=None, limits=limits or Limits(max_steps=10, max_cost_usd=0.5),
                 final_schema=Investigation, output_filter=output_filter, purpose="investigate", effort=prompt.effort)


# ---------------------------------------------------------------------------------------------
# Rules baseline
# ---------------------------------------------------------------------------------------------
@dataclass
class Finding:
    cause: str
    statement: str
    confidence: float
    evidence: list[tuple[str, str]]  # (tool name or call id, excerpt)


def _text(obj: Any) -> str:
    return json.dumps(obj, default=str)


def diagnose(obs: dict[str, list[tuple[str, dict]]], service: str) -> list[Finding]:
    """obs maps tool name -> list of (call_id, output). Returns findings, most likely first."""
    def outputs(tool: str) -> list[tuple[str, dict]]:
        return obs.get(tool, [])

    def find(tool: str, pattern: str) -> tuple[str, str] | None:
        for call_id, out in outputs(tool):
            m = re.search(pattern, _text(out), flags=re.I)
            if m:
                return call_id, m.group(0)
        return None

    pods = [p for _, out in outputs("get_pods") for p in out.get("pods", [])]
    revisions = [r for _, out in outputs("get_deploy_history") for r in out.get("revisions", [])]
    recent_deploy = bool(revisions) and revisions[-1]["minutes_ago"] <= 60
    changes = [c for _, out in outputs("get_config_changes") for c in out.get("changes", [])]
    findings: list[Finding] = []

    def add(cause: str, statement: str, confidence: float, *evidence: tuple[str, str] | None) -> None:
        findings.append(Finding(cause, statement, confidence, [e for e in evidence if e]))

    if any(p["status"] in ("ImagePullBackOff", "ErrImagePull") for p in pods) or find("get_events", r"manifest unknown"):
        add("image_pull", "New pods can't pull their image; the release's tag doesn't exist.", 0.9,
            find("get_events", r"manifest unknown") or find("get_events", r"Failed to pull image"),
            find("get_pods", r"(ImagePullBackOff|ErrImagePull)"))
    if find("get_events", r"DiskPressure"):
        add("disk_pressure", "A node ran out of disk and is evicting pods.", 0.85,
            find("get_events", r"DiskPressure"), find("get_config_changes", r"LOG_LEVEL changed from INFO to DEBUG"))
    if find("get_logs", r"certificate has expired") or find("get_events", r"Failed to renew"):
        add("cert_expired", "The TLS certificate expired after a failed renewal.", 0.85,
            find("get_logs", r"certificate has expired"), find("get_events", r"Failed to renew"))
    if any(p.get("last_state", {}).get("reason") == "OOMKilled" for p in pods):
        add("memory_leak", "Containers are OOMKilled after memory climbs steadily since the last release.", 0.8,
            find("get_pods", r"OOMKilled"), find("get_deploy_history", r"bump [^\"]+"))
    if find("get_events", r"Liveness probe failed"):
        add("probe_misconfigured", "The liveness probe kills containers that are slow under load (exit 137, not OOM).", 0.75,
            find("get_events", r"Liveness probe failed"))
    if find("get_events", r"Readiness probe failed") or find("get_events", r"ProgressDeadlineExceeded"):
        add("readiness_failure", "New pods never become ready, so the rollout is stuck.", 0.8,
            find("get_events", r"Readiness probe failed"), find("get_events", r"ProgressDeadlineExceeded"))
    crashing = [p for p in pods if p["status"] in ("CrashLoopBackOff", "CreateContainerConfigError")]
    missing_setting = find("get_logs", r"(KeyError: '[A-Z_]+'|required setting [A-Z_]+ is not set)") \
        or find("get_events", r"couldn't find key [A-Z_]+")
    config_newer = bool(changes) and (not revisions or min(c["minutes_ago"] for c in changes) < revisions[-1]["minutes_ago"])
    if crashing and config_newer:
        add("config_change", "Newly started pods fail after a config change made since the last deploy.", 0.8,
            missing_setting, find("get_config_changes", r"renamed key [A-Z_]+ to [A-Z_]+"))
    elif crashing and recent_deploy:
        add("bad_release", "Pods from the latest release crash on startup; the previous revision is healthy.", 0.85,
            missing_setting, find("get_pods", r"CrashLoopBackOff"))
    db_full = next(((cid, "db_connections") for cid, out in outputs("query_metrics")
                    if out.get("series", {}).get("db_connections", {}).get("last", 0) >= 390), None)
    if find("get_logs", r"too many clients") or db_full:
        add("db_connections", "The database hit its connection limit after the service scaled out.", 0.75,
            find("get_logs", r"too many clients"), db_full, find("get_events", r"New size: \d+"))
    for call_id, out in outputs("query_metrics"):
        for name, s in out.get("series", {}).items():
            if name.startswith("carrier_error_rate_pct.") and s["last"] >= 50:
                add("dependency_outage", f"Carrier {name.split('.', 1)[1]} is failing; our services are waiting on it.", 0.8,
                    (call_id, name.split(".", 1)[1]), find("get_logs", r"timeout after \d+ms"))
    if find("get_logs", r"attempt (?:[5-9]|\d{2,})\)"):
        add("poison_message", "One message is retried forever and blocks its partition.", 0.75,
            find("get_logs", r"id=msg-\d+"), find("get_logs", r"attempt \d+\)"))
    if not findings:
        add("no_issue", "No evidence of an ongoing problem: pods healthy, no recent change, the spike resolved.", 0.6)
    return findings


def action_for(finding: Finding, obs: dict[str, list[tuple[str, dict]]], service: str) -> tuple[str, str, dict] | None:
    """The first action the runbooks suggest for a finding: (recommended_action, tool, args)."""
    revisions = [r for _, out in obs.get("get_deploy_history", []) for r in out.get("revisions", [])]
    if finding.cause in ("bad_release", "memory_leak", "readiness_failure", "image_pull") and len(revisions) >= 2:
        return "rollback", "propose_rollback", {"service": service, "to_revision": revisions[-2]["revision"],
                                                "reason": finding.statement}
    if finding.cause == "db_connections":
        for _, out in obs.get("query_metrics", []):
            ready = out.get("series", {}).get("ready_replicas")
            if out.get("service") == service and ready:
                return "scale", "propose_scale", {"service": service, "replicas": int(ready["first"]), "reason": finding.statement}
    if finding.cause == "dependency_outage":
        carrier = finding.evidence[0][1] if finding.evidence else "unknown"
        return "flag", "propose_flag", {"flag": f"carrier.{carrier}.circuit_open", "value": "true", "reason": finding.statement}
    if finding.cause in ("config_change", "disk_pressure"):
        return "revert_config", "", {}
    return None


# ---------------------------------------------------------------------------------------------
# Fake model: drives the real agent loop with the rules baseline
# ---------------------------------------------------------------------------------------------
TOOL_OUTPUT = re.compile(r'<tool_output tool="([^"]+)" call_id="([^"]+)">\n(.*?)\n</tool_output>', re.S)


def observations(messages: list[dict]) -> dict[str, list[tuple[str, dict]]]:
    obs: dict[str, list[tuple[str, dict]]] = {}
    for message in messages:
        if message["role"] != "user" or not isinstance(message["content"], list):
            continue
        for block in message["content"]:
            for tool, call_id, body in TOOL_OUTPUT.findall(block.get("content", "")):
                body = "\n".join(line for line in body.splitlines() if not line.startswith("[guardrail"))
                try:
                    data = json.loads(body)
                except ValueError:
                    data = {"text": body}
                obs.setdefault(tool, []).append((call_id, data))
    return obs


def fake_investigator(request: LLMRequest) -> Scripted:
    first = request.messages[0]["content"]
    service = re.search(r"(shipment-api|tracking-worker|rate-quote|label-printer|orders-consumer|carrier-gateway)", first)
    service_name = service.group(1) if service else "unknown"
    obs = observations(request.messages)
    called = set(obs)
    if "get_pods" not in called:
        return Scripted(tool_calls=[(t, {"service": service_name}) for t in
                                    ("get_deploy_history", "get_config_changes", "get_pods", "get_events", "query_metrics")])
    if "get_logs" not in called:
        pods = [p for _, out in obs["get_pods"] for p in out.get("pods", [])]
        unhealthy = [p for p in pods if not p["ready"] or p["restarts"] or p.get("last_state")][:2] or pods[:1]
        calls = [("get_logs", {"pod": p["name"], "previous": bool(p["restarts"] or p.get("last_state")), "tail": 50})
                 for p in unhealthy]
        calls += [("query_metrics", {"service": dep}) for dep in ("postgres-main", "carrier-gateway") if dep != service_name]
        calls.append(("search_runbooks", {"query": first[:200]}))
        return Scripted(tool_calls=calls)
    findings = diagnose(obs, service_name)
    top = findings[0]
    planned = action_for(top, obs, service_name)
    already_proposed = any(t.startswith("propose_") for t in called)
    if planned and planned[1] and not already_proposed:
        return Scripted(tool_calls=[(planned[1], planned[2])])
    approvals = [m.group(0) for _, out in [(c, o) for t, lst in obs.items() if t.startswith("propose_") for c, o in lst]
                 for m in [re.search(r"APR-\d+", json.dumps(out))] if m]
    report = {
        "summary": top.statement,
        "hypotheses": [{"cause": f.cause, "statement": f.statement, "confidence": f.confidence,
                        "evidence": [{"call_id": cid, "excerpt": ex} for cid, ex in f.evidence]} for f in findings[:3]],
        "recommended_action": planned[0] if planned else "none",
        "approvals_requested": approvals,
    }
    return Scripted(report)
