# Demo script (15 minutes)

For a customer sponsor or an interview panel. Runs on a laptop with the fake model, so it works offline and costs nothing; say that up front. Sign-in password is whatever `FIELD_DEMO_PASSWORD` is set to.

## Before you start

- API on port 8020 and web app on port 3100 (see "Run it locally" in the README); open http://127.0.0.1:3100.
- Two browser profiles: one signed in as `asha` (Corvane engineer), one as `ravi` (Corvane on-call lead). Later `j.patel` (Tallowmere nurse).
- Have the README's build log open in a tab for questions about "how do you know".

## 1. The problem in one sentence (1 min)

"Two very different customers, one platform: an on-call copilot for a freight company and a review-nurse copilot for a health plan. Each customer is a pack of documents, tools, prompts and evaluation sets; the platform code is shared."

## 2. A cited answer, and "I don't know" (2 min)

As `asha`: **"Who approves rollbacks at night and at weekends?"**
- Point at the claim, its quote, and the source section. "The quote was found in the passage by code, not by the model."

Then: **"Which cloud region does Corvane use for disaster recovery?"**
- "The documents don't cover that." (Don't use the on-call compensation question in this demo: with the fake model it's the evaluation suite's one known false answer, cq11. Mention that it exists; it's in the build log.) "A wrong confident answer is the worst outcome in an incident, so not knowing is a first-class answer, and it's in every evaluation set."

## 3. An investigation with evidence (4 min)

As `asha`: **"shipment-api pods restarting (service: shipment-api)"**
- Watch the steps stream: which tools the model asked for, each tool call.
- Ranked causes, each with its evidence: the event, the log line, the deploy.
- "There's a proposed rollback. **Nothing has run.**"
- Try to approve as `asha`: refused, wrong role.
- Switch to `ravi`: approve with a reason. Show the run's trace and the audit entry.

Talking points: actions are typed as read-only / propose / act; approvals are bound to a hash of the exact action and expire; log lines that try to instruct the model are flagged and treated as data.

## 4. The nurse's view (4 min)

As `j.patel`: **"What AHI qualifies for the initial 90-day CPAP rental?"** (cited answer).

Then: **"Does the plan cover dental implants?"** ("the policy doesn't say").

Then: **"Review case 3310"** (two required documents missing; case 1042 has none, so no letter):
- Each criterion: met, not met or unknown, with the quote.
- Missing documents, and **one** drafted RFI letter that follows the plan's letter rules, waiting in Approvals for the nurse.
- "The copilot never approves or denies; ask it to and it refuses." Try: **"Approve this case."**

## 5. How we know it works (2 min)

- Evaluations page: routing, retrieval, QA, investigations, red team, with gates that fail the build.
- The canary: "a new build is promoted only if its golden-set score holds. A deliberately broken build scores 0.48 against a 0.75 bar, and the drill in CI checks that the rollout aborts and the old build keeps serving" (Step 20; the drill runs in CI, not on this laptop).
- The cache story (Step 23): "at the usual threshold it would have given 5 of 13 near-miss questions the wrong answer, so it ships in shadow mode."

## 6. Deployment and close (2 min)

- Corvane: in its own AWS account, private Kubernetes API, Claude in Amazon Bedrock with a pod role limited to the listed models; the Mumbai inference-routing question goes to their security team.
- Tallowmere: Claude API under a BAA, minimum-necessary case data.
- "Next step is a six-week shadow-mode pilot with metrics agreed up front" ([pilot plans](pilot-plans.md)).

## If something goes wrong live

- An answer comes back "unverified": that's the citation check working; say so and ask another question.
- The API is down: show the screenshots in `docs/screenshots/` and the build log.
- Someone asks "is this Claude?": "In this demo it's the fake model, which makes the demo free and repeatable. The same code calls Claude with `FIELD_LLM_MODE=anthropic` or `bedrock`."
