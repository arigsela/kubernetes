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
        # NOTE: this "resource.exclusions" is currently INEFFECTIVE. The module
        # passes config under the deprecated Helm `server.config.*` path, but the
        # argo-cd chart reads `configs.cm.*` — so the live argocd-cm uses the
        # chart's own default exclusions, not these. (Migrating to configs.cm
        # would clobber those chart defaults, since the value replaces rather
        # than merges.) The agent-docs framework therefore does NOT rely on a
        # global backstage.io exclusion; each app's Argo CD Application instead
        # carries `spec.source.directory.exclude: catalog-info.yaml` (an in-band
        # guard that Argo CD honors at render time). See the agent-docs contract
        # README for the per-app requirement.
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

# Note: argocd_applicationsets (master-app) is no longer managed by Terraform.
# The master-app ArgoCD Application is managed directly via base-apps/ GitOps.