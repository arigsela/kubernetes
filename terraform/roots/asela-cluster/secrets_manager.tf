resource "random_password" "rds_west2_beappdb1_password" {
  length           = 16
  special          = true
  override_special = "_%"
}

resource "aws_secretsmanager_secret" "rds_west2_beappdb1_credentials" {
    name = "rds-us-west2-beappdb1-credentials"
}

resource "aws_secretsmanager_secret" "aws_credentials_infra" {
    name = "aws-credentials-infra"
}

resource "aws_secretsmanager_secret_version" "rds_west2_beappdb1_credentials_version" {
    secret_id = aws_secretsmanager_secret.rds_west2_beappdb1_credentials.id
    secret_string = <<EOF
    {
        "username": "beappadmin",
        "password": "${random_password.rds_west2_beappdb1_password.result}"
    }
EOF
    depends_on = [ random_password.rds_west2_beappdb1_password ]
}

# Importing the AWS secrets created previously using arn.
 
data "aws_secretsmanager_secret" "rds_west2_beappdb1_data" {
  arn = aws_secretsmanager_secret.rds_west2_beappdb1_credentials.arn
}

data "aws_secretsmanager_secret" "aws_credentials_infra_data" {
  arn = aws_secretsmanager_secret.aws_credentials_infra.arn
}

# Importing the AWS secret version created previously using arn.
 
data "aws_secretsmanager_secret_version" "rds_west2_beappdb1_version_data" {
  secret_id = data.aws_secretsmanager_secret.rds_west2_beappdb1_data.name
}
 
data "aws_secretsmanager_secret_version" "aws_credentials_infra_version_data" {
  secret_id = data.aws_secretsmanager_secret.aws_credentials_infra_data.name
}
