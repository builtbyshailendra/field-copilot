# Discovery: Corvane Freight (fictional)

Corvane Freight is a made-up logistics company. Everything here is synthetic and written as if after two discovery calls with their platform team. Numbers are hypotheses to test in the pilot, not findings.

## 1. What they asked for

> "Our on-call engineers spend the first half hour of every incident just finding things. Can AI do that part?"

## 2. What we think is going on

- 40 services on Kubernetes, owned by 7 product teams. One platform team of 6 runs the clusters and the on-call rotation.
- When a page fires, the on-call engineer opens Grafana, then logs, then `kubectl`, then the deploy history, then searches the wiki for a runbook. Each tool is fine; switching between them is the cost.
- Runbooks exist but are uneven. Past postmortems hold the best knowledge, and nobody searches them at 3 am.
- Most incidents are one of a few patterns: a bad release, a config change, resource limits, a dependency outage, a full disk or queue.

So the real problem is **time to first good hypothesis**, not "AI fixes production".

## 3. Stakeholders

| Who | What they care about | What they fear |
|---|---|---|
| Head of Platform (sponsor) | Shorter incidents, calmer on-call | An AI that touches production |
| On-call engineers (users) | Less tab-switching, a starting point fast | Being blamed for following a wrong suggestion |
| Security lead | Data stays in their AWS account; least privilege | A model with write access; secrets in prompts |
| Product team leads | Fewer customer-visible minutes | Another tool to learn |

## 4. Current workflow (one incident)

1. Alert fires in PagerDuty-like paging → on-call acknowledges.
2. Opens the service dashboard; checks error rate and latency.
3. Checks recent deploys; reads pod status and events.
4. Searches logs for errors around the start time.
5. Searches the wiki for a runbook; asks in chat if none.
6. Forms a hypothesis, acts (often a rollback), watches recovery.
7. Writes a postmortem days later.

Steps 2–5 are where the time goes, and they are all read-only. That's the part to assist.

## 5. Requirements

**Must**
- R1. Read-only access by default: metrics, logs, events, pod status, deploy history, runbooks, postmortems.
- R2. Every claim in an answer points to evidence: a metric query, a log line, an event, a runbook section.
- R3. Actions (rollback, scale, restart, config revert) are proposals. Nothing runs without a named human approving it, and every approval is audited.
- R4. Says "I don't know" when the evidence doesn't support a cause.
- R5. Runs inside the customer's AWS account if they require it (no data leaves; model through a private endpoint).
- R6. Secrets never enter a prompt; tool output is treated as data, not instructions.

**Should**
- R7. Investigation finishes in under 2 minutes for the common patterns.
- R8. Cost per investigation is visible and bounded.
- R9. Works from chat, the web app, and from engineers' own AI tools (MCP).
- R10. Voice notes from the on-call engineer are added to the incident timeline.

**Won't (for the pilot)**
- Auto-remediation without a human.
- Write access to source code or Terraform.

## 6. Success metrics (agreed up front, measured in the lab)

| Metric | Pilot target |
|---|---|
| Correct root cause ranked first, on the lab's failure scenarios | ≥ 70% |
| Correct cause in the top 3 | ≥ 90% |
| Every claim backed by evidence that exists | 100% |
| Actions executed without approval | 0 |
| Median time to first hypothesis | < 2 min |
| Cost per investigation | measured; target under USD 0.50 |

## 7. Risks

| Risk | Mitigation |
|---|---|
| Confident wrong answer during an incident | Evidence required for every claim; ranked hypotheses, not one; "I don't know" is valid |
| Prompt injection through log lines | Tool output wrapped and labelled as data; actions need approval anyway |
| Data residency | Private deployment pattern (see `docs/deployment-options.md`) |
| On-call trust | Pilot in shadow mode first: the copilot investigates, humans compare |

See `workshop-plan.md` for how the first session with their team would run.
