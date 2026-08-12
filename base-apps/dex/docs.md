---
type: "Kubernetes App Guide"
title: "Dex"
description: "OIDC provider fronting GitHub — issuer for Vault OIDC (human `vault` login via GitHub SSO)"
app: dex
catalog_entity: dex
kind: docs
namespace: dex
last_reviewed: 2026-07-15
status: current
tags: [oidc, authentication, github, vault]
sources:
  - base-apps/dex/deployment.yaml
  - base-apps/dex/configmap.yaml
  - base-apps/dex/external-secret.yaml
  - base-apps/dex/secret-store.yaml
  - base-apps/dex/service.yaml
  - base-apps/dex/httproute.yaml
  - base-apps/dex/rbac.yaml
---

# dex

## What it is
Dex (`ghcr.io/dexidp/dex:v2.41.1`) is an **OIDC provider** that fronts an upstream
identity source. In this cluster it wraps **GitHub** so that humans can log in to
other services with their GitHub account without those services holding GitHub
credentials directly. It is deployed as a single `Deployment` in the `dex`
namespace and served at `https://dex.arigsela.com` (`base-apps/dex/httproute.yaml`,
TLS via `letsencrypt-prod`).

Its OIDC issuer is `https://dex.arigsela.com` (`configmap.yaml`, `dex-config`).

## Who uses it
**HashiCorp Vault** is the primary relying party: Vault's OIDC auth method points
at Dex, so operators log in to the Vault UI (`vault.arigsela.com`) with GitHub via
Dex rather than with a Vault token. The Vault callback URLs are registered as
`redirectURIs` on Dex's static client, and Vault authenticates to Dex with the
`vault-client-secret` credential.

**Argo CD** is the second relying party (added 2026-08-12). It logs in through the
same GitHub identity, but as a **public client using PKCE** — there is no
`argocd-client-secret` anywhere, deliberately. Argo CD's login happens in a
browser, so a client secret could not be kept secret; PKCE is the correct control
for that flow. The practical benefit is that Argo CD needs no Vault-backed
`SecretStore` in its namespace at all.

Argo CD's own config lives in Terraform, not in `base-apps/`
(`terraform/roots/asela-cluster/argocd.tf`, under `configs.cm`), because Argo CD is
installed by the Helm chart rather than by a manifest here.

| Relying party | Client type | Credential | Config lives in |
|---|---|---|---|
| Vault | confidential | `vault-client-secret` from Vault | `base-apps/vault/` |
| Argo CD | public (PKCE) | none, by design | `terraform/roots/asela-cluster/argocd.tf` |

Because both depend on Dex, **Dex is a single point of failure for human login to
both** — and the two relying parties handle that differently. Vault keeps its own
token/root path as a break-glass route. Argo CD **does not**: its local `admin`
login was disabled on 2026-08-12, so if Dex is down there is no Argo CD UI login
at all. That is deliberate (Argo CD is driven by git and `kubectl`, not the UI),
but it means **taking Dex down locks out the Argo CD UI entirely** — worth
remembering before restarting or reconfiguring this app.

## Storage
Dex uses its **Kubernetes CRD storage backend** (`storage.type: kubernetes`,
`inCluster: true`). That is why it has a `ClusterRole`/`ClusterRoleBinding`
(`rbac.yaml`): it manages `dex.coreos.com` custom resources and creates its own
CRDs on first start. State (auth requests, refresh tokens) lives as CRs in-cluster,
so no external database is required.

## Secrets
`dex-secrets` (`external-secret.yaml`) resolves three values from Vault through the
namespace `SecretStore` (`secret-store.yaml`, Vault kubernetes-auth role `dex`,
path `k8s-secrets`, key `dex`):

| Secret property | Used for |
|---|---|
| `github-client-id` | the GitHub OAuth app client ID (Dex's GitHub connector) |
| `github-client-secret` | the GitHub OAuth app client secret |
| `vault-client-secret` | the shared secret Vault uses to authenticate to Dex |

No secret value is committed to Git — only the `ExternalSecret` mapping.

## How a login flows
1. A human opens the Vault UI and chooses OIDC login.
2. Vault redirects to Dex (`dex.arigsela.com`).
3. Dex redirects to GitHub; the user authorizes.
4. GitHub → Dex → Vault callback; Vault issues a Vault token scoped to the user's
   mapped policy.
