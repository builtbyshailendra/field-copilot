output "cluster_name" {
  description = "EKS cluster name (aws eks update-kubeconfig --name ...)."
  value       = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  description = "Private Kubernetes API endpoint, reachable only from inside the VPC."
  value       = aws_eks_cluster.main.endpoint
}

output "app_namespace" {
  description = "Namespace the Pod Identity association expects the chart in."
  value       = local.namespace
}

output "copilot_role_arn" {
  description = "AWS role the API and worker pods receive."
  value       = aws_iam_role.copilot.arn
}

output "db_endpoint" {
  description = "RDS endpoint (host:port) for the DSNs in the app secret."
  value       = aws_db_instance.main.endpoint
}

output "db_admin_secret_arn" {
  description = "Secrets Manager secret RDS keeps the admin password in (used by the migration job only)."
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

output "app_secret_arn" {
  description = "Secret holding the app's settings, synced into the cluster by External Secrets."
  value       = aws_secretsmanager_secret.app.arn
}

output "vpc_id" {
  value = aws_vpc.main.id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}
