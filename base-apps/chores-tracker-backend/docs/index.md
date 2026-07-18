---
app: chores-tracker-backend
catalog_entity: chores-tracker-backend
kind: docs
namespace: chores-tracker
last_reviewed: 2026-07-08
status: current
tags: [fastapi, postgresql, jwt]
sources:
  - base-apps/chores-tracker-backend/deployments.yaml
  - base-apps/chores-tracker-backend/external_secrets.yaml
  - base-apps/chores-tracker-backend/secret-store.yaml
  - base-apps/chores-tracker-backend/virtualservice.yaml
  - base-apps/chores-tracker-backend/crossplane_resources.yaml
  - base-apps/chores-tracker-backend/configmaps.yaml
---

# chores-tracker-backend

## What it is
FastAPI/Python backend for the Chores Tracker app: JWT auth, PostgreSQL persistence (via CloudNativePG), HTMX-driven frontend served separately.

## Architecture & data flow
Deployed via Argo CD from this directory. Requests arrive through nginx-ingress and the Istio `VirtualService` (`virtualservice.yaml`) and reach the Argo Rollouts `Rollout` (`deployments.yaml`), which connects to a CloudNativePG-managed PostgreSQL cluster (`postgresql-cluster-rw.postgresql.svc.cluster.local`, see `crossplane_resources.yaml`) via an `asyncpg` connection string. Config is in `configmaps.yaml`.

## Where config lives
- Runtime config: `configmaps.yaml`.
- Secrets: `external_secrets.yaml` + `secret-store.yaml` resolve DB credentials/JWT keys from Vault (path under `k8s-secrets`).
- Cloud resources: `crossplane_resources.yaml` — notes the migration off Crossplane-provisioned MySQL to CloudNativePG-managed PostgreSQL; the Crossplane MySQL resources have been removed.

## Gotchas & tribal knowledge
- DB credentials come from Vault via ExternalSecrets — a failed sync surfaces as the pod crashlooping on startup, not as an ingress error.
- The frontend is a separate app (`chores-tracker-frontend`); backend changes may need a matching frontend deploy.
- `deployments.yaml` defines an Argo Rollouts `Rollout` (canary via Istio traffic splitting), not a plain `Deployment` — use `kubectl -n chores-tracker get rollout` / `kubectl argo rollouts get rollout chores-tracker-backend -n chores-tracker`, not `get deploy`.
- OpenTelemetry tracing to Coroot is wired in `configmaps.yaml` (Phase 3 of the OTel rollout); it only activates on image `7.1.0`+ since older pod versions ignore the exporter env vars. CORS is locked to `https://chores.arigsela.com`.
