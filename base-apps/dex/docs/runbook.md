---
type: "Kubernetes App Runbook"
title: "Dex — Runbook"
description: "Operational runbook for Dex: failure modes, checks, and fixes."
app: dex
catalog_entity: dex
kind: runbook
namespace: dex
last_reviewed: 2026-09-30
status: stable
tags: [oidc, authentication, github, vault]
sources:
  - base-apps/dex/deployment.yaml
  - base-apps/dex/configmap.yaml
  - base-apps/dex/external-secret.yaml
  - base-apps/dex/secret-store.yaml
  - base-apps/dex/httproute.yaml
  - base-apps/dex/certificate.yaml
  - base-apps/dex/reference-grant.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - docs/troubleshooting/kubectl-oidc.md
---

# dex — runbook

## Health check
```bash
kubectl get deploy dex -n dex
kubectl get pods -n dex
kubectl logs -n dex deploy/dex --tail=50
# OIDC discovery should return JSON (only from an allow-listed address — a 403
# here is the Istio allow-list, see "Every Dex login fails at once" below):
curl -s https://dex.arigsela.com/.well-known/openid-configuration | head
```

## Common failure modes

### A new or changed client returns 400 "invalid client"
- **Cause:** Dex does not watch its config file and there is no reloader in this
  cluster. A `configmap.yaml` edit syncs cleanly and changes nothing — the old
  config keeps serving until the pod restarts. The pod only rolls when the
  `checksum/config` annotation in `deployment.yaml` changes.
- **Check:** compare the annotation with the hash of the current config (the
  command is in the comment above the annotation):
  ```bash
  python3 -c "import hashlib,yaml; print(hashlib.sha256(yaml.safe_load(open('base-apps/dex/configmap.yaml'))['data']['config.yaml'].encode()).hexdigest()[:16])"
  grep checksum/config base-apps/dex/deployment.yaml
  ```
- **Fix:** put the new hash in `checksum/config` **in the same PR** as the
  ConfigMap change. Any edit inside the `config.yaml: |` block counts, comments
  included.

### Every Dex login fails at once (Vault, Argo CD, kubectl, agent-audit)
- **Symptom:** `dex.arigsela.com` answers `403` or times out from home; kubectl's
  OIDC context gets `Unauthorized`.
- **Cause:** usually a home WAN IP rotation — the Dex rule in
  `base-apps/istio-ingress/authorizationpolicy.yaml` is IP-restricted, and in-cluster
  clients (Argo CD, agent-audit-web's oauth2-proxy, the API server) reach Dex through
  that public hostname via hairpin NAT too. `wan-ip-monitor`
  fixes DNS automatically and opens a PR for the allow-list; merging it is the fix
  (see the wan-ip-monitor and istio-ingress runbooks). If Dex itself is down, see
  "Dex pod CrashLoopBackOff" below.
- **Break-glass meanwhile:** kubectl with the x509 admin kubeconfig
  (`docs/troubleshooting/kubectl-oidc.md`); Vault via port-forward and a token
  (vault runbook). Argo CD has no local login.

### Vault login fails / "connector not found" or redirect error
- Check the `redirectURIs` in `configmap.yaml` match Vault's actual callback URLs
  (`vault.arigsela.com`, `vault.local`, `vault.10.0.1.110`). A mismatch is the most
  common cause.
- Confirm `vault-client-secret` in Vault matches what Vault's OIDC auth config uses
  (that config lives inside Vault: `vault read auth/oidc/config`, not in git).

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
`certificate.yaml` issues `dex-tls` from the `letsencrypt-route53` ClusterIssuer
(Route 53 DNS-01); `reference-grant.yaml` lets the `main` Gateway in `istio-ingress`
read it. Without the grant the `https-dex` listener comes up silently certless.
Check with `kubectl -n dex get certificate dex-tls`; if the cert is stuck, see the
cert-manager runbook.

## Notes
- Dex state lives as `dex.coreos.com` CRs in-cluster (Kubernetes storage backend);
  there is no external DB to back up. Losing them logs everyone out but is not data
  loss.
