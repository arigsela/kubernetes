---
app: chores-tracker-frontend
catalog_entity: chores-tracker-frontend
kind: docs
namespace: chores-tracker-frontend
last_reviewed: 2026-07-10
status: current
tags: [nginx, htmx, frontend]
sources:
  - base-apps/chores-tracker-frontend/deployments.yaml
  - base-apps/chores-tracker-frontend/nginx-ingress.yaml
  - base-apps/chores-tracker-frontend/services.yaml
---

# chores-tracker-frontend

## What it is
The static/HTMX web frontend for the Chores Tracker app (per `CLAUDE.md`, the backend is FastAPI/PostgreSQL with an HTMX frontend). It runs as an nginx-based container (`852893458518.dkr.ecr.us-east-2.amazonaws.com/chores-tracker-frontend:1.5.3`) that serves the UI and proxies/serves on port `3000` (`deployments.yaml`). The pod mounts writable `emptyDir` volumes for `/tmp`, `/var/cache/nginx`, `/var/run`, and `/etc/nginx/conf.d` to support `readOnlyRootFilesystem: true` under a non-root nginx process (`runAsUser: 1001`, `fsGroup: 1001`).

## How it's deployed
A `Deployment` (`deployments.yaml`) in namespace `chores-tracker-frontend` runs 2 replicas, scheduled onto `node.kubernetes.io/workload: application` nodes. Liveness/readiness probes hit `GET /health` on port `3000`. Resource requests/limits are modest (128Mi/100m request, 256Mi/200m limit), consistent with a static/HTMX UI rather than a heavy compute workload. A `Service` (`services.yaml`, ClusterIP) exposes port `80` → container port `3000`.

## How it reaches the backend
The container is configured with `API_URL=https://chores.arigsela.com/api/v1` (`deployments.yaml`) — the frontend calls the backend over the **public ingress host**, not an in-cluster service reference. Routing to the two components is split at the `Ingress` layer on the same host `chores.arigsela.com`:
- This app's `Ingress` (`nginx-ingress.yaml`, name `chores-tracker-frontend-nginx`) matches path `/` (`pathType: Prefix`) and is annotated `nginx.ingress.kubernetes.io/priority: "50"`.
- `chores-tracker-backend`'s `Ingress` (`base-apps/chores-tracker-backend/nginx-ingress.yaml`) matches `/api/(?!docs|openapi\.json)(.*)` with `priority: "100"` (higher priority, so it is evaluated first for `/api` paths on the shared host).

Both Ingresses share TLS via the same `chores-tracker-tls` secret (`cert-manager.io/cluster-issuer: letsencrypt-prod`), so requests to `chores.arigsela.com/api/*` route to `chores-tracker-backend` and everything else routes to this frontend.

## Key configuration
- `NODE_ENV=production`, `API_URL=https://chores.arigsela.com/api/v1` (`deployments.yaml`).
- `securityContext`: non-root (`runAsUser: 1001`), `readOnlyRootFilesystem: true`, all capabilities dropped.
- No Vault/`SecretStore`/`ExternalSecret` resources exist in this directory — the frontend has no secrets of its own.
