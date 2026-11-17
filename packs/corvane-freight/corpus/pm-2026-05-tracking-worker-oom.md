---
id: pm-2026-05-tracking-worker-oom
title: "Postmortem: tracking-worker OOM kills (May 2026)"
type: postmortem
owner: tracking-team
updated: 2026-05-27
---
# Postmortem: tracking-worker OOM kills

## Summary
Tracking updates were delayed by up to 2 hours over two days while tracking-worker pods were repeatedly OOMKilled. Severity 2.

## Timeline
- 20 May: release 88 upgraded the JSON parsing library.
- 20 May 18:00 onward: memory climbed steadily from 300 MiB to the 1 GiB limit over about 5 hours, then the container was OOMKilled and restarted. The cycle repeated.
- 21 May: the limit was raised to 2 GiB. The kills slowed but did not stop.
- 22 May: rolled back to release 87. Memory flat at 300 MiB.

## Root cause
The new library version cached parsed schemas without a size limit. Each carrier event format added entries forever.

## What we learned
Raising the limit hid a leak for a day. A steady climb after a release means roll back first, then investigate.

## Actions
- Memory growth alert: more than 50% growth in 3 hours after a deploy.
- Dependency upgrades get a 24-hour soak in staging under replayed traffic.
