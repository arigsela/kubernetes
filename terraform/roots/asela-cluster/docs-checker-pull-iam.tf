# docs-checker-pull: the IAM user Substrate's atelet pulls the docs-checker sandbox image as
# (SPEC §T.129, §V.100; design and spike: docs/superpowers/specs/2026-10-18-substrate-ecr-pulls.md).
#
# Why a user with static keys: atelet pulls actor images itself, so the cluster's ecr-registry pull
# Secret (ecr-credentials-sync) never reaches a sandbox. atelet instead runs ecr-credential-provider
# from a host directory on each worker. The workers are not EC2, so there is no instance profile, and
# atelet refuses the provider's service-account mode (actors have no Kubernetes service account). That
# is the same trust model as ecr-credentials-sync (static keys from Vault), but narrower: this user can
# read one repository, while ecr-auth's token pulls them all.
#
# No aws_iam_access_key here, on purpose: a key made by Terraform would sit in the state bucket.
# scripts/provision-docs-checker-pull.sh creates and rotates it with the AWS CLI, stores it in Vault
# (k8s-secrets/docs-checker-pull) and places it on the workers with Ansible (the crossplane-admin
# precedent in iam.tf).
#
# Path /system/: Atlantis may only manage IAM users under it (atlantis-iam.tf).
#
# Order: Atlantis applies this before the PR merges; then the provisioning script runs; then merge (the
# substrate.yaml change needs the files on the workers).

resource "aws_iam_user" "docs_checker_pull" {
  name = "docs-checker-pull"
  path = "/system/"

  # Tag values must match IAM's tagValueType ([\p{L}\p{Z}\p{N}_.:/=+\-@]*): no apostrophes or commas,
  # or CreateUser fails at apply (tests/substrate checks it; validate and tflint do not).
  tags = {
    Name        = "docs-checker-pull"
    Purpose     = "Substrate-ECR-Pull"
    ManagedBy   = "Terraform"
    Description = "Pull-only key for the docs-checker image used by the Substrate atelet on the k3s workers"
  }
}

resource "aws_iam_user_policy" "docs_checker_pull" {
  name = "docs-checker-pull"
  user = aws_iam_user.docs_checker_pull.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # GetAuthorizationToken has no resource-level permissions (AWS requires "*"). The token it
        # returns can only do what the statement below allows.
        Sid      = "EcrLogin"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid      = "PullDocsChecker"
        Effect   = "Allow"
        Action   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = aws_ecr_repository.this["docs-checker"].arn
      }
    ]
  })
}

output "docs_checker_pull_user_arn" {
  description = "ARN of the docs-checker-pull IAM user (its access key lives only in Vault, k8s-secrets/docs-checker-pull)"
  value       = aws_iam_user.docs_checker_pull.arn
}
