---
type: "Kubernetes App Guide"
title: "Weather Kitchen Frontend"
description: "Web frontend for Weather Kitchen (UI container on the shared weather-kitchen host)"
app: weather-kitchen-frontend
catalog_entity: weather-kitchen-frontend
kind: docs
namespace: weather-kitchen-frontend
last_reviewed: 2026-09-30
status: stable
tags: [nginx, node, frontend]
sources:
  - base-apps/weather-kitchen-frontend/deployments.yaml
  - base-apps/weather-kitchen-frontend/httproute.yaml
  - base-apps/weather-kitchen-frontend/services.yaml
  - base-apps/weather-kitchen-backend/httproute.yaml
  - base-apps/weather-kitchen-backend/certificate.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - base-apps/admission-policies/inject-ecr-pull-secret.yaml
---

# weather-kitchen-frontend

## What it is
The web frontend for the Weather Kitchen app, image `852893458518.dkr.ecr.us-east-2.amazonaws.com/weather-kitchen-frontend:latest` (`imagePullPolicy: Always`), listening on container port `3000` (`deployments.yaml`). The pod runs non-root (`runAsUser: 1001`) with `readOnlyRootFilesystem: true` and writable `emptyDir` volumes at `/tmp`, `/var/cache/nginx` and `/var/run`, which suggests nginx serves the built UI inside the container; the image's source isn't in this repo, so that is inferred, not verified.

## How it's deployed
A `Deployment` (`deployments.yaml`) in namespace `weather-kitchen-frontend` runs 2 replicas, scheduled onto `node.kubernetes.io/workload: application` nodes. Liveness (30s initial delay) and readiness (5s initial delay) probes both hit `GET /health` on port `3000`. Resources are modest (128Mi/100m request, 256Mi/200m limit). A `Service` (`services.yaml`, ClusterIP) exposes port `80` → container port `3000`. The Deployment declares no `imagePullSecrets`: the ECR pull secret (`ecr-registry`) is injected into every pod that pulls from ECR by the MutatingAdmissionPolicy `base-apps/admission-policies/inject-ecr-pull-secret.yaml`, and the Secret itself is kept fresh in each namespace by `base-apps/ecr-auth/cronjobs.yaml`.

## How it reaches the backend
The container sets `API_URL=https://weather-kitchen.arigsela.com` (`deployments.yaml`) — the frontend calls the backend over the **public host**, not an in-cluster Service reference. Routing between the two apps is split between two HTTPRoutes on that host, both attached to listener `https-weather-kitchen` on the shared `main` Gateway:
- **This app** (`httproute.yaml`): `Exact /api/docs` and `Exact /api/openapi.json`, plus `PathPrefix /` for everything else → `weather-kitchen-frontend:80`.
- **The backend** (`base-apps/weather-kitchen-backend/httproute.yaml`, namespace `weather-kitchen`): `PathPrefix /api` → `weather-kitchen-backend:80`.

Gateway API ranks exact matches above prefixes and longer prefixes above shorter ones, so `/api/docs` and `/api/openapi.json` come here, the rest of `/api/*` goes to the backend, and everything else comes here. This replaces the old nginx regex `/api/(?!docs|openapi\.json)(.*)`; Gateway API has no negative lookahead, hence the two exact rules.

TLS for the host is **the backend's**: `weather-kitchen-tls` is issued by the Certificate and exposed by the ReferenceGrant in `base-apps/weather-kitchen-backend/` (issuer `letsencrypt-route53`, DNS-01). This app has no certificate of its own. Access is IP-restricted by the `weather-kitchen.arigsela.com` rule in `base-apps/istio-ingress/authorizationpolicy.yaml`, one rule for both halves.

## Where config lives
- Runtime config: env vars in `deployments.yaml` (`NODE_ENV`, `API_URL`) — no `ConfigMap`/`ExternalSecret` exists in this directory, so the frontend holds no secrets of its own.
- Networking: `services.yaml` (ClusterIP `:80` → `:3000`), `httproute.yaml`; TLS in `base-apps/weather-kitchen-backend/`; access rule in `base-apps/istio-ingress/authorizationpolicy.yaml`.
