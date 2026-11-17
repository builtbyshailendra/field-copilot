---
id: rb-crashloop
title: "Runbook: pods in CrashLoopBackOff"
type: runbook
owner: platform-team
updated: 2026-09-12
---
# Runbook: pods in CrashLoopBackOff

## When to use
The alert `KubePodCrashLooping` fired, or `kubectl get pods` shows a pod with status `CrashLoopBackOff` and a rising restart count.

## First five minutes
1. Find out when it started and whether it matches a deploy: check the service's deploy history.
2. Read the logs of the **previous** container, not the current one: `kubectl logs <pod> --previous`. The crash is in the last run.
3. Read the pod's events with `kubectl describe pod <pod>`. Look at `Last State: Terminated` and its exit code.

## Reading the exit code
- Exit code 1: the application raised an error on startup. The previous logs usually name it, often a missing environment variable or config key.
- Exit code 137: the container was killed with SIGKILL. See the OOMKilled runbook before assuming memory.
- Exit code 139: segmentation fault, usually a native library problem after an image change.

## Common causes at Corvane
- A release expects a new environment variable or secret that was not added to the Helm values for production.
- A ConfigMap change with a typo in a key the service reads at startup.
- A database migration the new release depends on did not run.

## What to propose
If the crash started with a deploy and the previous version was healthy, propose a rollback to the previous revision (see the rollback runbook). Do not delete pods to "clear" the loop; they come back with the same error.
