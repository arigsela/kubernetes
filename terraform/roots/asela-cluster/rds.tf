# After importing the secrets storing into Locals
locals {
  rds_west2_beappdb1_credentials = jsondecode(data.aws_secretsmanager_secret_version.rds_west2_beappdb1_version_data.secret_string)
}

#create a security group for RDS Database Instance
resource "aws_security_group" "rds_west2_beappdb1_sg" {
  name = "rds_west2_beappdb1_sg"
  ingress {
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

#create a RDS Database Instance
resource "aws_db_instance" "rds_west2_beappdb1" {
  engine               = "mysql"
  identifier           = "west2-beappdb1"
  allocated_storage    =  10
  engine_version       = "5.7"
  instance_class       = "db.t2.micro"
  username             = local.rds_west2_beappdb1_credentials["username"]
  password             = local.rds_west2_beappdb1_credentials["password"]
  parameter_group_name = "default.mysql5.7"
  vpc_security_group_ids = ["${aws_security_group.rds_west2_beappdb1_sg.id}"]
  skip_final_snapshot  = true
  publicly_accessible =  true
  depends_on = [ aws_secretsmanager_secret_version.rds_west2_beappdb1_credentials_version ]
}