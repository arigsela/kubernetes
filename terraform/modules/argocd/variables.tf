#### Argo CD Variables ####
variable "enabled" {
  type        = bool
  default     = true
  description = "Variable indicating whether deployment is enabled."
}

variable "helm_services" {
  default = [
    {
      name         = "argo-cd"
      release_name = "argo-cd"
      # Latest GA argo-helm chart (appVersion v3.4.5). No chart packages Argo CD
      # 3.5 yet; the 3.5.0-rc2 binaries are pinned via global.image.tag in the
      # root module (terraform/roots/asela-cluster/argocd.tf). Bump this to the
      # real 3.5 chart once argo-helm publishes it after 3.5 GA (~2026-08-04).
      chart_version = "10.1.4"
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
  type    = any
  default = {}
}
