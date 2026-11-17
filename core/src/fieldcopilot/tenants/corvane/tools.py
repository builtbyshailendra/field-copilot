"""Corvane's tools. Read-only ones run freely; actions are proposals that need the on-call lead."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from fieldcopilot.agent import Tool, ToolContext, ToolRegistry, ToolResult
from fieldcopilot.rag.retriever import Retriever
from fieldcopilot.tenants.corvane.cluster import Cluster


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServiceIn(_In):
    service: str


class NoIn(_In):
    pass


class LogsIn(_In):
    pod: str
    previous: bool
    tail: int


class SearchIn(_In):
    query: str


class RollbackIn(_In):
    service: str
    to_revision: int
    reason: str


class ScaleIn(_In):
    service: str
    replicas: int
    reason: str


class RestartIn(_In):
    service: str
    reason: str


class FlagIn(_In):
    flag: str
    value: str
    reason: str


def build_tools(cluster: Cluster, retriever: Retriever | None = None) -> ToolRegistry:
    def search(ctx: ToolContext, a: SearchIn):
        if retriever is None:
            return ToolResult("runbook search is not configured", is_error=True)
        hits = retriever.search(a.query, k=4)
        return {"results": [{"chunk_id": h.chunk.id, "source": h.chunk.heading, "text": h.chunk.text} for h in hits]}

    def logs(ctx: ToolContext, a: LogsIn):
        return cluster.logs(a.pod, a.previous, max(1, min(a.tail, 200)))

    return ToolRegistry([
        Tool("get_deploy_history", "Deploy revisions of a service, oldest first, with image and minutes since deploy.",
             ServiceIn, lambda c, a: cluster.deploy_history(a.service)),
        Tool("get_config_changes", "Recent ConfigMap and Secret changes affecting a service (these don't create deploy revisions).",
             ServiceIn, lambda c, a: cluster.config_changes(a.service)),
        Tool("get_pods", "Pods of a service: status, readiness, restarts, last termination reason and exit code, revision.",
             ServiceIn, lambda c, a: cluster.pods(a.service)),
        Tool("get_events", "Kubernetes events for a service, plus node and certificate events.",
             ServiceIn, lambda c, a: cluster.events(a.service)),
        Tool("get_logs", "Log lines of one pod. previous=true reads the last crashed container, where crash causes are.",
             LogsIn, logs),
        Tool("query_metrics", "Metric series for a service or dependency (5-minute points, last = now): error rate, latency, "
             "memory, ready replicas, queue lag, DB connections, per-carrier error rates.",
             ServiceIn, lambda c, a: cluster.metrics(a.service)),
        Tool("search_runbooks", "Search Corvane's runbooks and past postmortems.", SearchIn, search),
        Tool("propose_rollback", "Propose rolling a service back to an earlier revision. Creates an approval request; does not run.",
             RollbackIn, lambda c, a: None, side_effect="proposes", executes="do_rollback",
             summarize=lambda a: f"roll back {a.service} to revision {a.to_revision} ({a.reason})"),
        Tool("propose_scale", "Propose changing a service's replica count. Creates an approval request; does not run.",
             ScaleIn, lambda c, a: None, side_effect="proposes", executes="do_scale",
             summarize=lambda a: f"scale {a.service} to {a.replicas} replicas ({a.reason})"),
        Tool("propose_restart", "Propose a rolling restart of a service. Creates an approval request; does not run.",
             RestartIn, lambda c, a: None, side_effect="proposes", executes="do_restart",
             summarize=lambda a: f"restart {a.service} ({a.reason})"),
        Tool("propose_flag", "Propose setting a feature flag, such as a carrier circuit breaker. Creates an approval request.",
             FlagIn, lambda c, a: None, side_effect="proposes", executes="do_set_flag",
             summarize=lambda a: f"set {a.flag}={a.value} ({a.reason})"),
        Tool("do_rollback", "Run an approved rollback.", RollbackIn, lambda c, a: cluster.rollback(a.service, a.to_revision), side_effect="acts"),
        Tool("do_scale", "Run an approved scale change.", ScaleIn, lambda c, a: cluster.scale(a.service, a.replicas), side_effect="acts"),
        Tool("do_restart", "Run an approved restart.", RestartIn, lambda c, a: cluster.restart(a.service), side_effect="acts"),
        Tool("do_set_flag", "Set an approved flag.", FlagIn, lambda c, a: cluster.set_flag(a.flag, a.value), side_effect="acts"),
    ])
