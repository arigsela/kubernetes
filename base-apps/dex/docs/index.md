---
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
  - base-apps/dex/ingress.yaml
  - base-apps/dex/rbac.yaml
---

# dex

## What it is
Dex (`ghcr.io/dexidp/dex:v2.41.1`) is an **OIDC provider** that fronts an upstream
identity source. In this cluster it wraps **GitHub** so that humans can log in to
other services with their GitHub account without those services holding GitHub
credentials directly. It is deployed as a single `Deployment` in the `dex`
namespace and served at `https://dex.arigsela.com` (`base-apps/dex/ingress.yaml`,
TLS via `letsencrypt-prod`).

Its OIDC issuer is `https://dex.arigsela.com` (`configmap.yaml`, `dex-config`).

## Who uses it
**HashiCorp Vault** is the primary relying party: Vault's OIDC auth method points
at Dex, so operators log in to the Vault UI (`vault.arigsela.com`) with GitHub via
Dex rather than with a Vault token. The Vault callback URLs are registered as
`redirectURIs` on Dex's static client, and Vault authenticates to Dex with the
`vault-client-secret` credential.

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
