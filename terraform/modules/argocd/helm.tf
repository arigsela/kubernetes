resource "helm_release" "argocd" {
  depends_on = [var.mod_dependency, kubernetes_namespace.argocd]
  count      = var.enabled ? length(var.helm_services) : 0
  name       = var.helm_services[count.index].name
  chart      = var.helm_services[count.index].release_name
  repository = var.helm_chart_repo
  version    = var.helm_services[count.index].chart_version
  namespace  = var.namespace

  set {
    name  = "installCRDs"
    value = true
  }

  set {
      name = "server.service.type"
      value = "ClusterIP"
  }

  set {
    name  = "server.config.exec\\.enabled"
    value = "true"
    type  = "string"
  }


  // Add this block to enable insecure mode
  set {
    name  = "configs.params.server\\.insecure"
    value = "true"
    type  = "string"
  }
  
  values = [
    yamlencode(var.helm_services[count.index].settings),
    yamlencode(var.settings)
  ]
}