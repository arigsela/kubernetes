resource "kubectl_manifest" "applications_application_set" {
    yaml_body = <<YAML
---
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: applications
  namespace: argo-cd
spec:
  generators:
    - git:
        repoURL: https://github.com/arigsela/kubernetes
        revision: main
        files:
          - path: app-discovery/*
  template:
    metadata:
      name: "{{app_path}}-{{overlay}}"
    spec:
      project: applications
      source:
        repoURL: https://github.com/arigsela/kubernetes
        targetRevision: main
        path: applications/{{app_path}}/overlays/{{overlay}}
        kustomize:
          images:
            - "{{image}}:{{tag}}"
      destination:
        server: https://kubernetes.default.svc
        namespace: "{{namespace}}"
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
        syncOptions:
          - CreateNamespace=true
YAML
}