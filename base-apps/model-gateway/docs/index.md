---
type: "Kubernetes App Guide"
title: "model gateway"
description: "Daily model budget per kagent agent group: a standalone agentgateway in front of the Anthropic API holds the real key, maps each group's virtual key to its budget and refuses (429) once it is spent"
app: model-gateway
catalog_entity: model-gateway
kind: docs
namespace: model-gateway
last_reviewed: 2026-10-23
status: draft
tags: [agents, llm-gateway, agentgateway, cost]
sources:
  - base-apps/model-gateway.yaml
  - base-apps/model-gateway/budgets.yaml
  - base-apps/model-gateway/configmap.yaml
  - base-apps/model-gateway/deployment.yaml
  - base-apps/model-gateway/service.yaml
  - base-apps/model-gateway/network-policy.yaml
  - base-apps/model-gateway/external-secrets.yaml
  - base-apps/model-gateway/env-external-secret.yaml
  - base-apps/model-gateway/request-log-retention.yaml
  - base-apps/kagent/model-gateway-keys.yaml
  - base-apps/postgresql/init-model-gateway-db.yaml
  - scripts/gen-model-budgets.py
  - scripts/model_budgets.py
  - scripts/provision-model-gateway-vault.sh
  - tests/model-gateway/proof/README.md
  - docs/superpowers/specs/2026-10-23-model-gateway-budgets-design.md
---

# model gateway

Every kagent agent group has a daily model budget. Once the group has spent it, the model gateway
refuses the group's model calls (HTTP 429, `budget_exceeded`) until 00:00 UTC; every other group
keeps working. Before this, one Anthropic workspace limit covered every agent, and hitting it
(2026-10-14, §T.119) stopped them all. Design: `docs/superpowers/specs/2026-10-23-model-gateway-budgets-design.md`
(§T.137, §V.108).

## How a model call flows

1. An agent runs in a Substrate sandbox. Its ModelConfig's `baseUrl` is
   `http://model-gateway.model-gateway.svc.cluster.local:8080` and its `apiKeySecret` is its
   group's `model-gateway-key-<group>` (base-apps/kagent/model-gateway-keys.yaml).
2. Substrate's egress (`atenet-egress`, ate-system) injects that group's **virtual key** as
   `x-api-key`. The sandbox never holds it, so it cannot present another group's.
3. The gateway's front listener (8080) passes the call to its llm listener (127.0.0.1:8081),
   which maps the virtual key to its group, checks the group's daily budget, swaps in the real
   Anthropic key and forwards the call to `https://api.anthropic.com` unchanged (passthrough: prompt
   caching marks and thinking settings survive).
4. The response's token usage is priced with the collector's prices (§V.105) and charged to the
   group. A call is checked before it runs and charged after, so a group can overshoot by the
   calls already running.
5. Over budget: the llm listener answers 429 `budget_exceeded`. The front listener adds
   `x-should-retry: false`: without it the Anthropic Go SDK (kagent's ADK) waits out the
   `Retry-After` (the seconds left in the day) twice before giving up. With it, the run fails at
   once with the reason in its error (local proof check 11). Anthropic's own 429s keep their
   retries.

## Budgets

`budgets.yaml` is the only place they are written. `scripts/gen-model-budgets.py` generates the
gateway's config (`configmap.yaml`), its env Secret (`env-external-secret.yaml`), the kagent key
Secrets, and the Grafana budget alerts; CI fails if any is stale.

A group is the agents that share a key. An agent that runs inside another's sandbox (a sub-agent)
must share its parent's group, because kagent allows one Secret per host there. k8s-agent and
istio-agent share `build`, the group they were in with build-orchestrator (retired 2026-10-23),
whose sandbox they used to run in. The `ui-default` group is the kagent chart's default model config, for agents
created in the UI.

`gateway: true` in a group means its agents' ModelConfigs point at the gateway. Groups move over one
at a time (spec §7); until a group moves it is only measured and alerted on.

## One replica

Two replicas would need to share their counts through Postgres, and that failed in about one
local proof run in three in agentgateway 1.6.0, with nothing logged. An active/standby split with
Service affinity does not work here either: Substrate's egress restarts twice a day and would land
on the other replica every time. So the gateway runs one replica, which counts exactly.
- **Config changes.** They roll without a gap (maxSurge 1, maxUnavailable 0). The old pod drains
  for up to 175 s, so streams finish.
- **Node drains and crashes.** These are short outages of every group on the gateway. "Model
  gateway down" alerts after 5 minutes.
- **Lost counts.** On a restart the day's count resumes from Postgres. Counts not yet flushed (up to
  5 s) are lost.

## Alerts (Grafana, folder Agent Budgets, to Slack)

- **Agent near its daily budget** (warning): estimated spend over the last 24 hours above 80% of the
  budget.
- **Agent over its daily budget** (critical): above 100%. For a group not yet on the gateway the
  message says nothing is blocked yet.
- **Agent blocked by its daily budget** (critical): the gateway refused a group's calls in the last
  10 minutes.
- **Model gateway down** (critical): no replica up for 5 minutes.

The dispatcher routes all four to `none`: an investigation would spend the budgets they are about.

## Where to watch it

- **Grafana: Agents → Model Gateway & Budgets** (uid `model-gateway`). It opens on today (UTC) and
  shows each group's spend against its daily budget, refusals, calls by status, latency and the
  gateway's log.
- **The gateway's own UI:** `kubectl -n model-gateway port-forward deploy/model-gateway 15000:15000`,
  then http://localhost:15000/ui. Keys shows each group's budget, today's spend and window; Costs
  and Logs come from its request log. The config is mounted read-only, so the UI cannot change it.

## What it keeps

- Postgres database `model_gateway` (login `model_gateway`, base-apps/postgresql/init-model-gateway-db.yaml):
  `budget_usage` (one row per group and day) and `request_logs`, one metadata row per call (route,
  group, model, tokens, cost, status; no prompt, answer or key). agentgateway keeps no retention for
  the request log; `request-log-retention` deletes rows older than 30 days.
- Logs: one access line per request on stdout (Loki), no keys. The database URL carries no password
  (agentgateway prints its startup config in Kubernetes); sqlx reads `PGPASSWORD`.
- Metrics on :15020 (`agentgateway_requests_total`, `agentgateway_gen_ai_client_cost_usd_total`, …),
  each with a `group` label.

## Keys

- The real Anthropic key: Vault `k8s-secrets/kagent-anthropic`, read into Secret
  `model-gateway-anthropic` and mounted as a file the gateway watches.
- The virtual keys: Vault `k8s-secrets/model-gateway-keys`, one property per group (32 random bytes),
  read into the gateway's environment and into `model-gateway-key-<group>` in kagent. A key is worth
  nothing outside the gateway.
- `scripts/provision-model-gateway-vault.sh` creates them, the database password and the three Vault
  roles. It never prints a value and never replaces a key unless asked (`--rotate <group>`).

## Proving a change

`tests/model-gateway/proof/run-proof.sh` runs two gateways from the cluster's image on the committed
config, a mock Anthropic API and Postgres in Docker, and checks keys, prices, the refusal, the
no-retry mark, the Go SDK's behaviour, the logs and what Postgres keeps. Re-run it after any
agentgateway, Substrate or kagent upgrade.
