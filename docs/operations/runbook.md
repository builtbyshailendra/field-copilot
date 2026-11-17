# field-copilot runbook

For whoever is on call for the copilot itself (not for the systems it helps investigate). Every alert in `deploy/observability/prometheus-rules.yaml` links to a section here. Commands assume the customer's namespace (`copilot-corvane` or `copilot-tallowmere`) and the Argo Rollouts kubectl plugin.

```bash
NS=copilot-corvane
```

## First five minutes, whatever the alert

1. Is it the copilot or its dependencies? `kubectl -n $NS get rollout,pods` and the Grafana dashboard (`deploy/observability/grafana-dashboard.json`): HTTP errors, model errors, latency, quality score.
2. Did a release just happen? `kubectl argo rollouts -n $NS get rollout field-copilot-<tenant>-api`. A canary that is failing its analysis aborts on its own; a promoted bad build can be undone (see [Roll back a release](#roll-back-a-release)).
3. Remember what the copilot can't do: every action it proposes waits for a person's approval, so a misbehaving copilot can at worst give bad answers or bad proposals. If in doubt, take it out of the path (see [Turn the copilot off safely](#turn-the-copilot-off-safely)) and investigate calmly.

## API errors

**Alerts:** `CopilotAvailabilityFastBurn` (page: at this rate the 30-day error budget is gone in about 2 days), `CopilotAvailabilitySlowBurn` (ticket: about 5 days).

Check:
- `kubectl -n $NS logs deploy/field-copilot-<tenant>-api --since=15m | grep '"level": "ERROR"'`. Logs are JSON with trace IDs and are already redacted.
- Database: `kubectl -n $NS get pods` (readiness fails when PostgreSQL is unreachable: `/readyz` runs `SELECT 1`). On AWS, RDS events and Performance Insights.
- Model errors usually show up here too; if `CopilotModelErrors` is also firing, go to [Model provider outage](#model-provider-outage).

Mitigate:
- Bad release: [roll back](#roll-back-a-release).
- Database connections exhausted: scale down the worker first (`kubectl -n $NS scale deploy/field-copilot-<tenant>-worker --replicas=0`), it is the least urgent consumer.
- One user hammering the API: the per-user rate limit (`FIELD_RATE_PER_MINUTE`, default 30) already returns 429s; check `fieldcopilot_http_requests_total{code="429"}`.

## Model provider outage

**Alert:** `CopilotModelErrors` (page: more than 1% of model calls fail after retries and fallback, for 10 minutes).

The client already retries with backoff (honouring `retry-after`) and falls back from Opus to Sonnet (strong) or Haiku to Sonnet (fast) before an error counts. So when this fires, the provider is having a real problem or our credentials are.

Check:
- `sum by (model, outcome) (rate(fieldcopilot_llm_calls_total[10m]))`: one model or all of them?
- 401/403 means credentials: Corvane's pod role (`bedrock-mantle:CreateInference` on the listed model ARNs) or Tallowmere's API key in the secret. 429 means quota: Bedrock's default is 2M input tokens per minute; raise it with AWS.
- Provider status pages (Anthropic, AWS Health for Bedrock in the region).

Mitigate:
- Tell users: the web app shows an error event per question, and investigations stop safely (no partial actions are possible).
- If only one model is failing, pin the chain to the healthy one by changing the model table in a hotfix release; don't edit pods by hand.
- A second provider is possible but not instant: Corvane could move from Bedrock to the Claude API (`FIELD_LLM_MODE=anthropic`) only if its security team has approved that data path. Don't switch without that approval.

## Injection attempts

**Alert:** `CopilotInjectionAttempts` (ticket: three or more tool outputs that looked like instructions in 15 minutes, per tenant).

This means text inside logs, events, documents or faxes looks like it's trying to instruct the model. The copilot has already labelled it as data and shown the flag to the user, and any action still needs approval.

- Find the runs: every run keeps its guardrail flags (`runs.flags`), so `SELECT id, created_at, flags FROM runs WHERE flags::text LIKE '%possible_injection%' ORDER BY created_at DESC` (as the tenant), then open each run's trace in the web app.
- Is it an attack or noise? Real attacks often come from user-controlled fields that end up in logs (shipment notes, ticket text, fax cover sheets).
- Tell the customer's security team with the run IDs. If it's noise from a known source, add it to the red-team set (`core/evals/datasets/redteam.yaml`) as a negative case rather than weakening the detector.

## Policy blocks

**Alert:** `CopilotPolicyBlocks` (ticket: five or more replies blocked by the tenant's output policy in an hour).

The output policy (`core/src/fieldcopilot/guard/policy.py`) stops replies that break a tenant rule (for Tallowmere, anything that reads as an approval or denial). A burst usually means a prompt change made the model drift.

- Which rule: the `rule` label. Which prompt version: the run's trace, or `GET /v1/admin/prompts`.
- If a prompt changed recently, revert it (prompts are versioned files under `core/src/fieldcopilot/prompts/library/`), then run the evaluation suite with the gates before releasing again.

## Spend spike

**Alert:** `CopilotSpendSpike` (page: more than $5 of model spend in an hour for one tenant).

- `topk(5, sum by (tenant, purpose) (increase(fieldcopilot_llm_cost_usd_total[1h])))`: which job is spending (investigations, answers, routing)?
- A loop: per-run budgets (`Budget`, Step 2) cap each run, so a spike is usually many runs, not one. Check for a script or integration calling the API or MCP server in a loop: `fieldcopilot_http_requests_total` by path, and the audit log for MCP calls.
- Revoke a misbehaving MCP client by deleting its token hash from `mcp-tokens.json` (see [Rotate secrets and tokens](#rotate-secrets-and-tokens)).
- On AWS, the account budget alarm (Terraform) is the backstop.

## Approvals waiting

**Alert:** `CopilotApprovalsWaiting` (ticket: an approval has waited 30 minutes).

A proposed action (a rollback, a letter) is waiting for someone with the approver role (Corvane: `oncall-lead`; Tallowmere: `review-nurse`). Approvals expire on their own and can't be approved after expiry, so nothing will happen silently.

- Tell the approver group. If the proposal is stale (the incident is over), reject it with a reason so the audit trail is complete.

## Canary quality low

**Alert:** `CopilotCanaryQualityLow` (page: a running build scores below 0.75 on its golden-set self-check).

Each pod runs a self-check at start (routing accuracy, retrieval MRR per tenant, output-policy cases), published as `fieldcopilot_canary_quality_score` and at `GET /v1/selfcheck`. A healthy build scores about 0.83.

- During a rollout, Argo Rollouts' analysis should already have aborted the canary. Confirm: `kubectl argo rollouts -n $NS get rollout field-copilot-<tenant>-api`.
- If a stable pod scores low, look at what changed outside the image: the document corpus (an empty or broken reindex), the embedding model cache, or configuration (`FIELD_FAULT` must never be set outside a drill).
- `curl http://<pod>:8020/v1/selfcheck` shows which part dropped.

## Roll back a release

```bash
kubectl argo rollouts -n $NS abort field-copilot-<tenant>-api      # stop a canary in progress
kubectl argo rollouts -n $NS undo field-copilot-<tenant>-api       # back to the previous stable build
```

Then revert the image tag in `deploy/helm/values-<tenant>.yaml` with a pull request, or Argo CD will roll forward again on its next sync.

## Turn the copilot off safely

From least to most drastic:
1. Stop actions only: set `clusterAccess.allowActions: false` (Corvane). The investigator keeps reading and proposing; nothing can patch Deployments even after approval.
2. Stop investigations: scale the API to zero and keep the database. Users get a clear "unavailable" from the web app.
3. Remove model access: detach the copilot's Pod Identity association (Corvane) or remove the API key from the secret (Tallowmere).

Each is a pull request to the tenant's values file (or a Terraform change), so the audit trail stays in Git.

## Rotate secrets and tokens

- **JWT signing secret** (`FIELD_JWT_SECRET`, 32+ characters outside dev): update it in Secrets Manager; External Secrets syncs it; restart the API. Everyone signs in again.
- **MCP tokens:** `mcp-tokens.json` holds SHA-256 hashes, each bound to one user and tenant. Remove a hash to revoke it; add a new hash to issue one. The server reloads on restart.
- **Database:** RDS rotates the admin password in Secrets Manager. The app role's password lives in the app secret.
- **Model credentials:** Corvane has none to rotate (pod role). Tallowmere's API key is in its secret.

## Reindex documents

After the customer changes runbooks or policies: `POST /v1/jobs/reindex` (watch it with `GET /v1/jobs/{id}`). The job runs on the worker through the PostgreSQL job queue. The answer cache keys on a fingerprint of the indexed chunks, so old cached answers stop matching on their own.

## A data deletion request

Runs, approvals and feedback are stored per tenant with row-level security. The audit log is append-only by design (a trigger blocks UPDATE and DELETE), so deleting a user's audit entries needs an agreed process with the customer's compliance team, normally a retention-based purge run as the database owner, not by the app.
