resource "kubernetes_namespace" "argocd" {
  depends_on = [var.mod_dependency]
  count      = (var.enabled && var.create_namespace && var.namespace != "kube-system") ? 1 : 0

  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/managed-by" = "Helm"
    }
    annotations = {
      "meta.helm.sh/release-name"      = "argo-cd"
      "meta.helm.sh/release-namespace" = "argo-cd"
    }
  }
}
