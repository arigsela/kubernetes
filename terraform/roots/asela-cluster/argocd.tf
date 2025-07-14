module "argocd" {
  source = "../../modules/argocd"
  enabled = true
  namespace = "argo-cd"
  
  # Configure ArgoCD to ignore Crossplane-generated resources
  settings = {
    server = {
      config = {
        # Exclude Crossplane composite resources from being tracked
        "resource.exclusions" = <<-EOT
          - apiGroups:
            - platform.io
            kinds:
            - XMySQLDatabase
            clusters:
            - "*"
          - apiGroups:
            - mysql.sql.crossplane.io
            kinds:
            - User
            - Database
            - Grant
            clusters:
            - "*"
        EOT
      }
    }
  }
}

module "argocd_applicationsets" {
  source = "../../modules/application-sets"
}