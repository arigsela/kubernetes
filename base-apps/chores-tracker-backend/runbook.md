---
type: "Kubernetes App Runbook"
title: "Chores Tracker Backend — Runbook"
description: "Operational runbook for Chores Tracker Backend: failure modes, checks, and fixes."
app: chores-tracker-backend
catalog_entity: chores-tracker-backend
kind: runbook
namespace: chores-tracker
last_reviewed: 2026-07-08
status: current
tags: [fastapi, postgresql, jwt]
sources:
  - base-apps/chores-tracker-backend/deployments.yaml
  - base-apps/chores-tracker-backend/external_secrets.yaml
  - base-apps/chores-tracker-backend/crossplane_resources.yaml
---

# chores-tracker-backend — Runbook

## Failure modes
### Symptom: pod CrashLoopBackOff on startup
- **Check:** `kubectl -n chores-tracker get externalsecret,secret` — confirm the ExternalSecret synced and the target Secret exists. Also confirm the CloudNativePG cluster (`postgresql-cluster-rw.postgresql.svc.cluster.local`, see `crossplane_resources.yaml`) is reachable — the app connects via `postgresql+asyncpg://`.
- **Fix:** if the ExternalSecret is not Ready, verify the Vault role/path in `secret-store.yaml`; once Vault resolves, ESO recreates the Secret and the pod recovers.

### Symptom: 502/503 through ingress
- **Check:** Rollout ready replicas (`kubectl -n chores-tracker get rollout` or `kubectl argo rollouts get rollout chores-tracker-backend -n chores-tracker`) and the `VirtualService` route in `virtualservice.yaml`. Note: `deployments.yaml` defines a `kind: Rollout` (Argo Rollouts canary), so plain `kubectl get deploy` will return nothing.
- **Fix:** scale/restart via the Rollout (e.g. `kubectl argo rollouts restart chores-tracker-backend -n chores-tracker`); confirm the mesh/ingress selectors match the stable/canary Services.

## How-to
### Deploy / update
Edit manifests in this directory, commit to a branch, open a PR. Argo CD syncs on merge to `main`, and Argo Rollouts progresses the canary through the steps defined in `deployments.yaml`.

### Rotate secrets
Update the value in Vault; ESO re-syncs within the `refreshInterval` in `external_secrets.yaml`. Restart the Rollout to pick up new env-injected values if needed.
