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

# Create access key for Crossplane
# WARNING: This will be stored in Terraform state - protect your state file!
resource "aws_iam_access_key" "crossplane_admin_key" {
  user = aws_iam_user.crossplane_admin.name
}

# Store credentials in Kubernetes secret for Crossplane to use
resource "kubernetes_secret" "aws_credentials" {
  metadata {
    name      = "aws-secret"
    namespace = "crossplane-system"
  }

  data = {
    creds = <<-EOT
      [default]
      aws_access_key_id = ${aws_iam_access_key.crossplane_admin_key.id}
      aws_secret_access_key = ${aws_iam_access_key.crossplane_admin_key.secret}
    EOT
  }

  type = "Opaque"
}

# Output the access key ID (secret will be in state file)
output "crossplane_admin_access_key_id" {
  description = "Access Key ID for Crossplane admin user"
  value       = aws_iam_access_key.crossplane_admin_key.id
}

output "crossplane_admin_user_arn" {
  description = "ARN of the Crossplane admin IAM user"
  value       = aws_iam_user.crossplane_admin.arn
}

# Note: The secret access key is sensitive and stored in Terraform state
# Access it with: terraform output -raw crossplane_admin_access_key_id
# For the secret: terraform state show aws_iam_access_key.crossplane_admin_key
