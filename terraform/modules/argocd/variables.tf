#### Argo CD Variables ####
variable "enabled" {
  type        = bool
  default     = true
  description = "Variable indicating whether deployment is enabled."
}

variable "helm_services" {
  default = [
    {
      name          = "argo-cd"
      release_name  = "argo-cd"
      chart_version = "9.0.5"
      settings      = {}
    }
  ]
}

variable "helm_chart_repo" {
  type        = string
  default     = "https://argoproj.github.io/argo-helm"
  description = "Argo CD repository name."
}

variable "create_namespace" {
  type        = bool
  default     = true
  description = "Whether to create Kubernetes namespace with name defined by `namespace`."
}

variable "namespace" {
  type        = string
  default     = "argo-cd"
  description = "Kubernetes namespace to deploy Argo CD Helm chart."
}

variable "mod_dependency" {
  default     = null
  description = "Dependence variable binds all AWS resources allocated by this module, dependent modules reference this variable."
}

variable "settings" {
  type = any
  default = {}
}
