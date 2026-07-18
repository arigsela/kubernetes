---
app: n8n
catalog_entity: n8n
kind: docs
namespace: n8n
last_reviewed: 2026-07-15
status: current
tags: [automation, postgresql, webhooks, alerting]
sources:
  - base-apps/n8n/deployments.yaml
  - base-apps/n8n/external-secrets.yaml
  - base-apps/n8n/secret-store.yaml
  - base-apps/n8n/pvc.yaml
  - base-apps/n8n/services.yaml
  - base-apps/n8n/nginx-ingress-admin.yaml
  - base-apps/n8n/nginx-ingress-webhook.yaml
  - base-apps/n8n/workflows-configmap.yaml
---

# n8n

## What it is
n8n (`n8nio/n8n:latest`) is a workflow-automation platform, deployed as a single-replica `Deployment` (`deployments.yaml`) in the `n8n` namespace, listening on container port `5678`.

## GitOps-managed workflows
Most workflows are authored in the UI and live only in n8n's database, but workflows that other cluster components depend on are declared in Git: `workflows-configmap.yaml` holds the workflow JSON, and the n8n Deployment's `import-workflows` **initContainer** imports and activates it with the n8n CLI at pod start (idempotent upsert by fixed workflow id). Currently one workflow is managed this way: **Grafana Alerts to Slack** — the `POST /webhook/grafana-alerts` endpoint that `base-apps/logging/grafana-alerting.yaml` delivers all Grafana alert notifications to; it formats the unified-alerting payload and posts to Slack `#oncall-alerts` using the `SLACK_BOT_TOKEN` env var (Vault key `n8n`, property `slack-bot-token` — deliberately an env var, not an n8n credential object, because credential objects are encrypted at rest and cannot be imported declaratively).

The import lives in an initContainer, not a per-sync PreSync Job, on purpose: n8n only registers webhook routes for active workflows at **startup**, so tying the import to the pod lifecycle guarantees the running server always has the webhook registered. (The old PreSync Job re-ran on every app sync and, when a sync didn't restart the pod — e.g. an ingress-only change — left the workflow active in the DB but unregistered in the live server, 404ing the webhook.) To apply a workflow **edit**, the pod must roll: bump the `checksum/workflows` pod annotation in `deployments.yaml` (sha256 of `grafana-alerts.json`), or `kubectl rollout restart deploy/n8n -n n8n`.

## Architecture & data flow
The `Deployment` runs one pod on nodes labeled `node.kubernetes.io/workload: application`, with `fsGroup`/`runAsUser`/`runAsGroup` all set to `1000` so it can write to its mounted volume. A `n8n-pvc` `PersistentVolumeClaim` (`pvc.yaml`, `5Gi`, `storageClassName: local-path`) is mounted at `/home/node/.n8n` — this holds n8n's local state directory (settings file, etc.), **not** workflow/execution data: `DB_TYPE` is set to `postgresdb` (`deployments.yaml`), so n8n persists workflows, credentials, and execution history in the shared PostgreSQL instance (`base-apps/postgresql`) via `DB_POSTGRESDB_HOST/PORT/DATABASE/USER/PASSWORD` env vars sourced from the `n8n-secrets` Secret. The `n8n` `Service` (`services.yaml`, ClusterIP, port `5678`) fronts the pod for both ingresses below.

## Secrets
`secret-store.yaml` defines a `SecretStore` named `vault-backend` pointing at `http://vault.vault.svc.cluster.local:8200`, KV v2 path `k8s-secrets`, Kubernetes auth with role `n8n`. `external-secrets.yaml`'s `n8n-secrets` `ExternalSecret` resolves most keys (`encryption-key`, `db-host`, `db-port`, `db-name`, `db-user`, `webhook-url`, `basic-auth-user`, `basic-auth-password`) from Vault path `n8n`, but `db-password` is resolved from Vault path `postgresql` property `n8n-password` — the same credential the shared PostgreSQL app provisions for n8n (see `base-apps/postgresql/external-secrets.yaml`'s `n8n-user`/`n8n-password` keys), confirming n8n's database is the shared in-cluster PostgreSQL instance, not an app-local database. `N8N_ENCRYPTION_KEY` (also Vault-sourced) encrypts stored credentials at rest — losing or rotating it without care makes existing workflow credentials unreadable. Admin UI access also requires HTTP Basic Auth (`N8N_BASIC_AUTH_ACTIVE=true`, `N8N_BASIC_AUTH_USER`/`PASSWORD` from Vault).

## Networking — two ingress endpoints
- **Admin UI** (`nginx-ingress-admin.yaml`, `n8n-admin`): host `n8n.arigsela.com`, path `/`, TLS via `letsencrypt-prod`. Restricted by `nginx.ingress.kubernetes.io/whitelist-source-range` to a small set of home/office IPs plus `10.0.0.0/8` — the workflow editor and admin UI are not publicly reachable.
- **Webhook endpoint** (`nginx-ingress-webhook.yaml`, `n8n-webhook`): same host `n8n.arigsela.com`, paths `/webhook`, `/webhook-test`, and `/mcp-server` — deliberately **not** IP-restricted, since external services (Slack, etc.) and n8n's MCP server integration need to reach it. Security here relies on n8n's per-workflow unique webhook URLs/tokens rather than network restriction.

Both ingresses route to the same `n8n` Service/port `5678`; they exist only to apply different rate-limit and IP-whitelist annotations to different paths of the same app.

## Where config lives
- Workload: `deployments.yaml` (env, probes on `/healthz`, resources, volume mount).
- Persistence: `pvc.yaml` (local `/home/node/.n8n` state) + the shared PostgreSQL instance for workflow/execution data.
- Secrets: `secret-store.yaml` + `external-secrets.yaml` (Vault-backed, path `n8n` plus the shared `postgresql` path for `n8n-password`).
- Networking: `services.yaml` (ClusterIP :5678) + `nginx-ingress-admin.yaml` (IP-restricted UI) + `nginx-ingress-webhook.yaml` (public webhook/MCP paths).
