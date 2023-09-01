module "argocd" {
  source = "../../modules/argocd"
  enabled = true
  namespace = "argo-cd"
}