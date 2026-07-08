---
app: argo-cd
catalog_entity: argo-cd
kind: runbook
namespace: argo-cd
last_reviewed: 2026-07-08
status: current
tags: [gitops, control-plane]
sources:
  - base-apps/argo-cd.yaml
  - base-apps/argo-cd/ingress.yaml
  - terraform/modules/argocd
  - terraform/modules/application-sets
  - terraform/roots/asela-cluster/argocd.tf
---

# argo-cd — Runbook

## Failure modes
### Symptom: one app is OutOfSync / not deploying
- **Check:** `kubectl -n argo-cd get applications` for that app's sync/health status and any error message.
- **Fix:** correct the manifest/path in git and push — `selfHeal: true` will reconcile it. If a manual change is fighting `selfHeal`, revert the manual change instead of re-applying it.

### Symptom: nothing is syncing, across all apps
- **Check:** the `argo-cd` namespace's `application-controller`, `repo-server`, and `server` pods (`kubectl -n argo-cd get pods`); also check the `master-app` Application's own status, since it is what discovers every other Application under `base-apps/`.
- **Fix:** restart the failing controller pod; confirm repo connectivity/credentials to `https://github.com/arigsela/kubernetes`. If `master-app` itself is broken, no new or changed `base-apps/*.yaml` Applications will be picked up even if the other controllers are healthy.

### Symptom: UI at `argocd.arigsela.com` is unreachable or fails TLS
- **Check:** `base-apps/argo-cd/ingress.yaml` — confirm the `letsencrypt-prod` `ClusterIssuer`-issued `argocd-tls` secret is valid, and that the client IP is covered by `nginx.ingress.kubernetes.io/whitelist-source-range` (a fixed allowlist of IPs/CIDRs; anything else is rejected at the ingress).
- **Fix:** renew/repair the cert-manager certificate, or update the whitelist annotation and push via git — do not `kubectl edit` the ingress, `selfHeal` will revert it.

## How-to
### Deploy a new application
Add `base-apps/<app>.yaml` (an Argo CD `Application`) plus a `base-apps/<app>/` manifest directory; the `master-app` Application discovers the new file and creates the child Application automatically. There is no manual `argocd app create` step in this repo's workflow.

### Change Argo CD's own install/config
Edit `terraform/modules/argocd/helm.tf` (chart/values) or the `module "argocd"` block in `terraform/roots/asela-cluster/argocd.tf` (node placement, resource exclusions), then run the normal `terraform plan`/`terraform apply` from `terraform/roots/asela-cluster/`. This is one of the few things in this repo that is *not* GitOps-synced by Argo CD — it's applied out-of-band via Terraform.

### Change this app's own GitOps-managed resources (e.g. the ingress)
Edit `base-apps/argo-cd/ingress.yaml` and push; it is synced like any other app, via the `argo-cd-config` Application (`base-apps/argo-cd.yaml`).
