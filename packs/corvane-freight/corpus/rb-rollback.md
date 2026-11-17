---
id: rb-rollback
title: "Runbook: rolling back a release"
type: runbook
owner: platform-team
updated: 2026-10-20
---
# Runbook: rolling back a release

## When a rollback is the right first move
- The problem started within 30 minutes of a deploy of the affected service.
- The previous revision ran without the problem for at least a day.
- No database migration in the new release is irreversible. Check the release notes first: if a migration dropped or renamed a column, rolling back the code alone will break the old version.

## Who can approve
At night and at weekends the on-call lead approves rollbacks. During business hours the service owner or the on-call lead. Every rollback is recorded in the incident channel with the revision numbers.

## How
1. Find the last healthy revision: `helm history <release> -n freight`.
2. Roll back: `helm rollback <release> <revision> -n freight --wait --timeout 5m`.
3. Watch the error rate and the ready replica count for ten minutes.
4. Post the before and after numbers in the incident channel.

## After
Open a ticket for the fix. Do not redeploy the same revision. If a rollback does not fix the problem, the deploy was not the cause: go back to investigating.
