---
type: "Kubernetes App Runbook"
title: "Argo CD — Runbook"
description: "Operational runbook for Argo CD: failure modes, checks, and fixes."
app: argo-cd
catalog_entity: argo-cd
kind: runbook
namespace: argo-cd
last_reviewed: 2026-07-08
status: current
tags: [gitops, control-plane]
sources:
  - base-apps/argo-cd.yaml
  - base-apps/argo-cd/httproute.yaml
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

### Symptom: "Log in via Dex" fails, or SSO login lands in an empty Argo CD
Added 2026-08-12 with SSO. Work through these in order — they fail at different stages and look similar from the browser.

- **Login succeeds but no applications are visible.** Authentication worked and RBAC did not. Argo CD matches on the claims listed in `configs.rbac.scopes` (`[email,preferred_username]`), and `policy.default` is empty, so an identity matching no `policy.csv` line gets nothing. **Check** which claim actually arrived: `kubectl -n argo-cd logs deploy/argo-cd-argocd-server | grep -i "claim\|rbac"`. **Fix:** add that value to `policy.csv` in `terraform/roots/asela-cluster/argocd.tf` and apply. This is expected if the GitHub primary email is private — `email` is then absent and `preferred_username` (the login) is what arrives.
- **Dex rejects the callback ("unregistered redirect URI" / generic login error).** The `url` in `argocd-cm` must match a `redirectURI` on Dex's `argocd` static client. **Check:** `kubectl -n argo-cd get cm argocd-cm -o jsonpath='{.data.url}'` — it must be `https://argocd.arigsela.com`, and it derives from `global.domain` in Terraform, *not* from the HTTPRoute.
- **"invalid client" from Dex.** The `argocd` static client is missing from the running Dex, usually because `base-apps/dex/configmap.yaml` changed without bumping `checksum/config` in `base-apps/dex/deployment.yaml` — Dex does not watch its config file, so the pod kept the old one. **Check:** `kubectl -n dex exec deploy/dex -- cat /etc/dex/config.yaml | grep -A3 argocd`. **Fix:** recompute the checksum (the command is in `deployment.yaml`) and push.
- **Login page times out or 403s before reaching GitHub.** Argo CD reaches `dex.arigsela.com` over the public address via hairpin NAT, so it is subject to the ingress allow-list. After an ISP address rotation this fails until `base-apps/istio-ingress/authorizationpolicy.yaml` is updated. **Check from the server's own namespace**, since a working laptop proves nothing: `kubectl -n argo-cd run t --rm -i --restart=Never --image=curlimages/curl -- curl -sS -o /dev/null -w '%{http_code}' https://dex.arigsela.com/.well-known/openid-configuration` — expect `200`.
- **Break-glass, any of the above:** local username/password login is **disabled** as of 2026-08-12 (`admin.enabled = false`), so there is no fallback login. First ask whether you actually need the UI — Argo CD is driven by git and its Applications are plain CRs, so `kubectl -n argo-cd get/edit applications` does everything without a session, and the allow-list or Dex fix that restores SSO syncs on its own without anyone logging in. If you do need the UI before SSO is repaired:
  ```
  kubectl -n argo-cd patch cm argocd-cm --type merge -p '{"data":{"admin.enabled":"true"}}'
  kubectl -n argo-cd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
  ```
  `argocd-cm` is Helm-managed and **not** synced by an Argo CD Application, so `selfHeal` will not revert that patch — it holds until the next `terraform apply` re-renders the chart. Treat it as a stopgap: land the real fix in git, and let the next apply put `admin.enabled` back to `false`. The password secret is untouched by this change but will be absent if it was rotated or deleted after install.

## How-to
### Deploy a new application
Add `base-apps/<app>.yaml` (an Argo CD `Application`) plus a `base-apps/<app>/` manifest directory; the `master-app` Application discovers the new file and creates the child Application automatically. There is no manual `argocd app create` step in this repo's workflow.

### Change Argo CD's own install/config
Edit `terraform/modules/argocd/helm.tf` (chart/values) or the `module "argocd"` block in `terraform/roots/asela-cluster/argocd.tf`. This is one of the few things in this repo that is *not* GitOps-synced by Argo CD — it is applied by the in-cluster Atlantis, which holds the AWS credentials and cluster reachability that GitHub-hosted runners lack.

**Apply before you merge — merging is the last step, not the trigger.** Nothing applies on merge.

1. Open the PR. Atlantis autoplans on any `**/*.tf` change (`atlantis.yaml`).
2. Wait for `atlantis/plan: asela-cluster` to go green, and read the diff.
3. Run the **Terraform Apply (gated)** Action with the PR number, or comment `atlantis apply` directly. The Action only posts that comment; the real gate is the `terraform-apply` GitHub Environment's required reviewer.
4. Confirm `atlantis/apply` is green, **then** merge.

**If you merge first, the change is silently stranded.** Atlantis deletes the saved `plan.tfplan` and the workspace locks within seconds of the PR closing, and `.github/workflows/terraform-apply.yaml` hard-fails on a non-`OPEN` PR (`PR #N is not OPEN (state=MERGED)`). Git then disagrees with the cluster and **no check anywhere reports a failure** — the PR is green and merged, it simply never took effect. Recovery is a fresh PR touching any `.tf` file to re-trigger autoplan, then the sequence above. Verify with `kubectl -n argo-cd get cm argocd-cm -o jsonpath='{.data.admin\.enabled}'` (or whichever key you changed) rather than trusting the merge.

### Change this app's own GitOps-managed resources (e.g. the ingress)
Edit `base-apps/argo-cd/ingress.yaml` and push; it is synced like any other app, via the `argo-cd-config` Application (`base-apps/argo-cd.yaml`).
