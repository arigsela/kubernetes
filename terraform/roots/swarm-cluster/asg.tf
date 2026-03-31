# --- Launch Template for Workers ---
resource "aws_launch_template" "worker" {
  name_prefix   = "swarm-worker-"
  image_id      = var.worker_ami
  instance_type = var.instance_type
  key_name      = aws_key_pair.swarm.key_name

  vpc_security_group_ids = [aws_security_group.swarm.id]

  user_data = base64encode(<<-EOF
    #!/bin/bash
    yum update -y
    yum install -y docker
    systemctl enable docker
    systemctl start docker
    usermod -aG docker ec2-user
    iptables -C DOCKER-USER -j RETURN 2>/dev/null || iptables -A DOCKER-USER -j RETURN
    docker swarm join --token ${var.swarm_join_token} ${aws_instance.manager.private_ip}:2377
  EOF
  )

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name        = "swarm-worker"
      ManagedBy   = "Terraform"
      Role        = "worker"
      Environment = "production"
    }
  }

  tags = {
    Name      = "swarm-worker-lt"
    ManagedBy = "Terraform"
  }
}

# --- Auto Scaling Group ---
resource "aws_autoscaling_group" "workers" {
  name                = "swarm-workers-asg"
  desired_capacity    = 3
  min_size            = 3
  max_size            = 3
  vpc_zone_identifier = module.networking.public_subnet_ids
  target_group_arns   = [module.alb.target_group_arn]
  health_check_type   = "ELB"

  launch_template {
    id      = aws_launch_template.worker.id
    version = "$Latest"
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 0
    }
  }

  tag {
    key                 = "Name"
    value               = "swarm-worker"
    propagate_at_launch = true
  }
}
