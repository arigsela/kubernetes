# Crossplane Admin IAM User
# This user allows Crossplane to manage AWS resources (S3, IAM) via GitOps
# Used in: Loki observability stack implementation

resource "aws_iam_user" "crossplane_admin" {
  name = "crossplane-admin"
  path = "/system/"

  tags = {
    Purpose     = "Crossplane-AWS-Provider"
    ManagedBy   = "Terraform"
    Service     = "Observability"
    Description = "Admin user for Crossplane to manage AWS resources declaratively"
  }
}

# IAM policy for Crossplane with permissions to manage S3 and IAM
resource "aws_iam_user_policy" "crossplane_admin_policy" {
  name = "crossplane-admin-policy"
  user = aws_iam_user.crossplane_admin.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3FullAccess"
        Effect = "Allow"
        Action = [
          "s3:*"
        ]
        Resource = "*"
      },
      {
        Sid    = "IAMFullAccess"
        Effect = "Allow"
        Action = [
          "iam:*"
        ]
        Resource = "*"
      }
    ]
  })
}

# Note: Access key and Kubernetes secret for Crossplane are no longer managed by Terraform.
# Credentials are managed externally and stored in Vault.

output "crossplane_admin_user_arn" {
  description = "ARN of the Crossplane admin IAM user"
  value       = aws_iam_user.crossplane_admin.arn
}

