# Restart approvals page: approve a pending restart from a browser, as yourself

**Date:** 2026-10-14. **Follows:** `2026-10-14-remediate-leak-restarter-design.md` (§T.118).

## Goal

A pending restart (a restarter session the dispatcher opened, paused on its gated pod delete)
is approved or rejected from a web page behind Dex login, with one click, and the decision is
recorded with the approver's email. Today it needs `ask-hk … --as-serviceaccount
dispatcher/dispatcher --context <id> --task <id> --approve` on a laptop, and the kagent UI
cannot show the session at all (it is owned by the dispatcher's service account, and a
session is scoped to whoever created it).

Out of scope: approving sessions the dispatcher did not open; Slack buttons (Slack would have
to call into the cluster through the IP allow-list); a real login for the kagent UI.

## Design

### 1. The host and the sidecar (the agent-audit-web pattern)

`dispatcher.arigsela.com`: a Gateway listener `https-dispatcher`, a Certificate
`dispatcher-web-tls` (Route 53 DNS-01), a ReferenceGrant, an HTTPRoute to Service `dispatcher`
port `4180`, an entry in the edge IP allow-list AuthorizationPolicy, and a Homepage tile.

The dispatcher pod gains an `oauth2-proxy` sidecar (same image and flags as agent-audit-web):
OIDC via Dex client `dispatcher` (confidential + PKCE), `--authenticated-emails-file` with the
owner's email from Vault, cookie `_dispatcher`, `--upstream=http://127.0.0.1:8081/`. The
sidecar is the only way in: the approvals server binds `127.0.0.1:8081`, so nothing but the
sidecar can reach it, and the `:8080` webhook, its NetworkPolicy rule and the projected-token
identity are unchanged. The NetworkPolicy gains one rule: from namespace `istio-ingress` to
ports `4180` and `15008` (HBONE).

### 2. The approvals server (stdlib, in `scripts/dispatch.py`)

A second `ThreadingHTTPServer` on `127.0.0.1:APPROVALS_PORT` (default 8081; `0` disables it).

- `GET /approvals`: an HTML list of pending restarts from the in-memory registry: alert,
  namespace/pod, the restarter's exact pending call (tool and JSON args), the evidence session
  link, age, and two forms: Approve, and Reject with a reason. Empty list: "nothing pending".
- `POST /approvals/<ctx>/approve` and `POST /approvals/<ctx>/reject` (form-encoded `nonce`,
  `reason`). The approver is `X-Forwarded-Email`, set by the sidecar; a missing header is 403.
  A wrong or missing nonce is 409 (the nonce is per pending item, generated when it was
  registered: a stale page cannot decide a newer item). An unknown `ctx` is 404.
- On a valid decision the dispatcher sends the `tool_approval_response` for exactly the
  registered call ids on the registered task and context, under its own token
  (`Dispatcher.decide_pending`), removes the item, posts a third finding to n8n
  (`kind: decision`, text "Restart approved by <email>: <restarter's answer>" or
  "Restart rejected by <email>: <reason>"), and logs `{"event": "decision", "approved_by",
  "approved", "session", "rule", "state"}`. The response is an HTML page with the result and
  a link back.
- Everything rendered is HTML-escaped. Only the two POST paths change state.

The registry (`PendingRestarts`): keyed by context id; an item holds task id, agent, calls,
labels, rule id, verdict, evidence session, nonce, created time; a lock; `add`, `get`,
`remove`, `items()`. `remediate()` registers an item when the pending call matches the
alert's pod (the same check that prints the approve line). In memory, like the budgets: a
rollout forgets it. The Slack message therefore keeps the ask-hk fallback with the ids.

### 3. The Slack message

The remediation finding's text now reads: "Restart awaiting approval … Decide at
`<APPROVALS_URL>` (fallback if the dispatcher restarted: `ask-hk … --task … --approve`)".
`APPROVALS_URL` defaults to `https://dispatcher.arigsela.com/approvals`.

### 4. Secrets (owner action, before the manifests merge)

`scripts/provision-dispatcher-web-vault.sh --email <owner email>`:
- generates the client secret and the cookie secret inside the script (never printed);
- `k8s-secrets/dispatcher-web`: `oauth2-client-secret`, `oauth2-cookie-secret`,
  `allowed-emails` (one line);
- `k8s-secrets/dex`: patches `dispatcher-client-secret` (same value as the client secret);
- policy and Kubernetes-auth role `dispatcher-web` for SA `eso-dispatcher-web` in `dispatcher`.
Re-running rotates both secrets; Dex and the sidecar pick them up within the ESO refresh.
A SecretStore whose role does not exist fails the whole app sync, so the script runs first.

Manifests: SA `eso-dispatcher-web`, SecretStore `vault-dispatcher-web`, ExternalSecret
`dispatcher-web-oauth2` (three keys), the Dex `staticClients` entry `dispatcher` with
`secretEnv: DISPATCHER_CLIENT_SECRET`, and the Dex ExternalSecret key
`DISPATCHER_CLIENT_SECRET` ← `dex/dispatcher-client-secret`.

### 5. Invariants

- **V88 (amended):** the dispatcher never decides an approval or answers a question on its own.
  The one answer it ever sends is a `tool_approval_response` forwarding a decision a human made
  on the Dex-authenticated approvals page, for exactly the call ids it registered, and it logs
  `approved_by`. CI: `tests/dispatch/`.
- **V97 (new):** the approvals server listens on `127.0.0.1` only and trusts
  `X-Forwarded-Email` because only the oauth2-proxy sidecar can reach it; the sidecar's
  allowed-emails file (Vault, never in git) is the access control, the edge allow-list the first
  layer; a decision needs the item's nonce. CI: `tests/dispatch/test_manifests.py`.

### 6. Testing

- Unit (`tests/dispatch/test_dispatch.py`): the registry; `remediate()` registers a matching
  item and not a mismatching one; the Slack text names the URL and keeps the fallback;
  `decide_pending` approve sends `tool_approval_response` with the registered ids on the right
  task/context and posts the decision finding, reject carries the reason; unknown ctx, wrong
  nonce, missing email; the restarter's answer after approval is scrubbed; the decision log
  line names the approver; V88: no other send ever carries metadata.
- HTTP (`serve_approvals` on port 0): GET renders the pending item and its nonce; POST approve
  with the header decides; without the header 403; wrong nonce 409; GET after decision shows
  nothing pending.
- Manifests (`test_manifests.py`): the sidecar's flags (issuer, client id, localhost upstream
  on 8081, emails file, cookie name, redirect URL), the three secret keys, the SecretStore role
  and SA, the Service port 4180, the NetworkPolicy's second rule, the route and listener host,
  the allow-list entry, the Dex client and its secret key.
- Live: open the page, approve the pending prometheus-0 restart (or a fresh replay), see the
  third Slack message with the approver's email and the pod replaced.

### 7. Documentation

Dispatcher docs and runbook (the page replaces the command; the fallback), kagent docs row and
the restarter header, dex docs (sixth client), SPEC (V88 amended, V97, T119), memory, the
owner's global CLAUDE.md ask-hk line.

## Residuals

- kagent's own audit still attributes the approval to the dispatcher identity; the human's
  email is in the dispatcher log and the Slack message. Closing it needs an approver claim in
  kagent's HITL response.
- The registry forgets on a rollout; the ask-hk fallback covers it.
