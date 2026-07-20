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
  - base-apps/argo-cd/ingress.yaml
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

## Gotchas & tribal knowledge
- Because every Application (including `argo-cd-config`) has `selfHeal: true`, manual `kubectl` edits anywhere are reverted — all changes must go through git.
- The `resource.exclusions` in `terraform/roots/asela-cluster/argocd.tf` (Crossplane kinds, etc.) is currently **ineffective** — the argocd module passes config under the deprecated Helm `server.config.*` path while the chart reads `configs.cm.*`, so the live `argocd-cm` uses the chart's own default exclusions instead. If you actually need those exclusions live, migrate the module to write `configs.cm.*` (and include the chart's default exclusions, since the value replaces rather than merges). This is why the agent-docs framework uses per-app `spec.source.directory.exclude` rather than a global exclusion.
- The Argo CD server runs with `server.insecure=true` — do not expose the `argo-cd-argocd-server` Service directly without a TLS-terminating proxy (currently the nginx `Ingress`) in front of it.
- A stuck/broken Argo CD, or a broken `master-app` Application specifically, affects every app's ability to sync — triage the control plane before chasing individual-app symptoms.
