---
type: "Kubernetes App Guide"
title: "n8n"
description: "Workflow automation platform (shared PostgreSQL, Vault, admin UI + public webhooks)"
app: n8n
catalog_entity: n8n
kind: docs
namespace: n8n
last_reviewed: 2026-10-28
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
  - base-apps/n8n/bug-investigator-workflow-configmap.yaml
  - base-apps/n8n/bug-investigator-secrets.yaml
  - base-apps/n8n/github-read-mcp.yaml
  - base-apps/n8n/github-read-mcp-secrets.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - base-apps/istio-waf/wasmplugin.yaml
---

# n8n

## What it is
n8n (`n8nio/n8n`, version pinned in `deployments.yaml`, `2.45.5` since 2026-10-28 (§T.151), same tag on the init container) is a workflow-automation platform, deployed as a single-replica `Deployment` (`deployments.yaml`) in the `n8n` namespace, listening on container port `5678`.

## GitOps-managed workflows
Every active workflow is declared in Git: `workflows-configmap.yaml` holds the workflow JSON, and the n8n Deployment's `import-workflows` **initContainer** unpublishes, imports and publishes it with the n8n CLI at pod start (idempotent upsert by fixed workflow id). Six workflows are managed this way:

- **Grafana Alerts to Slack** (`grafana-alerts.json`, id `grafana-alerts-slack`) — the `POST /webhook/grafana-alerts` endpoint that `base-apps/logging/grafana-alerting.yaml` delivers all Grafana alert notifications to; it formats the unified-alerting payload and posts to Slack `#oncall-alerts` using the `SLACK_BOT_TOKEN` env var (Vault key `n8n`, property `slack-bot-token` — deliberately an env var, not an n8n credential object, because credential objects are encrypted at rest and cannot be imported declaratively).
- **WAN IP Monitor Notifications** (`wan-ip-rotated.json`, id `wan-ip-rotated`) — the `POST /webhook/wan-ip-rotated` endpoint that `base-apps/wan-ip-monitor` posts to, for both a completed rotation (`"event": "wan-ip-reconciled"`) and a crashed run (`"event": "wan-ip-monitor-failed"`, with `error_type`/`error`). A single Webhook node, no routing: the value is that the POST lands in n8n's execution list instead of 404ing. It is the **only** failure signal that job has, and its `notify()` swallows delivery errors by design — so if this workflow is inactive or unregistered, failures go completely silent.
- **Image Vulnerability Scan Report** (`image-scan-report.json`, id `image-scan-report`) — the `POST /webhook/image-scan-report` endpoint the image-scan Argo Workflow (`base-apps/argo-workflow-tasks/image-scan.yaml`) posts its summary to; it formats the counts and posts to Slack `#oncall-alerts` with the same `SLACK_BOT_TOKEN`.
- **Interview Janitor to Slack** (`interview-janitor.json`, id `interview-janitor`) — the `POST /webhook/interview-janitor` endpoint the interview-labs janitor (a GitHub Actions workflow in a private repo) calls on every run. It checks the caller's `X-Janitor-Token` header against the `INTERVIEW_JANITOR_TOKEN` env var (Vault key `n8n`, property `interview-janitor-token`; an unset or short token rejects every call) and records the check-in in the workflow's static data. Runs with findings (stale lab stacks, leftovers, anything the weekly aws-nuke dry run would remove) are posted to Slack `#oncall-alerts`, and the workflow fails unless Slack answers `ok`; all-clear runs (`"post": false`) are only recorded. A Schedule trigger every 6 hours is the dead-man's switch: after 30 hours without a check-in it posts "janitor silent" to `#oncall-alerts`, at most once a day. Two more Schedule triggers make n8n the janitor's scheduler (GitHub's own `schedule:` never fired in that repo): daily at 11:00 UTC and Sundays at 12:00 UTC (the workflow's timezone is UTC) it starts the janitor with a GitHub `workflow_dispatch` call (`task=daily` / `task=nuke-dry-run`, on `main`), authenticated by `INTERVIEW_JANITOR_GH_TOKEN` (Vault key `n8n`, property `interview-janitor-gh-token`: a fine-grained token with Actions read and write on `arigsela/interview-labs` only). Any answer but 2xx is posted to `#oncall-alerts` ("n8n could not start the janitor"); a network failure fails the execution and the watchdog catches it. The Webhook node answers with the last node, so a bad token or a Slack error reaches the caller as a 500 and fails its run.
- **Agent Findings to Slack** (`agent-findings.json`, id `agent-findings`) — the `POST /webhook/agent-findings` endpoint the dispatcher (`base-apps/dispatcher/`) posts an agent's investigation to; formats it for `#oncall-alerts` (the `ui` link line is only added when the dispatcher sets it; it no longer does, a dispatcher-owned session cannot be opened in the kagent UI). A finding that carries `decision {ctx, nonce}` (a registered pending restart, §T.120) is posted as Block Kit with Approve / Reject buttons whose value is `d:<ctx>:<nonce>`; the Slack bridge (`base-apps/slack-bridge/`) receives the click, since it is the same Slack app. In-cluster caller only.
- **Newsletter Digest — Send** (`newsletter-digest-send.json`, id `qX9W779auOovEQe9`, its original UI id) — the `POST /webhook/newsletter-digest` endpoint the `newsletter-digest-n8n` and `feed-digest` skills send their digest email through. Header Auth on the Webhook node, then a check that `subject`, `to` and `html_body` are all non-empty (400 if not), then Send Email over SMTP and a 200 with the message id. It is the one GitOps workflow that uses n8n credential objects: the Header Auth credential "Newsletter Digest Webhook Token" and "SMTP account", referenced by id. Those stay in n8n's encrypted database (see the runbook); only the workflow is in Git. It was authored in the UI and kept as a hand export until 2026-10-09.

The first three callers use the in-cluster Service URL (`http://n8n.n8n.svc.cluster.local:5678/webhook/...`), so they bypass the Gateway, its allow-list and the WAF. The interview janitor (on GitHub's runners) and the digest skills run outside the cluster, so they call `https://n8n.arigsela.com/webhook/...`: the public webhook path, through the WAF. Each has its body exempt from inspection in `base-apps/istio-waf/wasmplugin.yaml`: rule `9014` for the janitor (the reports change every run and tripped the RCE rules) and `9013` for the digest (an HTML email); headers and URI are still inspected.

The import lives in an initContainer, not a per-sync PreSync Job, on purpose: n8n only registers webhook routes for active workflows at **startup**, so tying the import to the pod lifecycle guarantees the running server always has the webhook registered. (The old PreSync Job re-ran on every app sync and, when a sync didn't restart the pod, left the workflow active in the DB but unregistered in the live server, 404ing the webhook.) To apply a workflow **edit — or to add a new workflow —** the pod must roll: bump the `checksum/workflows` pod annotation in `deployments.yaml` (sha256 of every workflow in the ConfigMap, concatenated in sorted-key order; the recompute one-liner is in the annotation's own comment), or `kubectl rollout restart deploy/n8n -n n8n`. Adding a workflow also means adding its `<ConfigMap key without .json>:<workflow id>` entry to the loop in the initContainer's `args`. The loop runs `n8n unpublish:workflow`, `n8n import:workflow` and `n8n publish:workflow` for each entry (§T.151). Importing over an active workflow makes the CLI deactivate it through a permission check, which fails ("You do not have permission to deactivate this workflow") on a database whose server has never started, such as a fresh install whose init is retried; unpublishing first avoids it, and `update:workflow --active` is deprecated since 2.45. Each CLI call boots n8n, about 47 s on these nodes, so six workflows keep a new pod in `Init` for about 14 minutes; the old pod serves until the new one is Ready.

Change these workflows here, not in the editor: the next pod start re-imports the ConfigMap and overwrites edits made in the UI. A workflow created in the UI lives only in n8n's database until it is added here. The `n8n-workflows/` export folder is gone (2026-10-09): Newsletter Digest moved into the ConfigMap, and the three feed-aggregator workflows (Feed Data Ingestion, Claude Digest Generator, DevOps Daily Dispatch), inactive since March 2026 and replaced by the `feed-digest` skill, were retired. Git history has their exports.

## Bug investigator (POC)
An n8n version of the Taskboard bug investigator (§T.152, plan `docs/superpowers/plans/2026-10-28-n8n-bug-investigator-poc.md`) runs beside `kagent/bug-investigator`, so both answer the same `bug` issues and their comments can be compared with `scripts/compare-bug-investigations.py`. It is a proof of concept, active since 2026-10-28 for issues created from `NOT_BEFORE` (2026-10-28T17:21:00Z) on; the switch is `ACTIVE` in `scripts/gen-n8n-bug-investigator-workflow.py`.

- **Two workflows**, generated into `bug-investigator-workflow-configmap.yaml` and imported by their own block of the init script (`# BEGIN/END bug-investigator POC`; a failure there cannot stop the other workflows or the pod):
  - `bug-investigator-n8n-poc` (parent): every 2 minutes, **Pick issue**, then the child. Its successful runs are not saved, so idle runs leave nothing in the database.
  - `bug-investigator-n8n-poc-run` (child): **Gather facts** → **Investigate** (AI Agent on Anthropic `claude-sonnet-5-5`, 16000 tokens, at most 40 iterations, prompt caching for 5 minutes, the `bug-investigator` AgentTemplate's systemPrompt verbatim) → **Verify** → **Render and post** → **Finish**; the agent's error output goes to **Agent failed** → **Finish**. Every run is saved; 900 s timeout. A sub-workflow must be published to be called, and only the server's start records a CLI publish, which the pod's order (init, then server) gives.
- **The same checks as kagent.** The Code nodes run JavaScript ports of the bug-investigator core (`scripts/n8n-bug-investigator/lib/`): facts gathered over the GitHub API (markdown identical to the core's), every evidence link re-checked before posting, the core's comment format. `tests/n8n_bug_investigator/` runs the ports against the Python core, and the workflows in the pinned n8n image.
- **Built from settings, and handed over as a zip.** `scripts/n8n-bug-investigator/builder.py` builds both workflows from one settings dict; this homelab's values are in the generator and `scripts/n8n-bug-investigator/lib/settings.js`. `scripts/n8n-bug-investigator/make-package.py --out <zip>` packs the same code for someone else's n8n: `package/` (README, `config.example.toml`, `build.py`, a docker-compose for the read-only GitHub MCP server, and ADAPTERS.md for other ticket and telemetry systems) with neutral defaults in place of `lib/settings.js`.
- **Tools:** the AgentTemplate's 11. Six GitHub reads go through `n8n-github-read-mcp` (`github-read-mcp.yaml`, read-only on the server side); five telemetry tools go through kagent's `taskboard-telemetry-mcp`, whose scope check keeps every query in namespace `taskboard`. n8n prefixes each tool with its node's name (`github_…`, `telemetry_…`), which kagent's agent does not see.
- **Its own labels and claim; kagent's are never touched:** `n8n-investigating` (the lock), `n8n-investigated`, `n8n-investigation-failed`. A 👀 reaction from the token's user claims an issue (GitHub answers 201 once), so two pods overlapping in a rollout cannot both investigate it. One investigation at a time; issues opened before `NOT_BEFORE` are never picked; an issue locked 3 times without finishing is failed. Comments carry `<!-- bug-investigator mode=n8nagent -->`.
- **Credentials:** `BUG_INVESTIGATOR_GH_TOKEN` (PAT A: Issues read/write and Contents, Pull requests, Metadata read on `arigsela/taskboard`) from `bug-investigator-secrets.yaml`, and PAT B (read only) for the MCP server from `github-read-mcp-secrets.yaml`. Each has its own Vault path, policy, role and ESO ServiceAccount (`scripts/provision-n8n-bug-investigator-vault.sh`); neither is in `n8n-secrets`. The Anthropic key is the UI-created n8n credential **Anthropic — bug investigator POC**, referenced by id, with a spend limit on its Anthropic workspace.
- **What n8n does not get** (kagent does): a sandbox per run, the admission and capability checks, the action record, the Agent Platform dashboards and the eval gate, and a budget at the model gateway. PAT A sits in n8n's environment, readable by anyone who can edit a workflow (`N8N_BLOCK_ENV_ACCESS_IN_NODE=false`).
- **Rollout cost:** the POC adds 4 CLI calls to the init container (about 3 minutes), 6 once active.

## Architecture & data flow
The `Deployment` runs one pod on nodes labeled `node.kubernetes.io/workload: application`, with `fsGroup`/`runAsUser`/`runAsGroup` all set to `1000` so it can write to its mounted volume. A `n8n-pvc` `PersistentVolumeClaim` (`pvc.yaml`, `5Gi`, `storageClassName: local-path`, `Prune=false` so an Argo prune can't delete it) is mounted at `/home/node/.n8n` — this holds n8n's local state directory (settings file, etc.), **not** workflow/execution data: `DB_TYPE` is set to `postgresdb` (`deployments.yaml`), so n8n persists workflows, credentials, and execution history in the shared PostgreSQL instance (`base-apps/postgresql`) via `DB_POSTGRESDB_HOST/PORT/DATABASE/USER/PASSWORD` env vars sourced from the `n8n-secrets` Secret. The `n8n` `Service` (`services.yaml`, ClusterIP, port `5678`) fronts the pod.

Both probes hit `/healthz/readiness`, which checks the database connection (plain `/healthz` keeps answering 200 while every real request fails with `503 Database is not ready!`, and n8n does not reconnect on its own). Liveness allows 18 × 10s = 3 minutes of DB outage before restarting the pod, enough to ride out a node reboot; readiness pulls it from the Service after 30s. Both start after `initialDelaySeconds: 240`.

A rollout takes about 20 minutes (the init container's 3 CLI calls per workflow, then the probe delay), so the Deployment sets `progressDeadlineSeconds: 1800`; with the default 600 s every rollout was marked `ProgressDeadlineExceeded` and Argo showed n8n `Degraded` until the new pod was Ready.

`N8N_BLOCK_ENV_ACCESS_IN_NODE=false` is set so workflow expressions can read `$env.SLACK_BOT_TOKEN`. The trade-off: anyone who can edit a workflow can read the pod's whole environment, including the DB password and `N8N_ENCRYPTION_KEY`. It is accepted because the editor is IP-restricted and single-operator.

## Secrets
`secret-store.yaml` defines a `SecretStore` named `vault-backend` pointing at `http://vault.vault.svc.cluster.local:8200`, KV v2 path `k8s-secrets`, Kubernetes auth with role `n8n` as the namespace's `default` ServiceAccount. `external-secrets.yaml`'s `n8n-secrets` `ExternalSecret` resolves most keys (`encryption-key`, `db-host`, `db-port`, `db-name`, `db-user`, `webhook-url`, `basic-auth-user`, `basic-auth-password`, `slack-bot-token`, `interview-janitor-token`, `interview-janitor-gh-token`) from Vault path `n8n`, but `db-password` is resolved from Vault path `postgresql` property `n8n-password` — the same credential the shared PostgreSQL app provisions for n8n (see `base-apps/postgresql/external-secrets.yaml`). `webhook-url` is synced but unused: `WEBHOOK_URL` is a literal in `deployments.yaml`. `N8N_ENCRYPTION_KEY` encrypts stored credentials at rest — losing or rotating it without care makes existing workflow credentials unreadable.

The Deployment still sets `N8N_BASIC_AUTH_ACTIVE=true` with `N8N_BASIC_AUTH_USER`/`PASSWORD` from Vault. n8n dropped its built-in basic auth in 1.0 in favour of user accounts, so on 2.x don't count on these as a control; the editor's own login and the IP allow-list are what protect it.

Credentials created in the n8n UI (e.g. the digest webhook token and the SMTP account, see the runbook) live only in n8n's encrypted database, with no Vault copy.

The bug-investigator POC's two GitHub tokens do not use `vault-backend` or the `n8n` key: each has its own `SecretStore` (`vault-n8n-bug-investigator`, `vault-n8n-github-read-mcp`), ESO ServiceAccount and Vault role, reading only `k8s-secrets/n8n-bug-investigator` (property `gh-token`) or `k8s-secrets/n8n-github-read-mcp` (property `github-token`).

## Networking
`n8n.arigsela.com` is one Gateway API `HTTPRoute`, `n8n` (`httproute.yaml`, listener `https-n8n` on the shared `main` Gateway), sending every path to `n8n:5678`. TLS is `certificate.yaml` (`n8n-tls`, issuer `letsencrypt-route53`, DNS-01), read by the Gateway through `reference-grant.yaml`.

The admin-vs-webhook split is enforced by path in `base-apps/istio-ingress/authorizationpolicy.yaml`, not by routing:
- **Public:** `/webhook`, `/webhook-test`, `/mcp-server` and everything under them have a rule with no source restriction, because external services can't be allow-listed. Each workflow authenticates its own callers (e.g. Header Auth).
- **Restricted:** everything else on the host (the editor and admin UI) is limited to the allow-listed addresses.

In-cluster, `n8n-github-read-mcp` (port 3000, no auth) has a NetworkPolicy that admits only the n8n pod and Prometheus (port 15020); namespace `n8n` is not in the mesh. The POC's agent and verify also call `taskboard-telemetry-mcp.kagent:3000`, which the kagent waypoint lets through (only the controller's port is restricted).

n8n is also one of the three hosts inside the Coraza WAF's scope (`base-apps/istio-waf/wasmplugin.yaml`, rule 9000), enforcing since 2026-08-11. Request bodies are inspected only on the public paths (rule 9010), parsed as JSON when sent as JSON (9012), except `/webhook/newsletter-digest`, whose body is exempt (9013). The nginx-era per-path rate limits, body-size limit and timeouts did not carry over.

## Where config lives
- Workload: `deployments.yaml` (image tag, env, probes on `/healthz/readiness`, resources, volume mount, `checksum/workflows`).
- Workflows: `workflows-configmap.yaml` (every active workflow). Credentials created in the UI live only in n8n's database.
- Persistence: `pvc.yaml` (local `/home/node/.n8n` state) + database `n8n` on the plain `postgresql` Deployment (Service `postgresql.postgresql`, not the CNPG cluster; confirmed 2026-10-28 from the pod's open connection) for workflow/execution data. That instance has no scheduled backup (`recovery/CLUSTER-RECOVERY.md`), so dump `n8n` before an upgrade (runbook, "Upgrade n8n").
- Secrets: `secret-store.yaml` + `external-secrets.yaml` (Vault-backed, path `n8n` plus the shared `postgresql` path for `n8n-password`).
- Bug investigator POC: `bug-investigator-workflow-configmap.yaml` (generated, with the init block and `checksum/bug-investigator-workflow` in `deployments.yaml`), `github-read-mcp.yaml`, `bug-investigator-secrets.yaml`, `github-read-mcp-secrets.yaml`; sources in `scripts/n8n-bug-investigator/` and `scripts/gen-n8n-bug-investigator-workflow.py`.
- Networking: `services.yaml` (ClusterIP :5678), `httproute.yaml`, `certificate.yaml`, `reference-grant.yaml`; access rules in `base-apps/istio-ingress/authorizationpolicy.yaml`; WAF rules in `base-apps/istio-waf/wasmplugin.yaml`.
