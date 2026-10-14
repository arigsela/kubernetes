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
  - base-apps/dispatcher/network-policy.yaml
  - base-apps/dispatcher/authorization-policy.yaml
  - base-apps/dispatcher/httproute.yaml
  - base-apps/dispatcher/certificate.yaml
  - base-apps/dispatcher/secret-store.yaml
  - base-apps/dispatcher/external-secret.yaml
  - scripts/provision-dispatcher-web-vault.sh
  - docs/superpowers/specs/2026-10-14-restart-approvals-page-design.md
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
- Not reachable from anywhere: `network-policy.yaml` admits only Grafana's pods (namespace
  `logging`) and a replay pod labelled `dispatcher/replay: "true"`. Since a row can end in a
  pull request, a forged alert would be a write-capable prompt under the dispatcher's identity.

## The table
`dispatch-table.yaml` is a ConfigMap whose `dispatch.json` key is the table: `limits`
(`investigations_per_hour`, `repeat_after_hours`, `open_prs_max`) and ordered `rules` (`id`,
`source`, `match` on alert labels, `route`, `prompt` for agent routes, `proposes`, `why`).
Every Grafana rule title must have a row; `none` with a `why` is a decision. Only `firing`
alerts are routed. A row with `proposes: true` routes to the implementer and is skipped
(`skip-open-prs`) while that agent has `open_prs_max` pull requests open, counted
unauthenticated from the public repo every five minutes; a failed count is logged
(`event: open-prs`) and the alert is routed anyway (§T.117). The two resource alerts are such
rows: the finding ends in a pull request link, or `no pull request: <reason>`, and its last
line is the verdict (`verdict: limit-too-low | leak | contention | rollout-pending |
inconclusive`), the one line the dispatcher parses. A row may also carry `remediate` (`on`, `agent`, `prompt`): on a
listed verdict the dispatcher opens a SECOND new session with `restarter` (class admin, one
gated `k8s_delete_resource`), within `limits.restarts_per_day` (2) and once per
namespace/workload per 24 h, and posts a second finding carrying the pending call's exact
arguments and a link to the approvals page, with the ask-hk `--task <id>` command as the
fallback (§T.118, §V.96, §T.119). Before any session opens the dispatcher refuses a deny-listed
namespace or a malformed pod label (`skip-target`); after it, the approve command is printed
only when the pending call is exactly one delete of the alert's pod ("MISMATCH" otherwise).
The memory row remediates on `leak` and `rollout-pending`, the CPU row on `rollout-pending`
(a merged raise that an OnDelete pod has not picked up: the restart applies it); contention
has no mechanical fix and `limit-too-low` is a pull request. One log line
`event: remediate` says `remediate`, `skip-target`, `skip-restarts`, `skip-recurring` or
`no-match`; `verdict: null` means the answer had no verdict line.

## The approvals page
`https://dispatcher.arigsela.com/approvals` (§T.119, §V.97) lists the restarter sessions this
dispatcher opened and is waiting on, each with the exact pending call, the evidence session
and Approve / Reject buttons. It is served by a second HTTP server inside the dispatcher bound
to `127.0.0.1:8081`, reachable only through the `oauth2-proxy` sidecar in `deployment.yaml`
(OIDC via Dex client `dispatcher`, PKCE, cookie `_dispatcher`) whose `allowed-emails` file
from Vault (`k8s-secrets/dispatcher-web`, written by
`scripts/provision-dispatcher-web-vault.sh`, never in git) is the access control; the edge IP
allow-list is the first layer, `network-policy.yaml` admits only the Gateway to `4180`. A
decision claims the item atomically (two tabs or a double click can never both send), answers
the browser at once (202: oauth2-proxy would give up on a 30 s upstream) and sends the
`tool_approval_response` for exactly the registered call ids under the dispatcher's token in
the background (the one send that ever carries metadata, §V.88 amended); the outcome is a
third Slack finding ("Restart approved by <email>: …", or "sent, outcome unknown" after a
timeout, or "paused again" if the restarter asked for more) and a log line `event: decision`
with `approved_by`. The list is in memory: a rollout empties it, and the Slack message's ask-hk
fallback still works. A stale page (wrong nonce) is refused; the kagent UI cannot show these
sessions (owned by the dispatcher's identity).

**A second door, the Slack bridge (§T.120).** The same "Restart awaiting approval" message
carries Approve / Reject buttons (the finding's `decision {ctx, nonce}`, rendered by n8n). The
Slack bridge (`base-apps/slack-bridge/docs.md`) forwards the owner's click to
`POST :8082/decide/<ctx>/approve|reject` (JSON `nonce`, `reason`, `approver`), which runs the
same claimed `decide_pending` path with `approved_by: <email> (via slack)`. That port is reachable
only from the bridge's pod (`network-policy.yaml`) and only as its principal
(`authorization-policy.yaml`), through its own Service `dispatcher-decide` that no route names.

## Identity and record
Sessions are owned by `system:serviceaccount:dispatcher:dispatcher` (the projected token in
`deployment.yaml`, audience `kagent`, read on every send; `serviceaccount.yaml`); the daily
audit attributes their tool calls; `scripts/ask-hk --as-serviceaccount dispatcher/dispatcher
--agent kagent/<agent> --context <id>` continues one by hand (an audited TokenRequest, no
shared secret). A pending restart is decided the same way, plus `--task <id>` so ask-hk
fetches a pause it never saw (`--agent kagent/restarter … --task <id> --approve` or
`--reject REASON`); the kagent UI cannot see a dispatcher-owned session. The audit records
the approval under the dispatcher's identity, not the approver's (a known residual, §V.96).
The dispatcher itself never answers a pause (§V.88).

## Observability
One JSON line per decision (`event: dispatch`, `decision` ∈ investigate / none / resolved /
unmatched / skip-budget / skip-repeat / skip-open-prs) and per finding (`event: finding`, with the session id,
never the answer text). Loki: `{namespace="dispatcher"} | json | event="dispatch"`.
