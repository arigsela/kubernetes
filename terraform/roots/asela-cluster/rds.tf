# Local variable for RDS MySQL username
# Password comes from random_password resource
locals {
  rds_mysql_username = "mysqladmin"
}

# Security group for RDS MySQL instance
# Allows MySQL port 3306 from any IP (for cluster and external access)
resource "aws_security_group" "rds_mysql_sg" {
  name        = "rds-mysql-asela-cluster-sg"
  description = "Security group for RDS MySQL instance - asela-cluster"

  ingress {
    description = "MySQL access"
    from_port   = 3306
    to_port     = 3306
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "rds-mysql-asela-cluster-sg"
    Environment = "production"
    ManagedBy   = "terraform"
  }
}

# RDS MySQL instance - Minimal cost configuration
# Instance class: db.t4g.micro (ARM-based Graviton2 for lowest cost)
# Storage: 20GB gp3 (minimum for MySQL 8.0)
# Engine: MySQL 8.0 (latest stable version)
# Region: us-east-2 (Ohio) - configured in provider
resource "aws_db_instance" "rds_mysql" {
  # Engine configuration
  engine               = "mysql"
  engine_version       = "8.0.42"  # Latest MySQL 8.0 version
  instance_class       = "db.t4g.micro"  # Cheapest option: ~$0.016/hour = ~$12/month

  # Database identification
  identifier           = "asela-cluster-mysql"
  db_name              = "n8n"  # Initial database to create

  # Storage configuration - minimal for cost optimization
  allocated_storage     = 20  # Minimum for MySQL 8.0
  max_allocated_storage = 100  # Auto-scaling limit
  storage_type          = "gp3"  # Latest generation, better performance
  storage_encrypted     = true  # Encryption at rest for security

  # Credentials
  username = local.rds_mysql_username
  password = random_password.rds_mysql_password.result

  # Parameter group for MySQL 8.0
  parameter_group_name = "default.mysql8.0"

  # Network and security
  publicly_accessible    = true  # Required for external cluster access
  vpc_security_group_ids = [aws_security_group.rds_mysql_sg.id]

  # Backup and maintenance configuration
  backup_retention_period = 7  # Keep backups for 7 days
  backup_window          = "03:00-04:00"  # UTC backup window
  maintenance_window     = "sun:04:00-sun:05:00"  # UTC maintenance window
  skip_final_snapshot    = true  # No final snapshot on deletion (cost savings)

  # Deletion protection disabled for easier management
  deletion_protection = false

  # Performance Insights disabled for cost savings
  enabled_cloudwatch_logs_exports = []  # No CloudWatch logs to save costs

  # Dependencies
  depends_on = [
    aws_secretsmanager_secret_version.rds_mysql_credentials_version,
    aws_security_group.rds_mysql_sg
  ]

  tags = {
    Name        = "asela-cluster-mysql"
    Environment = "production"
    ManagedBy   = "terraform"
    CostCenter  = "database"
  }
}
