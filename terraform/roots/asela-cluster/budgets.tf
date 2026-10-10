# Budgets: the account's monthly cost budget, imported 2026-10-10.
#
# Normal spend is about $10 a month (ECR, S3, Route 53, KMS, Secrets Manager).
# Two domains renew through Route 53 Domains once a year for $16 each, so
# August (drkittymeowmeow.com) and October (arigsela.com) come to about $26.
# monthly-budget was created by hand in 2022 with a $10 limit and one email at
# 50% of actual spend, so it fired around the middle of every month. It now
# emails only when a month passes $15, or is forecast to: the renewal months,
# or a real change in spend.
#
# Budgets is a global service; the provider's region does not matter here.
# Atlantis can manage it since the BudgetsManagement statement in
# atlantis-iam.tf.

locals {
  budget_alert_emails = ["arigsela+trainingawsgeneral@gmail.com"]
}

resource "aws_budgets_budget" "monthly" {
  name         = "monthly-budget"
  budget_type  = "COST"
  limit_amount = "15.0"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # What the budget already had. Credits and refunds are left out, so it
  # measures what was actually spent (the provider's defaults include them).
  cost_types {
    include_credit = false
    include_refund = false
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = local.budget_alert_emails
  }

  # Fires early in a renewal month, when the $16 charge lands.
  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "FORECASTED"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = local.budget_alert_emails
  }
}

# One-time import of the hand-made budget. A no-op once it is in state; remove
# it in a later PR, as ecr.tf did.
import {
  to = aws_budgets_budget.monthly
  id = "852893458518:monthly-budget"
}
