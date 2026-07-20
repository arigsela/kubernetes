---
type: "Kubernetes App Runbook"
title: "n8n — Runbook"
description: "Operational runbook for n8n: failure modes, checks, and fixes."
app: n8n
catalog_entity: n8n
kind: runbook
namespace: n8n
last_reviewed: 2026-07-10
status: current
tags: [automation, postgresql, webhooks]
sources:
  - base-apps/n8n/deployments.yaml
  - base-apps/n8n/external-secrets.yaml
  - base-apps/n8n/nginx-ingress-admin.yaml
  - base-apps/n8n/nginx-ingress-webhook.yaml
  - base-apps/n8n/pvc.yaml
---

# n8n runbook

## Failure modes

### Symptom: admin UI (`https://n8n.arigsela.com/`) times out or returns connection-refused from a normal browser, but works from the office/VPN
- **Check:** `nginx.ingress.kubernetes.io/whitelist-source-range` in `nginx-ingress-admin.yaml` only allows a fixed set of `/32` IPs plus `10.0.0.0/8`. Confirm the caller's current public IP isn't in that list: `curl -s ifconfig.me` from the affected machine, and compare against the annotation.
- **Fix:** this is IP allowlisting working as designed, not an outage. If a legitimate IP changed (e.g. new home/office egress IP), open a PR adding the new `/32` to `nginx.ingress.kubernetes.io/whitelist-source-range` in `base-apps/n8n/nginx-ingress-admin.yaml`. Do not widen the range to `0.0.0.0/0` — the admin UI is deliberately restricted while `/webhook*` and `/mcp-server` on `nginx-ingress-webhook.yaml` remain public.

### Symptom: webhook calls to `https://n8n.arigsela.com/webhook/...` (or `/webhook-test/...`, `/mcp-server/...`) return 404
- **Check:** `kubectl -n n8n get ingress` to confirm both `n8n-admin` and `n8n-webhook` exist and are pointed at the `n8n` Service/port `5678`; `kubectl -n n8n logs deploy/n8n --tail=100` for n8n-side routing errors. Also confirm from the n8n UI (once reachable) that the target workflow is **Active** — n8n only registers a webhook path once its workflow is activated, and production webhooks (`/webhook/...`) vs. test webhooks (`/webhook-test/...`) use different URLs by design.
- **Fix:** if the workflow is inactive, activate it in the editor (operational action, not a manifest change). If the ingress path itself is missing or misrouted, PR a fix to `base-apps/n8n/nginx-ingress-webhook.yaml`'s `rules[].http.paths`.

### Symptom: pod CrashLoopBackOff, or n8n runs but reports it cannot decrypt existing credentials/workflows
- **Check:** `kubectl -n n8n get pods` and `kubectl -n n8n logs deploy/n8n --tail=200`. First rule out normal slow startup — `livenessProbe`/`readinessProbe` in `deployments.yaml` use `initialDelaySeconds: 240`, so the pod is expected to take up to 4 minutes before probes even begin. If logs show Postgres connection errors, check the shared PostgreSQL instance is healthy: `kubectl -n postgresql get pods`. If logs show credential/decryption errors instead, check whether `N8N_ENCRYPTION_KEY` (`external-secrets.yaml`, Vault key `n8n`/`encryption-key`) was rotated in Vault — n8n cannot decrypt previously stored credentials/workflow secrets with a different encryption key than the one used to save them.
- **Fix:** for DB issues, resolve the shared `postgresql` app first (see its runbook) — n8n has no local fallback DB since `DB_TYPE=postgresdb`. For an encryption-key mismatch, restore the original Vault value at `n8n`/`encryption-key` rather than rotating it in place; rotating `N8N_ENCRYPTION_KEY` requires n8n's own credential re-encryption process, not a plain secret swap.

### Symptom: pod healthy but new workflow executions fail to write data / pod evicted for disk pressure
- **Check:** `kubectl -n n8n exec deploy/n8n -- df -h /home/node/.n8n` against the `n8n-pvc` (`pvc.yaml`, `5Gi`, `local-path`). This volume holds n8n's local `.n8n` state directory, not workflow execution history (that's in Postgres per `EXECUTIONS_DATA_SAVE_*` settings in `deployments.yaml`), but it can still fill from logs/binary temp files.
- **Fix:** PR a size increase to `spec.resources.requests.storage` in `base-apps/n8n/pvc.yaml` (note: `local-path` PVCs are not trivially resizable in-place — check the storage class's expansion support before relying on a live resize).

## How-to
### Deploy / update
Edit manifests here and PR; Argo CD auto-syncs on merge (`prune: true`, `selfHeal: true`).
