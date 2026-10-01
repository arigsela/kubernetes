---
type: "Kubernetes App Guide"
title: "Atlantis"
description: "Terraform/OpenTofu PR automation: plans and applies terraform/roots/asela-cluster on the open PR, gated by the terraform-apply GitHub Environment"
app: atlantis
catalog_entity: atlantis
kind: docs
namespace: atlantis
last_reviewed: 2026-09-30
status: stable
tags: [terraform, opentofu, gitops, ci-cd]
sources:
  - base-apps/istio-ingress/gateway-options.yaml
  - base-apps/atlantis.yaml
  - base-apps/atlantis-config.yaml
  - atlantis.yaml
  - .github/workflows/terraform-apply.yaml
  - base-apps/atlantis/external-secrets.yaml
  - base-apps/atlantis/secret-store.yaml
  - base-apps/atlantis/network-policy.yaml
  - base-apps/atlantis/httproute.yaml
  - base-apps/atlantis/certificate.yaml
  - base-apps/atlantis/reference-grant.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - terraform/roots/asela-cluster/argocd.tf
---

# atlantis

## What it is
[Atlantis](https://www.runatlantis.io/) runs Terraform/OpenTofu `plan`/`apply`
as PR comments/checks against `github.com/arigsela/kubernetes`
(`orgAllowlist: github.com/arigsela/*`, `base-apps/atlantis.yaml`), covering
the one project in the repo-level `atlantis.yaml`: `asela-cluster`
(`terraform/roots/asela-cluster`). It is the **only** way Terraform gets
applied here: it holds the AWS credentials and cluster reachability that
GitHub-hosted runners lack.
It is deployed by **two** Argo CD Applications: `base-apps/atlantis.yaml`
installs the upstream Helm chart (`runatlantis/atlantis` 6.1.0, the server
itself — image `infracost/infracost-atlantis:atlantis0.40-infracost0.10`,
which bundles Infracost cost estimation), and
`base-apps/atlantis-config.yaml` syncs `base-apps/atlantis/` (its
`ExternalSecret`s, `SecretStore`, `NetworkPolicy`, `HTTPRoute`,
`Certificate` and `ReferenceGrant`).

## OpenTofu distribution
The chart values set `defaultTFDistribution: opentofu` /
`defaultTFVersion: "1.12.3"` (`base-apps/atlantis.yaml`). This is a deliberate
workaround: Atlantis's Terraform-binary downloads verify HashiCorp's GPG
release signature, which expired, breaking `terraform` binary downloads with
`unable to verify checksums signature: openpgp: key expired`. OpenTofu is
signed independently, so switching the default distribution sidesteps the
issue. `providers.tf` requires `>= 1.11.0`; OpenTofu `1.12.3` satisfies that.

## Auth: GitHub + AWS via Vault
Two `ExternalSecret`s (`base-apps/atlantis/external-secrets.yaml`) resolve
from Vault through the `vault-backend` `SecretStore`
(`base-apps/atlantis/secret-store.yaml`, Kubernetes auth role `atlantis`,
`k8s-secrets` KV v2 mount at `http://vault.vault.svc.cluster.local:8200`):
- `atlantis-vcs` (Vault key `atlantis/github`, `atlantis/webhook`) — the
  GitHub token and webhook secret, wired into the chart via
  `vcsSecretName: atlantis-vcs`.
- `atlantis-env` (Vault keys `atlantis/aws`, `atlantis/infracost`,
  `atlantis/k8s`) — AWS access key/secret (for the AWS provider Atlantis
  plans/applies against), the Infracost API key, and `TF_VAR_host` /
  `TF_VAR_client_certificate` / `TF_VAR_client_key` /
  `TF_VAR_cluster_ca_certificate` (Kubernetes provider credentials for the TF
  root), injected as env vars via the chart's `environmentSecrets`.

## Workflow and apply gating
**Apply happens on the open PR, before merge. Nothing applies on merge.** The
sequence (also the header of `terraform/roots/asela-cluster/argocd.tf`):

1. Open the PR. Atlantis autoplans when `**/*.tf`, `**/*.tfvars` or
   `../../modules/**/*.tf` change (`atlantis.yaml`); the plan is saved as
   `plan.tfplan`.
2. Wait for `atlantis/plan: asela-cluster` to finish and read the plan.
3. Run the **Terraform Apply (gated)** Action
   (`.github/workflows/terraform-apply.yaml`) with the PR number. It waits
   for approval on the `terraform-apply` GitHub Environment (required
   reviewer), checks the PR is `OPEN`, then posts
   `atlantis apply -p asela-cluster`.
4. Confirm `atlantis/apply` is green, **then** merge.

The effective gate: the server-side `repoConfig` in `base-apps/atlantis.yaml`
sets `apply_requirements: [approved, mergeable]`, but it also lists
`apply_requirements` in `allowed_overrides`, and the repo-level `atlantis.yaml`
overrides it with `apply_requirements: []`. Branch protection wants an approving
review the sole owner can't give, so "approved" and "mergeable" could never be
satisfied. **The only enforced gate is the `terraform-apply` Environment on the
Action.** A hand-typed `atlantis apply` comment skips that gate. What limits it
in practice is who can comment on the repo (`atlantis.yaml` says as much). Re-add
the requirements there if a second reviewer ever exists.

## Network policy and exposure
**Exposure:** `base-apps/atlantis/httproute.yaml` (HTTPRoute `atlantis` on the
shared `main` Gateway, listener `https-atlantis`, host
`atlantis.arigsela.com`, backend `atlantis:4141`) with `certificate.yaml`
(`atlantis-tls`, issuer `letsencrypt-route53`, DNS-01) and
`reference-grant.yaml`. The source-IP allow-list is the `atlantis.arigsela.com`
rule in `base-apps/istio-ingress/authorizationpolicy.yaml`: the operator's
addresses plus GitHub's webhook-delivery CIDRs (IPv4 + IPv6). GitHub's ranges
change occasionally, so re-check them against `https://api.github.com/meta` if
webhooks start getting 403s. The Coraza WAF doesn't inspect this host
(IP-restricted hosts are out of its scope).

**NetworkPolicy — known issue, not working protection.**
`base-apps/atlantis/network-policy.yaml` still reflects the nginx era: its only
ingress rule admits namespace `nginx-ingress` on 4141, and that namespace no
longer exists. The Istio Gateway's Envoy pods run in namespace `istio-ingress`
as ordinary pods (no `hostNetwork`; see `base-apps/istio-ingress/gateway-options.yaml`),
so the rule matches nothing. The fix is a `namespaceSelector` on
`kubernetes.io/metadata.name: istio-ingress`, not a node-IP `ipBlock`. Its egress "Kubernetes API server"
rule also excludes all RFC1918 ranges, which contain the cluster's own
addresses. The catch-all `443` egress rule still admits the in-cluster
`kubernetes` Service, but nothing admits a node's `:6443`. Atlantis evidently still receives webhooks and applies against the
cluster, so either the cluster isn't enforcing this policy or the traffic
reaches the pod by a path the rules don't describe. One candidate: the
policy selects `app.kubernetes.io/name: atlantis`, while the HTTPRoute's
homepage annotation finds the pod by `app=atlantis`, so the policy may not
select the chart's pod at all. The repo can't settle which; check the live
cluster (`kubectl -n atlantis get pods --show-labels`) before relying on it. Treat the pod as
**unprotected by NetworkPolicy** until the manifest is rewritten for
`istio-ingress`.

## Where config lives
- Server/chart values (image, OpenTofu distribution, `repoConfig`, secret
  wiring, resources, volume, placement): `base-apps/atlantis.yaml`.
- Repo-level project + apply requirements: `atlantis.yaml` (repo root).
- Apply trigger/gate: `.github/workflows/terraform-apply.yaml` + the
  `terraform-apply` Environment (GitHub repo settings, not in git).
- Config-only sync path (no `path:` change needed for chart upgrades):
  `base-apps/atlantis-config.yaml` → `base-apps/atlantis/`.
- Secrets: `base-apps/atlantis/external-secrets.yaml` +
  `base-apps/atlantis/secret-store.yaml`.
- Network: `base-apps/atlantis/network-policy.yaml` (see known issue above).
- Exposure: `httproute.yaml`, `certificate.yaml`, `reference-grant.yaml`; allow-list in `base-apps/istio-ingress/authorizationpolicy.yaml`.

## Gotchas & tribal knowledge
- **Merging before apply strands the change silently.** Atlantis deletes the
  saved plan and locks within seconds of the PR closing, and the Apply Action
  refuses a non-`OPEN` PR. Every check stays green while git and the cluster
  disagree (hit on #547 and #581). After any Terraform merge, verify the live
  resource.
- **Push once per PR.** Atlantis serializes per project directory; a second push
  while a plan is running fails on the workspace lock and can wedge the
  checkout. `atlantis unlock` clears it (runbook).
- **`prune: false` on the Helm Application** (`base-apps/atlantis.yaml`,
  SPEC §V.19). The chart renders `atlantis-data` as a standalone local-path PVC
  that reclaims on delete, and chart 6.1.0 can't annotate it `Prune=false`.
  Resources dropped from the chart linger instead of being pruned. That's
  intended; clean them up by hand.
- **`ingress.enabled: false`** in the chart values. The chart's own Ingress
  was class-less and routed nothing after nginx was removed, and Argo read its
  empty load-balancer status as permanently Progressing. Exposure is the
  HTTPRoute only.
- **Pinned to the control plane on a node-bound volume.** `nodeSelector:
  node.kubernetes.io/workload: infrastructure` plus the control-plane
  toleration puts the pod on the control-plane node, with its working directory
  on a 5Gi `local-path` RWO PVC bound to that node. It can't reschedule
  elsewhere, and a control-plane outage (e.g. a k3s upgrade hop) takes Terraform
  applies with it.
