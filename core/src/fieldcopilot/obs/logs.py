"""JSON logs with trace and span IDs, redacted before they're written."""
from __future__ import annotations

import json
import logging
import time

from opentelemetry import trace

from fieldcopilot.guard.pii import redact


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        span = trace.get_current_span().get_span_context()
        message, kinds = redact(record.getMessage())
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname, "logger": record.name, "msg": message,
        }
        if span.is_valid:
            entry["trace_id"] = format(span.trace_id, "032x")
            entry["span_id"] = format(span.span_id, "016x")
        if kinds:
            entry["redacted"] = kinds
        for key in ("tenant", "run_id", "route"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        return json.dumps(entry)


def setup(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
