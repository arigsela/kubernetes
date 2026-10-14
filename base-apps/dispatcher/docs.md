---
type: "Kubernetes App Guide"
title: "Dispatcher"
description: "Routes Grafana alerts to a read-only agent investigation by a declared table; humans keep every alert (ADP path 1, dispatch work)"
app: dispatcher
catalog_entity: dispatcher
kind: docs
namespace: dispatcher
last_reviewed: 2026-10-12
status: stable
tags: [agents, dispatch, grafana, a2a]
sources:
  - base-apps/dispatcher.yaml
  - base-apps/dispatcher/dispatch-table.yaml
  - base-apps/dispatcher/deployment.yaml
  - base-apps/dispatcher/service.yaml
  - base-apps/dispatcher/code-configmap.yaml
  - scripts/dispatch.py
  - scripts/gen-dispatcher-configmap.py
  - base-apps/logging/grafana-alerting.yaml
  - base-apps/n8n/workflows-configmap.yaml
  - docs/superpowers/specs/2026-10-12-signal-dispatch-and-promotion-design.md
---

# Dispatcher

## What it is
One Python process (`scripts/dispatch.py`, standard library only, shipped in the generated
ConfigMap `dispatcher-code`) that receives a second copy of every Grafana alert notification,
matches it against the table in `dispatch-table.yaml`, and for an `agent:<name>` route opens a
new A2A session with that agent, presenting its projected service account token (the mesh
verifies it: the session is owned by `system:serviceaccount:dispatcher:dispatcher`, V91), then posts the answer to n8n's
`agent-findings` webhook, which formats it for Slack `#oncall-alerts`.

## What it is not
- Not in the human path. Grafana's contact point `n8n` has two integrations: n8n (→ Slack,
  unchanged) and this service. If the dispatcher is down, Slack is unaffected (§V.86).
- Not an operator. It may only route to agents whose every tool is `read` or `propose` in the
  capability taxonomy with no `requireApproval` binding (§V.87, amended 2026-10-14: a `propose`
  agent can open a pull request, never touch the cluster); `tests/dispatch/test_table.py`
  enforces it. It never answers an approval or a question (§V.88).
- Not a second source of truth for Slack: n8n holds the Slack token; this service holds no
  credential at all.

## The table
`dispatch-table.yaml` is a ConfigMap whose `dispatch.json` key is the table: `limits`
(`investigations_per_hour`, `repeat_after_hours`, `open_prs_max`) and ordered `rules` (`id`,
`source`, `match` on alert labels, `route`, `prompt` for agent routes, `proposes`, `why`).
Every Grafana rule title must have a row; `none` with a `why` is a decision. Only `firing`
alerts are routed. A row with `proposes: true` routes to the implementer and is skipped
(`skip-open-prs`) while that agent has `open_prs_max` pull requests open, counted
unauthenticated from the public repo every five minutes; a failed count is logged
(`event: open-prs`) and the alert is routed anyway (§T.117). The two resource alerts are such
rows: the finding ends in a pull request link, or `no pull request: <reason>`.

## Identity and record
Sessions are owned by `system:serviceaccount:dispatcher:dispatcher` (the projected token in
`deployment.yaml`, audience `kagent`, read on every send; `serviceaccount.yaml`); the daily
audit attributes their tool calls; `scripts/ask-hk --as-serviceaccount dispatcher/dispatcher
--agent kagent/<agent> --context <id>` continues one by hand (an audited TokenRequest, no
shared secret).

## Observability
One JSON line per decision (`event: dispatch`, `decision` ∈ investigate / none / resolved /
unmatched / skip-budget / skip-repeat / skip-open-prs) and per finding (`event: finding`, with the session id,
never the answer text). Loki: `{namespace="dispatcher"} | json | event="dispatch"`.
