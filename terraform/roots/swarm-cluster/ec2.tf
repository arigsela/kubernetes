# --- Key Pair ---
resource "tls_private_key" "swarm" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "aws_key_pair" "swarm" {
  key_name   = "swarm-key"
  public_key = tls_private_key.swarm.public_key_openssh

  tags = {
    Name      = "swarm-key"
    ManagedBy = "Terraform"
  }
}

# --- Manager Instance ---
resource "aws_instance" "manager" {
  ami                    = var.manager_ami
  instance_type          = var.instance_type
  subnet_id              = module.networking.public_subnet_ids[0]
  vpc_security_group_ids = [aws_security_group.swarm.id]
  key_name               = aws_key_pair.swarm.key_name

  user_data = <<-EOF
    #!/bin/bash
    apt-get update -y
    apt-get install -y docker.io
    systemctl enable docker
    systemctl start docker
    usermod -aG docker ubuntu
    iptables -C DOCKER-USER -j RETURN 2>/dev/null || iptables -A DOCKER-USER -j RETURN
  EOF

  tags = {
    Name        = "swarm-manager"
    ManagedBy   = "Terraform"
    Role        = "manager"
    Environment = "production"
  }
}
