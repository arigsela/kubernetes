---
type: "Kubernetes App Guide"
title: "n8n"
description: "Workflow automation platform (shared PostgreSQL, Vault, admin UI + public webhooks)"
app: n8n
catalog_entity: n8n
kind: docs
namespace: n8n
last_reviewed: 2026-09-30
status: stable
tags: [automation, postgresql, webhooks, alerting]
sources:
  - base-apps/n8n/deployments.yaml
  - base-apps/n8n/external-secrets.yaml
  - base-apps/n8n/secret-store.yaml
  - base-apps/n8n/pvc.yaml
  - base-apps/n8n/services.yaml
  - base-apps/n8n/httproute.yaml
  - base-apps/n8n/certificate.yaml
  - base-apps/n8n/reference-grant.yaml
  - base-apps/n8n/workflows-configmap.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - base-apps/istio-waf/wasmplugin.yaml
  - n8n-workflows/newsletter-digest-send.json
---

# n8n

## What it is
n8n (`n8nio/n8n`, version pinned in `deployments.yaml`, `2.30.5` at review, same tag on the init container) is a workflow-automation platform, deployed as a single-replica `Deployment` (`deployments.yaml`) in the `n8n` namespace, listening on container port `5678`.

## GitOps-managed workflows
Most workflows are authored in the UI and live only in n8n's database, but workflows that other cluster components depend on are declared in Git: `workflows-configmap.yaml` holds the workflow JSON, and the n8n Deployment's `import-workflows` **initContainer** imports and activates it with the n8n CLI at pod start (idempotent upsert by fixed workflow id). Four workflows are managed this way:

- **Grafana Alerts to Slack** (`grafana-alerts.json`, id `grafana-alerts-slack`) — the `POST /webhook/grafana-alerts` endpoint that `base-apps/logging/grafana-alerting.yaml` delivers all Grafana alert notifications to; it formats the unified-alerting payload and posts to Slack `#oncall-alerts` using the `SLACK_BOT_TOKEN` env var (Vault key `n8n`, property `slack-bot-token` — deliberately an env var, not an n8n credential object, because credential objects are encrypted at rest and cannot be imported declaratively).
- **WAN IP Monitor Notifications** (`wan-ip-rotated.json`, id `wan-ip-rotated`) — the `POST /webhook/wan-ip-rotated` endpoint that `base-apps/wan-ip-monitor` posts to, for both a completed rotation (`"event": "wan-ip-reconciled"`) and a crashed run (`"event": "wan-ip-monitor-failed"`, with `error_type`/`error`). A single Webhook node, no routing: the value is that the POST lands in n8n's execution list instead of 404ing. It is the **only** failure signal that job has, and its `notify()` swallows delivery errors by design — so if this workflow is inactive or unregistered, failures go completely silent.
- **Image Vulnerability Scan Report** (`image-scan-report.json`, id `image-scan-report`) — the `POST /webhook/image-scan-report` endpoint the image-scan Argo Workflow (`base-apps/argo-workflow-tasks/image-scan.yaml`) posts its summary to; it formats the counts and posts to Slack `#oncall-alerts` with the same `SLACK_BOT_TOKEN`.
- **Interview Janitor to Slack** (`interview-janitor.json`, id `interview-janitor`) — the `POST /webhook/interview-janitor` endpoint the interview-labs janitor (a GitHub Actions workflow in a private repo) posts its findings to: stale lab stacks it destroyed, leftovers, and what the weekly aws-nuke dry run would remove. It checks the caller's `X-Janitor-Token` header against the `INTERVIEW_JANITOR_TOKEN` env var (Vault key `n8n`, property `interview-janitor-token`; an unset or short token rejects every call), formats the title and text, posts to Slack `#oncall-alerts`, and fails unless Slack answers `ok`. The Webhook node answers with the last node, so a bad token or a Slack error reaches the caller as a 500 and fails its run.

The first three callers use the in-cluster Service URL (`http://n8n.n8n.svc.cluster.local:5678/webhook/...`), so they bypass the Gateway, its allow-list and the WAF. The interview janitor runs on GitHub's runners, so it calls `https://n8n.arigsela.com/webhook/interview-janitor`: the public webhook path, through the WAF, with body inspection on.

The import lives in an initContainer, not a per-sync PreSync Job, on purpose: n8n only registers webhook routes for active workflows at **startup**, so tying the import to the pod lifecycle guarantees the running server always has the webhook registered. (The old PreSync Job re-ran on every app sync and, when a sync didn't restart the pod, left the workflow active in the DB but unregistered in the live server, 404ing the webhook.) To apply a workflow **edit — or to add a new workflow —** the pod must roll: bump the `checksum/workflows` pod annotation in `deployments.yaml` (sha256 of every workflow in the ConfigMap, concatenated in sorted-key order; the recompute one-liner is in the annotation's own comment), or `kubectl rollout restart deploy/n8n -n n8n`. Adding a workflow also means adding its `n8n import:workflow` / `n8n update:workflow --active=true` pair to the initContainer's `args`.

UI-authored workflows that matter are backed up as exports under `n8n-workflows/` at the repo root (e.g. `newsletter-digest-send.json`). Those files are **not** imported by anything; the live copy is in n8n's database, so re-export after editing one in the UI.

## Architecture & data flow
The `Deployment` runs one pod on nodes labeled `node.kubernetes.io/workload: application`, with `fsGroup`/`runAsUser`/`runAsGroup` all set to `1000` so it can write to its mounted volume. A `n8n-pvc` `PersistentVolumeClaim` (`pvc.yaml`, `5Gi`, `storageClassName: local-path`, `Prune=false` so an Argo prune can't delete it) is mounted at `/home/node/.n8n` — this holds n8n's local state directory (settings file, etc.), **not** workflow/execution data: `DB_TYPE` is set to `postgresdb` (`deployments.yaml`), so n8n persists workflows, credentials, and execution history in the shared PostgreSQL instance (`base-apps/postgresql`) via `DB_POSTGRESDB_HOST/PORT/DATABASE/USER/PASSWORD` env vars sourced from the `n8n-secrets` Secret. The `n8n` `Service` (`services.yaml`, ClusterIP, port `5678`) fronts the pod.

Both probes hit `/healthz/readiness`, which checks the database connection (plain `/healthz` keeps answering 200 while every real request fails with `503 Database is not ready!`, and n8n does not reconnect on its own). Liveness allows 18 × 10s = 3 minutes of DB outage before restarting the pod, enough to ride out a node reboot; readiness pulls it from the Service after 30s. Both start after `initialDelaySeconds: 240`.

`N8N_BLOCK_ENV_ACCESS_IN_NODE=false` is set so workflow expressions can read `$env.SLACK_BOT_TOKEN`. The trade-off: anyone who can edit a workflow can read the pod's whole environment, including the DB password and `N8N_ENCRYPTION_KEY`. It is accepted because the editor is IP-restricted and single-operator.

## Secrets
`secret-store.yaml` defines a `SecretStore` named `vault-backend` pointing at `http://vault.vault.svc.cluster.local:8200`, KV v2 path `k8s-secrets`, Kubernetes auth with role `n8n` as the namespace's `default` ServiceAccount. `external-secrets.yaml`'s `n8n-secrets` `ExternalSecret` resolves most keys (`encryption-key`, `db-host`, `db-port`, `db-name`, `db-user`, `webhook-url`, `basic-auth-user`, `basic-auth-password`, `slack-bot-token`, `interview-janitor-token`) from Vault path `n8n`, but `db-password` is resolved from Vault path `postgresql` property `n8n-password` — the same credential the shared PostgreSQL app provisions for n8n (see `base-apps/postgresql/external-secrets.yaml`). `webhook-url` is synced but unused: `WEBHOOK_URL` is a literal in `deployments.yaml`. `N8N_ENCRYPTION_KEY` encrypts stored credentials at rest — losing or rotating it without care makes existing workflow credentials unreadable.

The Deployment still sets `N8N_BASIC_AUTH_ACTIVE=true` with `N8N_BASIC_AUTH_USER`/`PASSWORD` from Vault. n8n dropped its built-in basic auth in 1.0 in favour of user accounts, so on 2.x don't count on these as a control; the editor's own login and the IP allow-list are what protect it.

Credentials created in the n8n UI (e.g. the digest webhook token and the SMTP account, see the runbook) live only in n8n's encrypted database, with no Vault copy.

## Networking
`n8n.arigsela.com` is one Gateway API `HTTPRoute`, `n8n` (`httproute.yaml`, listener `https-n8n` on the shared `main` Gateway), sending every path to `n8n:5678`. TLS is `certificate.yaml` (`n8n-tls`, issuer `letsencrypt-route53`, DNS-01), read by the Gateway through `reference-grant.yaml`.

The admin-vs-webhook split is enforced by path in `base-apps/istio-ingress/authorizationpolicy.yaml`, not by routing:
- **Public:** `/webhook`, `/webhook-test`, `/mcp-server` and everything under them have a rule with no source restriction, because external services can't be allow-listed. Each workflow authenticates its own callers (e.g. Header Auth).
- **Restricted:** everything else on the host (the editor and admin UI) is limited to the allow-listed addresses.

n8n is also one of the three hosts inside the Coraza WAF's scope (`base-apps/istio-waf/wasmplugin.yaml`, rule 9000), enforcing since 2026-08-11. Request bodies are inspected only on the public paths (rule 9010), parsed as JSON when sent as JSON (9012), except `/webhook/newsletter-digest`, whose body is exempt (9013). The nginx-era per-path rate limits, body-size limit and timeouts did not carry over.

## Where config lives
- Workload: `deployments.yaml` (image tag, env, probes on `/healthz/readiness`, resources, volume mount, `checksum/workflows`).
- Workflows: `workflows-configmap.yaml` (Git-managed) and n8n's database (everything else); exports in `n8n-workflows/`.
- Persistence: `pvc.yaml` (local `/home/node/.n8n` state) + the shared PostgreSQL instance for workflow/execution data.
- Secrets: `secret-store.yaml` + `external-secrets.yaml` (Vault-backed, path `n8n` plus the shared `postgresql` path for `n8n-password`).
- Networking: `services.yaml` (ClusterIP :5678), `httproute.yaml`, `certificate.yaml`, `reference-grant.yaml`; access rules in `base-apps/istio-ingress/authorizationpolicy.yaml`; WAF rules in `base-apps/istio-waf/wasmplugin.yaml`.
