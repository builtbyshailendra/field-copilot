# Product feedback from the field

What building and deploying for two customers taught about the product and its platform, written for the product and engineering teams. Each item has the evidence behind it, who it blocks, and a suggested fix. Ordered by what blocks a pilot first.

| # | Finding | Evidence | Blocks | Suggested fix | Priority |
|---|---|---|---|---|---|
| 1 | The prior-auth workbench has no read-only principal for the copilot and no endpoint to queue an approved letter | Step 9: the workbench issues service tokens only to its connectors and extractor | Tallowmere pilot | A `copilot` principal with read-only case scope, and `POST` for approved letters with the approval ID | Must, before the pilot |
| 2 | No single sign-on | The API uses a demo user directory (Step 16) | Both pilots | OIDC sign-in with role mapping from the customer's groups | Must, before the pilot |
| 3 | Corvane asked for "the model through a private endpoint" (R5); no PrivateLink endpoint for Claude in Amazon Bedrock is documented | Bedrock docs as of Nov 2026; Step 22 | Corvane security sign-off | Ask the AWS and Anthropic account teams; meanwhile, TLS and SigV4 through the NAT gateway, explained in the deployment doc | Must, decided before the pilot |
| 4 | In ap-south-1, Claude in Amazon Bedrock lists only the global endpoint, so inference may run outside India | Same docs; decision 0006 | Corvane, if in-country inference is required | Customer decision; track regional endpoint availability | Must, decided before the pilot |
| 5 | Semantic caching is unsafe at the thresholds people usually pick | bge-small at 0.90 gave 5 of 13 near-misses the wrong answer (Step 23) | Anyone turning caching on | Ship caching as shadow-first with an agreement metric, never on by default; keep near-miss sets per customer | Should |
| 6 | Router misroutes document questions that contain words like "request" or "alert" | Load test, Step 23 | Answer quality, latency (misrouted questions run a full investigation) | Feed rated misroutes into the router's training data through the feedback loop; add a "document question" fast path | Should |
| 7 | Secrets in tool output reached the model until Step 22 | Found while writing the data-residency doc | Any customer whose logs contain credentials | Make tool-output redaction a platform default, not an agent option (done here) | Done here; upstream it |
| 8 | Guardrail flags weren't stored with runs | Found while writing the runbook | Audit, incident review | Store flags with every run (done here) | Done here; upstream it |
| 9 | The investigation baseline scores 13/13 but was written alongside the scenarios | Step 8 caveat | Trusting lab numbers | Build eval sets from customers' real postmortems, held out from whoever writes rules or prompts | Should |
| 10 | The heuristic faithfulness judge agrees with labels only moderately (kappa 0.50) | Step 13 | Automated quality scores | Calibrate a Claude judge against the labelled set before relying on it in gates | Should |
| 11 | Fax values can't be verified in code the way typed text can | Step 9 | Nurse effort on faxes | An OCR text layer to check quotes against, so verified values don't need confirmation | Could |
| 12 | The investigator shells out to kubectl | Step 22 needed kubectl in the image | Image size, attack surface | Use the Kubernetes API client with the same narrow Role | Could |
| 13 | Rate limits and the answer cache are per replica | Step 16, Step 23 | Consistency at more than one replica | Move both to PostgreSQL or Redis when a customer runs several replicas | Could |
| 14 | GitHub Actions pinned to tags, not commit SHAs | Step 21 | Customers with strict supply-chain policies | Pin to SHAs, with Dependabot keeping them current | Should, before a customer pilot |
