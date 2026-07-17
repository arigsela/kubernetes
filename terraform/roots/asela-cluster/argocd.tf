module "argocd" {
  source    = "../../modules/argocd"
  enabled   = true
  namespace = "argo-cd"

  # Configure ArgoCD with node placement and Crossplane resource exclusions
  settings = {
    # Global node placement - applies to all ArgoCD components
    global = {
      # Run the Argo CD 3.5.0-rc2 release candidate. No argo-helm chart packages
      # 3.5 yet (chart 10.1.4 ships appVersion v3.4.5), so we override the image
      # tag on the latest GA chart. global.image.tag applies to the core Argo CD
      # components (server, repo-server, application-controller,
      # applicationset-controller); dex and redis keep their chart-default images.
      # 3.4->3.5 adds no new CRDs and mTLS is opt-in/off by default, so the 3.4.x
      # chart manifests are compatible with the 3.5-rc2 binaries.
      # Remove this override once chart_version points at a real 3.5 chart.
      image = {
        tag = "v3.5.0-rc2"
      }
      # Chart 10.0.0 flipped global.networkPolicy.create false->true. Pin it back
      # to false to keep this upgrade behavior-preserving (no new NetworkPolicies
      # introduced alongside the RC binary swap). Enabling netpols should be a
      # separate, deliberate change once 3.5-rc is confirmed healthy.
      networkPolicy = {
        create = false
      }
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