module "argocd" {
  source = "../../modules/argocd"
  enabled = true
  namespace = "argo-cd"

  # Configure ArgoCD to ignore Crossplane-generated resources
  settings = {
    # Performance Optimizations
    configs = {
      params = {
        # Cache settings - increase cache expiration for better performance
        "server.default.cache.expiration" = "24h"
        "reposerver.repo.cache.expiration" = "24h"
        "server.repo.server.timeout.seconds" = "120"
      }
    }

    # Resource limits and requests for controller
    controller = {
      resources = {
        limits = {
          cpu    = "1000m"
          memory = "2Gi"
        }
        requests = {
          cpu    = "250m"
          memory = "512Mi"
        }
      }
    }

    # Resource limits and requests for server
    server = {
      resources = {
        limits = {
          cpu    = "500m"
          memory = "1Gi"
        }
        requests = {
          cpu    = "100m"
          memory = "256Mi"
        }
      }

      # Increase health probe timeouts
      livenessProbe = {
        timeoutSeconds = 5
        periodSeconds = 30
      }
      readinessProbe = {
        timeoutSeconds = 5
        periodSeconds = 30
      }

      config = {
        # Exclude Crossplane composite resources from being tracked
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

    # Resource limits and requests for repo-server
    repoServer = {
      resources = {
        limits = {
          cpu    = "500m"
          memory = "1Gi"
        }
        requests = {
          cpu    = "100m"
          memory = "256Mi"
        }
      }

      # Increase health probe timeouts
      livenessProbe = {
        timeoutSeconds = 5
        periodSeconds = 30
      }
      readinessProbe = {
        timeoutSeconds = 5
        periodSeconds = 30
      }
    }

    # Resource limits for Redis
    redis = {
      resources = {
        limits = {
          cpu    = "200m"
          memory = "256Mi"
        }
        requests = {
          cpu    = "100m"
          memory = "128Mi"
        }
      }
    }

    # Resource limits for applicationSet controller
    applicationSet = {
      resources = {
        limits = {
          cpu    = "200m"
          memory = "512Mi"
        }
        requests = {
          cpu    = "100m"
          memory = "128Mi"
        }
      }
    }

    # Resource limits for notifications controller
    notifications = {
      resources = {
        limits = {
          cpu    = "100m"
          memory = "256Mi"
        }
        requests = {
          cpu    = "50m"
          memory = "128Mi"
        }
      }
    }

    # Resource limits for dex
    dex = {
      resources = {
        limits = {
          cpu    = "100m"
          memory = "256Mi"
        }
        requests = {
          cpu    = "50m"
          memory = "128Mi"
        }
      }
    }
  }
}

module "argocd_applicationsets" {
  source = "../../modules/application-sets"
}