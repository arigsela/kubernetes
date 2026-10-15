---
type: "Kubernetes App Runbook"
title: "Slack bridge"
description: "Runbook: the bot does not answer, a button does nothing, the socket keeps dropping, rotate the Slack tokens, change the owner or the agents"
app: slack-bridge
catalog_entity: slack-bridge
kind: runbook
namespace: slack-bridge
last_reviewed: 2026-10-14
status: stable
tags: [agents, slack, a2a, approvals]
sources:
  - base-apps/slack-bridge/deployment.yaml
  - scripts/slack_bridge.py
  - scripts/provision-slack-bridge-vault.sh
---

# Slack bridge runbook

## The bot does not answer a mention or a DM
1. Is the socket up? `kubectl get pods -n slack-bridge` (READY 0/1 = no `hello` yet) and
   `kubectl logs -n slack-bridge deploy/slack-bridge | grep '"event": "socket"'`. `invalid_auth`
   on `apps.connections.open` = the app-level token (rotate with
   `scripts/provision-slack-bridge-vault.sh --app-token-only`, then delete the pod).
2. Is it you? `grep '"event": "refused"'`: the bridge answers one user id only
   (`OWNER_SLACK_USER_ID` in `deployment.yaml`). A new Slack account is a PR.
3. Did the event arrive at all? No `ask` line and no `refused` line = Slack did not deliver:
   check the app's Event Subscriptions (`app_mention`, `message.channels`, `message.im`) and
   that the bot is in the channel. A scope change needs the app reinstalled.
4. Did the agent fail? The `ask` line's `error`: `HTTP 403 … RBAC` = the bridge's principal is
   not admitted (`base-apps/kagent/authorization-policy.yaml` rule 2 and
   `network-policy-controller.yaml`); `no answer after 300s` = the agent is slow or stuck,
   continue the session with the ask-hk command in the thread.
5. Did the agent review the wrong alert? Mention the bot *in the alert's thread*: the alert text
   goes to the agent as context. A top-level mention carries only your words and the time zone,
   and the agent guesses from live Grafana state.

## A button does nothing, or says "already decided" / "no longer holds"
- "no longer holds that restart": the dispatcher restarted since it posted (its pending list is
  in memory). Use the ask-hk fallback in the same message.
- "already decided": the nonce did not match; someone (the approvals page, a redelivered
  click) decided it first. Read the thread for the outcome.
- "could not reach the dispatcher": `kubectl logs -n slack-bridge deploy/slack-bridge | grep decision`
  for the error. Connection refused = the dispatcher's `DECIDE_PORT`; RBAC denied = the
  AuthorizationPolicy or the NetworkPolicy in `base-apps/dispatcher/` no longer names this pod
  or principal.
- Nothing at all: `grep '"event": "refused"'` (another user clicked) or the app's Interactivity
  is off.

## The socket keeps dropping
`grep '"state": "down"'`. Slack asks for a reconnect every few hours (`disconnect`,
`refresh_requested`): normal, one line, no `down`. An `idle timeout` every ~90 s means
something between the pod and Slack drops the websocket; a Slack outage shows as
`connections.open` errors with backoff up to 60 s. The pod never needs a restart for this.

## Rotate the Slack tokens
Regenerate in the Slack app (OAuth & Permissions for the bot token, Basic Information →
App-Level Tokens for the socket token), then
`scripts/provision-slack-bridge-vault.sh` (both) or `--app-token-only` / `--bot-token-only`,
wait for ESO (≤1 h, or annotate the ExternalSecret `force-sync`), then
`kubectl delete pod -n slack-bridge -l app=slack-bridge`. The bot token is also n8n's: rotate
it there too (`k8s-secrets/n8n`, `slack-bot-token`) or the alerts stop.

## Change the owner, the agents or the code
`deployment.yaml` env (`OWNER_SLACK_USER_ID`, `OWNER_EMAIL`, `AGENTS`, `DEFAULT_AGENT`) by PR;
the pod rolls. Code: edit `scripts/slack_bridge.py`, run
`python -m pytest tests/slack_bridge/ -q` and `scripts/gen-slack-bridge-configmap.py --repo-root .`
(CI fails on drift), PR; the checksum rolls the pod.
