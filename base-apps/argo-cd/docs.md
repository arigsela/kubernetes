---
type: "Kubernetes App Guide"
title: "Argo CD"
description: "GitOps control plane"
app: argo-cd
catalog_entity: argo-cd
kind: docs
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

# argo-cd

## What it is
The GitOps control plane for this cluster. Argo CD itself is installed via Terraform (Helm release configured in `terraform/modules/argocd/helm.tf`, invoked from `terraform/roots/asela-cluster/argocd.tf`) — it is not one of the `base-apps/*.yaml` Applications it manages. `base-apps/argo-cd/` only holds this app's own GitOps-synced supplementary resources: currently just the UI `Ingress` (`ingress.yaml`), synced by the `argo-cd-config` Application defined in `base-apps/argo-cd.yaml`.

## Architecture & data flow
Once installed, Argo CD watches an Application named `master-app` (namespace `argo-cd`, source path `base-apps`, `targetRevision: main`) which discovers every `.yaml` file directly under `base-apps/` and turns each into its own child Application — this is the app-of-apps ("master-app") pattern the rest of the repo's Applications rely on for auto-deployment.

**Correction vs. prior assumptions:** there is no `base-apps/master-app.yaml` file in this repo. The `master-app` Application was originally created by Terraform (`terraform/modules/application-sets/application-sets.tf`, a `kubectl_manifest` resource with `path: base-apps`). `terraform/roots/asela-cluster/argocd.tf` carries a comment stating the module "is no longer managed by Terraform" and that master-app "is managed directly via base-apps/ GitOps" — but no such file exists under `base-apps/` today. Treat `master-app` as a live cluster object of unclear current provenance; don't expect to find it via `git grep` in this repo.

All Applications, including `argo-cd-config` (`base-apps/argo-cd.yaml`) itself, use `syncPolicy.automated` with `prune: true` and `selfHeal: true`.

## Where config lives
- Install (Helm release, node placement, Crossplane resource exclusions): `terraform/modules/argocd/helm.tf` and `terraform/modules/argocd/namespaces.tf`, with settings supplied by the `module "argocd"` block in `terraform/roots/asela-cluster/argocd.tf`.
- This app's own GitOps-managed manifests: `base-apps/argo-cd/` (currently only `ingress.yaml`), synced by the `argo-cd-config` Application (`base-apps/argo-cd.yaml`, `path: base-apps/argo-cd`).
- UI ingress: `base-apps/argo-cd/ingress.yaml` — host `argocd.arigsela.com`, TLS via `cert-manager.io/cluster-issuer: letsencrypt-prod` into secret `argocd-tls`, backend `argo-cd-argocd-server:80` (the Argo CD server runs with `server.insecure=true`, set in `terraform/modules/argocd/helm.tf`, so TLS is terminated at the ingress, not the server), and an IP allowlist via `nginx.ingress.kubernetes.io/whitelist-source-range`.
- Original source of the `master-app` Application object: `terraform/modules/application-sets/application-sets.tf` — see the provenance caveat above.

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
- Because every Application (including `argo-cd-config`) has `selfHeal: true`, manual `kubectl` edits anywhere are reverted — all changes must go through git.
- The `resource.exclusions` in `terraform/roots/asela-cluster/argocd.tf` (Crossplane kinds, etc.) is currently **ineffective** — the argocd module passes config under the deprecated Helm `server.config.*` path while the chart reads `configs.cm.*`, so the live `argocd-cm` uses the chart's own default exclusions instead. The module's `exec.enabled = true` is silently dead for the same reason (live value is the chart default, `false`). This is why the agent-docs framework uses per-app `spec.source.directory.exclude` rather than a global exclusion.
  - **Corrected 2026-08-12:** this bullet used to add "since the value replaces rather than merges" as the reason not to migrate. That is wrong, and it is why the block stayed broken. Verified against chart 10.1.4: `configs.cm` defaults live in the chart's `values.yaml`, so Helm deep-merges over them, and the chart then applies `mergeOverwrite $preset $config` (`templates/_helpers.tpl`). **Adding a new key under `configs.cm` preserves every default** — which is exactly how SSO was added. Only re-specifying `resource.exclusions` itself would replace the chart's list, so that one still needs its defaults carried over deliberately.
- The Argo CD server runs with `server.insecure=true` — do not expose the `argo-cd-argocd-server` Service directly without a TLS-terminating proxy (currently the nginx `Ingress`) in front of it.
- A stuck/broken Argo CD, or a broken `master-app` Application specifically, affects every app's ability to sync — triage the control plane before chasing individual-app symptoms.
