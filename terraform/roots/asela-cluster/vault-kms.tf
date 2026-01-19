# Vault Auto-Unseal KMS Configuration
# This KMS key allows Vault to automatically unseal after restarts
# without requiring manual intervention with Shamir keys.

# -----------------------------------------------------------------------------
# KMS Key for Vault Auto-Unseal
# -----------------------------------------------------------------------------

resource "aws_kms_key" "vault_auto_unseal" {
  description              = "Vault Auto Unseal Key"
  key_usage                = "ENCRYPT_DECRYPT"
  customer_master_key_spec = "SYMMETRIC_DEFAULT"
  is_enabled               = true
  enable_key_rotation      = true
  deletion_window_in_days  = 30

  tags = {
    Name        = "vault-auto-unseal"
    Purpose     = "Vault-Auto-Unseal"
    ManagedBy   = "Terraform"
    Environment = "production"
  }
}

resource "aws_kms_alias" "vault_auto_unseal" {
  name          = "alias/vault-auto-unseal"
  target_key_id = aws_kms_key.vault_auto_unseal.key_id
}

# -----------------------------------------------------------------------------
# IAM User for Vault KMS Access
# -----------------------------------------------------------------------------

resource "aws_iam_user" "vault_kms" {
  name = "vault-kms-user"
  path = "/system/"

  tags = {
    Name        = "vault-kms-user"
    Purpose     = "Vault-Auto-Unseal"
    ManagedBy   = "Terraform"
    Description = "IAM user for Vault to access KMS for auto-unseal"
  }
}

# Minimal IAM policy - only the permissions Vault needs
resource "aws_iam_user_policy" "vault_kms" {
  name = "vault-kms-unseal-policy"
  user = aws_iam_user.vault_kms.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "VaultKMSUnseal"
        Effect = "Allow"
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = aws_kms_key.vault_auto_unseal.arn
      }
    ]
  })
}

# Access key for Vault to authenticate to AWS
resource "aws_iam_access_key" "vault_kms" {
  user = aws_iam_user.vault_kms.name
}

# -----------------------------------------------------------------------------
# Kubernetes Secret for Vault to Use
# -----------------------------------------------------------------------------

resource "kubernetes_secret" "vault_kms_credentials" {
  metadata {
    name      = "vault-kms-credentials"
    namespace = "vault"
  }

  data = {
    "aws-access-key-id"     = aws_iam_access_key.vault_kms.id
    "aws-secret-access-key" = aws_iam_access_key.vault_kms.secret
    "aws-region"            = "us-east-2"
    "kms-key-id"            = aws_kms_key.vault_auto_unseal.key_id
  }

  type = "Opaque"
}

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------

output "vault_kms_key_id" {
  description = "KMS Key ID for Vault auto-unseal"
  value       = aws_kms_key.vault_auto_unseal.key_id
}

output "vault_kms_key_arn" {
  description = "KMS Key ARN for Vault auto-unseal"
  value       = aws_kms_key.vault_auto_unseal.arn
}

output "vault_kms_user_arn" {
  description = "ARN of the Vault KMS IAM user"
  value       = aws_iam_user.vault_kms.arn
}

output "vault_kms_access_key_id" {
  description = "Access Key ID for Vault KMS user"
  value       = aws_iam_access_key.vault_kms.id
}
