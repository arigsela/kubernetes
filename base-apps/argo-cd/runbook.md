---
type: "Kubernetes App Runbook"
title: "Argo CD — Runbook"
description: "Operational runbook for Argo CD: sync outages (suspended root, stale CRD schema), UI/SSO failures, Terraform-applied config changes."
app: argo-cd
catalog_entity: argo-cd
kind: runbook
namespace: argo-cd
last_reviewed: 2026-09-30
status: stable
tags: [gitops, control-plane]
sources:
  - base-apps/argo-cd.yaml
  - base-apps/master-app.yaml
  - base-apps/managed-apps.yaml
  - docs/managed-apps-appset.md
  - base-apps/argo-cd/httproute.yaml
  - base-apps/argo-cd/certificate.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - terraform/modules/argocd
  - terraform/roots/asela-cluster/argocd.tf
  - scripts/argo-sync-window.sh
  - atlantis.yaml
  - .github/workflows/terraform-apply.yaml
---

# argo-cd — Runbook

## Failure modes
### Symptom: one app is OutOfSync / not deploying
- **Check:** `kubectl -n argo-cd get applications` for that app's sync/health status and any error message.
- **Fix:** correct the manifest/path in git and push — `selfHeal: true` will reconcile it. If a manual change is fighting `selfHeal`, revert the manual change instead of re-applying it.

### Symptom: nothing is syncing, across all apps
Work through these in order:
- **Root suspended by a sync window.** `scripts/argo-sync-window.sh pause` (used around k3s upgrade hops) removes `syncPolicy.automated` from `master-app` and every PVC-bearing app, and a suspended root can't sync itself back. **Check:** `kubectl -n argo-cd get app master-app -o jsonpath='{.spec.syncPolicy.automated}'`. Empty means suspended. **Fix:** if the maintenance window is over, `scripts/argo-sync-window.sh resume` (restores exactly what `pause` recorded in its state file, default `~/.k3s-hop-argo-state.json`). Without the state file, restore `automated: {prune: true, selfHeal: true}` on master-app by hand, and it re-applies its children from git.
- **Stale schema after a CRD upgrade.** Affected apps show `ComparisonError` with `field not declared in schema` (the CRD gained a field the controller's cached schema doesn't know), and auto-sync stops for them. **Check:** `kubectl -n argo-cd get applications` and look at the condition message. **Fix:** restart the application controller: `kubectl -n argo-cd rollout restart statefulset -l app.kubernetes.io/name=argocd-application-controller`. It's a control-plane-wide action, so confirm with the owner first.
- **Control plane down.** **Check:** the `application-controller`, `repo-server`, and `server` pods (`kubectl -n argo-cd get pods`) and the `master-app` Application's own status. **Fix:** restart the failing component; confirm repo connectivity/credentials to `https://github.com/arigsela/kubernetes`. If `master-app` is broken, no new or changed top-level `base-apps/*.yaml` Application is picked up even while the other controllers are healthy. Apps from the `managed-apps` ApplicationSet depend on the applicationset-controller instead (`kubectl -n argo-cd get applicationset managed-apps -o yaml` shows its conditions).
- **`master-app` pointed at the wrong path/revision.** It re-points the whole tree at once. **Fix:** `kubectl -n argo-cd edit application master-app` to recover, then fix `base-apps/master-app.yaml` in git (it manages itself and would otherwise revert your edit on the next sync).

### Symptom: UI at `argocd.arigsela.com` is unreachable or fails TLS
- **Check:** a 403 (Istio RBAC) means the client IP isn't in the `argocd.arigsela.com` rule of `base-apps/istio-ingress/authorizationpolicy.yaml`; after a WAN IP rotation every host fails at once. TLS errors: `kubectl -n argo-cd get certificate argocd-tls` (issuer `letsencrypt-route53`, DNS-01) and confirm `reference-grant.yaml` still grants the Gateway access to the `argocd-tls` Secret. Without it the `https-argocd` listener comes up without a certificate and logs no error. Route: `kubectl -n argo-cd get httproute argo-cd -o yaml` (status shows whether the `main` Gateway accepted it).
- **Fix:** allow-list and DNS changes go through `base-apps/istio-ingress/` (see its runbook); certificate problems through cert-manager. Push via git; don't `kubectl edit` the route or certificate, since `selfHeal` reverts them.

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

### Change this app's own GitOps-managed resources (e.g. the UI route)
Edit `base-apps/argo-cd/httproute.yaml` / `certificate.yaml` / `reference-grant.yaml` and push; they sync like any other app, via the `argo-cd-config` Application (`base-apps/argo-cd.yaml`). The IP allow-list lives in `base-apps/istio-ingress/authorizationpolicy.yaml`, not here.

### Add an app without writing an Application
If the app needs no bespoke Argo CD config, add `appsets/managed-apps/<name>.yaml` (plus the golden and test entry) instead of `base-apps/<app>.yaml`. The `managed-apps` ApplicationSet generates it. Removing a config does **not** delete the Application (`applicationsSync: create-update`), so delete the orphan by hand. Full procedure: `docs/managed-apps-appset.md`.
