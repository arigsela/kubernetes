# Restart approvals page: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** approve or reject a pending restart from `https://dispatcher.arigsela.com/approvals` behind Dex, with the approver's email recorded.

**Architecture:** the dispatcher keeps an in-memory registry of the restarter sessions it opened and serves an HTML approvals page on `127.0.0.1:8081`; an oauth2-proxy sidecar (Dex, one-email allow-list) is the only way to it; a decision sends the `tool_approval_response` for the registered call ids and posts a third finding.

**Tech Stack:** Python 3.12 stdlib, oauth2-proxy v7.15.5, Dex, Gateway API, ESO/Vault.

**Spec:** `docs/superpowers/specs/2026-10-14-restart-approvals-page-design.md`

## Global Constraints

- Two PRs: (1) code, tests, script, spec, plan (inert without the sidecar); (2) manifests and docs, merged only AFTER the owner ran `scripts/provision-dispatcher-web-vault.sh` (a SecretStore with no Vault role fails the whole app sync).
- `base-apps/dispatcher/code-configmap.yaml` is generated: re-run `gen-dispatcher-configmap.py`.
- Stdlib only; no templates library; every rendered value HTML-escaped.
- V88: the only send carrying `metadata` is `decide_pending`.
- Never commit a secret; the script generates both secrets inside itself and prints neither.

## Review Focus

1. A POST with a valid nonce but no `X-Forwarded-Email` header must be 403, never a decision (Task 1 HTTP test).
2. A decision for a ctx registered under a different nonce (stale page) must be 409 and leave the item pending (Task 1).
3. The approvals server must bind 127.0.0.1 only; a manifest test pins the sidecar upstream to it and the Service exposes 4180, never 8081 (Task 1 and 2).
4. The webhook server must still reject `/approvals*` on 8080 (Task 1).
5. An approval whose send fails (A2A error) must keep the item pending and say so (Task 1).

### Task 1: Registry, decision, approvals server, Slack text (code PR)

**Files:** `scripts/dispatch.py`, `tests/dispatch/test_dispatch.py`, `scripts/provision-dispatcher-web-vault.sh`, generated `base-apps/dispatcher/code-configmap.yaml` + `deployment.yaml` checksum.

**Produces:** `PendingRestarts` (`add(item) -> nonce`, `get(ctx)`, `remove(ctx)`, `items()`), `Dispatcher.pending`, `Dispatcher.decide_pending(ctx, nonce, *, approved, reason, approver) -> dict` (keys `ok`, `status` 200/403/404/409/502, `text`), `serve_approvals(dispatcher, port) -> server`, `Config.approvals_url`, `Config.approvals_port`, `render_approvals_page(items, message=None) -> str`.

- [ ] Tests first (RED): registry; remediate registers on match only; Slack text has the URL and the fallback; decide approve/reject shapes; unknown/nonce/approver failures; A2A failure keeps the item; HTTP GET/POST/403/409; 8080 rejects `/approvals`.
- [ ] Implement; `python -m pytest tests/dispatch/ -q` green; regenerate the ConfigMap.
- [ ] Script `provision-dispatcher-web-vault.sh --email` (idempotent; `--rotate` regenerates).
- [ ] Commit, PR, merge (the dispatcher rolls; the approvals server starts on 8081 but nothing can reach it).

### Task 2: Manifests, Dex, edge, docs (manifests PR, after the owner ran the script)

**Files:** `base-apps/dispatcher/{deployment.yaml, service.yaml, serviceaccount.yaml, secret-store.yaml, external-secret.yaml, certificate.yaml, reference-grant.yaml, httproute.yaml, network-policy.yaml, docs.md, runbook.md}`, `base-apps/dex/{configmap.yaml, external-secret.yaml, docs.md}`, `base-apps/istio-ingress/{gateway.yaml, authorizationpolicy.yaml}`, `tests/dispatch/test_manifests.py`, `base-apps/kagent/{docs.md, agent-templates/restarter.yaml}`, `SPEC.md`, memory, `~/.claude/CLAUDE.md`.

- [ ] Manifest tests first (RED), then the manifests; yamllint + kubeconform; `gen-okf`, `gen-techdocs`, `validate-agent-docs`.
- [ ] Commit, PR, merge; watch the dispatcher pod (two containers Ready), the Certificate Ready, Dex rolled.
- [ ] Live: open the page, decide a pending restart, see the Slack decision message and `event: decision` with `approved_by`.
