# --- EC2 / Swarm Security Group ---
resource "aws_security_group" "swarm" {
  name        = "swarm-sg"
  description = "Docker Swarm cluster security group"
  vpc_id      = module.networking.vpc_id

  # SSH
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.my_ip]
  }

  # HTTP (ALB traffic)
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Swarm cluster management
  ingress {
    from_port = 2377
    to_port   = 2377
    protocol  = "tcp"
    self      = true
  }

  # Swarm node communication (TCP)
  ingress {
    from_port = 7946
    to_port   = 7946
    protocol  = "tcp"
    self      = true
  }

  # Swarm node communication (UDP)
  ingress {
    from_port = 7946
    to_port   = 7946
    protocol  = "udp"
    self      = true
  }

  # VXLAN overlay network
  ingress {
    from_port = 4789
    to_port   = 4789
    protocol  = "udp"
    self      = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "swarm-sg"
    ManagedBy   = "Terraform"
    Environment = "Prod"
    Service     = "Docker-Swarm"
  }
}

# --- ALB Security Group ---
resource "aws_security_group" "alb" {
  name        = "swarm-alb-sg"
  description = "ALB security group"
  vpc_id      = module.networking.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "swarm-alb-sg"
    ManagedBy   = "Terraform"
    Environment = "Prod"
    Service     = "Docker-Swarm"
  }
}
