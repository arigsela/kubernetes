variable "aws_region" {
  description = "AWS region to deploy resources into"
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.12.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "List of CIDR blocks for public subnets (one per AZ)"
  type        = list(string)
  default     = ["10.12.1.0/24", "10.12.2.0/24", "10.12.3.0/24"]
}

variable "instance_type" {
  description = "EC2 instance type for manager and worker nodes"
  type        = string
  default     = "t3.micro"
}

variable "manager_ami" {
  description = "Ubuntu 24.04 AMI ID"
  type        = string
  default     = "ami-0ec10929233384c7f"
}

variable "worker_ami" {
  description = "Amazon Linux 2023 AMI ID"
  type        = string
  default     = "ami-08b09ec6240624138"
}

variable "swarm_join_token" {
  description = "Docker Swarm worker join token (obtained from 'docker swarm join-token worker -q' on the manager)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "domain_name" {
  description = "Route53 hosted zone domain"
  type        = string
  default     = "arigsela.com"
}

variable "hostname" {
  description = "Subdomain for the swarm service"
  type        = string
  default     = "swarm"
}

variable "my_ip" {
  description = "Your IP for SSH access (CIDR format, e.g. 1.2.3.4/32)"
  type        = string
  default     = "0.0.0.0/32"

  validation {
    condition     = can(cidrnetmask(var.my_ip))
    error_message = "my_ip must be a valid CIDR block (e.g. 1.2.3.4/32)."
  }
}
