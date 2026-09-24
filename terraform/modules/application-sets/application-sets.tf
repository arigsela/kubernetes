# Wait for ArgoCD CRDs to be fully registered
resource "time_sleep" "wait_for_argocd_crds" {
  create_duration = "30s"
}

resource "kubectl_manifest" "master_app" {
  depends_on = [time_sleep.wait_for_argocd_crds]
  yaml_body  = <<YAML
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

    # Argo CD adds its own pre-/post-delete finalizers to any Application whose chart
    # has PreDelete/PostDelete hooks (kyverno today). They are not in git, so
    # master-app read them as drift and self-healed in a loop (SPEC.md T85).
    # RespectIgnoreDifferences keeps a master-app sync from applying git's finalizer
    # list over them: Applications are CRs, so the list is replaced, not merged.
    ignoreDifferences:
      - group: argoproj.io
        kind: Application
        jqPathExpressions:
          - '.metadata.finalizers[] | select(startswith("pre-delete-finalizer.argocd.argoproj.io") or startswith("post-delete-finalizer.argocd.argoproj.io"))'

    syncPolicy:
      automated:
        prune: true
        selfHeal: true
      syncOptions:
        - RespectIgnoreDifferences=true
  YAML
}
