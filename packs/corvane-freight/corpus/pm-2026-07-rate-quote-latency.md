---
id: pm-2026-07-rate-quote-latency
title: "Postmortem: rate-quote latency and database connection exhaustion (July 2026)"
type: postmortem
owner: data-platform
updated: 2026-07-30
---
# Postmortem: rate-quote latency and connection exhaustion

## Summary
Quote latency went from 300 ms to over 4 seconds at p95 for 55 minutes during the July sales event. Several other services also slowed. Severity 2.

## Timeline
- 10:00 HPA scaled rate-quote from 8 to 20 replicas on traffic.
- 10:04 Database active connections reached 400 (the maximum). New connections waited or failed.
- 10:20 On-call suspected a slow query and found none.
- 10:48 `pg_stat_activity` grouped by application showed rate-quote holding 500 connection attempts across 20 pools of 25.
- 10:55 rate-quote pool size lowered to 10 through an approved change. Latency recovered.

## Root cause
Each rate-quote replica opened a pool of 25 connections. The autoscaler's maximum replica count was set without considering the database's connection limit.

## Actions
- PgBouncer in front of the main cluster (done in September).
- HPA maximums reviewed against connection budgets for every service.
