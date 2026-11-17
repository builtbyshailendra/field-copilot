"""Prometheus metrics: what an operator (and the tenant's budget owner) needs to see.

Labels are low-cardinality on purpose: tenant, purpose, model, outcome. Never user IDs,
questions or run IDs.
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

LLM_CALLS = Counter("fieldcopilot_llm_calls_total", "Model calls", ["tenant", "purpose", "model", "outcome"], registry=REGISTRY)
LLM_LATENCY = Histogram("fieldcopilot_llm_latency_seconds", "Model call latency, including retries", ["purpose"],
                        buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32, 64), registry=REGISTRY)
LLM_TOKENS = Counter("fieldcopilot_llm_tokens_total", "Tokens", ["tenant", "direction"], registry=REGISTRY)
LLM_COST = Counter("fieldcopilot_llm_cost_usd_total", "Estimated model spend in USD", ["tenant"], registry=REGISTRY)
LLM_FALLBACKS = Counter("fieldcopilot_llm_fallbacks_total", "Calls served by a fallback model", ["purpose"], registry=REGISTRY)
ANSWERS = Counter("fieldcopilot_answers_total", "Replies by outcome", ["tenant", "specialist", "outcome"], registry=REGISTRY)
APPROVALS = Counter("fieldcopilot_approvals_total", "Approval decisions", ["tenant", "status"], registry=REGISTRY)
INJECTION_FLAGS = Counter("fieldcopilot_injection_flags_total", "Tool outputs flagged as possible injection", ["tenant"],
                          registry=REGISTRY)
POLICY_VIOLATIONS = Counter("fieldcopilot_policy_violations_total", "Replies blocked by the output policy",
                            ["tenant", "rule"], registry=REGISTRY)
PENDING_APPROVALS = Gauge("fieldcopilot_pending_approvals", "Approvals waiting for a person", ["tenant"], registry=REGISTRY)
HTTP_REQUESTS = Counter("fieldcopilot_http_requests_total", "HTTP requests by method and status code",
                        ["method", "code"], registry=REGISTRY)
CANARY_QUALITY = Gauge("fieldcopilot_canary_quality_score", "Golden-set score of this build (0-1), from the startup self-check",
                       registry=REGISTRY)


def llm_hook(record) -> None:
    """Subscribe to LLM call records (fieldcopilot.llm.CallRecord)."""
    tenant = record.tenant or "none"
    LLM_CALLS.labels(tenant, record.purpose, record.model, record.outcome).inc()
    LLM_LATENCY.labels(record.purpose).observe(record.latency_ms / 1000)
    LLM_TOKENS.labels(tenant, "input").inc(record.input_tokens)
    LLM_TOKENS.labels(tenant, "output").inc(record.output_tokens)
    LLM_COST.labels(tenant).inc(record.cost_usd)
    if record.fell_back:
        LLM_FALLBACKS.labels(record.purpose).inc()


def record_reply(tenant: str, reply) -> None:
    outcome = getattr(getattr(reply, "payload", None), "status", None) or reply.status
    ANSWERS.labels(tenant, reply.specialist, str(outcome)).inc()
    for rule in reply.violations:
        POLICY_VIOLATIONS.labels(tenant, rule).inc()
    if any("possible_injection" in f for f in reply.flags):
        INJECTION_FLAGS.labels(tenant).inc()
