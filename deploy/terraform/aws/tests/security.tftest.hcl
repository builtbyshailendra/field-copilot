# `terraform test` with a mocked AWS provider: no account, no credentials, no cost. It
# checks the properties a customer's security team will ask about, so a later edit can't
# quietly undo them.

mock_provider "aws" {
  # Mocked values must still look like ARNs where the provider validates them.
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::111122223333:role/mock" }
  }
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:ap-south-1:111122223333:key/mock", key_id = "mock" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:ap-south-1:111122223333:log-group:mock" }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:mock" }
  }
  mock_resource "aws_launch_template" {
    defaults = { id = "lt-0123456789abcdef0", latest_version = 1 }
  }
  mock_resource "aws_db_instance" {
    defaults = { master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:rds", kms_key_id = "mock", secret_status = "active" }] }
  }
  override_data {
    target = data.aws_availability_zones.available
    values = { names = ["ap-south-1a", "ap-south-1b", "ap-south-1c"] }
  }
}

variables {
  tenant              = "corvane"
  admin_role_arns     = ["arn:aws:iam::111122223333:role/platform-admins"]
  bedrock_model_arns  = ["arn:aws:bedrock-mantle:ap-south-1:111122223333:model/anthropic.claude-opus-5-5"]
  budget_alert_emails = ["platform-team@corvane.example"]
}

run "private_by_default" {
  command = plan

  assert {
    condition     = aws_eks_cluster.main.vpc_config[0].endpoint_public_access == false
    error_message = "The Kubernetes API must not be public."
  }
  assert {
    condition     = aws_eks_cluster.main.access_config[0].bootstrap_cluster_creator_admin_permissions == false
    error_message = "Whoever runs Terraform must not become cluster admin implicitly."
  }
  assert {
    condition     = aws_db_instance.main.publicly_accessible == false && aws_db_instance.main.storage_encrypted
    error_message = "The database must be private and encrypted."
  }
  assert {
    condition     = aws_db_instance.main.manage_master_user_password && aws_db_instance.main.deletion_protection
    error_message = "RDS must keep the admin password itself and refuse deletion."
  }
  assert {
    condition     = alltrue([for s in aws_subnet.public : s.map_public_ip_on_launch == false])
    error_message = "Nothing should get a public IP automatically."
  }
  assert {
    condition     = aws_launch_template.nodes.metadata_options[0].http_tokens == "required" && aws_launch_template.nodes.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "Nodes must require IMDSv2 and keep pods away from the node role."
  }
  assert {
    condition     = length(aws_nat_gateway.main) == 1 && length(aws_subnet.private) == 3
    error_message = "Pilot defaults: one NAT gateway, three private subnets."
  }
  assert {
    condition     = aws_eks_pod_identity_association.copilot.namespace == "copilot-corvane" && aws_eks_pod_identity_association.copilot.service_account == "field-copilot"
    error_message = "Pod Identity must match the namespace and service account the chart uses."
  }
}

run "copilot_role_is_least_privilege" {
  command = apply

  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.copilot.policy).Statement :
      !anytrue([for a in s.Action : strcontains(a, "*")])
    ])
    error_message = "The copilot role must not use wildcard actions."
  }
  assert {
    condition = toset(flatten([
      for s in jsondecode(aws_iam_role_policy.copilot.policy).Statement : s.Resource if s.Sid == "CallClaudeInBedrock"
    ])) == toset(var.bedrock_model_arns)
    error_message = "Model calls must be limited to the listed model ARNs."
  }
}

run "zone_redundant_nat" {
  command = plan
  variables {
    single_nat_gateway = false
  }
  assert {
    condition     = length(aws_nat_gateway.main) == 3
    error_message = "With single_nat_gateway off, each zone gets its own NAT gateway."
  }
}
