---
app: chores-tracker-backend
catalog_entity: chores-tracker-backend
kind: runbook
namespace: chores-tracker
last_reviewed: 2026-07-08
status: current
tags: [fastapi, mysql, jwt]
sources:
  - base-apps/chores-tracker-backend/deployments.yaml
  - base-apps/chores-tracker-backend/external_secrets.yaml
---

# chores-tracker-backend — Runbook

## Failure modes
### Symptom: pod CrashLoopBackOff on startup
- **Check:** `kubectl -n chores-tracker get externalsecret,secret` — confirm the ExternalSecret synced and the target Secret exists.
- **Fix:** if the ExternalSecret is not Ready, verify the Vault role/path in `secret-store.yaml`; once Vault resolves, ESO recreates the Secret and the pod recovers.

### Symptom: 502/503 through ingress
- **Check:** Deployment ready replicas (`kubectl -n chores-tracker get deploy`) and the `VirtualService` route in `virtualservice.yaml`.
- **Fix:** scale/restart the Deployment; confirm the mesh/ingress selectors match the Service.

## How-to
### Deploy / update
Edit manifests in this directory, commit to a branch, open a PR. Argo CD syncs on merge to `main`.

### Rotate secrets
Update the value in Vault; ESO re-syncs within the `refreshInterval` in `external_secrets.yaml`. Restart the Deployment to pick up new env-injected values if needed.
