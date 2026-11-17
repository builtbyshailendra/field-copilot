---
id: rb-dependency-outage
title: "Runbook: upstream carrier API outage"
type: runbook
owner: integrations-team
updated: 2026-09-25
---
# Runbook: upstream carrier API outage

## Symptoms
- `carrier-gateway` logs timeouts or 5xx responses for one carrier.
- `rate-quote` and `orders-consumer` slow down because they wait on the gateway.
- Our own services look healthy: pods ready, no recent deploy.

## Checks
1. Error rate by carrier on the carrier-gateway dashboard. One carrier failing while others succeed points outside Corvane.
2. The carrier's public status page.
3. Retry volume: retries multiply load on a struggling carrier and on our own queue.

## What to propose
Enable the circuit breaker for that carrier through the feature flag `carrier.<name>.circuit_open`. Requests for that carrier fail fast and orders are parked for later. Turn it off once the carrier recovers and the error rate stays below 1% for 15 minutes. Changing the flag needs the on-call lead's approval.
