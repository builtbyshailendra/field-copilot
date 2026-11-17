# Model card: message router (scikit-learn)

## What it does
Assigns an incoming message to one of six routes (investigate_incident, policy_question,
draft_letter, case_lookup, general_question, out_of_scope). When its probability is at
least 0.8, the copilot uses its answer and skips the LLM routing call; otherwise the LLM
router decides.

## Model
TF-IDF on word 1-2 grams and character 3-5 grams, then logistic regression (C=4). Trains
in under a second at start-up from code; no binary artifact is committed.

## Training data
294 synthetic messages generated from templates in `core/src/fieldcopilot/ml/router.py`,
covering both customers (service names, case IDs, policy names, runbook topics). No real
user data.

## Evaluation (held-out)
Tested on the 37 hand-written messages in `core/evals/datasets/route.yaml`, written
separately and never used for training (5 marked tricky).

| Metric | Value |
|---|---|
| Accuracy, all messages | 91.9% |
| Share of messages with confidence ≥ 0.8 | 59.5% |
| Accuracy on those confident messages | 100% |
| Mean latency | ~2 ms on a laptop CPU |
| Cost per message | $0 |

Per-route F1: investigate 0.92, policy 0.88, draft_letter 1.00, case_lookup 0.86,
general 1.00, out_of_scope 0.91.

Errors, all below the confidence threshold, so they go to the LLM router:
- r31 "what's a good pizza place near the office" → case_lookup (0.48)
- r33 "checkout is down and I need to know what the runbook says" → policy_question (0.49)
- r34 "the policy says 6 weeks of PT, does case 1042 have that?" → policy_question (0.78)

Leakage check: a test asserts no generated training message equals a test message. It
caught one on the first run ("what does exit code 137 mean" was both a template and test
question r23); the template was reworded and the numbers above are after the fix (they
didn't change).

## Intended use and limits
- A first-stage filter to save cost and latency, not the final authority: out_of_scope
  refusals are also enforced by the output policy and approvals, whatever routes them.
- Synthetic, template-generated training data: it will miss phrasings unlike the
  templates. Uncertain messages fall back to the LLM; feedback (thumbs-down on a wrong
  route) should be added to the training templates.
- 37 test messages is a small set: one message moves accuracy by 2.7 points.
- Re-evaluate when routes change or a new customer pack is added.
