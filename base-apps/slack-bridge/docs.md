---
type: "Kubernetes App Guide"
title: "Slack bridge"
description: "Talk to any kagent agent from Slack (one thread, one session) and decide pauses with buttons, as the owner only, under the bridge's own verified identity"
app: slack-bridge
catalog_entity: slack-bridge
kind: docs
namespace: slack-bridge
last_reviewed: 2026-10-14
status: stable
tags: [agents, slack, a2a, approvals]
sources:
  - base-apps/slack-bridge.yaml
  - base-apps/slack-bridge/deployment.yaml
  - base-apps/slack-bridge/serviceaccount.yaml
  - base-apps/slack-bridge/code-configmap.yaml
  - base-apps/slack-bridge/secret-store.yaml
  - base-apps/slack-bridge/external-secret.yaml
  - base-apps/dispatcher/authorization-policy.yaml
  - base-apps/dispatcher/network-policy.yaml
  - base-apps/kagent/network-policy-controller.yaml
  - base-apps/n8n/workflows-configmap.yaml
  - scripts/slack_bridge.py
  - scripts/kagent_a2a.py
  - scripts/gen-slack-bridge-configmap.py
  - scripts/provision-slack-bridge-vault.sh
  - docs/superpowers/specs/2026-10-14-slack-bridge-design.md
---

# Slack bridge

## What it is
One Python process (`scripts/slack_bridge.py`, standard library only, shipped in the generated
ConfigMap `slack-bridge-code`) holding one Slack Socket Mode connection. A mention of the bot
in a channel, or a DM to it, is a question to a kagent agent over A2A; the answer comes back as
a reply in the thread, and every later reply in that thread continues the same kagent session.
The first reply carries the session id and the `ask-hk` command that continues it from a
laptop. The connection is outbound (the bridge opens it with the app-level token), so nothing
inbound crosses the ingress IP allow-list, which is why the oncall-agent's Slack Events
integration died and this one does not.

The first word picks the agent: `k8s-reader: which pods restarted today?` Any name in the
Deployment's `AGENTS` list works; anything else is text for `DEFAULT_AGENT`
(`homelab-knowledge`). The agent is fixed for the thread.

## Who may talk
One Slack user id, `OWNER_SLACK_USER_ID` in `deployment.yaml`. Anyone else who mentions, DMs
or clicks gets an ephemeral "not for you" and nothing is sent anywhere (§V.98). The bridge is
one principal on the platform, `system:serviceaccount:slack-bridge:slack-bridge`, verified by
the mesh from the pod's projected token (V91); its sessions are its own, invisible to the
kagent UI, continuable with `scripts/ask-hk --as-serviceaccount slack-bridge/slack-bridge`.

## Decisions
Two kinds of Approve / Reject buttons, told apart by the button's value:
- **The bridge's own sessions** (`s:<session>:<nonce>`). When an agent asked from Slack pauses
  on a gated tool (`restarter`'s pod delete, `k8s-agent`'s writes), the thread shows the exact
  pending calls with their arguments and the two buttons. A click sends the
  `tool_approval_response` for exactly the registered call ids on the registered task, under
  the bridge's token, once: the nonce claims the item, a second click is "already decided".
  If the agent pauses again, that is a new pending block, never auto-approved. An agent's
  question (`ask_user_request`) is answered by the next reply in the thread.
- **The dispatcher's restarts** (`d:<session>:<nonce>`). The "Restart awaiting approval"
  message n8n posts for the dispatcher (`base-apps/dispatcher/docs.md`, "The approvals page")
  now carries the buttons: the dispatcher adds `decision {ctx, nonce}` to the finding and the
  `agent-findings` workflow renders them. A click is forwarded to the dispatcher's decide port
  (`POST http://dispatcher-decide.dispatcher.svc.cluster.local:8082/decide/<ctx>/approve|reject`)
  with `OWNER_EMAIL` as the approver; the dispatcher claims, sends and posts the outcome
  exactly as it does for the approvals page, and records `approved_by: <email> (via slack)`.
  The page remains the second door; both share one claim.
After a click the message loses its buttons and gains "Approved by <email> via Slack". A
reject carries the fixed reason `rejected in Slack`.

## The dispatcher's door
Only this pod may knock: `base-apps/dispatcher/network-policy.yaml` admits the `slack-bridge`
pod on 8082 (and HBONE), and `base-apps/dispatcher/authorization-policy.yaml` admits port 8082
only from the bridge's principal while leaving every other port as open as before (ALLOW
semantics would otherwise refuse Grafana's plaintext webhook). The port has its own Service,
`dispatcher-decide`, so the public HTTPRoute can never reach it.

## Secrets (Vault)
`secret-store.yaml`: SecretStore `vault-slack-bridge`, role `slack-bridge`, ServiceAccount
`eso-slack-bridge`, reading only `k8s-secrets/slack-bridge`. `external-secret.yaml` materializes
`bot-token` (xoxb, a copy of n8n's: the same Slack app, its own path) and `app-token` (xapp,
scope `connections:write`, Socket Mode only). Neither token can decide anything: a decision
needs a click Slack attributes to the owner's user id. `scripts/provision-slack-bridge-vault.sh`
writes both and the policy and role; run it before the manifests merge, re-run it to rotate.
The Slack app itself: Socket Mode and Interactivity on, bot events `app_mention`,
`message.channels`, `message.im`, scopes `app_mentions:read`, `channels:history`,
`im:history`, `chat:write`, `reactions:write`, the Messages tab enabled; a scope change needs
a reinstall of the app, not a pod restart.

## Where config lives
- Who and what: `deployment.yaml` env (`OWNER_SLACK_USER_ID`, `OWNER_EMAIL`, `DEFAULT_AGENT`,
  `AGENTS`, `A2A_TIMEOUT`).
- Code: `scripts/slack_bridge.py` → `code-configmap.yaml` via `scripts/gen-slack-bridge-configmap.py`
  (also sets `checksum/code` so the pod rolls). Never edit the ConfigMap by hand.
- Tests: `tests/slack_bridge/` (the websocket client against a fake server, the Socket Mode
  loop, the conversation with a fake agent and a fake Slack, the manifests, the generator).

## Observability
One JSON line per event on stdout (Loki): `socket` (hello, disconnect, down), `ask` (user,
agent, session, state, error), `decision` (kind, session, approved, approved_by, state),
`refused` (user), `error`. `/readyz` is 503 while the socket is down.

## Limits
In-memory state: a restart forgets the thread→session map (a reply in an old thread starts a
new session and says so) and the bridge's own pending buttons (the thread keeps the ask-hk
fallback). No rate limit beyond one owner and one connection; the Anthropic console spend
limit is the backstop. Slash commands and per-user identity are not built.
