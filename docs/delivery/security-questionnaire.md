# Security questionnaire: field-copilot

Answers to the questions a customer's security and privacy team asks before a pilot, in the order they usually come. Each answer points to where it is implemented or says plainly that it isn't. This is a portfolio project on synthetic data; a real vendor would add its company-level certifications, policies and contracts.

## 1. Organisation and assurance

| Question | Answer |
|---|---|
| SOC 2 / ISO 27001? | No. Personal portfolio project. The controls below are what the software does; organisational controls would come from the operating company. |
| Penetration test? | No external test. A red-team suite for prompt injection and data leakage runs in CI (`core/evals/datasets/redteam.yaml`, 19 of 19 cases pass with the fake model). |
| Who has access to customer environments? | In deployment option A, nobody outside the customer: it runs in the customer's AWS account; the customer's platform team holds cluster admin through EKS access entries. |

## 2. Data handled

| Question | Answer |
|---|---|
| What data does it process? | The customer's documents (runbooks, postmortems, medical policies), questions and answers, investigation evidence (metrics, logs, events) and, for Tallowmere, case facts from the prior-auth workbench. All data in this repository is synthetic. |
| PHI? | Tallowmere only. The case view given to the model is minimum-necessary (member names and NPIs removed before a prompt). The model provider must be covered by a BAA. |
| Where does each kind of data live? | Table in [deployment-options.md](../deployment-options.md#where-data-lives-option-a-corvane). |
| Is customer data used to train models? | Governed by the model provider's terms for the chosen path (Claude API commercial terms, or Amazon Bedrock's data protection terms). To be confirmed in the customer's contract with that provider, not assumed here. |
| Retention | Runs, approvals and feedback: customer's choice, agreed in discovery. The audit log is append-only. Voice-note audio is not stored, only the redacted transcript. |

## 3. Hosting and residency

| Question | Answer |
|---|---|
| Hosting options | In the customer's AWS account (EKS, RDS; Terraform in `deploy/terraform/aws`), in the customer's existing cluster (Helm), or hosted multi-tenant. See [deployment-options.md](../deployment-options.md). |
| Can data stay in-country? | Data at rest, yes (one region, customer's account). Model inference depends on the provider's endpoints: Claude in Amazon Bedrock lists only the global (cross-region) endpoint in ap-south-1, so in-country inference there is not available today ([decision 0006](../decisions/0006-where-each-customers-model-runs.md)). |
| What leaves the customer's network? | Model calls (TLS, SigV4 or API key) and image pulls of signed images. Everything else uses VPC endpoints. |

## 4. Encryption

| Question | Answer |
|---|---|
| At rest | RDS storage, Performance Insights, EKS Kubernetes secrets and Secrets Manager use the customer's KMS key (rotation on). Node volumes are encrypted. |
| In transit, external | TLS to the model provider; RDS requires TLS (`rds.force_ssl=1`). |
| In transit, inside the cluster | Plain HTTP between the web app and the API inside the cluster; NetworkPolicy limits who can reach PostgreSQL. **No service mesh / mTLS.** If required, add one; nothing in the app prevents it. |
| Key management | Customer-managed KMS key per deployment, deletion window 30 days. |

## 5. Identity and access

| Question | Answer |
|---|---|
| User sign-in | Short-lived HS256 JWTs carrying user, tenant and roles; httpOnly, SameSite=strict cookie for the web app with a CSRF header on writes. **The demo user directory is not for production**: a real pilot needs SSO (OIDC or SAML) against the customer's identity provider, which is **not built yet**. |
| Roles | Corvane: engineer, on-call lead (approves actions). Tallowmere: review nurse (approves letters), viewer. Checked on every approval, and approvals are bound to tenant and role. |
| Service identity | Corvane: the pod's own AWS role through EKS Pod Identity, allowed only `bedrock-mantle:CreateInference` on listed models and reading its own secret. Kubernetes access is a Role in one namespace; patching Deployments is a separate switch. |
| Engineers' AI tools (MCP) | Per-user bearer tokens stored as SHA-256 hashes, each bound to one user and tenant; OAuth is the recommended next step. Every MCP call is audited. |
| Tenant isolation | PostgreSQL row-level security with FORCE, through a non-superuser role; a test proves a query with no WHERE clause sees only its tenant's rows. Customers also get separate deployments. |

## 6. AI-specific controls

| Question | Answer |
|---|---|
| Can the AI take actions by itself? | No. Tools are typed as read-only, propose, or act. Anything with a side effect becomes a proposal that a person with the right role approves; the approval is bound to a hash of the exact action, expires, and is audited. "Actions executed without approval" is 0 across the evaluation suite. |
| Prompt injection | Tool output and documents are wrapped and labelled as data; a detector flags instruction-like text to the user; the agent can only call allowlisted tools with schema-checked arguments; side effects need approval anyway ([decision 0005](../decisions/0005-tool-output-is-data.md)). |
| Secrets reaching the model | Tool results are redacted for keys, tokens, passwords and connection-string credentials before the model sees them; logs, traces, feedback and the audit log are redacted too. |
| Hallucinated citations | Every claim's quote is located in the cited passage in code; claims that can't be verified are dropped, and an answer with none left is withheld. |
| "I don't know" | Supported and evaluated: questions the documents don't cover are in every evaluation set. |
| Change control for prompts and models | Prompts are versioned files with content hashes; the evaluation suite has quality gates in CI; new builds roll out as a canary that is promoted only if its golden-set score holds. |
| Output policy | Per-tenant rules block replies that cross a line (for Tallowmere, anything that reads as an approval or denial). |

## 7. Logging, monitoring and audit

| Question | Answer |
|---|---|
| Audit trail | Append-only table (a trigger rejects UPDATE and DELETE): sign-ins, approvals and decisions, MCP calls, voice notes. Each run also keeps its guardrail flags. |
| Logs and traces | JSON logs with trace IDs, redacted before writing. OpenTelemetry traces never carry prompt or output text; the collector config strips content attributes as a second guard. |
| Alerting | SLO burn-rate alerts, model errors, injection attempts, policy blocks, spend spikes, waiting approvals, low quality score; [runbook](../operations/runbook.md). |

## 8. Secure development and supply chain

| Question | Answer |
|---|---|
| Testing | Unit and integration tests against real PostgreSQL, browser tests (Playwright), evaluation gates, red-team suite, Terraform tests, Kyverno policy tests, all in CI. |
| Dependencies | pip-audit and Trivy on every push and weekly; Dependabot for Python, npm, Docker and GitHub Actions. Actions are pinned to version tags; pinning to commit SHAs is planned before a customer pilot. |
| Images | Scanned before push (fixable HIGH/CRITICAL blocks the release), SPDX SBOM, keyless cosign signature and signed SBOM attestation, build provenance. The cluster admits only images signed by the release workflow, pinned to digest. |
| Runtime hardening | Non-root, read-only root filesystem, no privilege escalation, all capabilities dropped, seccomp, enforced by Kyverno. |

## 9. Resilience

| Question | Answer |
|---|---|
| Backups | RDS automated backups, 14 days, point-in-time recovery; Multi-AZ recommended beyond the pilot. |
| Model provider outage | Retries with backoff, then fallback to the next model in the chain; investigations stop safely with no partial actions. |
| Bad release | Canary analysis aborts it automatically; [rollback steps](../operations/runbook.md#roll-back-a-release). |

## 10. Subprocessors (per deployment)

| Subprocessor | Used for | Customer |
|---|---|---|
| Amazon Web Services (Bedrock, EKS, RDS) | Model inference and hosting | Corvane |
| Anthropic (Claude API) | Model inference, under a BAA | Tallowmere |
| GitHub | Source code, CI, image registry (no customer data) | Both |
