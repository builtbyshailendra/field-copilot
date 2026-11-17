# One customer deployment in the customer's own AWS account: a private network, an EKS
# cluster with no public API endpoint, RDS PostgreSQL (pgvector) and the copilot's AWS
# identity. Argo CD runs inside the cluster and pulls the chart, so nothing outside the
# VPC needs to reach the Kubernetes API. See docs/deployment-options.md.
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.72"
    }
  }

  # State lives in the customer's account, one key per environment:
  # backend "s3" {
  #   bucket       = "<customer>-terraform-state"
  #   key          = "field-copilot/pilot.tfstate"
  #   region       = "ap-south-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      app         = "field-copilot"
      tenant      = var.tenant
      environment = var.environment
      managed-by  = "terraform"
    }
  }
}
