---
type: "Kubernetes App Runbook"
title: "Weather Kitchen Backend — Runbook"
description: "Operational runbook for Weather Kitchen Backend: failure modes, checks, and fixes."
app: weather-kitchen-backend
catalog_entity: weather-kitchen-backend
kind: runbook
namespace: weather-kitchen
last_reviewed: 2026-09-30
status: current
tags: [fastapi, jwt, postgresql]
sources:
  - base-apps/weather-kitchen-backend/deployments.yaml
  - base-apps/weather-kitchen-backend/external_secrets.yaml
  - base-apps/weather-kitchen-backend/secret-store.yaml
  - base-apps/weather-kitchen-backend/httproute.yaml
  - base-apps/weather-kitchen-backend/certificate.yaml
  - base-apps/weather-kitchen-backend/reference-grant.yaml
  - base-apps/weather-kitchen-frontend/httproute.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
---

# weather-kitchen-backend runbook

## Failure modes

### Symptom: pods stuck in `CreateContainerConfigError` / `CrashLoopBackOff` on deploy
- **Check:** `kubectl -n weather-kitchen get externalsecret weather-kitchen-backend-secrets` (look for `SecretSynced` / `Ready` status) and `kubectl -n weather-kitchen get secret weather-kitchen-backend-secrets`. The `Deployment` (`deployments.yaml`) pulls `JWT_SECRET_KEY`, `DATABASE_URL`, `BETA_ACCESS_CODE` via `envFrom.secretRef: weather-kitchen-backend-secrets` — if the `ExternalSecret` hasn't synced from Vault (`secret-store.yaml`, role `weather-kitchen` as SA `default`, path `k8s-secrets/weather-kitchen-backend`), the Secret is missing or stale and every pod fails at container-create time, not at request time.
- **Fix:** confirm Vault is unsealed and reachable (`vault.vault.svc.cluster.local:8200`) and that the `weather-kitchen` Vault role/policy grants read on `k8s-secrets/weather-kitchen-backend` with the `jwt-secret-key`/`database-url`/`beta-access-code` properties populated. If the SecretStore role or Vault path needs to change, open a PR updating `secret-store.yaml`/`external_secrets.yaml`.

### Symptom: clients get `403 Forbidden` (`RBAC: access denied`) from `https://weather-kitchen.arigsela.com`
- **Check:** that 403 comes from the Gateway, not the app: the `weather-kitchen.arigsela.com` rule in `base-apps/istio-ingress/authorizationpolicy.yaml` allows only the allow-listed `/32`s (no `10.0.0.0/8`; LAN traffic arrives hairpin-NATed as the WAN address). Compare the client's public IP (`curl -s ifconfig.me`) with that rule. If every allow-listed host broke at once, the WAN address probably rotated.
- **Fix:** open a PR adding the address to that rule (it covers both the backend and the frontend). For a WAN rotation, see `base-apps/wan-ip-monitor/runbook.md`.

### Symptom: `/api/*` calls return the frontend's HTML (or 404), or `/api/docs` hits the backend
- **Check:** both HTTPRoutes on listener `https-weather-kitchen` are accepted and their paths are intact: `kubectl -n weather-kitchen get httproute weather-kitchen-backend -o yaml` (`PathPrefix /api`) and `kubectl -n weather-kitchen-frontend get httproute weather-kitchen-frontend -o yaml` (`Exact /api/docs`, `Exact /api/openapi.json`, `PathPrefix /`). Look at each route's `status.parents[].conditions` for `Accepted`/`ResolvedRefs`. If the backend route isn't accepted, the frontend's `/` prefix catches every `/api` request. If the frontend's two `Exact` rules are dropped or turned into prefixes, `/api/docs` moves to the backend (or `/api` breaks).
- **Fix:** restore the route by PR. Never express the split as a single `/api` prefix rule to the backend; the `Exact` pair on the frontend is what keeps `/api/docs` and `/api/openapi.json` there.

### Symptom: TLS errors on `weather-kitchen.arigsela.com` (both halves at once)
- **Check:** the host's certificate is this app's: `kubectl -n weather-kitchen get certificate weather-kitchen-tls` and `kubectl -n weather-kitchen get referencegrant gateway-to-weather-kitchen-tls`. Without the grant the listener comes up silently certless.
- **Fix:** restore the missing object by PR; a stuck Certificate is a cert-manager DNS-01 issue (`base-apps/cert-manager/runbook.md`).

### Symptom: pods never become `Ready`, rolling deploys stall
- **Check:** `kubectl -n weather-kitchen get pods -l app=weather-kitchen-backend` and `kubectl -n weather-kitchen logs deploy/weather-kitchen-backend`. The readiness probe hits `/health` on port `8000` with a 60s initial delay and the liveness probe a 90s initial delay (`deployments.yaml`); a backend that can't reach its database (bad/rotated `DATABASE_URL`) or is still starting up past those windows will fail both probes and never go Ready, blocking the rollout. Because the tag is `latest` with `imagePullPolicy: Always`, a restart can also pull a newer, broken build.
- **Fix:** check the app logs for a DB connection error first (points back to the Vault-sourced `DATABASE_URL`); if it's just slow startup under load, a PR increasing `initialDelaySeconds`/`failureThreshold` in `deployments.yaml` is the durable fix.
