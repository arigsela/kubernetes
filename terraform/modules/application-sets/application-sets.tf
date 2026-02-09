# Wait for ArgoCD CRDs to be fully registered
resource "time_sleep" "wait_for_argocd_crds" {
  create_duration = "30s"
}

resource "kubectl_manifest" "master_app" {
  depends_on = [time_sleep.wait_for_argocd_crds]
  yaml_body = <<YAML
  apiVersion: argoproj.io/v1alpha1
  kind: Application
  metadata:
    name: master-app
    namespace: argo-cd
  spec:
    destination:
      namespace: argo-cd
      server: https://kubernetes.default.svc
    project: default
    source:
      path: base-apps
      repoURL: https://github.com/arigsela/kubernetes
      targetRevision: main

    syncPolicy:
      automated:
        prune: true
        selfHeal: true
  YAML
}
