---
id: rb-disk-pressure
title: "Runbook: disk pressure and full volumes"
type: runbook
owner: platform-team
updated: 2026-06-22
---
# Runbook: disk pressure and full volumes

## Two different problems
- **Node disk pressure:** the node's own disk is filling, usually with container logs or images. The kubelet starts evicting pods and the node shows `DiskPressure=True`.
- **Full persistent volume:** a PVC used by a database or queue is full. The pod keeps running but writes fail.

## Checks
1. `kubectl describe node <node>` for `DiskPressure` and eviction events.
2. For a PVC, check the volume usage metric and the application's own errors (`No space left on device`, `could not extend file`).
3. Find what grew: a service logging at debug level after a config change filled two nodes in June.

## What to propose
- Debug logging left on: revert the log level through GitOps.
- PVC nearly full: expand the volume (our storage class allows online expansion) with the owner's approval. Never delete database files to free space.
