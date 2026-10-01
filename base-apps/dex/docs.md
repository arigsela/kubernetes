---
type: "Kubernetes App Guide"
title: "Dex"
description: "OIDC provider fronting GitHub — human SSO for Vault, Argo CD, kubectl (kubelogin) and agent-audit-web"
app: dex
catalog_entity: dex
kind: docs
namespace: dex
last_reviewed: 2026-09-30
status: stable
tags: [oidc, authentication, github, vault]
sources:
  - base-apps/dex/deployment.yaml
  - base-apps/dex/configmap.yaml
  - base-apps/dex/external-secret.yaml
  - base-apps/dex/secret-store.yaml
  - base-apps/dex/service.yaml
  - base-apps/dex/httproute.yaml
  - base-apps/dex/certificate.yaml
  - base-apps/dex/reference-grant.yaml
  - base-apps/dex/rbac.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - docs/troubleshooting/kubectl-oidc.md
---

# dex

## What it is
Dex (`ghcr.io/dexidp/dex:v2.41.1`) is an **OIDC provider** that fronts an upstream
identity source. In this cluster it wraps **GitHub** so that humans can log in to
other services with their GitHub account without those services holding GitHub
credentials directly. It is deployed as a single `Deployment` in the `dex`
namespace and served at `https://dex.arigsela.com` through the shared Istio `main`
Gateway (`httproute.yaml`; TLS from `certificate.yaml`, ClusterIssuer
`letsencrypt-route53`, read by the Gateway via `reference-grant.yaml`). The host is
**IP-restricted** by its rule in `base-apps/istio-ingress/authorizationpolicy.yaml`
— a `403` from `dex.arigsela.com` is the allow-list, not Dex.

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
| Vault | confidential | `vault-client-secret` from Vault | inside Vault (`vault read auth/oidc/config`, `auth/oidc/role`) — not in git |
| Argo CD | public (PKCE) | none, by design | `terraform/roots/asela-cluster/argocd.tf` |
| kubectl (kubelogin) | public (PKCE) | none, by design | `ansible/roles/k3s_node/files/authn/authn-config.yaml` (API server) |
| agent-audit-web (oauth2-proxy) | confidential + PKCE | `agent-audit-client-secret` from Vault | `base-apps/agent-audit-web/` |

All four clients are `staticClients` in `configmap.yaml`: `vault`, `argocd`,
`kubernetes`, `agent-audit`.

**Dex is a single point of failure for human login to every relying party**, and
each handles that differently:
- **Vault** keeps its own token path as break-glass (port-forward and use a token —
  see the vault runbook).
- **Argo CD** has none: its local `admin` login was disabled on 2026-08-12, so with
  Dex down there is no Argo CD UI login at all. That is deliberate (Argo CD is driven
  by git and `kubectl`, not the UI).
- **kubectl via OIDC fails too** — the API server validates Dex tokens and fetches
  Dex discovery/JWKS through the public, allow-listed hostname. Break-glass is the
  x509 admin kubeconfig, which does not depend on Dex
  (`docs/troubleshooting/kubectl-oidc.md`). So "use kubectl" only rescues an Argo CD
  lockout if you reach for the admin context.
- **agent-audit-web** has no local fallback: nobody can log in until Dex is back.

Worth remembering before restarting or reconfiguring this app. A home WAN IP
rotation that leaves the Dex allow-list stale has the same effect on logins made
from home and on kubectl OIDC (see the wan-ip-monitor docs).

## Storage
Dex uses its **Kubernetes CRD storage backend** (`storage.type: kubernetes`,
`inCluster: true`). That is why it has a `ClusterRole`/`ClusterRoleBinding`
(`rbac.yaml`): it manages `dex.coreos.com` custom resources and creates its own
CRDs on first start. State (auth requests, refresh tokens) lives as CRs in-cluster,
so no external database is required.

## Secrets
`dex-secrets` (`external-secret.yaml`) resolves four values from Vault through the
namespace `SecretStore` (`secret-store.yaml`, Vault kubernetes-auth role `dex`,
path `k8s-secrets`, key `dex`):

| Secret property | Used for |
|---|---|
| `github-client-id` | the GitHub OAuth app client ID (Dex's GitHub connector) |
| `github-client-secret` | the GitHub OAuth app client secret |
| `vault-client-secret` | the shared secret Vault uses to authenticate to Dex |
| `agent-audit-client-secret` | the shared secret agent-audit-web's oauth2-proxy uses (also in `k8s-secrets/agent-audit-web`; rotate both) |

No secret value is committed to Git — only the `ExternalSecret` mapping.

## How a login flows
1. A human opens the Vault UI and chooses OIDC login.
2. Vault redirects to Dex (`dex.arigsela.com`).
3. Dex redirects to GitHub; the user authorizes.
4. GitHub → Dex → Vault callback; Vault issues a Vault token scoped to the user's
   mapped policy.
