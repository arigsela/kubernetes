---
type: "Kubernetes App Guide"
title: "Weather Kitchen Backend"
description: "Backend API for Weather Kitchen (likely FastAPI, JWT, Vault-backed DB)"
app: weather-kitchen-backend
catalog_entity: weather-kitchen-backend
kind: docs
namespace: weather-kitchen
last_reviewed: 2026-07-10
status: current
tags: [fastapi, jwt, postgresql]
sources:
  - base-apps/weather-kitchen-backend/deployments.yaml
  - base-apps/weather-kitchen-backend/configmaps.yaml
  - base-apps/weather-kitchen-backend/external_secrets.yaml
  - base-apps/weather-kitchen-backend/secret-store.yaml
  - base-apps/weather-kitchen-backend/nginx-ingress.yaml
  - base-apps/weather-kitchen-backend/services.yaml
---

# weather-kitchen-backend

## What it is
Backend API for the Weather Kitchen app, image `852893458518.dkr.ecr.us-east-2.amazonaws.com/weather-kitchen-backend:latest` (`deployments.yaml`). The env var names in `configmaps.yaml` (`BACKEND_CORS_ORIGINS`, `DEBUG`, `ENVIRONMENT`) and the Vault-sourced `JWT_SECRET_KEY`/`DATABASE_URL` secrets follow the common FastAPI backend-template convention, so this is very likely a FastAPI/Python service with JWT auth and a Postgres-style database, listening on port `8000` (`/health` used for both liveness and readiness probes).

## Architecture & data flow
Deployed as a `Deployment` (`deployments.yaml`, `replicas: 2`) on nodes labeled `node.kubernetes.io/workload: application`. Config comes from two sources wired via `envFrom`: the `weather-kitchen-backend-config` ConfigMap (`configmaps.yaml` — `ENVIRONMENT`, `DEBUG`, `BACKEND_CORS_ORIGINS: https://weather-kitchen.arigsela.com`) and the `weather-kitchen-backend-secrets` Secret populated by an `ExternalSecret`. The `weather-kitchen-backend` `Service` (`services.yaml`, ClusterIP, port `80` → container port `8000`) is fronted by an nginx `Ingress` (`nginx-ingress.yaml`) at host `weather-kitchen.arigsela.com`, TLS via `cert-manager.io/cluster-issuer: letsencrypt-prod`, restricted by an IP whitelist annotation, and rewriting `/api/(?!docs|openapi\.json)(.*)` to `/api/$1` on the backend. The companion `weather-kitchen-frontend` app (separate namespace `weather-kitchen-frontend`) points its `API_URL` at this same host.

## Secrets
`secret-store.yaml` defines a `SecretStore` named `vault-backend` pointing at `http://vault.vault.svc.cluster.local:8200`, KV v2 path `k8s-secrets`, using Vault's Kubernetes auth method with role `weather-kitchen`. `external_secrets.yaml` resolves three keys under Vault path `weather-kitchen-backend` into the `weather-kitchen-backend-secrets` Secret: `JWT_SECRET_KEY` (`jwt-secret-key`), `DATABASE_URL` (`database-url`), and `BETA_ACCESS_CODE` (`beta-access-code`). The `DATABASE_URL` is an opaque connection string from Vault — these manifests don't reference an in-cluster Postgres service host directly, so the database target (shared `postgresql` app vs. something external) isn't verifiable from this directory alone.

## Where config lives
- Runtime config: `configmaps.yaml`.
- Secrets: `secret-store.yaml` + `external_secrets.yaml` (Vault-backed).
- Networking: `services.yaml` (ClusterIP :80 → :8000), `nginx-ingress.yaml` (TLS host `weather-kitchen.arigsela.com`, IP whitelist, `/api` rewrite).
