module "argocd" {
  source    = "../../modules/argocd"
  enabled   = true
  namespace = "argo-cd"

  # Configure ArgoCD with node placement and Crossplane resource exclusions
  settings = {
    # Global node placement - applies to all ArgoCD components
    global = {
      nodeSelector = {
        "node.kubernetes.io/workload" = "infrastructure"
      }
      tolerations = [
        {
          key    = "node-role.kubernetes.io/control-plane"
          effect = "NoSchedule"
        }
      ]
    }

    # Controller node placement
    controller = {
      nodeSelector = {
        "node.kubernetes.io/workload" = "infrastructure"
      }
      tolerations = [
        {
          key    = "node-role.kubernetes.io/control-plane"
          effect = "NoSchedule"
        }
      ]
    }

    # Dex server node placement
    dex = {
      nodeSelector = {
        "node.kubernetes.io/workload" = "infrastructure"
      }
      tolerations = [
        {
          key    = "node-role.kubernetes.io/control-plane"
          effect = "NoSchedule"
        }
      ]
    }

    # Redis node placement
    redis = {
      nodeSelector = {
        "node.kubernetes.io/workload" = "infrastructure"
      }
      tolerations = [
        {
          key    = "node-role.kubernetes.io/control-plane"
          effect = "NoSchedule"
        }
      ]
    }

    # Repo server node placement
    repoServer = {
      nodeSelector = {
        "node.kubernetes.io/workload" = "infrastructure"
      }
      tolerations = [
        {
          key    = "node-role.kubernetes.io/control-plane"
          effect = "NoSchedule"
        }
      ]
    }

    # Server node placement and config
    server = {
      nodeSelector = {
        "node.kubernetes.io/workload" = "infrastructure"
      }
      tolerations = [
        {
          key    = "node-role.kubernetes.io/control-plane"
          effect = "NoSchedule"
        }
      ]
      config = {
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
          # Agent-docs framework: catalog-info.yaml files are co-located in
          # base-apps/<app>/ for AI-agent/Backstage consumption. They are NOT
          # Kubernetes manifests, so Argo CD must ignore the Backstage entity
          # kinds to avoid failing sync on those app directories. Scoped to the
          # kinds the framework actually emits (Component, Resource).
          - apiGroups:
            - backstage.io
            kinds:
            - Component
            - Resource
            clusters:
            - "*"
        EOT
      }
    }
  }
}

# Note: argocd_applicationsets (master-app) is no longer managed by Terraform.
# The master-app ArgoCD Application is managed directly via base-apps/ GitOps.