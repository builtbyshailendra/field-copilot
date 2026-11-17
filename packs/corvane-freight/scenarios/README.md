# Corvane failure scenarios

Each file is a frozen snapshot of the `freight` namespace during one incident: deploy
history, config changes, pods, events, logs and metrics (one value per 5 minutes, last
value = now). `truth` holds the known cause, the right first action and the evidence
that proves it. The copilot only sees what its tools return; `truth` is used by the
evaluation.

Causes: bad_release, config_change, memory_leak, probe_misconfigured, readiness_failure,
image_pull, db_connections, dependency_outage, disk_pressure, cert_expired,
poison_message, no_issue.

Five of these are also injected into a real kind cluster in CI (`lab/`), where the same
tools read live `kubectl` output instead of these files.
