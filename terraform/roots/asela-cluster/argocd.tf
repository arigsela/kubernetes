module "argocd" {
  source    = "../../modules/argocd"
  enabled   = true
  namespace = "argo-cd"

  # Configure ArgoCD with node placement and Crossplane resource exclusions
  settings = {
    # Global node placement - applies to all ArgoCD components
    global = {
      # Argo CD's own public URL. The chart derives configs.cm `url` from this
      # (the argo-cd.config.cm.presets helper does
      # `url = printf "https://%s" .Values.global.domain`), and until now it was
      # left at the chart's placeholder default, so the live argocd-cm carried
      # url: https://argocd.example.com.
      #
      # That was inert while auth was local-admin-only, but OIDC builds
      # redirect_uri from `url`. A wrong value here does not fail loudly - Dex
      # rejects the callback as an unregistered redirect URI and the user sees a
      # generic login error, so set it before wiring OIDC, not after.
      #
      # Set via global.domain rather than overriding configs.cm.url directly so
      # the chart's other domain-derived values stay consistent with it.
      domain = "argocd.arigsela.com"
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
      # Creates the argocd-application-controller-metrics Service, which exposes
      # argocd_app_info on :8082. Without this the metric does not exist at all —
      # the chart only renders that Service when controller.metrics.enabled.
      #
      # The scrape annotations are picked up by the EXISTING Prometheus
      # `kubernetes-service-endpoints` job (base-apps/logging/prometheus-config.yaml),
      # which keeps any Service annotated prometheus.io/scrape=true and honors
      # prometheus.io/port. So no Prometheus config change is needed here.
      #
      # Set in this `settings` map rather than as a `set` block in
      # modules/argocd/helm.tf: helm's set syntax requires escaping the dots in
      # annotation keys (cf. the `server.config.exec\\.enabled` line there).
      #
      # Consumed by the Argo CD tile on home.arigsela.com, which reads Prometheus
      # instead of the Argo CD API to avoid needing an apiKey account in argocd-cm.
      metrics = {
        enabled = true
        service = {
          annotations = {
            "prometheus.io/scrape" = "true"
            "prometheus.io/port"   = "8082"
          }
        }
      }
    }

    # Bundled Dex, DISABLED 2026-08-12. Argo CD now authenticates against the
    # standalone Dex in base-apps/dex (the one that already fronts GitHub for
    # Vault), so the chart's own Dex has nothing left to do - it had in fact
    # never had anything to do, since no dex.config was ever set. It ran unused
    # for 25+ days.
    #
    # Turning it off is not just tidiness: while dex.enabled is true the chart
    # sets the server.dex.server param, pointing Argo CD's /api/dex routes at
    # the bundled instance. Leaving both a configured external issuer and an
    # empty bundled one wired up is how you get a login that half-works and is
    # miserable to debug.
    #
    # This does NOT affect the local admin account, which is independent of Dex
    # either way - see configs.cm admin.enabled below.
    dex = {
      enabled = false
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
        # chart's own default exclusions, not these.
        #
        # CORRECTED 2026-08-12: the parenthetical here used to claim that
        # migrating to configs.cm "would clobber those chart defaults, since the
        # value replaces rather than merges". That is WRONG and it is why this
        # block sat broken instead of being moved. Verified against chart 10.1.4:
        # the defaults live in the chart's own values.yaml under configs.cm, so
        # Helm deep-merges user values over them key-by-key, and the chart then
        # does `mergeOverwrite $preset $config` (templates/_helpers.tpl, the
        # argo-cd.config.cm helper). Adding a NEW key under configs.cm therefore
        # keeps every default intact - which is exactly what the configs.cm block
        # further down does for oidc.config.
        #
        # Only re-specifying THIS key would override the chart's default
        # exclusion list (Cilium, Kyverno, cert-manager, Lease, TokenReview...)
        # with just the two Crossplane groups below, which is a real behavior
        # change and the actual reason this is still parked here rather than a
        # one-line move. Same trap applies to the module's `exec.enabled = true`
        # in modules/argocd/helm.tf: also silently a no-op, live value is the
        # chart default `false`. Do not "fix" either by reflex - enabling pod
        # exec is a security decision, and narrowing resource.exclusions is a
        # performance one. Both deserve their own change.
        #
        # The agent-docs framework therefore does NOT rely on a
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

    # SSO via the standalone Dex (base-apps/dex), added 2026-08-12.
    #
    # This block is under `configs`, NOT `server.config` above. That is the
    # whole trick: the chart reads configs.cm and ignores server.config
    # entirely, so anything added to the older block silently does nothing.
    # See the long note in server.config for how that was verified.
    configs = {
      cm = {
        # PUBLIC client + PKCE - there is no client secret anywhere, by design.
        # The matching Dex staticClient sets `public: true`; see the rationale
        # in base-apps/dex/configmap.yaml. Adding a clientSecret here would
        # break the flow rather than harden it.
        #
        # `issuer` must match Dex's own issuer EXACTLY (https://dex.arigsela.com)
        # or OIDC discovery fails the issuer check. Argo CD's server pod reaches
        # that public URL out through the router and back in via hairpin NAT,
        # arriving at the Gateway as the WAN address, which the ingress
        # allow-list accepts. Confirmed from a pod in the argo-cd namespace
        # before this change: discovery returned 200.
        #
        # CONSEQUENCE, worth knowing before the next ISP address rotation: SSO
        # login therefore depends on the WAN IP being correct in BOTH Route 53
        # and base-apps/istio-ingress/authorizationpolicy.yaml. When that
        # address next changes, Argo CD's login breaks along with everything
        # else until the allow-list catches up - and the admin account below is
        # how you get back in to fix it.
        "oidc.config" = <<-EOT
          name: Dex
          issuer: https://dex.arigsela.com
          clientID: argocd
          enablePKCEAuthentication: true
          requestedScopes:
            - openid
            - profile
            - email
        EOT

        # Local admin DISABLED 2026-08-12, once SSO was confirmed working end to
        # end. Username/password login is gone; Dex is now the only way into the
        # UI. Nothing automated depended on it - checked before flipping: no
        # `accounts.*` entries exist (admin was the only local account), no API
        # tokens, and nothing in this repo references argocd-initial-admin-secret
        # or does an `argocd login`. The home.arigsela.com tile reads Prometheus
        # rather than the Argo CD API precisely to avoid needing an account here.
        #
        # WHAT THIS COSTS: there is no longer a login that survives Dex being
        # down. SSO depends on GitHub, on Dex, and on the ingress allow-list, and
        # the WAN address rotation on this same day proved that last one is not
        # hypothetical - when the ISP moves the address, dex.arigsela.com stops
        # being reachable from the Argo CD pod and SSO stops with it.
        #
        # WHY THAT IS ACCEPTABLE: losing the UI is not losing control. Argo CD is
        # driven by git and the Applications are plain CRs, so kubectl still
        # manages everything without a UI session, and the allow-list fix that
        # restores SSO is itself a git push that syncs without anyone logging in.
        #
        # EMERGENCY RE-ENABLE, if you ever need the UI before SSO is repaired:
        #   kubectl -n argo-cd patch cm argocd-cm --type merge \
        #     -p '{"data":{"admin.enabled":"true"}}'
        # argocd-cm is Helm-managed, NOT synced by an Argo CD Application, so
        # selfHeal will not revert that patch - it holds until the next
        # `terraform apply` re-renders the chart. Treat it as a stopgap and land
        # the real fix in git. The admin password is still in the untouched
        # argocd-initial-admin-secret (unless it was rotated since install).
        "admin.enabled" = "false"
      }

      # RBAC. Without this an SSO login SUCCEEDS and then lands with zero
      # permissions - an empty Argo CD showing no applications - which reads
      # like a broken migration rather than a missing grant.
      rbac = {
        # Deny by default: anyone who authenticates but matches no line below
        # gets nothing.
        "policy.default" = ""

        # The chart default is '[groups]', which CANNOT WORK here. Dex's GitHub
        # connector only emits groups for GitHub *orgs*, and this is a personal
        # account with no `orgs:` filter configured - see the comment in
        # base-apps/dex/configmap.yaml. So match on identity claims instead.
        "scopes" = "[email,preferred_username]"

        # Both claims are granted admin because which one Dex populates depends
        # on the GitHub account's email visibility: `email` is absent from the
        # token if the GitHub primary address is private, in which case
        # preferred_username (the login) is what arrives. Listing both means
        # the login works either way rather than depending on a setting in
        # GitHub that is not visible from this repo.
        "policy.csv" = <<-EOT
          g, arigsela@gmail.com, role:admin
          g, arigsela, role:admin
        EOT
      }
    }
  }
}

# Note: argocd_applicationsets (master-app) is no longer managed by Terraform.
# The master-app ArgoCD Application is managed directly via base-apps/ GitOps.