---
id: rb-readiness
title: "Runbook: failing readiness probes and stuck rollouts"
type: runbook
owner: platform-team
updated: 2026-10-02
---
# Runbook: failing readiness probes and stuck rollouts

## Symptoms
- A Deployment shows fewer ready replicas than desired, for example `2/4`, and the rollout does not finish.
- New pods are `Running` but never `Ready`. Old pods keep serving.
- `kubectl rollout status` waits until the progress deadline and then reports `ProgressDeadlineExceeded`.

## Why users may still see errors
Pods that never pass readiness receive no traffic, so a stuck rollout alone is often invisible. If users see errors, check whether only part of the old capacity is left (maxUnavailable above 0) and whether the remaining pods are overloaded.

## Checks
1. `kubectl describe pod` on a new pod: the events show the failing probe, its path, port and the HTTP status or timeout.
2. Compare the probe path and port in the new release with the old one. A renamed health endpoint is a classic cause.
3. Check startup time. A service that now loads more data at start may need a `startupProbe` or a longer `initialDelaySeconds`.
4. Check the new pods' logs for dependency errors: a readiness endpoint that checks the database fails when the database is unreachable.

## Important
Kubernetes marks a stuck rollout as failed but does **not** roll it back by itself. Our deploy pipeline runs `helm rollback` when the deadline passes; manual deploys need a person to do it.

## What to propose
Roll back to the last healthy revision, then fix the probe or the endpoint in a new release.
