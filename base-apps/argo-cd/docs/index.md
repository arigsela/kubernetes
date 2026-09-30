---
type: "Kubernetes App Guide"
title: "Argo CD"
description: "GitOps control plane: Terraform-installed, self-managing master-app root plus the managed-apps ApplicationSet"
app: argo-cd
catalog_entity: argo-cd
kind: docs
namespace: argo-cd
last_reviewed: 2026-09-30
status: current
tags: [gitops, control-plane]
sources:
  - base-apps/argo-cd.yaml
  - base-apps/master-app.yaml
  - base-apps/managed-apps.yaml
  - appsets/managed-apps
  - docs/managed-apps-appset.md
  - base-apps/argo-cd/httproute.yaml
  - base-apps/argo-cd/certificate.yaml
  - base-apps/argo-cd/reference-grant.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - terraform/modules/argocd
  - terraform/roots/asela-cluster/argocd.tf
  - scripts/argo-sync-window.sh
---

# argo-cd

## What it is
The GitOps control plane for this cluster. Argo CD itself is installed via Terraform (Helm release in `terraform/modules/argocd/helm.tf`, chart pin in `terraform/modules/argocd/variables.tf` — `10.9.2`, app `v3.5.3` at time of writing — invoked from `terraform/roots/asela-cluster/argocd.tf`). It is not one of the `base-apps/*.yaml` Applications it manages. `base-apps/argo-cd/` only holds this app's own GitOps-synced supplementary resources, the UI's `httproute.yaml`, `certificate.yaml` and `reference-grant.yaml`, synced by the `argo-cd-config` Application defined in `base-apps/argo-cd.yaml`.

## Architecture & data flow
Applications are discovered two ways, and the two sets are disjoint (`tests/appset/test_disjoint.py`):

1. **`master-app`** (`base-apps/master-app.yaml`), the root app-of-apps: source path `base-apps`, `targetRevision: main`. It creates an Application for every **top-level** `base-apps/*.yaml` and doesn't recurse. Since 2026-09-24 that includes its own file, so **master-app manages itself**. Before that it existed only in the cluster: Terraform created it (`terraform/modules/application-sets`) until the module was dropped in 2026-03 without a replacement manifest. The file's header comment is the handling guide:
   - **No finalizers, ever.** With the resources finalizer, deleting master-app would cascade-delete every Application under it; without one, the children keep running.
   - A bad `source.path`/`targetRevision` re-points the whole tree at once. Recover with `kubectl -n argo-cd edit application master-app`, then fix git.
   - Deleting the file makes master-app prune its own Application. The children survive, but the root is gone until re-created by hand.
   - `ignoreDifferences` + `RespectIgnoreDifferences=true` stop it fighting the pre-/post-delete finalizers Argo CD adds to hook-bearing apps (kyverno).
   - `scripts/argo-sync-window.sh pause` suspends master-app first (then every PVC-bearing app) by removing `syncPolicy.automated`. A suspended root can't sync itself back, so nothing resumes until `argo-sync-window.sh resume`.
2. **`managed-apps` ApplicationSet** (`base-apps/managed-apps.yaml`): a git-files generator over `appsets/managed-apps/*.yaml` produces Applications for subdirectories that need no bespoke Argo CD config (no Helm values, `ignoreDifferences`, sync waves or `directory.exclude`). Adding one means adding a config file, not an Application. It runs with `applicationsSync: create-update` (it never deletes an Application, so removing a config leaves an orphan to delete by hand) and `preserveResourcesOnDeletion: true`. See `docs/managed-apps-appset.md`.

All Applications, including `master-app` and `argo-cd-config`, use `syncPolicy.automated` with `prune: true` and `selfHeal: true`.

## Where config lives
- Install (Helm release, node placement, SSO, RBAC): `terraform/modules/argocd/helm.tf` and `terraform/modules/argocd/namespaces.tf`, with settings supplied by the `module "argocd"` block in `terraform/roots/asela-cluster/argocd.tf`. Applied by Atlantis on the open PR (see the header of `argocd.tf` and the runbook).
- Controller metrics: `controller.metrics.enabled` in `argocd.tf` creates the application-controller metrics Service (`argocd_app_info` on :8082, annotated for the existing Prometheus scrape job). The homepage Argo CD tile reads it.
- Root and generated apps: `base-apps/master-app.yaml`, `base-apps/managed-apps.yaml`, `appsets/managed-apps/`.
- This app's own GitOps-managed manifests: `base-apps/argo-cd/`, synced by the `argo-cd-config` Application (`base-apps/argo-cd.yaml`, `path: base-apps/argo-cd`).
- UI exposure: `base-apps/argo-cd/httproute.yaml` (HTTPRoute on the shared `main` Gateway, listener `https-argocd`, host `argocd.arigsela.com`, backend `argo-cd-argocd-server:80`), `certificate.yaml` (`argocd-tls`, issuer `letsencrypt-route53`, DNS-01) and `reference-grant.yaml` (lets the Gateway read `argocd-tls`). TLS terminates at the Gateway; the server runs with `server.insecure=true` (`configs.params.server.insecure` in `helm.tf`). The source-IP allow-list is the `argocd.arigsela.com` rule in `base-apps/istio-ingress/authorizationpolicy.yaml`.

## Authentication (SSO)
Since 2026-08-12 Argo CD authenticates **only** through GitHub via the standalone
Dex (`base-apps/dex`). Local username/password login is **disabled**
(`admin.enabled = false`) — there is no fallback login.

- Argo CD is a **public OIDC client using PKCE** — there is deliberately no client
  secret, so nothing to store in Vault and nothing to rotate. The matching
  `staticClient` (`public: true`) is in `base-apps/dex/configmap.yaml`.
- Config is in `terraform/roots/asela-cluster/argocd.tf` under `configs.cm`
  (`oidc.config`) and `configs.rbac` — **not** `server.config`, which the chart
  ignores entirely.
- RBAC matches on `[email,preferred_username]`, not the chart-default `[groups]`.
  Dex's GitHub connector only emits groups for GitHub **orgs**, and this is a
  personal account, so a `groups`-based rule could never match.
- The chart's **bundled** Dex is disabled (`dex.enabled = false`). It ran unused
  for 25+ days before this change; do not re-enable it expecting SSO to improve.

**Login depends on the WAN IP being correct.** Argo CD's server resolves
`dex.arigsela.com` to the public address and reaches it back through the router via
hairpin NAT, so it is subject to the ingress allow-list in
`base-apps/istio-ingress/authorizationpolicy.yaml`. When the ISP rotates that
address, SSO breaks along with everything else until the allow-list is updated.

**With local admin disabled there is no login that survives Dex being down.** That
is deliberate, and it is survivable because losing the UI is not losing control:
Argo CD is driven by git, Applications are plain CRs that `kubectl` manages
without a UI session, and the allow-list fix that restores SSO is itself a git
push that syncs without anyone logging in. If you genuinely need the UI before SSO
is repaired, the emergency re-enable is in the runbook.

## Gotchas & tribal knowledge
- Because every Application (including `master-app` and `argo-cd-config`) has `selfHeal: true`, manual `kubectl` edits anywhere are reverted — all changes must go through git. The exception is Helm-managed Argo CD config (`argocd-cm` etc.), which no Application syncs; it changes only on the next Terraform apply.
- The `resource.exclusions` in `terraform/roots/asela-cluster/argocd.tf` (Crossplane kinds) is currently **ineffective** — it sits under the deprecated `server.config.*` path while the chart reads `configs.cm.*`, so the live `argocd-cm` uses the chart's own default exclusions. The module's `exec.enabled = true` is silently dead for the same reason (live value is the chart default, `false`). This is why the agent-docs framework uses per-app `spec.source.directory.exclude` rather than a global exclusion.
  - Adding a **new** key under `configs.cm` preserves every chart default (Helm deep-merges, then the chart applies `mergeOverwrite`), which is how SSO was added. Only re-specifying `resource.exclusions` itself would replace the chart's default list. Chart ≥10.5.0 adds `configs.cm.resourceExclusionsAdditional`, which appends instead, so the two Crossplane groups can move there without losing defaults. It's still a behavior change (they start taking effect), so it's parked for its own Terraform PR (`argocd.tf` comment, SPEC T82).
- The Argo CD server runs with `server.insecure=true` — do not expose the `argo-cd-argocd-server` Service directly without a TLS-terminating proxy (today, the Istio `main` Gateway) in front of it.
- A stuck/broken Argo CD, or a broken or suspended `master-app`, affects every app's ability to sync. Triage the control plane before chasing individual-app symptoms.
- After a CRD upgrade (e.g. an operator chart bump), the application controller can keep a stale schema and report `ComparisonError: ... field not declared in schema`, which blocks auto-sync for the affected apps. Restarting the application controller clears it (runbook).
