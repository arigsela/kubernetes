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

# Attach manager to ALB target group
resource "aws_lb_target_group_attachment" "manager" {
  target_group_arn = module.alb.target_group_arn
  target_id        = aws_instance.manager.id
  port             = 80
}
