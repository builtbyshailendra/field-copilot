---
id: rb-queue-lag
title: "Runbook: orders queue consumer lag"
type: runbook
owner: orders-team
updated: 2026-09-18
---
# Runbook: orders queue consumer lag

## Symptoms
The `orders-consumer` lag metric climbs and the alert `OrdersQueueLagHigh` fires when lag passes 5,000 messages for 10 minutes. Customers see shipments stuck in "processing".

## Checks
1. Are consumers running? Check ready replicas of `orders-consumer`.
2. Are they processing slowly or failing? Compare the processed-per-second metric with the error rate.
3. Is a downstream dependency slow? Consumers call `carrier-gateway`; when a carrier API is down, every message waits for a timeout.
4. Is there a poison message? The same message ID retried again and again in the logs means one bad message is blocking a partition.

## What to propose
- Dependency outage: enable the carrier circuit breaker (see the dependency outage runbook) so messages for that carrier are parked instead of retried.
- Consumers simply behind: scale `orders-consumer` up to the partition count (12), no further.
- Poison message: move it to the dead-letter queue with the orders team's approval.
