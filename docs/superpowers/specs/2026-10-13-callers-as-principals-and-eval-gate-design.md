# Callers as principals, and evaluation as a gate — Design

**Date:** 2026-10-13
**Status:** approved in conversation (section 1 reviewed by the owner; sections 2–4 written on
stated assumptions while the owner was away, per their instruction to continue)
**Follows:** `2026-10-12-signal-dispatch-and-promotion-design.md` (ADP paths 1, 5, 7)
**Fills:** the whole-picture brief's "what next" items 2 (callers as principals) and 1
(evaluation as a gate); ADP L3 governance · identity, and harness · evaluation (E3)

## Problem

1. **Every A2A caller names itself.** The kagent controller runs in `insecure` mode: identity is
   whatever `X-User-Id` says, or `admin@kagent.dev` when absent. The gateway's IP allow-list is
   the only control on `kagent-mcp.arigsela.com`, and in-cluster callers (the dispatcher,
   Backstage, the eval capture) reach `kagent-controller:8083` directly, bypassing even that.
   The audit record, the per-user memory, session ownership and the dispatcher's "own identity"
   all rest on a header anyone on the LAN or in the cluster can send. The whole-picture deck
   says so on screens 07 and 13; it is the gap every later path (implement in the platform,
   remediate on signal) inherits.
2. **Evaluation is a tool, not a gate.** The golden corpus (8 questions), the scorer and the
   Jev-then-Claude judge exist and are calibrated, but nothing runs them when an agent's
   template, prompt, model or harness changes. CI validates the corpus file and nothing else;
   CI cannot reach the cluster, and a PR's template is not live until it merges.

## Goals and non-goals

Goals:
- A verified identity on every A2A call that reaches the controller, whether it arrives from the
  internet through the gateway or from a pod. Humans are their GitHub email (via Dex);
  automations are their Kubernetes service account. The controller never sees a client-chosen
  identity.
- The audit record, memory and session ownership key on those verified identities from the
  day this lands; the dispatcher's sessions are owned by `system:serviceaccount:dispatcher:dispatcher`.
- `scripts/ask-hk` and the capture script work from the laptop with the token kubectl already
  caches; no new login flow, no long-lived secret on disk.
- After every sync of the kagent app that changes an agent's definition, the live agent is
  scored against the corpus; the result is a commit status on the synced revision and a Grafana
  alert on regression, routed by the dispatcher table. A regression is loud within minutes of
  the merge that caused it.
- The IP allow-list stays. Nothing here weakens an existing control.

Non-goals:
- Per-person login on the kagent UI. The UI keeps one identity (the controller default), its
  host stays allow-listed, and oauth2-proxy + Dex in front of it is a named follow-up.
- Authenticating sub-agent calls between agents (the runtime's own `X-Agent-Name` path): the
  workers are trusted as the runtime; this design only stops *other* callers from using that path.
- A pre-merge evaluation gate (in-cluster runner, preview agents): deliberately not built; the
  post-sync gate names the commit that regressed and that is what we can afford now.
- Growing the corpus. E1's mining script exists; new questions are their own small PRs.
- Keeping the kagent MCP server in Claude Code: `ask-hk --context` does what `invoke_session`
  did, with a token. The MCP registration is removed from the shortcut docs and the user config.

## Design 1: the mesh owns identity (callers as principals)

### Why the controller cannot be the authority

kagent 1.0.0-alpha11 has two auth modes (`controller.auth.mode`): `insecure` (identity from the
`user_id` query parameter, then `X-User-Id`, then `admin@kagent.dev`) and `trusted-proxy`
(identity from a claim of the bearer JWT, **never signature-verified**, and every direct caller
must carry a parseable JWT or gets `ErrUnauthenticated`). Two consequences:
- In `trusted-proxy` mode the UI pod's calls carry no JWT without oauth2-proxy in front of the
  UI, which the owner deferred. The UI would break.
- In either mode, verification has to happen in front of the controller. Upstream says the same:
  "ClusterIP alone does not prevent bypass from another pod".

So the controller stays in `insecure` mode and **the service mesh is the identity authority**:
it verifies the token and *sets* `X-User-Id` from the verified claim, replacing anything the
client sent, and it refuses the paths where identity matters to anyone without a token, except
the runtime and the UI, which are identified by their workload principal instead.

### Shape (primary): ambient + a waypoint in `kagent`

- Namespaces `kagent`, `dispatcher` and `backstage` get `istio.io/dataplane-mode: ambient`. A
  waypoint `Gateway` `kagent-waypoint` (class `istio-waypoint`) in `kagent`; the
  `kagent-controller` Service gets `istio.io/use-waypoint: kagent-waypoint`. Only that Service
  uses the waypoint; everything else in the namespace is L4-captured only.
- `PeerAuthentication` STRICT in `kagent`: no plaintext from outside the mesh reaches any pod
  in the namespace, and ztunnel on the destination enforces that captured traffic to the
  controller Service traverses the waypoint.
- `RequestAuthentication` on the waypoint (`targetRef` the waypoint Gateway), two `jwtRules`:
  1. issuer `https://dex.arigsela.com`, `jwksUri https://dex.arigsela.com/keys`, audiences
     `["kubernetes"]` — the public Dex client kubectl uses (kubelogin). Humans reuse that token.
     `outputClaimToHeaders: [{header: X-User-Id, claim: email}]`.
  2. issuer `https://kubernetes.default.svc.cluster.local`, audiences `["kagent"]` — projected
     service account tokens. `outputClaimToHeaders: [{header: X-User-Id, claim: sub}]`
     (`system:serviceaccount:<ns>:<sa>`). JWKS: `jwksUri https://kubernetes.default.svc/openid/v1/jwks`
     if istiod can fetch it (needs a `ClusterRoleBinding` of
     `system:service-account-issuer-discovery` to `system:unauthenticated` in
     `base-apps/cluster-rbac/`); otherwise the JWKS inline, written by a generator from
     `kubectl get --raw /openid/v1/jwks`, with a drift test. The spike decides.
  `forwardOriginalToken: true` on both (the controller ignores it in `insecure` mode; harmless,
  and it lets a later `trusted-proxy` switch work without a mesh change).
- `AuthorizationPolicy` on the waypoint, `ALLOW` semantics (so anything unlisted is denied):
  - user paths `/agents/*`, `/mcp*`, `/api/*`: `from.source.requestPrincipals: ["*"]` (a valid
    JWT from either issuer), **and** not carrying `X-Agent-Name`;
  - the same user paths from the UI principal `cluster.local/ns/kagent/sa/kagent-ui` without a
    JWT (the UI's sessions are the controller default identity; see "the UI");
  - runtime paths (whatever the workers call: discovered in the observation window; expected
    `/api/*` and gRPC on 8083) from the worker principal `cluster.local/ns/kagent/sa/default`,
    with `X-Agent-Name` allowed only here;
  - a `DENY` rule: any request with `X-Agent-Name` whose source principal is not the workers.
- The ingress path gets the same check independently: a `RequestAuthentication` and an
  `AuthorizationPolicy` on the `main` Gateway for host `kagent-mcp.arigsela.com` requiring a
  valid JWT on `/agents/*` and `/mcp`, in addition to the existing allow-list rule. The external
  path is therefore checked twice (gateway, waypoint); the in-cluster path once (waypoint).
- The UI host's `HTTPRoute` (`base-apps/kagent/httproute.yaml`) gets a `RequestHeaderModifier`
  filter removing `X-User-Id`, `X-Agent-Name` and `Authorization`, so a browser user on the
  allow-list cannot smuggle an identity through the UI's nginx.
- A `NetworkPolicy` in `kagent` limiting ingress to the controller pods to the namespaces
  `istio-ingress`, `istio-system`, `kagent`, `dispatcher`, `backstage`: belt and braces,
  independent of the mesh, as upstream recommends.

### The four things the spike must prove, with pass criteria

Task 0 of the plan is a spike on the live cluster, each step reversible by deleting what it
created, and the plan does not continue to enforcement until all four pass:
1. **Ingress → waypoint.** With `use-waypoint` on the controller Service, a request from the
   internet to `https://kagent-mcp.arigsela.com/agents/kagent/homelab-knowledge` appears in the
   waypoint's access log. (Istio 1.30.5; gateways route to waypoints since 1.23.)
2. **Header overwrite.** A request with a valid JWT **and** a client-supplied `X-User-Id: evil`
   arrives at the controller with `X-User-Id` equal to the token's claim (observed by creating a
   session and reading its owner in `kagent_v1`, or with an echo backend behind the same
   waypoint). If Envoy only sets-when-absent, an `HTTPRoute` filter removing `X-User-Id` on the
   waypoint's route runs before JWT output and the test is repeated.
3. **Service account JWKS.** istiod resolves the Kubernetes issuer (the `jwksUri` form or the
   inline form) and a projected token with audience `kagent` passes; one with audience
   `kubernetes` (kubectl's default) is rejected.
4. **Substrate survives capture.** With `kagent` enrolled (L4 capture of the worker pods, which
   carry only `ate.dev/worker-pool` labels and cannot opt out individually), a full agent turn
   (ask-hk to `homelab-knowledge`, a tool call, memory save) completes, an idle actor snapshots,
   and a new session resumes it. If atenet (the Substrate network) breaks under ztunnel capture,
   the primary shape is abandoned for the fallback below.

### Fallback shape: the gateway is the only authority, plus a NetworkPolicy

If 1 or 4 fails: no ambient enrollment, no waypoint, no STRICT. The `RequestAuthentication` and
`AuthorizationPolicy` on the `main` Gateway (above) become the single enforcement point and
**every** in-cluster caller (dispatcher, Backstage, the eval job) calls the gateway's ClusterIP
with the public hostname instead of `kagent-controller.kagent.svc`, so their tokens are checked
too. The kagent-mcp allow-list rule gains the pod CIDR (`10.42.0.0/16`). The `NetworkPolicy`
narrows controller ingress to `istio-ingress` and `kagent` only (the dispatcher and Backstage
no longer need direct access). Residual risk, stated in the docs and on the deck: a pod *inside*
`kagent` (a tool server, a compromised sandbox) can still reach the controller directly and name
itself; the runtime already lets agents do that, so the exposure is the namespace, not the
cluster. The spike's result and the chosen shape are recorded in SPEC.

### The controller

`controller.auth.mode` stays `insecure` (the chart default) and this is written next to the
value: the mesh sets the identity header; flipping to `trusted-proxy` is the UI follow-up.
`admin@kagent.dev` becomes, by elimination, "the UI": every script and automation stops
sending it.

### Clients

- **`scripts/kagent_a2a.py`.** `request_headers(user, token)` sends `Authorization: Bearer
  <token>` when a token is given and *no* `X-User-Id` (the mesh sets it; sending one is at best
  ignored and at worst a reason for a DENY). `send_message(..., token=None, token_file=None)`:
  `token_file` is read on every call (projected tokens rotate). `user` stays for the no-mesh
  dev path (`--endpoint http://127.0.0.1:8083` port-forward) and for tests.
- **`scripts/ask-hk`.** Token source, in order: `--token-file`, `$KAGENT_TOKEN`,
  `$KAGENT_TOKEN_FILE`, else `kubectl oidc-login get-token --oidc-issuer-url=https://dex.arigsela.com
  --oidc-client-id=kubernetes --oidc-extra-scope=email --oidc-extra-scope=offline_access`
  (kubelogin's cache; the browser opens only when the refresh token is gone). The ExecCredential
  JSON's `status.token` is the ID token. `--user` is kept only with `--endpoint` for the
  port-forward path and prints a warning otherwise. `--as-serviceaccount <ns>/<name>` mints a
  projected token with `kubectl create token <name> -n <ns> --audience kagent --duration 10m`:
  this is how the owner continues a dispatcher investigation session (owned by the dispatcher's
  service account) and seeds the eval identity's memory. It is an audited TokenRequest, not a
  shared secret.
- **Dispatcher.** The Deployment mounts a projected token (`audience: kagent`,
  `expirationSeconds: 3600`, path `/var/run/kagent/token`); `dispatch.py` passes
  `token_file` on every send and drops `DISPATCH_USER`. Its sessions become
  `system:serviceaccount:dispatcher:dispatcher`. The finding text and runbook say how to continue
  one (`ask-hk --as-serviceaccount dispatcher/dispatcher --context <id>`).
- **Backstage.** The kagent plugin in `arigsela/backstage` sends `X-User-Id: backstage-portal`
  today. The Deployment gets the same projected token volume; the plugin reads
  `KAGENT_TOKEN_FILE` when set and sends the bearer (and no `X-User-Id`), else behaves as today.
  The backstage change is a PR in that repo and must be live **before** enforcement; until then
  the Backstage principal is allowed without a JWT on the user paths (a dated exception row in
  the policy, removed in the enforcement task).
- **Eval capture.** Runs as `system:serviceaccount:kagent:eval-gate` (design 2). Locally it
  uses the kubelogin token like ask-hk.
- **Claude Code.** The `kagent` MCP server is removed from the user's MCP config and from the
  "ask hk" shortcut text; follow-ups use `ask-hk --context`. Sandboxes via MCP were unused.

### Rollout, in order, each its own PR and each reversible

1. Spike (Task 0) on the live cluster, results in SPEC. Nothing merged yet except the spike's
   ledger row.
2. Clients first, backward compatible: `kagent_a2a.py`, `ask-hk`, `dispatch.py` learn tokens;
   the dispatcher mounts its token and sends it (harmless in `insecure` mode). Backstage PR.
3. Mesh in **observe** mode: enrollment, waypoint, `RequestAuthentication` (which only rejects
   *invalid* tokens, not missing ones), `AuthorizationPolicy` with an `ALLOW *` rule plus access
   logging on the waypoint. 48 hours of logs answer "who calls the controller on which paths"
   and populate the runtime-paths rule.
4. Enforce: the real `AuthorizationPolicy`, STRICT, the ingress JWT rule, the UI header strip,
   the `NetworkPolicy`. Acceptance: (a) ask-hk from the laptop works with the Dex token and the
   session owner in `kagent_v1` is the owner's email; (b) a curl from the LAN with
   `X-User-Id: admin@kagent.dev` and no token gets 403 on `/agents/`; (c) a dispatcher replay
   creates a session owned by its service account and the finding reaches Slack; (d) Backstage's
   agent page still lists agents; (e) the UI still chats; (f) `agent-audit` export shows the
   new principals.
5. Docs, SPEC (V91–V93), the deck's "needs a decision" row, memory.

### Failure modes, stated

- **Dex down.** Humans cannot get a new token; cached ID tokens (24 h) keep working until they
  expire; automations are unaffected (Kubernetes issuer). Acceptable: Dex already gates Vault,
  Argo CD and kubectl.
- **JWKS fetch fails** (Dex keys rotate, API server JWKS unreachable): Envoy keeps the last
  cached keys and refreshes in the background; a cold waypoint with no keys rejects every token
  with a clear `Jwks remote fetch is failed` in its log. Grafana alert on waypoint 401s above a
  threshold, routed to `none` (human).
- **Waypoint down.** The controller is unreachable from everywhere except the workers and UI
  (which also traverse it — so the UI breaks too). Waypoints are a Deployment with 1 replica by
  default; set 2 and a PodDisruptionBudget. The dispatcher's existing timeout handling turns a
  dead waypoint into "still running / error" findings, not lost alerts (V86 unchanged).
- **Token audience mistakes.** A kubectl token (audience `kubernetes`) presented to the
  Kubernetes-issuer rule is rejected; a projected token with the wrong audience is rejected;
  both are 401 with the audience named in the log.
- **Session continuity.** Sessions created before the change are owned by `admin@kagent.dev`;
  the owner's new sessions are owned by their email. Old conversations are continued from the
  UI (which still is `admin@kagent.dev`), not from ask-hk. Memory keyed by (agent, user) starts
  fresh for the new principals; the eval identity's memory is re-seeded (design 2).

## Design 2: evaluation as a gate (post-sync score, status, alert)

### Shape

A Kubernetes `Job` in `base-apps/kagent/` with `argocd.argoproj.io/hook: PostSync` and
`hook-delete-policy: BeforeHookCreation` runs after every sync of the `kagent` Application:

1. **Did an agent change?** It reads the live `AgentTemplate`, `Agent`, `Harness` and
   `ModelConfig` objects (`kubectl get ... -o json`, specs only, sorted) and hashes them. The
   last scored hash per agent lives in ConfigMap `eval-gate-state` (`kagent`), written by the
   job. Unchanged hash → the job logs `{"event":"eval-gate","agent":…,"skipped":"unchanged"}`
   and exits 0. The kagent app syncs for many reasons (chart bumps, routes); scoring only when
   the agent surface changed keeps the judge bill and the sync time honest.
2. **Capture.** For each agent with a corpus file in `tests/eval-corpus/<agent>.yaml` (today:
   `homelab-knowledge`), `capture-eval-answers.py` logic runs in-cluster against
   `http://kagent-controller.kagent.svc:8083` (through the waypoint) as service account
   `eval-gate` (projected token, audience `kagent`): one fresh session per question. The memory
   category expects state saved earlier as that identity; the runbook's seed command is
   `ask-hk --as-serviceaccount kagent/eval-gate "My favorite color is octarine. Please save that."`.
3. **Score.** `score-eval.py` logic: rubric always; the Jev-then-Claude judge when
   `TYPESAFE_API_KEY` / `ANTHROPIC_API_KEY` are present (ExternalSecret `eval-gate` from Vault
   `k8s-secrets/eval-gate`, per-consumer SecretStore and role per `templates/agent-identity`).
   Without them the rubric stands alone and the result says `judge: none`. This is how the gate
   runs on day one, before the owner writes the keys.
4. **Decide.** `pass_rate = passed/total`. Regression ⇔ any `security-refusal` question fails
   (hard), or `pass_rate` is below the stored baseline for that agent. No slack: with 8 questions
   one failure is 12.5 %, and a flaky question is handled by the runbook, not by a tolerance. The baseline is the best pass rate seen for the current hash family;
   it is reset by deleting the agent's key from the ConfigMap (runbook).
5. **Report.** One JSON line per agent to stdout (→ Loki): `{"event":"eval-gate","agent","revision","hash","passed","total","judge","regression":bool,"failed":[ids]}`.
   A commit status `eval/<agent>` on the synced revision (`kubectl -n argocd get application
   kagent -o jsonpath={.status.sync.revision}` at PostSync time) — `success`/`failure` with
   `"7/8 (baseline 8/8, judge jev)"` — when `GITHUB_TOKEN` is present in the same secret
   (a fine-grained PAT, Commit statuses RW on this repo, owner-minted). Without it: log only.
   The Grafana alert rule `Agent eval regression` (Loki query on `event="eval-gate"` and
   `regression=true`, for 0 s, fail-closed `NoData: OK`) goes to Slack via n8n like every alert
   and gets a dispatch-table row routed to `none` (a human reads the failed ids; nothing to
   investigate automatically yet). The job's exit code is 0 on regression (the sync succeeded;
   the gate is the status and the alert, not an Argo failure that would wedge the app) and 1
   only when it could not score (capture or scorer error), which Argo shows as a failed hook.

### What runs the job

Image `python:3.12-slim` (digest-pinned, like the dispatcher), code from a generated ConfigMap
(`scripts/gen-eval-gate-configmap.py` bundling `scripts/eval_gate.py`, `kagent_a2a.py`, the
reusable parts of `capture-eval-answers.py` and `score-eval.py`, and the corpus files from
`tests/eval-corpus/*.yaml`; checksum annotations on the Job like the dispatcher's). No pip at
runtime: the Claude judge is called with `urllib` (the `anthropic` SDK is not required for one
`messages.create`), Jev already uses `urllib`, and the generator converts the corpus YAML to
JSON at generation time, so the job needs no YAML parser and no third-party package. ServiceAccount `eval-gate` with a Role: get/list the four kagent kinds; get/update the
one ConfigMap; a ClusterRole-free read of the `kagent` Application in `argocd` via a Role in
`argocd` bound to that SA. Resources 50m/128Mi–500m/512Mi, `activeDeadlineSeconds: 1500`
(8 questions × up to 3 minutes), `backoffLimit: 0`.

### Scripts, reused not copied

`scripts/eval_gate.py` imports `capture_eval_answers.capture(...)` and `score_eval.score(...)`
(the two scripts get importable functions; their CLIs stay). The generator bundles the modules
by path, as `gen-dispatcher-configmap.py` does.

### Failure modes, stated

- **Judge key missing or Jev down:** rubric-only / Claude-only verdicts, labelled as such in the
  log and the status description; never a silent pass.
- **Capture times out** (agent slow, waypoint down): the job exits 1, Argo marks the hook
  failed, the app shows `Degraded` until the next sync; the Grafana alert on failed hook jobs
  (existing `Argo sync failed` rule) carries it. No status is posted (unknown ≠ failure).
- **The ConfigMap is lost:** the first run after that sets a new baseline from its own result;
  stated in the log.
- **A flaky question** (the model answers differently on a rerun): the runbook says re-run the
  job by syncing the app (`argocd app sync kagent --prune=false`), and that two consecutive
  regressions on the same hash are the signal; one is a notice.

## Invariants (new §V rows)

- **V91** Every A2A request reaching the controller's user paths carries an `X-User-Id` set by
  the mesh from a verified JWT (Dex `email` or Kubernetes `sub`), or comes from the UI principal
  (identity = controller default) or the worker principal (runtime). No client-supplied identity
  header reaches the controller; CI: the waypoint and gateway policies exist and name the two
  issuers (tests/identity).
- **V92** No automation holds a long-lived credential for the agents: service accounts present
  projected tokens (audience `kagent`, ≤ 1 h), humans present Dex ID tokens from kubelogin.
  Nothing in git or Vault is an agent-API token.
- **V93** Every sync of the kagent app that changes an agent's definition is scored against the
  corpus before the next change can land unnoticed: a commit status on the synced revision when
  the token exists, always a Loki line and an alert on regression. A regression never fails the
  sync; a scoring failure always does.

## Owner actions (not automatable from this repo)

1. Vault: run `scripts/provision-eval-gate-vault.sh` (written by the plan) to create
   `k8s-secrets/eval-gate` with `anthropic-api-key` (may reuse the kagent key value),
   `typesafe-api-key` (optional), `github-token` (optional, a fine-grained PAT with *Commit
   statuses: read and write* on `arigsela/kubernetes`), the policy and the kubernetes-auth role.
   The gate runs rubric-only until then.
2. `arigsela/backstage`: merge the kagent plugin change (bearer token from `KAGENT_TOKEN_FILE`).
3. Remove the `kagent` MCP server from the Claude Code user config (`claude mcp remove kagent`)
   and `brew install int128/kubelogin/kubelogin` if not present.
4. After enforcement: confirm the kagent UI still chats (the one path this design does not
   test from a script).

## What this is not yet

- Per-person UI login (oauth2-proxy + Dex, then `trusted-proxy`).
- Sub-agent calls authenticated (`X-Agent-Name` trusted from the workers).
- A pre-merge gate; a corpus for any agent but `homelab-knowledge`.
- Auto-revert on regression. The alert goes to a human; the dispatcher row is `none`.
