---
id: rb-escalation
title: "On-call handbook: severity levels and escalation"
type: handbook
owner: head-of-platform
updated: 2026-04-01
---
# On-call handbook: severity levels and escalation

## Severity levels
- **SEV1:** customers can't create or track shipments, or data is at risk. Page the on-call lead immediately and open an incident channel.
- **SEV2:** a major feature is degraded (quotes slow, labels delayed) with a workaround. Open an incident channel within 15 minutes.
- **SEV3:** minor or internal impact. A ticket is enough.

## Escalation
1. The on-call engineer owns the incident until it is handed over in writing.
2. If there is no clear hypothesis after 30 minutes on a SEV1, page the service owner's team.
3. Any production change during an incident (rollback, scale, flag) needs the on-call lead's approval and a note in the channel.

## Communication
Post an update in the incident channel every 30 minutes for SEV1, every hour for SEV2: what we know, what we're doing, the next update time. Customer-facing status updates go through support, never directly from engineers.

## After the incident
A postmortem within five working days for every SEV1 and SEV2. Blameless: describe what happened and why the system allowed it, not who.
