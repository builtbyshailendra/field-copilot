"""OpenTelemetry tracing with the GenAI semantic conventions.

Spans, nested the way the work happens:
- invoke_agent <purpose>           gen_ai.operation.name=invoke_agent, gen_ai.agent.name
  - chat <model>                    gen_ai.operation.name=chat, gen_ai.provider.name, gen_ai.request.model,
                                    gen_ai.response.model, gen_ai.usage.input_tokens / output_tokens,
                                    gen_ai.response.finish_reasons, plus our purpose, tenant, cost, attempts
  - execute_tool <tool>             gen_ai.operation.name=execute_tool, gen_ai.tool.name, gen_ai.tool.call.id

Prompts and completions are NOT recorded on spans (they can contain PHI); only sizes,
models, costs and outcomes. With no exporter configured the API is a no-op.

    FIELD_OTLP_ENDPOINT=http://otel-collector:4318  ->  OTLP/HTTP to a collector (Tempo, Jaeger, ...)
"""
from __future__ import annotations

import os

from opentelemetry import trace

tracer = trace.get_tracer("fieldcopilot")


def setup(service_name: str = "field-copilot") -> None:
    """Install an OTLP exporter when FIELD_OTLP_ENDPOINT is set. Idempotent."""
    endpoint = os.environ.get("FIELD_OTLP_ENDPOINT")
    if not endpoint or getattr(setup, "_done", False):
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")))
    trace.set_tracer_provider(provider)
    setup._done = True
