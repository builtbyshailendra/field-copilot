---
id: rb-db-connections
title: "Runbook: PostgreSQL connection exhaustion"
type: runbook
owner: data-platform
updated: 2026-07-28
---
# Runbook: PostgreSQL connection exhaustion

## Symptoms
- Services log `FATAL: sorry, too many clients already` or connection timeouts.
- Latency rises across several services at once, not just one.
- The database dashboard shows active connections at or near `max_connections` (400 on the main cluster).

## Usual causes
1. A service was scaled up and each replica opens its full pool. Twenty replicas with a pool of 25 is 500 connections.
2. A slow query holds connections longer, so pools grow to their maximum.
3. A connection leak after a release: connections are opened and never returned.

## Checks
- Count connections per application name in `pg_stat_activity`.
- Compare the replica count of the top consumer with yesterday's.
- Look for long-running queries (`state = 'active'` for more than 30 seconds).

## What to propose
- Scale back the replica count of the top consumer, or lower its pool size, through an approved change.
- Kill a single runaway query only with the data platform on-call's agreement.
- Long term: route services through PgBouncer in transaction mode.
