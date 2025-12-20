# RDS MySQL Instance Outputs
output "rds_mysql_endpoint" {
  description = "RDS MySQL instance endpoint"
  value       = aws_db_instance.rds_mysql.endpoint
}

output "rds_mysql_address" {
  description = "RDS MySQL instance address (hostname only)"
  value       = aws_db_instance.rds_mysql.address
}

output "rds_mysql_port" {
  description = "RDS MySQL instance port"
  value       = aws_db_instance.rds_mysql.port
}

output "rds_mysql_database_name" {
  description = "RDS MySQL database name"
  value       = aws_db_instance.rds_mysql.db_name
}

output "rds_mysql_arn" {
  description = "RDS MySQL instance ARN"
  value       = aws_db_instance.rds_mysql.arn
}

output "rds_mysql_secrets_manager_arn" {
  description = "ARN of the Secrets Manager secret containing RDS credentials"
  value       = aws_secretsmanager_secret.rds_mysql_credentials.arn
}
