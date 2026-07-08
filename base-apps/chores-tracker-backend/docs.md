---
app: chores-tracker-backend
catalog_entity: chores-tracker-backend
kind: docs
namespace: chores-tracker
last_reviewed: 2026-07-08
status: current
tags: [fastapi, mysql, jwt]
sources:
  - base-apps/chores-tracker-backend/deployments.yaml
  - base-apps/chores-tracker-backend/external_secrets.yaml
  - base-apps/chores-tracker-backend/secret-store.yaml
  - base-apps/chores-tracker-backend/virtualservice.yaml
---

# chores-tracker-backend

## What it is
FastAPI/Python backend for the Chores Tracker app: JWT auth, MySQL persistence, HTMX-driven frontend served separately.

## Architecture & data flow
Deployed via Argo CD from this directory. Requests arrive through nginx-ingress and the Istio `VirtualService` (`virtualservice.yaml`) and reach the Deployment (`deployments.yaml`) → MySQL. Config is in `configmaps.yaml`.

## Where config lives
- Runtime config: `configmaps.yaml`.
- Secrets: `external_secrets.yaml` + `secret-store.yaml` resolve DB credentials/JWT keys from Vault (path under `k8s-secrets`).
- Cloud resources: `crossplane_resources.yaml`.

## Gotchas & tribal knowledge
- DB credentials come from Vault via ExternalSecrets — a failed sync surfaces as the pod crashlooping on startup, not as an ingress error.
- The frontend is a separate app (`chores-tracker-frontend`); backend changes may need a matching frontend deploy.
