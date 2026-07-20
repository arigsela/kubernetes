---
type: "Kubernetes App Guide"
title: "Atlantis"
description: "Terraform/OpenTofu PR automation (Atlantis, GitHub + AWS auth via Vault, Infracost)"
app: atlantis
catalog_entity: atlantis
kind: docs
namespace: atlantis
last_reviewed: 2026-07-10
status: current
tags: [terraform, opentofu, gitops, ci-cd]
sources:
  - base-apps/atlantis.yaml
  - base-apps/atlantis-config.yaml
  - base-apps/atlantis/external-secrets.yaml
  - base-apps/atlantis/secret-store.yaml
  - base-apps/atlantis/network-policy.yaml
  - base-apps/atlantis/nginx-ingress.yaml
---

# atlantis

## What it is
[Atlantis](https://www.runatlantis.io/) runs Terraform/OpenTofu `plan`/`apply`
as PR comments/checks against `github.com/arigsela/kubernetes`
(`orgAllowlist: github.com/arigsela/*`, `base-apps/atlantis.yaml`), covering
the `terraform/roots/asela-cluster` root described elsewhere in this repo.
It is deployed by **two** Argo CD Applications: `base-apps/atlantis.yaml`
installs the upstream Helm chart (`runatlantis/atlantis` 6.1.0, the server
itself — image `infracost/infracost-atlantis:atlantis0.40-infracost0.10`,
which bundles Infracost cost estimation), and
`base-apps/atlantis-config.yaml` syncs `base-apps/atlantis/` (its
`ExternalSecret`s, `SecretStore`, `NetworkPolicy`, and `Ingress`).

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

## Repo config and apply gating
`repoConfig` (`base-apps/atlantis.yaml`) allows custom workflows for
`github.com/arigsela/kubernetes` and sets server-side
`apply_requirements: [approved, mergeable]` — `apply` only runs once the PR
is approved and mergeable (`allowed_overrides` lets a PR override `workflow`
or `apply_requirements` locally).

## Network policy and ingress
`base-apps/atlantis/network-policy.yaml` restricts the `atlantis` pod to:
ingress from the `nginx-ingress` namespace on port `4141` only; egress for
DNS (53), Vault in the `vault` namespace (8200), the Kubernetes API server on
443/6443 (excluding RFC1918 ranges), and HTTPS (443) generally (GitHub API +
AWS APIs). `base-apps/atlantis/nginx-ingress.yaml` fronts the chart's
`atlantis` Service (port 4141) at `atlantis.arigsela.com` (TLS via
`letsencrypt-prod`), with `whitelist-source-range` limited to the operator's
IPs plus GitHub's published webhook-delivery CIDRs.

## Where config lives
- Server/chart values (image, OpenTofu distribution, `repoConfig`, secret
  wiring, resources, volume): `base-apps/atlantis.yaml`.
- Config-only sync path (no `path:` change needed for chart upgrades):
  `base-apps/atlantis-config.yaml` → `base-apps/atlantis/`.
- Secrets: `base-apps/atlantis/external-secrets.yaml` +
  `base-apps/atlantis/secret-store.yaml`.
- Network: `base-apps/atlantis/network-policy.yaml`.
- Exposure: `base-apps/atlantis/nginx-ingress.yaml`.
- Working directory persistence: `volumeClaim` (5Gi, `local-path`,
  `ReadWriteOnce`) in `base-apps/atlantis.yaml`.
