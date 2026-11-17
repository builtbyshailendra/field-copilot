# 0003. Humans approve every side effect

**Status:** accepted · 17 Nov 2026

## Context
Corvane's on-call engineers want suggestions, not an AI touching production. Tallowmere's nurses must send every letter themselves, and no tool may change a case's status.

## Decision
Every tool declares `side_effect: none | proposes | acts`.
- `none` (read-only) runs inside the agent loop.
- `proposes` returns an **approval request** with exactly what would run, and the loop pauses.
- `acts` can only be called by the approval service, after a user with the tenant's approver role approves that exact request. The agent never calls it.

Approvals expire, are tied to the request's content hash (approve the thing you saw, not something edited after), and are written to the audit log with who, when and why.

## Consequences
- The agent is safe to point at production read-only data.
- Some useful automation is slower. That's the point of the pilot; auto-approval rules can come later, per action, with evidence.
