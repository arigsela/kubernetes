# Velero Backup Infrastructure
# This configuration creates the AWS resources needed for Velero to backup
# Kubernetes cluster resources and PVC data to S3.

# -----------------------------------------------------------------------------
# S3 Bucket for Velero Backups
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "velero_backups" {
  bucket = "asela-velero-backups"

  tags = {
    Name        = "asela-velero-backups"
    Purpose     = "Velero-Kubernetes-Backups"
    ManagedBy   = "Terraform"
    Environment = "production"
  }
}

resource "aws_s3_bucket_versioning" "velero_backups" {
  bucket = aws_s3_bucket.velero_backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "velero_backups" {
  bucket = aws_s3_bucket.velero_backups.id

  rule {
    id     = "expire-old-backups"
    status = "Enabled"

    filter {
      prefix = "" # Apply to all objects
    }

    expiration {
      days = 7
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "velero_backups" {
  bucket = aws_s3_bucket.velero_backups.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "velero_backups" {
  bucket = aws_s3_bucket.velero_backups.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# -----------------------------------------------------------------------------
# IAM User for Velero S3 Access
# -----------------------------------------------------------------------------

resource "aws_iam_user" "velero" {
  name = "velero-backup-user"
  path = "/system/"

  tags = {
    Name        = "velero-backup-user"
    Purpose     = "Velero-Kubernetes-Backups"
    ManagedBy   = "Terraform"
    Description = "IAM user for Velero to access S3 for cluster backups"
  }
}

# Minimal IAM policy - only the permissions Velero needs for S3
resource "aws_iam_user_policy" "velero" {
  name = "velero-s3-backup-policy"
  user = aws_iam_user.velero.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "VeleroS3Access"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = [
          aws_s3_bucket.velero_backups.arn,
          "${aws_s3_bucket.velero_backups.arn}/*"
        ]
      }
    ]
  })
}

# Access key for Velero to authenticate to AWS
resource "aws_iam_access_key" "velero" {
  user = aws_iam_user.velero.name
}

# -----------------------------------------------------------------------------
# Kubernetes Resources for Velero
# -----------------------------------------------------------------------------

resource "kubernetes_namespace" "velero" {
  metadata {
    name = "velero"
    labels = {
      "app.kubernetes.io/name"       = "velero"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

resource "kubernetes_secret" "velero_credentials" {
  metadata {
    name      = "velero-aws-credentials"
    namespace = kubernetes_namespace.velero.metadata[0].name
  }

  data = {
    cloud = <<-EOF
      [default]
      aws_access_key_id=${aws_iam_access_key.velero.id}
      aws_secret_access_key=${aws_iam_access_key.velero.secret}
    EOF
  }

  type = "Opaque"
}

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------

output "velero_s3_bucket" {
  description = "S3 bucket for Velero backups"
  value       = aws_s3_bucket.velero_backups.id
}

output "velero_s3_bucket_arn" {
  description = "ARN of S3 bucket for Velero backups"
  value       = aws_s3_bucket.velero_backups.arn
}

output "velero_iam_user_arn" {
  description = "ARN of the Velero IAM user"
  value       = aws_iam_user.velero.arn
}

output "velero_access_key_id" {
  description = "Access Key ID for Velero IAM user"
  value       = aws_iam_access_key.velero.id
}
