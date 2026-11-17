---
id: rb-oomkilled
title: "Runbook: OOMKilled and exit code 137"
type: runbook
owner: platform-team
updated: 2026-08-30
---
# Runbook: OOMKilled and exit code 137

## What 137 means
Exit code 137 is 128 + 9: the container received SIGKILL. That is all the number says. It is not always out of memory.

## Telling the cases apart
Run `kubectl describe pod <pod>` and read `Last State` → `Reason`.
- `OOMKilled`: the container went over its memory limit. Check memory usage against the limit in the service dashboard for the hour before the kill.
- `Error`: usually a shutdown problem. The process ignored SIGTERM (often because it runs as PID 1 behind `sh -c`) and was killed when the grace period ended.
- If the events show `Liveness probe failed` just before the restart, the probe killed it; fix the probe, not the memory.

## Memory checks
- Is the JVM or Node heap sized to the container limit, not the node? A heap sized to the node causes more OOM kills at Corvane than real leaks.
- Did memory climb steadily over hours (a leak) or jump at one moment (a large request or batch)?
- Did a dependency upgrade land in the last release? Library bumps caused two of our last three leaks.

## What to propose
- Steady climb after a release: roll back, then open a ticket for the leak.
- Limit simply too low for normal load: propose a limit increase through the Helm values, reviewed by the service owner.
- Never raise the limit without reading the reason first.
