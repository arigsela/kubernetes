module "argocd" {
  source = "../../modules/argocd"
  enabled = true
  namespace = "argo-cd"
}

module "argocd_applicationsets" {
  source = "../../modules/application-sets"
}