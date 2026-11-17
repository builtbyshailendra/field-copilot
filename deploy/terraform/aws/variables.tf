variable "tenant" {
  description = "Customer short name, used in resource names (for example corvane)."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.tenant))
    error_message = "Use 3-21 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "pilot, staging or prod."
  type        = string
  default     = "pilot"
}

variable "region" {
  description = "AWS region for everything, including the Bedrock endpoint the copilot calls."
  type        = string
  default     = "ap-south-1"
}

variable "vpc_cidr" {
  description = "VPC address range. Private subnets get /20s, public subnets /24s."
  type        = string
  default     = "10.40.0.0/16"
}

variable "az_count" {
  description = "Availability zones to spread subnets, nodes and the database across."
  type        = number
  default     = 3
  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "Use 2 or 3 availability zones."
  }
}

variable "single_nat_gateway" {
  description = "One NAT gateway (cheaper, pilot) or one per zone (survives a zone outage)."
  type        = bool
  default     = true
}

variable "interface_endpoints" {
  description = "AWS services reached privately through VPC interface endpoints. No PrivateLink endpoint for Claude in Amazon Bedrock (bedrock-mantle) is documented as of November 2026, so model calls leave through the NAT gateway over TLS."
  type        = list(string)
  default     = ["sts", "eks-auth", "secretsmanager", "kms", "logs", "ecr.api", "ecr.dkr"]
}

variable "kubernetes_version" {
  description = "EKS Kubernetes version."
  type        = string
  default     = "1.34"
}

variable "admin_role_arns" {
  description = "IAM roles given cluster-admin through EKS access entries (the customer's platform team). The cluster creator gets no implicit access."
  type        = list(string)
}

variable "node_instance_types" {
  description = "Instance types for the managed node group."
  type        = list(string)
  default     = ["m7i.large"]
}

variable "node_count" {
  description = "Minimum, desired and maximum nodes."
  type        = object({ min = number, desired = number, max = number })
  default     = { min = 2, desired = 3, max = 6 }
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.medium"
}

variable "db_allocated_storage" {
  description = "Initial database storage in GiB; autoscaling allows up to four times this."
  type        = number
  default     = 50
}

variable "db_multi_az" {
  description = "Standby in a second zone (recommended beyond the pilot)."
  type        = bool
  default     = true
}

variable "postgres_version" {
  description = "RDS PostgreSQL major version (pgvector is available on 15.2 and later)."
  type        = string
  default     = "16"
}

variable "app_namespace" {
  description = "Kubernetes namespace the chart is installed in (matches deploy/argocd/applicationset.yaml)."
  type        = string
  default     = ""
}

variable "service_account" {
  description = "Service account the API and worker run as (serviceAccount.name in the chart)."
  type        = string
  default     = "field-copilot"
}

variable "bedrock_model_arns" {
  description = "ARNs of the Claude models the copilot may call through bedrock-mantle (bedrock-mantle:CreateInference), copied from the AWS documentation for the account's region. Kept explicit so the role can't call any other model."
  type        = list(string)
}

variable "log_retention_days" {
  description = "Retention for EKS control-plane and VPC flow logs."
  type        = number
  default     = 90
}

variable "monthly_budget_usd" {
  description = "Monthly AWS budget for this account, model usage included."
  type        = number
  default     = 2000
}

variable "budget_alert_emails" {
  description = "Who hears about forecast and actual budget overruns."
  type        = list(string)
  validation {
    condition     = length(var.budget_alert_emails) > 0
    error_message = "Give at least one address."
  }
}
