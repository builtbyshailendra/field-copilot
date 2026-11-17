---
id: rb-image-pull
title: "Runbook: ImagePullBackOff and ErrImagePull"
type: runbook
owner: platform-team
updated: 2026-07-14
---
# Runbook: ImagePullBackOff and ErrImagePull

## Symptoms
New pods stay in `ImagePullBackOff` or `ErrImagePull`. Existing pods keep running, so the service may be degraded rather than down.

## Checks
1. `kubectl describe pod <pod>`: the event names the image and the error, for example `manifest unknown` or `unauthorized`.
2. `manifest unknown` or `not found`: the tag does not exist. Compare the tag in the release with the tags in the registry. A typo in the image tag in the Helm values caused the August label-printer incident.
3. `unauthorized` or `denied`: the image pull secret expired or is missing in the namespace.
4. `toomanyrequests`: registry rate limit. Check whether the image is pulled from a public registry instead of our mirror.

## What to propose
- Wrong tag: roll back the release, or redeploy with the correct tag after review.
- Expired pull secret: rotate it through the secrets pipeline; never paste credentials into chat or tickets.
