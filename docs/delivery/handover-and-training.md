# Handover and training

What each customer's team needs to run field-copilot without me, and how they learn it.

## Who owns what after handover

| Area | Corvane | Tallowmere |
|---|---|---|
| Infrastructure (account, cluster, database) | Platform team | Their existing platform team |
| Releases (values files, Argo CD) | Platform team, by pull request | Platform team, by pull request |
| Documents in the copilot (runbooks, policies) | SRE leads | UM operations lead |
| Evaluation set and quality gates | SRE leads, with the platform team | UM operations lead, with a nurse educator |
| Approver roles | On-call leads | Review nurses |
| Incidents in the copilot itself | Platform on-call, using the [runbook](../operations/runbook.md) | Platform on-call |
| Model provider relationship and spend | Platform team (AWS bill) | IT, under the BAA |

## Training, by audience

| Audience | Session | Covers | Done when they can |
|---|---|---|---|
| On-call engineers | 45 min, hands-on in the lab | Asking well; reading ranked causes and their evidence; why a cause with no evidence is shown as unknown; proposing vs approving; voice notes | Investigate a lab incident and explain the evidence behind the top cause |
| On-call leads | 30 min | Approving and rejecting with a reason; expiry; what the audit log records; the `allowActions` switch | Approve a rollback in the lab and find it in the audit log |
| Review nurses | 45 min, on synthetic cases | Cited answers and checking the quote; "the policy doesn't say"; criterion status (met / not met / unknown); approving a drafted letter; fax values that need confirmation | Review a synthetic case end to end and approve a letter |
| Medical directors | 20 min | What the copilot never does (decisions), the output policy, how to report decision creep | Know where to report a reply that overstepped |
| Platform team | 2 × 90 min | Chart and values, Argo CD, canary analysis and rollback, Terraform, Kyverno policies, alerts and runbook, rotating secrets, reindexing | Run the canary drill on kind and roll back a bad build on their own |
| Document owners | 30 min | How documents become chunks; what makes a runbook or policy easy to cite; adding questions to the eval set | Add a document and an eval question, and see the gate result |

Materials: the [demo script](demo-script.md), the [runbook](../operations/runbook.md), the README build log, and the lab (`lab/run_lab.py`) for safe practice.

## Handover checklist

- [ ] Platform team has run the canary drill and a rollback themselves
- [ ] Alerts route to the customer's paging tool, and each alert's runbook link opens
- [ ] Secrets are rotated away from anything I've seen; my access is removed
- [ ] Evaluation gates run in the customer's CI, with their own questions added
- [ ] Document owners know how to reindex, and the answer cache mode is agreed (shadow by default)
- [ ] Open product feedback items are in the vendor's tracker, with owners
- [ ] Two weeks of office hours after handover, then the normal support channel

## Support during the pilot

Office hours twice a week; a shared channel for questions; anything that looks like a safety issue (an action without approval, a decision-like reply, PHI in logs) goes to me and the sponsor the same day and gets a written incident note.
