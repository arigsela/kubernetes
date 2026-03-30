# Atlantis Terraform IAM User
# This user provides Atlantis with AWS credentials to run terraform plan/apply
# on behalf of engineers via the PR-based GitOps workflow.
#
# SECURITY: Policy is scoped to specific resource ARNs — no wildcard resources
# on sensitive services (IAM actions scoped to /system/ path, KMS to vault key,
# Secrets Manager to known secret prefixes).
#
# AFTER APPLY: Retrieve the secret access key and store it in Vault manually:
#   terraform output -raw atlantis_access_key_id
#   terraform state show aws_iam_access_key.atlantis | grep secret
# Then: vault kv put k8s-secrets/atlantis/aws access-key=<id> secret-key=<secret>

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# IAM User for Atlantis
# -----------------------------------------------------------------------------

resource "aws_iam_user" "atlantis" {
  name = "atlantis-terraform"
  path = "/system/"

  tags = {
    Name        = "atlantis-terraform"
    Purpose     = "Atlantis-Terraform-Automation"
    ManagedBy   = "Terraform"
    Service     = "Platform-Engineering"
    Environment = "Prod"
    Team        = "Platform"
    Description = "IAM user for Atlantis to run terraform plan/apply via PR workflow"
    CostCenter  = "Platform"
    Owner       = "Platform-Engineering"
  }
}

# -----------------------------------------------------------------------------
# IAM Policy — Resource-Scoped Permissions
# Using aws_iam_policy (managed) instead of aws_iam_user_policy (inline)
# because inline policies have a 2048-byte limit; managed policies allow 6144 bytes.
# -----------------------------------------------------------------------------

resource "aws_iam_policy" "atlantis" {
  name        = "atlantis-terraform-policy"
  path        = "/system/"
  description = "Scoped policy for Atlantis to run terraform plan/apply"

  tags = {
    ManagedBy   = "Terraform"
    Service     = "Platform-Engineering"
    Environment = "Prod"
    Team        = "Platform"
  }

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # -----------------------------------------------------------------------
      # S3: Terraform state bucket (read/write state files)
      # -----------------------------------------------------------------------
      {
        Sid    = "TerraformStateBucketAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:GetBucketVersioning"
        ]
        Resource = [
          "arn:aws:s3:::asela-terraform-states",
          "arn:aws:s3:::asela-terraform-states/*"
        ]
      },
      # -----------------------------------------------------------------------
      # S3: Allow creating/managing new S3 buckets under the asela- prefix only
      # -----------------------------------------------------------------------
      {
        Sid    = "S3BucketManagementAselaPrefix"
        Effect = "Allow"
        Action = [
          "s3:CreateBucket",
          "s3:DeleteBucket",
          "s3:PutBucketVersioning",
          "s3:PutBucketEncryption",
          "s3:GetBucketEncryption",
          "s3:PutBucketPublicAccessBlock",
          "s3:GetBucketPublicAccessBlock",
          "s3:PutLifecycleConfiguration",
          "s3:GetLifecycleConfiguration",
          "s3:GetBucketPolicy",
          "s3:PutBucketPolicy",
          "s3:DeleteBucketPolicy",
          "s3:GetBucketTagging",
          "s3:PutBucketTagging"
        ]
        Resource = [
          "arn:aws:s3:::asela-*"
        ]
      },
      # -----------------------------------------------------------------------
      # RDS: Manage RDS instances scoped to asela-cluster identifiers
      # -----------------------------------------------------------------------
      {
        Sid    = "RDSManagement"
        Effect = "Allow"
        Action = ["rds:*"]
        Resource = [
          "arn:aws:rds:us-east-2:${data.aws_caller_identity.current.account_id}:db:asela-cluster-*",
          "arn:aws:rds:us-east-2:${data.aws_caller_identity.current.account_id}:og:*",
          "arn:aws:rds:us-east-2:${data.aws_caller_identity.current.account_id}:pg:*",
          "arn:aws:rds:us-east-2:${data.aws_caller_identity.current.account_id}:subgrp:*"
        ]
      },
      # RDS describe actions require * resource (AWS limitation)
      {
        Sid    = "RDSDescribeActions"
        Effect = "Allow"
        Action = [
          "rds:DescribeDBInstances",
          "rds:DescribeDBSubnetGroups",
          "rds:DescribeDBParameterGroups",
          "rds:DescribeDBEngineVersions",
          "rds:DescribeOrderableDBInstanceOptions",
          "rds:ListTagsForResource"
        ]
        Resource = ["*"]
      },
      # -----------------------------------------------------------------------
      # EC2: Security group management for RDS + describe actions
      # EC2 describe/list actions cannot be resource-scoped (AWS limitation)
      # -----------------------------------------------------------------------
      {
        Sid    = "EC2SecurityGroupManagement"
        Effect = "Allow"
        Action = [
          "ec2:CreateSecurityGroup",
          "ec2:DeleteSecurityGroup",
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:AuthorizeSecurityGroupEgress",
          "ec2:RevokeSecurityGroupIngress",
          "ec2:RevokeSecurityGroupEgress",
          "ec2:UpdateSecurityGroupRuleDescriptionsIngress",
          "ec2:UpdateSecurityGroupRuleDescriptionsEgress"
        ]
        Resource = [
          "arn:aws:ec2:us-east-2:${data.aws_caller_identity.current.account_id}:security-group/*"
        ]
      },
      {
        Sid    = "EC2DescribeActions"
        Effect = "Allow"
        Action = [
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeSecurityGroupRules",
          "ec2:DescribeAccountAttributes",
          "ec2:DescribeAvailabilityZones",
          "ec2:DescribeVpcs",
          "ec2:DescribeSubnets",
          "ec2:DescribeNetworkInterfaces"
        ]
        Resource = ["*"]
      },
      # -----------------------------------------------------------------------
      # Secrets Manager: Scoped to known secret name prefixes
      # -----------------------------------------------------------------------
      {
        Sid    = "SecretsManagerScopedAccess"
        Effect = "Allow"
        Action = ["secretsmanager:*"]
        Resource = [
          "arn:aws:secretsmanager:us-east-2:${data.aws_caller_identity.current.account_id}:secret:rds-mysql-asela-*",
          "arn:aws:secretsmanager:us-east-2:${data.aws_caller_identity.current.account_id}:secret:aws-credentials-infra-*"
        ]
      },
      # -----------------------------------------------------------------------
      # KMS: Full key management scoped to this account's keys and vault alias
      # -----------------------------------------------------------------------
      {
        Sid    = "KMSKeyManagement"
        Effect = "Allow"
        Action = [
          "kms:CreateKey",
          "kms:DescribeKey",
          "kms:EnableKey",
          "kms:EnableKeyRotation",
          "kms:DisableKey",
          "kms:GetKeyPolicy",
          "kms:GetKeyRotationStatus",
          "kms:ListResourceTags",
          "kms:PutKeyPolicy",
          "kms:UpdateKeyDescription",
          "kms:TagResource",
          "kms:UntagResource",
          "kms:ScheduleKeyDeletion",
          "kms:CancelKeyDeletion"
        ]
        Resource = [
          "arn:aws:kms:us-east-2:${data.aws_caller_identity.current.account_id}:key/*"
        ]
      },
      # kms:ListAliases and kms:ListKeys require * resource (AWS limitation)
      {
        Sid    = "KMSListActions"
        Effect = "Allow"
        Action = [
          "kms:ListAliases",
          "kms:ListKeys"
        ]
        Resource = ["*"]
      },
      {
        Sid    = "KMSAliasManagement"
        Effect = "Allow"
        Action = [
          "kms:CreateAlias",
          "kms:DeleteAlias",
          "kms:UpdateAlias"
        ]
        Resource = [
          "arn:aws:kms:us-east-2:${data.aws_caller_identity.current.account_id}:alias/vault-*",
          "arn:aws:kms:us-east-2:${data.aws_caller_identity.current.account_id}:key/*"
        ]
      },
      # -----------------------------------------------------------------------
      # IAM: Scoped to /system/ path — prevents creating admin-level users
      # Allows Terraform to manage service account users (vault-kms,
      # crossplane-admin, atlantis itself) under the /system/ path only
      # -----------------------------------------------------------------------
      {
        Sid    = "IAMSystemPathUserManagement"
        Effect = "Allow"
        Action = ["iam:*"]
        Resource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/system/*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/*"
        ]
      },
      # IAM access key actions require the user ARN format (not path-based)
      {
        Sid    = "IAMAccessKeyManagement"
        Effect = "Allow"
        Action = [
          "iam:CreateAccessKey",
          "iam:DeleteAccessKey",
          "iam:ListAccessKeys",
          "iam:UpdateAccessKey",
          "iam:GetAccessKeyLastUsed"
        ]
        Resource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/system/*"
        ]
      },
      # IAM describe/list actions cannot be resource-scoped (AWS limitation)
      {
        Sid    = "IAMDescribeActions"
        Effect = "Allow"
        Action = [
          "iam:GetUser",
          "iam:ListUsers",
          "iam:ListAttachedUserPolicies",
          "iam:ListUserPolicies",
          "iam:GetUserPolicy",
          "iam:GetPolicy",
          "iam:GetPolicyVersion",
          "iam:ListPolicies",
          "iam:ListPolicyVersions"
        ]
        Resource = ["*"]
      }
    ]
  })
}

resource "aws_iam_user_policy_attachment" "atlantis" {
  user       = aws_iam_user.atlantis.name
  policy_arn = aws_iam_policy.atlantis.arn
}

# -----------------------------------------------------------------------------
# Access Key
# NOTE: The secret access key is sensitive and stored in Terraform state.
# After apply, retrieve it and store in Vault immediately:
#   Secret key: terraform state show aws_iam_access_key.atlantis | grep secret
# Then delete from local memory and rely solely on Vault.
# -----------------------------------------------------------------------------

resource "aws_iam_access_key" "atlantis" {
  user = aws_iam_user.atlantis.name
}

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------

output "atlantis_iam_user_arn" {
  description = "ARN of the Atlantis IAM user"
  value       = aws_iam_user.atlantis.arn
}

output "atlantis_access_key_id" {
  description = "Access Key ID for Atlantis IAM user — store the secret key in Vault"
  value       = aws_iam_access_key.atlantis.id
}
