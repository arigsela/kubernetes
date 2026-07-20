---
type: "Kubernetes App Runbook"
title: "Weather Kitchen Backend — Runbook"
description: "Operational runbook for Weather Kitchen Backend: failure modes, checks, and fixes."
app: weather-kitchen-backend
catalog_entity: weather-kitchen-backend
kind: runbook
namespace: weather-kitchen
last_reviewed: 2026-07-10
status: current
tags: [fastapi, jwt, postgresql]
sources:
  - base-apps/weather-kitchen-backend/deployments.yaml
  - base-apps/weather-kitchen-backend/external_secrets.yaml
  - base-apps/weather-kitchen-backend/secret-store.yaml
  - base-apps/weather-kitchen-backend/nginx-ingress.yaml
---

# weather-kitchen-backend runbook

## Failure modes

### Symptom: pods stuck in `CreateContainerConfigError` / `CrashLoopBackOff` on deploy
- **Check:** `kubectl -n weather-kitchen get externalsecret weather-kitchen-backend-secrets` (look for `SecretSynced` / `Ready` status) and `kubectl -n weather-kitchen get secret weather-kitchen-backend-secrets`. The `Deployment` (`deployments.yaml`) pulls `JWT_SECRET_KEY`, `DATABASE_URL`, `BETA_ACCESS_CODE` via `envFrom.secretRef: weather-kitchen-backend-secrets` — if the `ExternalSecret` hasn't synced from Vault (`secret-store.yaml`, role `weather-kitchen`, path `k8s-secrets/weather-kitchen-backend`), the Secret is missing or stale and every pod fails at container-create time, not at request time.
- **Fix:** confirm Vault is unsealed and reachable (`vault.vault.svc.cluster.local:8200`) and that the `weather-kitchen` Vault role/policy grants read on `k8s-secrets/weather-kitchen-backend` with the `jwt-secret-key`/`database-url`/`beta-access-code` properties populated. If the SecretStore role or Vault path needs to change, open a PR updating `secret-store.yaml`/`external_secrets.yaml`.

### Symptom: clients get `403 Forbidden` from `https://weather-kitchen.arigsela.com`
- **Check:** `kubectl -n weather-kitchen get ingress weather-kitchen-backend-nginx -o yaml` and look at the `nginx.ingress.kubernetes.io/whitelist-source-range` annotation — it hard-codes a small allow-list of IPs (plus `10.0.0.0/8`). A legitimate client whose public IP isn't in that list is blocked at the ingress before it reaches the pod.
- **Fix:** open a PR adding the new IP/CIDR to `whitelist-source-range` in `base-apps/weather-kitchen-backend/nginx-ingress.yaml`; do not edit the Ingress live (Argo CD `selfHeal` will revert it).

### Symptom: pods never become `Ready`, rolling deploys stall
- **Check:** `kubectl -n weather-kitchen get pods -l app=weather-kitchen-backend` and `kubectl -n weather-kitchen logs deploy/weather-kitchen-backend`. The readiness probe hits `/health` on port `8000` with a 60s initial delay and the liveness probe a 90s initial delay (`deployments.yaml`); a backend that can't reach its database (bad/rotated `DATABASE_URL`) or is still starting up past those windows will fail both probes and never go Ready, blocking the rollout.
- **Fix:** check the app logs for a DB connection error first (points back to the Vault-sourced `DATABASE_URL`); if it's just slow startup under load, a PR increasing `initialDelaySeconds`/`failureThreshold` in `deployments.yaml` is the durable fix.
