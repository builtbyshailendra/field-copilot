# Corvane Freight pilot (all identifiers are made up; 111122223333 is AWS's example account).
tenant      = "corvane"
environment = "pilot"
region      = "ap-south-1"

admin_role_arns = ["arn:aws:iam::111122223333:role/corvane-platform-admins"]

# Fill from the AWS bedrock-mantle documentation for ap-south-1 once model access is approved.
bedrock_model_arns = [
  "REPLACE_WITH_ARN_FOR_anthropic.claude-opus-5-5",
  "REPLACE_WITH_ARN_FOR_anthropic.claude-sonnet-5-5",
  "REPLACE_WITH_ARN_FOR_anthropic.claude-haiku-5-5",
]

node_count          = { min = 2, desired = 3, max = 6 }
db_multi_az         = false # pilot; turn on before production
monthly_budget_usd  = 2500
budget_alert_emails = ["platform-team@corvane.example"]
