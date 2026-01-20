# resource "kubectl_manifest" "applications_application_set" {
#     yaml_body = <<YAML
# ---
# apiVersion: argoproj.io/v1alpha1
# kind: ApplicationSet
# metadata:
#   name: applications
#   namespace: argo-cd
# spec:
#   generators:
#     - git:
#         repoURL: https://github.com/arigsela/kubernetes
#         revision: main
#         files:
#           - path: app-discovery/*
#   template:
#     metadata:
#       name: "{{app_path}}-{{overlay}}"
#     spec:
#       project: applications
#       source:
#         repoURL: https://github.com/arigsela/kubernetes
#         targetRevision: main
#         path: applications/{{app_path}}/overlays/{{overlay}}
#         kustomize:
#           images:
#             - "{{image}}:{{tag}}"
#       destination:
#         server: https://kubernetes.default.svc
#         namespace: "{{namespace}}"
#       syncPolicy:
#         automated:
#           prune: true
#           selfHeal: true
#         syncOptions:
#           - CreateNamespace=true
# YAML
# }

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
      targetRevision: fix/cni-recovery

    syncPolicy:
      automated:
        prune: true
        selfHeal: true
  YAML
}