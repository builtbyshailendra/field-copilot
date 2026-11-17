---
id: pm-2026-09-orders-queue-lag
title: "Postmortem: orders queue lag during a carrier outage (September 2026)"
type: postmortem
owner: integrations-team
updated: 2026-09-24
---
# Postmortem: orders queue lag during a carrier outage

## Summary
Orders lag reached 48,000 messages and shipments sat in "processing" for up to 90 minutes. Severity 1 for 25 minutes, then Severity 2.

## Timeline
- 08:10 Northwind Parcel's API started timing out (their outage).
- 08:12 orders-consumer messages for that carrier waited 30 seconds each for the timeout, then retried 5 times.
- 08:25 Lag alert. On-call scaled orders-consumer from 12 to 24 replicas. Lag kept growing: the extra replicas had no partitions to read and added retries.
- 08:50 Integrations on-call opened the circuit breaker for Northwind. Lag fell within 20 minutes.

## Root cause
No circuit breaker was enabled by default, and retries multiplied the load during the carrier's outage.

## What we learned
Scaling consumers past the partition count does nothing. When one carrier fails, isolate it.

## Actions
- Circuit breaker opens automatically after 50% errors for one carrier over 2 minutes.
- Runbook: check per-carrier error rates before scaling consumers.
