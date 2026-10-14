# Slack bridge: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ask any kagent agent from Slack (mention or DM, one thread = one session) and approve or reject pauses, the bridge's own and the dispatcher's restarts, with buttons, as the owner only.

**Architecture:** a stdlib Python Socket Mode client in a new `slack-bridge` app with its own verified principal; the dispatcher gains a decide endpoint on port 8082 reachable only from the bridge; the n8n `agent-findings` workflow renders buttons when a finding carries `decision`.

**Tech Stack:** Python 3.12 stdlib (socket, ssl, urllib, json), Slack Socket Mode + Web API, kagent A2A v1 (`scripts/kagent_a2a.py`), Istio ambient AuthorizationPolicy, NetworkPolicy, ESO/Vault.

**Spec:** `docs/superpowers/specs/2026-10-14-slack-bridge-design.md`

## Global Constraints

- One PR; merged only AFTER the owner (a) changed the Slack app (spec §2) and (b) ran `scripts/provision-slack-bridge-vault.sh` (a SecretStore with no Vault role fails the app sync).
- `base-apps/slack-bridge/code-configmap.yaml` and `base-apps/dispatcher/code-configmap.yaml` are generated; re-run both generators. `checksum/workflows` on n8n is recomputed by the one-liner in its comment.
- Stdlib only in both scripts. `kagent_a2a.py` is imported unchanged.
- V88/V96: the dispatcher's only metadata-carrying send stays `decide_pending`. The bridge's own approvals are its own sends under its own token.
- Never commit a token; the provisioning script reads both from prompts and prints neither.

## Review Focus

1. An event or click from any Slack user but the owner must produce an ephemeral refusal and NO A2A send and NO dispatcher call (Task 1 tests).
2. A `s:` approve must send exactly the registered call ids on the registered task and context; a second click must not send (Task 1).
3. `POST /decide` must map 404/409 from `decide_pending` unchanged, append ` (via slack)` to the approver, and refuse a missing approver with 403 (Task 2).
4. The AuthorizationPolicy must keep every port but 8082 open (Grafana is plaintext, the gateway's HBONE to the sidecar) and admit 8082 only to the bridge principal; the public Service must not expose 8082 (Task 3 manifest tests).
5. The n8n workflow must render buttons only when `decision` is present and keep `text` as the fallback (Task 3).

### Task 1: the bridge (`scripts/slack_bridge.py`, `tests/slack_bridge/`)

- [ ] `WebSocket` (handshake, masked frames, ping/pong, close) + fake server test.
- [ ] `SocketMode` (connections.open, hello, envelope → handler → ack, disconnect → reconnect) + fake server test.
- [ ] `Slack` Web API wrapper with an injectable `http_post`.
- [ ] `Bridge`: `handle_event`, `handle_action`, `pick_agent`, `Thread`/`Pending` registries, log lines; tests with a fake A2A sender and fake Slack.
- [ ] Health server; `main()`; `Config.from_env`.
- [ ] `scripts/gen-slack-bridge-configmap.py` + `tests/slack_bridge/test_gen_configmap.py`.

### Task 2: the dispatcher's door (`scripts/dispatch.py`, `tests/dispatch/`)

- [ ] `Config.decide_port`; `serve_decide`; `POST /decide/<ctx>/(approve|reject)` JSON → `decide_pending(..., approver + " (via slack)", background=True)`.
- [ ] The pending finding carries `decision: {ctx, nonce, agent, task}`.
- [ ] Tests; regenerate the dispatcher ConfigMap.

### Task 3: manifests, n8n, docs, SPEC

- [ ] `base-apps/slack-bridge.yaml` + `base-apps/slack-bridge/` (namespace, serviceaccounts, deployment, code-configmap, secret-store, external-secret, docs.md, runbook.md, catalog-info.yaml).
- [ ] `scripts/provision-slack-bridge-vault.sh`.
- [ ] Dispatcher: `network-policy.yaml` rule, `authorization-policy.yaml`, `service.yaml` second Service, `deployment.yaml` env `DECIDE_PORT`.
- [ ] kagent `network-policy-controller.yaml`: admit `slack-bridge`.
- [ ] n8n `agent-findings` workflow: Block Kit buttons; recompute `checksum/workflows`.
- [ ] `scripts/agent-docs-scope.txt`, `gen-techdocs`, `gen-okf`; `.github/workflows/validate.yaml` suite `slack-bridge-validate`.
- [ ] SPEC.md: T120, V98. Manifest tests (`tests/slack_bridge/test_manifests.py`).
- [ ] Validation: all suites, both `--check`s, yamllint, kubeconform, validators.
