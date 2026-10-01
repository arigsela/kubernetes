---
type: "Kubernetes App Guide"
title: "Weather Kitchen Backend"
description: "Backend API for Weather Kitchen (FastAPI, JWT, Vault-backed DB)"
app: weather-kitchen-backend
catalog_entity: weather-kitchen-backend
kind: docs
namespace: weather-kitchen
last_reviewed: 2026-09-30
status: stable
tags: [fastapi, jwt, postgresql]
sources:
  - base-apps/weather-kitchen-backend/deployments.yaml
  - base-apps/weather-kitchen-backend/configmaps.yaml
  - base-apps/weather-kitchen-backend/external_secrets.yaml
  - base-apps/weather-kitchen-backend/secret-store.yaml
  - base-apps/weather-kitchen-backend/httproute.yaml
  - base-apps/weather-kitchen-backend/certificate.yaml
  - base-apps/weather-kitchen-backend/reference-grant.yaml
  - base-apps/weather-kitchen-backend/services.yaml
  - base-apps/weather-kitchen-frontend/httproute.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - catalog/api-entities.yaml
---

# weather-kitchen-backend

## What it is
Backend API for the Weather Kitchen app: a FastAPI service with JWT auth (the catalog declares its API as `weather-kitchen-backend-api`, "Weather Kitchen FastAPI backend (OpenAPI)", in `catalog/api-entities.yaml`, with the spec read from `http://weather-kitchen-backend.weather-kitchen.svc.cluster.local/openapi.json`). Image `852893458518.dkr.ecr.us-east-2.amazonaws.com/weather-kitchen-backend:latest` (`imagePullPolicy: Always`, so every pod start pulls whatever `latest` is), listening on port `8000`, with `/health` used for both liveness and readiness probes (and as the Prometheus scrape path in the pod annotations).

## Architecture & data flow
Deployed as a `Deployment` (`deployments.yaml`, `replicas: 2`) on nodes labeled `node.kubernetes.io/workload: application`. Config comes from two sources wired via `envFrom`: the `weather-kitchen-backend-config` ConfigMap (`configmaps.yaml` — `ENVIRONMENT`, `DEBUG`, `BACKEND_CORS_ORIGINS: https://weather-kitchen.arigsela.com`) and the `weather-kitchen-backend-secrets` Secret populated by an `ExternalSecret`. The `weather-kitchen-backend` `Service` (`services.yaml`, ClusterIP, port `80` → container port `8000`) is the backend of this app's HTTPRoute. The companion `weather-kitchen-frontend` app (separate namespace `weather-kitchen-frontend`) points its `API_URL` at the same public host.

## Networking — one host, two routes
`weather-kitchen.arigsela.com` is a **split host**: two HTTPRoutes in two namespaces attach to the same listener, `https-weather-kitchen`, on the shared `main` Gateway (`base-apps/istio-ingress/gateway.yaml`).
- **This app** (`httproute.yaml`): `PathPrefix /api` → `weather-kitchen-backend:80`. The nginx `rewrite-target: /api/$1` was deliberately not ported: it was an identity rewrite, so paths reach the backend unchanged.
- **The frontend** (`base-apps/weather-kitchen-frontend/httproute.yaml`): `Exact /api/docs` and `Exact /api/openapi.json` plus `PathPrefix /` → the frontend. Exact matches outrank prefixes in Gateway API, so those two paths go to the frontend even though they fall under `/api`, reproducing the old nginx regex `/api/(?!docs|openapi\.json)(.*)`.

**This app owns the host's TLS for both halves:** `certificate.yaml` (`weather-kitchen-tls`, issuer `letsencrypt-route53`, DNS-01) and `reference-grant.yaml` (lets the Gateway read that Secret) live in the `weather-kitchen` namespace, and the listener's `certificateRefs` points here. Removing either breaks HTTPS for the frontend too. Access is IP-restricted by the host's rule in `base-apps/istio-ingress/authorizationpolicy.yaml`, which also covers both halves.

## Secrets
`secret-store.yaml` defines a `SecretStore` named `vault-backend` pointing at `http://vault.vault.svc.cluster.local:8200`, KV v2 path `k8s-secrets`, using Vault's Kubernetes auth method with role `weather-kitchen` as the namespace's `default` ServiceAccount. `external_secrets.yaml` resolves three keys under Vault path `weather-kitchen-backend` into the `weather-kitchen-backend-secrets` Secret: `JWT_SECRET_KEY` (`jwt-secret-key`), `DATABASE_URL` (`database-url`), and `BETA_ACCESS_CODE` (`beta-access-code`). The `DATABASE_URL` is an opaque connection string from Vault — these manifests don't reference an in-cluster Postgres service host directly, so the database target (shared `postgresql` app vs. something external) isn't verifiable from this directory alone.

## Where config lives
- Runtime config: `configmaps.yaml`.
- Secrets: `secret-store.yaml` + `external_secrets.yaml` (Vault-backed).
- Networking: `services.yaml` (ClusterIP :80 → :8000), `httproute.yaml` (`/api`), `certificate.yaml` + `reference-grant.yaml` (shared with the frontend); access rule in `base-apps/istio-ingress/authorizationpolicy.yaml`.
