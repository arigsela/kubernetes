---
type: "Kubernetes App Runbook"
title: "model gateway — Runbook"
description: "Operational runbook for the model gateway: a blocked agent, raising a budget, reading spend, the gateway down, Postgres trouble, key rotation."
app: model-gateway
catalog_entity: model-gateway
kind: runbook
namespace: model-gateway
last_reviewed: 2026-10-23
status: draft
tags: [agents, llm-gateway, agentgateway, cost]
sources:
  - base-apps/model-gateway/budgets.yaml
  - base-apps/model-gateway/deployment.yaml
  - base-apps/model-gateway/service.yaml
  - base-apps/model-gateway/configmap.yaml
  - base-apps/postgresql/init-model-gateway-db.yaml
  - scripts/gen-model-budgets.py
  - scripts/provision-model-gateway-vault.sh
---

# model gateway — Runbook

Logs: `kubectl -n model-gateway logs deploy/model-gateway` (one access line per request: listener
`front` then `llm`, `http.status`, and for a refusal `reason=RateLimit`).

## An agent is blocked ("Agent blocked by its daily budget")

Its runs fail with `429 … budget_exceeded`. Other groups are unaffected.

1. Is it a loop or a busy day? Grafana, Agents → Agent Platform: the group's runs, tokens and cost.
2. The day's spend as the gateway counts it:
   `kubectl -n model-gateway port-forward deploy/model-gateway 15000:15000` then
   `curl -s localhost:15000/api/budgets/status` (one entry per group: used, remaining, exceeded,
   window). The admin port listens on 127.0.0.1 in the pod only.
3. To let it continue today, raise its budget (below). Otherwise it resumes at 00:00 UTC.

## Raise or lower a budget

Edit `base-apps/model-gateway/budgets.yaml`, run `scripts/gen-model-budgets.py --write`, open a pull
request. After the merge the config checksum rolls the pods; the new limit applies to today's count
(the count is kept, only the limit changes). There is no other override.

## The gateway is down ("Model gateway down")

Every group with `gateway: true` fails its model calls.

- `kubectl -n model-gateway get pods,events`. ImagePullBackOff on a new namespace: `ecr-auth` copies
  `ecr-registry` into it every 15 minutes; wait.
- CrashLoopBackOff: `kubectl -n model-gateway logs deploy/model-gateway --previous`. "failed to
  connect postgres database": see Postgres below. "environment variable not found": a group in
  `budgets.yaml` has no Vault key yet; run `scripts/provision-model-gateway-vault.sh`.
- ExternalSecrets not Ready (`kubectl -n model-gateway get externalsecret`): the Vault script has not
  run, or the role was changed.
- Emergency: point a group back at the reference ModelConfig (`anthropic-claude-<model>`) in its
  AgentTemplates until the gateway is back (only while `kagent-anthropic` still exists in kagent,
  spec §4).

## Postgres trouble

The gateway needs Postgres to start, not to serve: a running replica keeps enforcing on its own
counts and logs `failed to flush budget usage` every 5 seconds. A replica that restarts during the
outage cannot start. The day's counts that were not flushed are lost on a restart: the group can
spend up to its budget again that day.

## Rotate a virtual key

1. `scripts/provision-model-gateway-vault.sh --rotate <group>`.
2. External Secrets updates the kagent Secret and the gateway's env Secret within an hour (or
   annotate the ExternalSecrets with `force-sync` in a pull request).
3. The gateway reads its env once, at start: bump `model-gateway.homelab/keys-rotated-at` in
   `deployment.yaml` to today's date in a pull request, so the pods restart on the new key. Until
   then the group's calls are refused 401 (the egress already sends the new key).

The budget counter is keyed by the key's hash, so a new key starts its group's day at zero.

## The request log grows

`request-log-retention` (daily, 03:17 UTC) deletes `request_logs` rows older than 30 days. A failed
run: `kubectl -n model-gateway logs job/<name>`; the table only grows a few MB a day.
