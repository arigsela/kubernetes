resource "kubectl_manifest" "applications_application_set" {
    yaml_body = <<YAML
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: applications
  namespace: argo-cd
spec:
  generators:
    - git:
        files:
          - path: app-discovery/*
        repoURL: https://github.com/arigsela/kubernetes
        revision: master
  template:
      name: "{{app_path}}-{{overlay}}"
    spec:
      destination:
        namespace: "{{namespace}}"
        server: https://kubernetes.default.svc
      project: applications
      source:
        kustomize:
          images:
            - "{{image}}:{{tag}}"
        path: applications/{{app_path}}/overlays/{{overlay}}
        repoURL: https://github.com/arigsela/kubernetes
        targetRevision: master
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
        syncOptions:
          - CreateNamespace=true
YAML
}