# --- Key Pair ---
resource "tls_private_key" "swarm" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "aws_key_pair" "swarm" {
  key_name   = "swarm-key"
  public_key = tls_private_key.swarm.public_key_openssh

  tags = {
    Name        = "swarm-key"
    ManagedBy   = "Terraform"
    Environment = "Prod"
    Service     = "Docker-Swarm"
  }
}
