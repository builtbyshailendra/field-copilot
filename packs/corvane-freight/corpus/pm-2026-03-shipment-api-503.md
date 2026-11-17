---
id: pm-2026-03-shipment-api-503
title: "Postmortem: shipment-api 503s after release 214 (March 2026)"
type: postmortem
owner: shipments-team
updated: 2026-03-19
---
# Postmortem: shipment-api 503s after release 214

## Summary
For 41 minutes on 12 March, 18% of shipment creation requests failed with 503. Severity 2.

## Timeline
- 14:02 Release 214 of shipment-api deployed.
- 14:06 New pods crash-looping; old pods still serving, at half capacity because maxUnavailable was 50%.
- 14:15 Alert on 5xx rate. On-call checked dashboards and the database first.
- 14:38 Previous container logs showed `KeyError: 'LABEL_SERVICE_TOKEN'`.
- 14:43 Rolled back to release 213. Errors stopped at 14:47.

## Root cause
Release 214 read a new secret, `LABEL_SERVICE_TOKEN`, that had been added to staging values but not to production values. New pods exited with code 1 on startup.

## What made it worse
Twenty-three minutes went on checking the database and dashboards before anyone read the previous container's logs.

## Actions
- CI now compares required environment variables against production values before deploy.
- maxUnavailable lowered to 0 for shipment-api.
- Runbook updated: read `kubectl logs --previous` first.
