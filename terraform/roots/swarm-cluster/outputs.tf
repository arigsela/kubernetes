output "manager_public_ip" {
  value = aws_instance.manager.public_ip
}

output "manager_private_ip" {
  value = aws_instance.manager.private_ip
}

output "alb_dns_name" {
  value = module.alb.alb_dns_name
}

output "swarm_url" {
  value = "http://${var.hostname}.${var.domain_name}"
}

output "ssh_private_key" {
  value     = tls_private_key.swarm.private_key_pem
  sensitive = true
}

output "ssh_command" {
  value = "ssh -i swarm-key.pem ubuntu@${aws_instance.manager.public_ip}"
}
