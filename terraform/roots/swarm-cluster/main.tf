# --- Networking ---
module "networking" {
  source = "../../modules/networking"

  name_prefix         = "swarm"
  vpc_cidr            = var.vpc_cidr
  public_subnet_cidrs = var.public_subnet_cidrs

  tags = {
    Environment = "Prod"
    Service     = "Docker-Swarm"
    Purpose     = "Docker-Swarm-Cluster"
  }
}

# --- Load Balancer ---
module "alb" {
  source = "../../modules/alb"

  name_prefix       = "swarm"
  vpc_id            = module.networking.vpc_id
  subnet_ids        = module.networking.public_subnet_ids
  security_group_id = aws_security_group.alb.id
  target_port       = 80
  health_check_path = "/"

  tags = {
    Environment = "Prod"
    Service     = "Docker-Swarm"
  }
}

# Installs Docker and initialises the swarm.
module "manager" {
  source = "../../modules/ec2-instance"

  name          = "swarm-manager"
  ami           = var.manager_ami
  instance_type = var.instance_type
  subnet_id     = module.networking.public_subnet_ids[0]
  key_name      = aws_key_pair.swarm.key_name

  vpc_security_group_ids = [aws_security_group.swarm.id]

  user_data = <<-EOF
    #!/bin/bash
    apt-get update -y
    apt-get install -y docker.io
    systemctl enable docker
    systemctl start docker
    usermod -aG docker ubuntu
    iptables -C DOCKER-USER -j RETURN 2>/dev/null || iptables -A DOCKER-USER -j RETURN
    docker swarm init --advertise-addr $(hostname -I | awk '{print $1}')
    docker swarm join-token worker -q > /home/ubuntu/token
  EOF

  tags = {
    Role        = "manager"
    Environment = "Prod"
    Service     = "Docker-Swarm"
  }
}

# --- Attach manager to ALB target group ---
resource "aws_lb_target_group_attachment" "manager" {
  target_group_arn = module.alb.target_group_arn
  target_id        = module.manager.instance_id
  port             = 80
}

# --- Swarm Workers (one per subnet/AZ) ---
module "worker" {
  source   = "../../modules/ec2-instance"
  for_each = { for i, id in module.networking.public_subnet_ids : "worker-${i + 1}" => id }

  name          = "swarm-${each.key}"
  ami           = var.worker_ami
  instance_type = var.instance_type
  subnet_id     = each.value
  key_name      = aws_key_pair.swarm.key_name

  vpc_security_group_ids = [aws_security_group.swarm.id]

  user_data = <<-EOF
    #!/bin/bash
    yum update -y
    yum install -y docker
    systemctl enable docker
    systemctl start docker
    usermod -aG docker ec2-user
    iptables -C DOCKER-USER -j RETURN 2>/dev/null || iptables -A DOCKER-USER -j RETURN
    docker swarm join --token ${var.swarm_join_token} ${module.manager.private_ip}:2377
  EOF

  tags = {
    Role        = "worker"
    Environment = "Prod"
    Service     = "Docker-Swarm"
  }
}

# --- Attach workers to ALB target group ---
resource "aws_lb_target_group_attachment" "worker" {
  for_each         = module.worker
  target_group_arn = module.alb.target_group_arn
  target_id        = each.value.instance_id
  port             = 80
}
