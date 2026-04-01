output "manager_public_ip" {
  value = module.manager.public_ip
}

output "manager_private_ip" {
  value = module.manager.private_ip
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
  value = "ssh -i swarm-key.pem ubuntu@${module.manager.public_ip}"
}

# Uncomment when worker modules are enabled
# output "worker_1_public_ip" {
#   value = module.worker_1.public_ip
# }
#
# output "worker_1_private_ip" {
#   value = module.worker_1.private_ip
# }
#
# output "worker_2_public_ip" {
#   value = module.worker_2.public_ip
# }
#
# output "worker_2_private_ip" {
#   value = module.worker_2.private_ip
# }
