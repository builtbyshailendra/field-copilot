---
id: pm-2026-08-label-printer-imagepull
title: "Postmortem: label-printer stuck on ImagePullBackOff (August 2026)"
type: postmortem
owner: labels-team
updated: 2026-08-15
---
# Postmortem: label-printer stuck on ImagePullBackOff

## Summary
A label-printer release never rolled out: new pods stayed in ImagePullBackOff for 3 hours. Old pods kept serving, so labels were slower but not down. Severity 3.

## Root cause
The image tag in the production Helm values was `1.42.O` (capital letter O) instead of `1.42.0`. The registry answered `manifest unknown`.

## Detection
Nobody noticed for 3 hours because the old pods were healthy and no alert covered a rollout that never finished.

## Actions
- Alert when a Deployment's updated replicas stay below desired for 20 minutes.
- CI checks that every image tag in Helm values exists in the registry before merge.
