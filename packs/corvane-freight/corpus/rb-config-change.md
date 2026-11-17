---
id: rb-config-change
title: "Runbook: bad ConfigMap or Secret change"
type: runbook
owner: platform-team
updated: 2026-09-03
---
# Runbook: bad ConfigMap or Secret change

## Why this is easy to miss
A ConfigMap or Secret change does not create a new Deployment revision. Pods only pick it up when they restart, so the failure can appear hours after the change, at the next scale-up or node drain, and nothing shows in the deploy history.

## Checks
1. Look for config changes in the change log for the namespace: our GitOps repo records every ConfigMap and Secret change with a commit.
2. Compare the keys the service reads at startup with the current ConfigMap. A renamed key (for example `CARRIER_API_URL` to `CARRIER_URL`) breaks only new pods.
3. If only some pods fail and they are the newest ones, suspect config before code.

## What to propose
Revert the config commit through GitOps, then restart the affected Deployment so all pods read the same config. Both steps need approval. Never edit a live ConfigMap with `kubectl edit`; the next sync will overwrite it.
