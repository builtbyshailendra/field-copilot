# 0006. Where each customer's model runs

**Status:** accepted · 18 Nov 2026

## Context
The platform calls Claude through one interface (decision 0002), so the model endpoint is a per-customer setting. The two customers have different constraints:

- **Corvane Freight** wants everything in its own AWS account, billed through AWS, with its security team's existing IAM and CloudTrail controls. Its region is ap-south-1 (Mumbai).
- **Tallowmere Health Plan** handles PHI. The model provider has to be covered by a BAA, and only the minimum necessary case data may go into prompts.

The options for calling Claude:

| Option | Operated by | Model IDs | Notes for us |
|---|---|---|---|
| Claude API | Anthropic | `claude-opus-5-5` | Full feature set, including server-side refusal fallback |
| Claude Platform on AWS | Anthropic, through AWS | `claude-opus-5-5` | Same features as the Claude API; SigV4 and IAM; AWS Marketplace billing |
| Claude in Amazon Bedrock (`bedrock-mantle`) | AWS | `anthropic.claude-opus-5-5` | Messages API on AWS infrastructure, no Anthropic operator access; IAM action `bedrock-mantle:CreateInference`; no server-side fallback, batches or server tools |

## Decision
- **Corvane: Claude in Amazon Bedrock** (`FIELD_LLM_MODE=bedrock`). The pod gets its own AWS role through EKS Pod Identity, scoped to `bedrock-mantle:CreateInference` on the listed model ARNs. Our reliability layer already falls back to the next model on a refusal, so losing server-side fallback costs nothing. We don't use batches or server tools.
- **Tallowmere: the Claude API** (`FIELD_LLM_MODE=anthropic`) under the customer's BAA, with the minimum-necessary case view from Step 9 deciding what reaches a prompt.

## The data-residency catch for Corvane
Claude in Amazon Bedrock offers two endpoint types: **global** (routed across regions) and **regional** (kept in one region, at a 10% premium). In ap-south-1 only the **global** endpoint is listed. So in Mumbai, prompts and outputs can be processed outside India, while everything at rest (documents, embeddings, runs, audit log, traces) stays in the Mumbai VPC.

We'll put this to Corvane's security team in discovery rather than decide it for them:
1. Accept global routing for inference and keep data at rest in Mumbai. This is the recommended pilot setup.
2. Move the deployment to a region with in-region routing, if in-country inference is a hard requirement and the region is acceptable.
3. Keep incident data that must not leave the country out of prompts: the redaction layer from Step 12 already strips secrets and personal data.

## Consequences
- Two model modes to test. The provider code is shared, Bedrock just uses a different client and model IDs (`build_llm("bedrock")`, test in `test_anthropic_provider.py`).
- Bedrock spend appears on Corvane's AWS bill, so the account budget in Terraform covers model spend too.
- No PrivateLink endpoint is documented for `bedrock-mantle` today, so model calls leave the VPC through the NAT gateway over TLS, signed with SigV4. If AWS publishes one, it goes into `interface_endpoints`.
- Availability and endpoint types are as published in November 2026. Check them again before a real pilot.
