locals {
  aws_credentials_secret = jsondecode(data.aws_secretsmanager_secret_version.aws_credentials_infra_version_data.secret_string)
}

module "ecr_secrets" {
  source = "../../modules/kube-secrets"
  name = "regcred"
  namespace = "book-project"
  type = "kubernetes.io/basic-auth"
  data = {
    "AWS_SECRET_ACCESS_KEY" = local.aws_credentials_secret["aws_secret_key"]
    "AWS_ACCESS_KEY_ID" = local.aws_credentials_secret["aws_access_id"]
    "AWS_ACCOUNT" = local.aws_credentials_secret["aws_account"]
  }
}