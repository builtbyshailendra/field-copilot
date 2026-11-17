# Pilot plans

Two six-week pilots, one per customer. Both start in **shadow mode**: the copilot works on real questions and incidents, and people compare its output with their own before anyone relies on it. The success metrics are the ones agreed in discovery ([Corvane](../discovery/corvane-freight/README.md#6-success-metrics-agreed-up-front-measured-in-the-lab), [Tallowmere](../discovery/tallowmere-health/README.md#5-success-metrics)).

## Where things stand before the pilot

Measured in the lab with the **fake model** (a scripted stand-in), so these show the plumbing works, not how good Claude is on the customer's data:

| | Lab result | What the pilot has to show |
|---|---|---|
| Corvane: correct cause ranked first, 13 lab incidents | 13/13, but the rules baseline was written alongside the scenarios | ≥ 70% with Claude, on incidents the rules weren't written for |
| Corvane: every claim backed by evidence that exists | 100% | 100% |
| Corvane: actions without approval | 0 | 0 |
| Tallowmere: policy answers citing the right section | 70% (hashing embedder); retrieval MRR 0.88 with bge-small + reranker | ≥ 90% |
| Tallowmere: correct "the policy doesn't say" | 100% | ≥ 95% |
| Tallowmere: letters sent without approval | 0 | 0 |

The first job in week 1 is to run the same evaluation suite with Claude (`FIELD_LLM_MODE=record`, then replay) and replace this table with real numbers.

## Corvane Freight: on-call investigations

**Goal:** time to first useful hypothesis under 2 minutes on common incidents, with evidence an on-call engineer trusts.

| Week | What happens | Exit check |
|---|---|---|
| 0 | Terraform applied in Corvane's pilot account; Bedrock model access approved; inference-routing decision recorded (decision 0006); SSO wired | Security sign-off on the data path |
| 1 | Evaluation suite with Claude; 10 of Corvane's real postmortems converted into lab scenarios the rules have never seen | Top-1 ≥ 70% on those 10 |
| 2–3 | **Shadow mode** on live pages: the copilot investigates every page; on-call engineers note time to their own first hypothesis and whether the copilot's matched | 20 incidents compared |
| 4–5 | **Assisted mode** for volunteers: the investigation opens with the page; actions stay proposals; `allowActions` only if the on-call leads agree | No unapproved actions; engineers' rating ≥ 4/5 |
| 6 | Readout: metrics against targets, cost per investigation from real token counts, go/no-go | Sponsor decision |

**Roles:** sponsor (Head of Platform Engineering); 4 to 6 on-call engineers; 2 on-call leads (approvers); Corvane's security team (data path, Bedrock routing); me as FDE (build, measure, fix, train).

**Stop the pilot if:** an action runs without approval (never expected; it's a bug), a confident wrong root cause misleads an incident twice, or the security team withdraws the data-path approval.

## Tallowmere Health Plan: review nurses

**Goal:** fewer minutes per policy check and per RFI letter, with no decision made by the tool.

| Week | What happens | Exit check |
|---|---|---|
| 0 | BAA confirmed with the model provider; read-only `copilot` principal and letter-queue endpoint added to the workbench (product feedback 1); SSO wired | Compliance sign-off |
| 1 | Evaluation suite with Claude on the labelled policy set; nurses add 30 questions from their own week | Citation accuracy ≥ 90% |
| 1 | Baseline: timed sample of policy checks and letters, before go-live | Baseline recorded |
| 2–3 | **Shadow mode:** nurses ask the copilot alongside their normal process and mark each answer right or wrong (the feedback loop turns wrong answers into eval cases) | 300 rated answers |
| 4–5 | **Assisted mode:** drafted RFI letters, approved by the nurse before they're queued; fax reading on with every value marked "needs confirmation" | 0 letters without approval |
| 6 | Readout: minutes per check and per letter against baseline, refusal accuracy, go/no-go | Sponsor decision |

**Roles:** sponsor (Director of Utilization Management); 6 review nurses; 1 medical director (watches for decision creep); compliance/privacy officer; workbench engineering contact; me as FDE.

**Stop the pilot if:** the tool states or implies a coverage decision in a reply that reaches a nurse, PHI appears in a log or trace, or citation accuracy on rated answers drops below 85%.

## Both pilots

- **Weekly 30-minute review** with the sponsor: metrics, top 5 failures, what changed.
- **Every wrong answer becomes an eval case** (Step 13's feedback loop), and a fix ships only if the gates pass.
- **Cost watch:** spend per tenant on the dashboard; per-run budgets cap any single run.
- **Exit:** if the pilot stops, the copilot is scaled to zero, credentials are revoked, and the customer keeps the database and audit log ([runbook](../operations/runbook.md#turn-the-copilot-off-safely)).
