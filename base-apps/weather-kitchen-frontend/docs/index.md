---
type: "Kubernetes App Guide"
title: "Weather Kitchen Frontend"
description: "Web frontend for Weather Kitchen (nginx-fronted Node build)"
app: weather-kitchen-frontend
catalog_entity: weather-kitchen-frontend
kind: docs
namespace: weather-kitchen-frontend
last_reviewed: 2026-07-10
status: current
tags: [nginx, node, frontend]
sources:
  - base-apps/weather-kitchen-frontend/deployments.yaml
  - base-apps/weather-kitchen-frontend/nginx-ingress.yaml
  - base-apps/weather-kitchen-frontend/services.yaml
---

# weather-kitchen-frontend

## What it is
The web frontend for the Weather Kitchen app, image `852893458518.dkr.ecr.us-east-2.amazonaws.com/weather-kitchen-frontend:latest`, listening on container port `3000` (`deployments.yaml`). The pod mounts writable `emptyDir` volumes for `/tmp`, `/var/cache/nginx`, and `/var/run` under `readOnlyRootFilesystem: true` (non-root, `runAsUser: 1001`) — the same nginx-cache/nginx-run pattern used by the `chores-tracker-frontend` sibling app — so this is very likely an nginx-fronted Node build serving the UI.

## How it's deployed
A `Deployment` (`deployments.yaml`) in namespace `weather-kitchen-frontend` runs 2 replicas, scheduled onto `node.kubernetes.io/workload: application` nodes. Liveness (30s initial delay) and readiness (5s initial delay) probes both hit `GET /health` on port `3000`. Resources are modest (128Mi/100m request, 256Mi/200m limit). A `Service` (`services.yaml`, ClusterIP) exposes port `80` → container port `3000`.

## How it reaches the backend
The container sets `API_URL=https://weather-kitchen.arigsela.com` (`deployments.yaml`) — the frontend calls the backend over the **public ingress host**, not an in-cluster Service reference. Routing between the two apps is split at the `Ingress` layer on that same shared host:
- This app's `Ingress` (`nginx-ingress.yaml`, name `weather-kitchen-frontend-nginx`) matches path `/` (`pathType: Prefix`) and is annotated `nginx.ingress.kubernetes.io/priority: "50"`.
- `weather-kitchen-backend`'s `Ingress` (`base-apps/weather-kitchen-backend/nginx-ingress.yaml`, namespace `weather-kitchen`) matches `/api/(?!docs|openapi\.json)(.*)` with `priority: "100"` (higher priority, evaluated first on the shared host) and rewrites to `/api/$1`.

Both Ingresses request TLS for host `weather-kitchen.arigsela.com` via the same `weather-kitchen-tls` secret name and `cert-manager.io/cluster-issuer: letsencrypt-prod`, and carry an identical IP `whitelist-source-range` annotation — so requests to `weather-kitchen.arigsela.com/api/*` route to `weather-kitchen-backend` (namespace `weather-kitchen`) and everything else routes to this frontend.

## Where config lives
- Runtime config: env vars in `deployments.yaml` (`NODE_ENV`, `API_URL`) — no `ConfigMap`/`ExternalSecret` exists in this directory, so the frontend holds no secrets of its own.
- Networking: `services.yaml` (ClusterIP `:80` → `:3000`), `nginx-ingress.yaml` (TLS host `weather-kitchen.arigsela.com`, IP whitelist, path `/`, priority `50`).
