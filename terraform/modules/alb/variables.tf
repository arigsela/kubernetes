variable "name_prefix" {
  description = "Prefix for resource names (e.g. 'swarm', 'prod')"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID to deploy the ALB into"
  type        = string
}

variable "subnet_ids" {
  description = "List of public subnet IDs for the ALB"
  type        = list(string)
}

variable "security_group_id" {
  description = "Security group ID to attach to the ALB"
  type        = string
}

variable "target_port" {
  description = "Port the target instances listen on"
  type        = number
  default     = 80
}

variable "health_check_path" {
  description = "Path for the ALB health check"
  type        = string
  default     = "/"
}

variable "tags" {
  description = "Additional tags to apply to all resources"
  type        = map(string)
  default     = {}
}
