# Deployment options and data residency

How field-copilot can be deployed for a customer, where each kind of data lives, and what has to be decided with the customer's security team. The model choice per customer is in [decision 0006](decisions/0006-where-each-customers-model-runs.md).

## Options

| | A. In the customer's AWS account (EKS) | B. Customer's cluster, Claude API | C. Hosted by us, multi-tenant | D. ECS Fargate instead of EKS |
|---|---|---|---|---|
| Who | Corvane | Tallowmere | Smaller customers later | Customers with no Kubernetes team |
| Where it runs | Customer VPC, private EKS, RDS | Customer's existing cluster | Our account, one database with row-level security per tenant | Customer VPC, ECS services |
| Model | Claude in Amazon Bedrock | Claude API under a BAA | Claude API | Bedrock or Claude API |
| How it's deployed | Terraform (`deploy/terraform/aws`) + Argo CD + Helm | Argo CD + Helm (`values-tallowmere.yaml`) | Same chart, one release per tenant | Terraform only; not built here |
| Customer effort | Highest: owns the account and the cluster | Medium | Lowest | Medium |
| Data control | Highest | High | Lower: shared infrastructure | High |

Options A and B are built and tested here. C is what the multi-tenant API (Step 16, with PostgreSQL row-level security) already allows. D is a documented alternative only.

## Where data lives (option A, Corvane)

| Data | Where | Encryption | Retention |
|---|---|---|---|
| Runbooks, postmortems, policy documents and their embeddings | RDS PostgreSQL (pgvector), private subnets | Customer KMS key | Until removed from the pack |
| Questions, answers, runs, approvals | RDS, row-level security per tenant | Customer KMS key | Customer's choice (default 1 year) |
| Audit log | RDS, append-only table (a trigger blocks UPDATE and DELETE) | Customer KMS key | 1 year minimum |
| Prompts and model outputs in transit | Claude in Amazon Bedrock (global endpoint from ap-south-1) | TLS, SigV4 | Governed by Amazon Bedrock's data protection terms |
| Traces | OpenTelemetry collector in the cluster; prompt and output content stripped | n/a | Customer's tracing backend |
| Metrics | Prometheus; labels never include users, questions or run IDs | n/a | Customer's Prometheus |
| Secrets (JWT key, DSNs, workbench token) | AWS Secrets Manager, synced by External Secrets | Customer KMS key | n/a |
| Voice notes | Transcribed in the pod (faster-whisper); audio is not stored | n/a | Transcript only, redacted |

## What leaves the customer's VPC

- **Model calls**, to `bedrock-mantle.<region>.api.aws` over TLS, signed with the pod's own AWS credentials. Prompts carry redacted text (Step 12): secrets and personal data are replaced before the call.
- **Image pulls** from `ghcr.io/builtbyshailendra`. Only images signed by the release workflow are admitted (Step 21). A customer who wants no internet pulls can mirror them into ECR; the ECR endpoints are already in the VPC.
- **Nothing else.** STS, Secrets Manager, KMS, CloudWatch Logs, ECR and EKS auth go through VPC endpoints; S3 through a gateway endpoint.

## The AWS stack (deploy/terraform/aws)

- **Network:** three private /20 subnets, small public subnets that only hold the NAT gateway(s), VPC flow logs, a locked-down default security group, interface endpoints, an S3 gateway endpoint.
- **EKS:** private API endpoint only; the Terraform runner gets no implicit admin (access entries name the customer's admin roles); Kubernetes secrets encrypted with the customer's KMS key; all control-plane log types on; nodes require IMDSv2 with hop limit 1, so pods can't use the node's role.
- **Identity:** EKS Pod Identity gives the copilot's service account one role. It can call the listed Claude models (`bedrock-mantle:CreateInference`), read its own secret and decrypt that secret, and do nothing else.
- **Database:** RDS PostgreSQL 16, private, encrypted, TLS required, IAM authentication on, the admin password kept by RDS in Secrets Manager (never in Terraform state), deletion protection, 14-day backups, Performance Insights.
- **Cost guardrail:** a monthly AWS budget with forecast and actual alerts. One account per customer deployment, so the budget includes Bedrock usage.

Checked without an AWS account: `terraform validate`, and `terraform test` with a mocked provider. The tests assert that the API endpoint is private, the database is private and encrypted, nothing gets a public IP, nodes require IMDSv2, the copilot role has no wildcard actions and can only call the listed models, and the NAT layout follows the setting. Nothing has been applied to a real account.

## Cost drivers (rough, before usage)

These are what to put into the AWS pricing calculator for the customer's region, not a quote:

- EKS control plane: $0.10 an hour (standard support).
- Nodes: 3 × m7i.large by default (on-demand; savings plans lower this).
- NAT gateway: one per zone in production (hourly charge plus per-GB processing).
- Interface endpoints: 7 services × 3 zones, each charged hourly.
- RDS: db.t4g.medium single-AZ for the pilot, Multi-AZ doubles the instance cost.
- Claude usage: the largest variable cost. The platform reports it per tenant (Step 19 metrics) and per run, and budgets cap it per run (Step 2).

## Decisions to make with the customer

1. Inference routing in ap-south-1: global endpoint or another region (decision 0006).
2. Retention for runs and the audit log.
3. Whether the investigator may take actions at all (`clusterAccess.allowActions`), or only read.
4. Mirror images into ECR, or pull signed images from GHCR.
5. Single NAT gateway for the pilot, or one per zone.
