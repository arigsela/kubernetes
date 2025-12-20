# Generate random password for RDS MySQL instance
resource "random_password" "rds_mysql_password" {
  length           = 16
  special          = true
  override_special = "!#$%&*()-_=+[]{}:?"
}

# Create AWS Secrets Manager secret for RDS MySQL credentials
resource "aws_secretsmanager_secret" "rds_mysql_credentials" {
  name = "rds-mysql-asela-cluster-credentials"
  description = "RDS MySQL credentials for asela-cluster"
}

resource "aws_secretsmanager_secret" "aws_credentials_infra" {
    name = "aws-credentials-infra"
}

# Store RDS MySQL credentials in AWS Secrets Manager
# Note: Host endpoint will need to be updated after RDS creation
resource "aws_secretsmanager_secret_version" "rds_mysql_credentials_version" {
  secret_id = aws_secretsmanager_secret.rds_mysql_credentials.id
  secret_string = jsonencode({
    username = "mysqladmin"
    password = random_password.rds_mysql_password.result
    engine   = "mysql"
    port     = 3306
    dbname   = "n8n"
  })
  depends_on = [random_password.rds_mysql_password]
}

# Data source to reference the created secret
data "aws_secretsmanager_secret" "rds_mysql_data" {
  arn = aws_secretsmanager_secret.rds_mysql_credentials.arn
}

data "aws_secretsmanager_secret" "aws_credentials_infra_data" {
  arn = aws_secretsmanager_secret.aws_credentials_infra.arn
}

# Data source to reference the secret version
data "aws_secretsmanager_secret_version" "rds_mysql_version_data" {
  secret_id = data.aws_secretsmanager_secret.rds_mysql_data.name
}
 
 data "aws_secretsmanager_secret_version" "aws_credentials_infra_version_data" {
   secret_id = data.aws_secretsmanager_secret.aws_credentials_infra_data.name
 }
