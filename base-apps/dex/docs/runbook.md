---
type: "Kubernetes App Runbook"
title: "Dex — Runbook"
description: "Operational runbook for Dex: failure modes, checks, and fixes."
app: dex
catalog_entity: dex
kind: runbook
namespace: dex
last_reviewed: 2026-07-15
status: current
tags: [oidc, authentication, github, vault]
sources:
  - base-apps/dex/deployment.yaml
  - base-apps/dex/configmap.yaml
  - base-apps/dex/external-secret.yaml
  - base-apps/dex/secret-store.yaml
  - base-apps/dex/ingress.yaml
---

# dex — runbook

## Health check
```bash
kubectl get deploy dex -n dex
kubectl get pods -n dex
kubectl logs -n dex deploy/dex --tail=50
# OIDC discovery should return JSON:
curl -s https://dex.arigsela.com/.well-known/openid-configuration | head
```

## Common failure modes

### Vault login fails / "connector not found" or redirect error
- Check the `redirectURIs` in `configmap.yaml` match Vault's actual callback URLs
  (`vault.arigsela.com`, `vault.local`, `vault.10.0.1.110`). A mismatch is the most
  common cause.
- Confirm `vault-client-secret` in Vault matches what Vault's OIDC auth config uses.

### Dex pod CrashLoopBackOff on start
- The `dex-secrets` ExternalSecret may not have synced. Check:
  ```bash
  kubectl get externalsecret dex-secrets -n dex
  ```
  If `SecretSyncedError`, verify the Vault role `dex` and the `dex` key exist
  (`secret-store.yaml`, `external-secret.yaml`).
- On first start Dex creates its CRDs; if RBAC is wrong it cannot. Confirm the
  `ClusterRole`/`ClusterRoleBinding` in `rbac.yaml` grant `dex.coreos.com` `*` and
  `customresourcedefinitions` create/get/list.

### GitHub login rejected
- The GitHub OAuth app's callback URL must be `https://dex.arigsela.com/callback`.
- `github-client-id` / `github-client-secret` in Vault must match that OAuth app.

## TLS
The cert is issued by `letsencrypt-prod` via the ingress annotation
(`ingress.yaml`). If the cert is stuck, see the cert-manager runbook.

## Notes
- Dex state lives as `dex.coreos.com` CRs in-cluster (Kubernetes storage backend);
  there is no external DB to back up. Losing them logs everyone out but is not data
  loss.
