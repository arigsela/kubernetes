module "argocd" {
  source = "../../modules/argocd"
  enabled = true
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
        EOT
      }
    }
  }
}

module "argocd_applicationsets" {
  source = "../../modules/application-sets"

  depends_on = [module.argocd]
}