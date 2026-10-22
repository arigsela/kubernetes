# hk demo: one question to an agent, shown end to end

**Date:** 2026-10-22. **Status:** design, for the owner's review. **Builds on:** the Agents dashboards
(`2026-10-21-agent-observability-dashboards-design.md`, §T.135), agent-audit-web (Plan B,
`docs/superpowers/plans/2026-09-27-agent-audit-web-gitops.md`) and the dispatcher approvals page
(`2026-10-14-restart-approvals-page-design.md`).

## Why

The owner wants to show Kiara, the CTO of a startup working out how to build its AI platform, what
the platform's infrastructure buys. That means two things: visibility into cost and security, and
how the pieces fit together behind one call to an agent. The owner drives the page over a screen
share; Kiara watches. The owner chooses the question.

What she should come away with: an AI platform is more than a model and a prompt. The gateway,
identity, sandboxing, tracing and cost tracking are what make it safe to hand to a team, and each one
is something you can see.

## Goals

1. Ask `kagent/homelab-knowledge` (hk) a question from a web page and watch it move through the
   platform as it happens.
2. Every piece on the path is a box on a map. Clicking it opens a panel with that piece's part in
   this one request: what the piece is for (one plain line), what it checked, its numbers, its log
   lines, its trace spans and its configuration.
3. Cost: tokens and estimated dollars for each model call and for the request, plus the day's total
   across agents.
4. Security: each check the request passed, marked either **observed** (✓, shown by this request's
   logs, trace or metrics) or **configured** (⚙, a setting that applies to every request).
5. Open any recent hk run instead of a live one, so a slow or failed call doesn't stall the demo.
6. Run in the cluster like agent-audit-web: its own host behind the Gateway IP allow-list and Dex
   login, containerised, with docs that show up in Backstage.

## Non-goals

- A product for Kiara's company, or for anyone but the owner.
- Agents other than hk. The agent name is one setting, but nothing else is designed for them.
- Follow-up turns. Each question starts a fresh session.
- Staged failures (a refused tool, an approval pause). The demo shows a normal question and what
  was checked along the way.
- Any change to kagent's authentication or authorization.
- Replacing Grafana. Panels link to Grafana for depth.

## Anti-goals (a failure even if everything works)

- **Leaking on the screen share.** Kiara is outside the household. Tokens, secrets, raw tool
  output, unmasked emails or full client IPs must never be on screen.
- **Overclaiming.** A ✓ that no data from this request backs. Settings that can't be observed per
  request say so (⚙), and checks whose details aren't visible say that too (for example, the
  sandbox egress policy's contents).
- **Blank or stuck panels with no explanation** while the trace or logs are still arriving.
- **Hurting the cluster.** Loki has about 0.5 CPU and Tempo about 1 CPU (§B.41). The page must not
  issue day-wide scans or polling loops.

## Decisions taken with the owner

- **Layout:** a map of the platform whose boxes light up as the request moves, each one clickable,
  with a panel under the map. This is option B from the brainstorm; mockup v3 is in the session's
  `.superpowers/brainstorm/` folder.
- **No staged failures:** a normal question, showing what was checked.
- **Hosting:** in the cluster like agent-audit-web, at its own address behind Dex login and the IP
  allow-list, documented for Backstage.
- **Identity, option 1:** the app calls hk under its own service account, the way the dispatcher and
  slack-bridge do. The person is checked at the app's front door and recorded by the app. Why not
  the person's own token: kagent's waypoint accepts Dex tokens only with audience `kubernetes`
  (`base-apps/kagent/request-authentication.yaml`). That token is also the owner's kube-apiserver
  credential, and Dex deliberately has no trustedPeers, so no web app may hold one.

## Design

### 1. The page

- **Top:** a question box with a Send button, and a "Recent runs" list.
- **Map:** four groups and eight boxes. A box lights up when its first evidence arrives and gets a ✓
  when all its observed checks are in.
  - **Front door:** Gateway, Login.
  - **This app:** hk demo.
  - **Agent platform:** Identity check, kagent, Sandbox.
  - **Outside calls:** Model API, Tools.
- **Panel:** under the map, for the selected box, in this order:
  - what the box is for, in at most two plain sentences for a CTO
  - the checks (✓ observed, ⚙ configured)
  - its numbers
  - three tabs: **Evidence** (log lines), **Trace spans**, **Config**
  - an "Open in Grafana" link: the Agent Platform dashboard with `var-trace_id` and `var-session`
    set, or Explore for that query
- **Progress and answer:** a progress feed (status updates and tool-call names) and hk's answer once
  it's done.

The Config tab shows the relevant excerpt of the manifest that sets the check, with its repo path.
The excerpts are static text shipped with the app (see §10).

### 2. What each box shows, and where the data comes from

All queries use the in-cluster endpoints in §4. The `RUN_LOGS` query, the trace-id handling and
`window_total()` come from `scripts/gen-agent-dashboards.py`.

| Box | What it is (gist) | Observed on this request (✓) and its source | Configured (⚙) | Numbers |
|---|---|---|---|---|
| Gateway | The front door; it decides who may reach the platform at all. | The Istio ingress access-log line for the page's request to `hk-demo.arigsela.com`, matched by the `x-request-id` the app received. Shows status, duration and the client IP masked to /24. | The `gateway-allow` rule for the host (`base-apps/istio-ingress/authorizationpolicy.yaml`); TLS certificate. | time at the Gateway |
| Login | Who the person is. | oauth2-proxy passed `X-Forwarded-Email` (shown masked), as seen by the app on the request. | Dex client `hk-demo`; allowed-emails list (count only); confidential client with PKCE. | — |
| hk demo | The app: records who asked and calls the agent for them. | The app's own log line for the run: masked email, question length, contextId, taskId, trace id. | Calls kagent only with its service-account token; binds 127.0.0.1 behind the sidecar. | time to the agent's first event |
| Identity check | The mesh checks which workload is calling before kagent sees the call. Here that is a workload, not a person: the person was checked at the front door. | The waypoint access log or the `istio_requests_total{reporter="waypoint"}` sample with `source_principal …/ns/hk-demo/sa/hk-demo`. `enduser.id = system:serviceaccount:hk-demo:hk-demo` on the `a2a.request` span. | RequestAuthentication `kagent-callers`: only Dex audience `kubernetes` and cluster service-account tokens with audience `kagent`. AuthorizationPolicy `kagent-controller`. | — |
| kagent | Starts the agent run and keeps its session and task. | Spans `POST /agents/` and `SendMessage`; controller log lines carrying the trace id. | Agent template `homelab-knowledge` and its capability class `read` (admission policy). The eval gate's last result for the agent, from the `eval-gate` log event. It's shown as context, not a check: the gate doesn't apply to this caller. | run duration |
| Sandbox | Substrate runs each session as an actor in a gVisor sandbox, snapshots it after the turn, and controls what it may reach. | Spans `ateapi.Control/CreateActor` (or a resume through the router span, `ate.router.resume`), `CreateActorEgressPolicy`, `GetActor`. Router and egress log lines for `session-<contextId>`. | An egress policy is set per actor. Its contents are not visible, and the page says so. | start or resume time |
| Model API | Every call to the model, counted and priced. The API key is added by Substrate's egress proxy, so it never enters the sandbox. | `generate_content` spans: model, input, output and cache tokens, `agent.cost.estimate_usd`. Egress log lines `api.anthropic.com … /v1/messages 200` for the trace. | Key injection (`ate-secret://`, `base-apps/substrate/docs.md`). The cost is an estimate at list prices, computed by the collector (`transform/genai-cost`). | calls, tokens, $ for this request; today's total from `agent_cost_estimate_usd_total` via `window_total()` |
| Tools | Each tool call, the server that ran it, and that it was allowed. | `execute_tool` spans (excluding `execute_tool (merged)`): tool name, arguments (truncated, masked), duration. MCP adapter `tools/call` log lines for the trace. | The adapter allow-list (`policies.mcpAuthorization`); kagent-tools RBAC (no Secrets); refused calls today (`REFUSED` query). | calls, time |

### 3. How a question flows

1. **The browser sends the question.** `POST /api/ask` goes through oauth2-proxy, which adds
   `X-Forwarded-Email`. The app records the email and the Gateway's `x-request-id`.
2. **The app calls hk.** It sends A2A v1 `SendStreamingMessage` to
   `http://kagent-controller.kagent.svc.cluster.local:8083/agents/kagent/homelab-knowledge`:
   - Auth: the projected service-account token (audience `kagent`, 3600 s, re-read on every send).
   - Session: none is passed, so each question gets a fresh one.
   - Request id: the app sets its own `x-request-id` on the call.
   - Pattern: copy `scripts/slack_bridge.py` (streaming and SSE parsing) and `scripts/kagent_a2a.py`
     (headers, errors, `GetTask`).
3. **The first event gives the ids.** The task id and contextId go to the browser.
4. **Progress streams in.** Status and artifact updates feed the progress panel: agent text, and
   tool names with their arguments. Tool response data is dropped.
5. **The answer arrives.** At a terminal state the app calls `GetTask` and shows the answer as
   escaped text, or a safe Markdown subset. If the stream drops, it falls back to polling `GetTask`
   with a 900 s budget, as slack-bridge does.
6. **The app finds the trace.**
   - First path: Tempo search `{span.a2a.task.id="<task>" && name=~"invoke_agent .*"}` over the
     last hour, every 3 s, for up to 2 min. The collector's tail sampling waits 10 s after the last
     span, so the trace usually appears about 15–30 s after the answer.
   - Second path: `agent_run_start_seconds{gen_ai_conversation_id="<ctx>"}` in Prometheus.
7. **The panels fill in.**
   - The trace by id comes from Tempo.
   - Logs come from Loki: `RUN_LOGS` with the id left-padded to 32 hex characters (§B.40),
     substring filter first (§B.41), over a window from run start −1 min to end +2 min.
   - Prometheus supplies the totals.
   - Panels that are still waiting say what they are waiting for, for example "trace arrives about
     20 s after the answer".
8. **Recent runs.** The list comes from `LATEST_RUNS` filtered to `service_name="homelab-knowledge"`,
   which covers the last 48 h. Picking one runs steps 6 and 7 from its trace id and contextId.
   - Runs made by other callers (ask-hk, the dispatcher) are listed too, with their caller masked.
   - Their Gateway and Login boxes say "not this app's request".

The app pushes events to the browser over one server-sent-events stream per question. The Send
button stays disabled while a run is in flight.

### 4. Identity, access and network

- **People.**
  - An oauth2-proxy sidecar, the dispatcher flavour (`--skip-oidc-discovery` with a fixed
    login, redeem and JWKS URL).
  - A new Dex static client `hk-demo` (confidential, PKCE), plus `--authenticated-emails-file` from
    Vault containing the owner only.
  - The app binds `127.0.0.1:8000`, and the Service exposes only 4180.
- **The workload.**
  - Service account `hk-demo`, with `automountServiceAccountToken: false` and a projected token
    with audience `kagent`.
  - The namespace is labelled `istio.io/dataplane-mode: ambient` and added to
    `base-apps/kagent/network-policy-controller.yaml`.
  - No change to kagent's RequestAuthentication or AuthorizationPolicy: rule 2 already admits
    service-account tokens.
- **Sessions.**
  - Owned by the service account, one per question; the app never continues one.
  - The app's log is the record of who asked. The kagent UI won't list these sessions, as with the
    dispatcher.
- **Reads.** `loki.logging:3100`, `tempo.tracing:3200` and `prometheus.logging:9090`, GET only, no
  auth (none of these has a policy today).
- **Network.** A NetworkPolicy on the namespace admits `istio-ingress` on 4180 and 15008 (HBONE), as
  dispatcher's does.

### 5. What never reaches the screen

- **Emails:** masked everywhere, in the app's own fields and inside log lines (same regex semantics
  as `MASK_EMAILS`).
- **Tokens and secrets:** never rendered or logged. The app drops `Authorization` and cookies from
  anything it logs.
- **Tool output:** dropped from the progress feed. Spans already have it scrubbed by the collector.
- **Client IPs:** masked to /24.
- **Log noise:** the same lines the dashboard drops are dropped here (the normalization debug lines
  and `TaskStoreService/UpdateTask`).
- **Test:** fixtures with emails, tokens, IPs and a tool response must render with none of them
  visible.

### 6. Load and cost

- **Loki:** the substring check comes first and the window is minutes, never 24 h; target under
  3 s per query.
- **Tempo:** search is scoped to the last hour, and a trace-by-id takes 1.5–4.5 s.
- **Polling:** only the trace lookup polls, every 3 s, with a 2-minute cap; nothing refreshes on a
  timer after that.
- **Cost:** one question to hk has cost between $0.001 and $0.21 so far.

### 7. Build, ship and run (the agent-audit-web pattern)

- **App repo `arigsela/hk-demo` (private):**
  - Python 3.12, FastAPI, Jinja2, uv.
  - Browser code is vendored (no CDN) under a strict CSP, with the agent-audit-web security
    headers. No AVX2-only wheels (the nodes lack AVX2).
  - Dockerfile: multi-stage, digest-pinned bases, uid 10001.
  - `ci.yml`: ruff, pytest, `docker build`. `release.yml`: on a `v*` tag that equals the
    `pyproject` version, push to ECR through the OIDC role and print `tag@digest`.
- **AWS:**
  - ECR repository `hk-demo` in `terraform/roots/asela-cluster/ecr.tf` (IMMUTABLE,
    scan_on_push), applied by Atlantis.
  - Push role via Crossplane in `base-apps/hk-demo-aws-infrastructure/`, copied from
    `agent-audit-aws-infrastructure/web-ecr-push.yaml`. The trust subject must use the immutable
    form; check it with `gh api repos/arigsela/hk-demo/actions/oidc/customization/sub`.
- **GitOps:**
  - `base-apps/hk-demo.yaml`, with `directory.exclude` for `catalog-info.yaml` and `mkdocs.yml`.
  - `base-apps/hk-demo/`: namespace (ambient), service accounts, SecretStore, ExternalSecret,
    Deployment (app plus oauth2-proxy, projected token, limits, `app.kubernetes.io/name`,
    `tag@digest`), Service, HTTPRoute, Certificate, ReferenceGrant, NetworkPolicy.
- **Gateway:**
  - Listener `https-hk-demo` and a `gateway-allow` rule with the same ipBlocks.
  - `MANAGED_HOSTNAMES` in `base-apps/wan-ip-monitor/cronjob.yaml`.
  - The owner creates the Route 53 A record by hand.
- **Dex:** static client `hk-demo`, the env in `external-secret.yaml`, and the `checksum/config`
  bump (#837).
- **Vault:** a provisioning script modelled on `scripts/provision-dispatcher-web-vault.sh`, with a
  cookie secret of 32 raw bytes (#836). The owner runs it before the SecretStore merges.
- **Releases:** tag the app repo, then open a PR that pins the printed `tag@digest`. Renovate skips
  our own ECR images.

### 8. Docs and Backstage

- `base-apps/hk-demo/{docs.md,runbook.md,catalog-info.yaml}`, per `templates/agent-docs/README.md`.
  `system: default/platform-observability`, as agent-audit-web and dispatcher use, with
  `dependsOn` on kagent, Dex and Vault.
- Add `hk-demo` to `scripts/agent-docs-scope.txt` and regenerate with `gen-okf.py` and
  `gen-techdocs.py`. The CI validators must pass.
- SPEC.md: a §T row for this work, and a §V invariant:
  - every ✓ comes from that request's evidence
  - emails, IPs, tokens and tool output never render
  - the app calls kagent only with its own service-account token
- A Homepage tile through the HTTPRoute's `gethomepage.dev/*` annotations.

### 9. Done when

- **Access:**
  - An unauthenticated request gets a 302 to Dex.
  - An IP not on the allow-list gets a 403.
  - The owner logs in and sees the page.
- **A live question:**
  - Progress starts within about 2 s and the answer arrives.
  - All eight boxes are ✓ within about a minute after the answer.
  - Tokens, cost, model calls and tool calls match the Agent Platform dashboard for the same trace.
- **Leading-zero runs:** a recent run whose trace id starts with 0 shows its logs (§B.40).
- **Masking:** the fixture test passes, and a manual look at every panel shows no email, IP, token
  or tool output.
- **Rehearsal:** one full rehearsal of the demo, run end to end.

### 10. Left to the builder

- Visual styling, colours and fonts, within the approved layout.
- HTMX or plain JavaScript.
- The wording of the "what it is" lines (plain English, at most two sentences).
- How the Config excerpts are kept current (static text with the source path, refreshed at release).
- Whether the app emits its own span to the collector and sends a `traceparent`, so its hop shows in
  the trace and the trace id is known up front. Fall back to the Tempo search if kagent doesn't keep
  it.
- Matching the waypoint log line for the app's call by its `x-request-id`; check that the waypoint
  logs it. If not, the `source_principal` metric sample is the evidence.
- The repo name, if `hk-demo` is taken.

## Risks

- **First deploy:** ECR pull-secret propagation can mean up to 15 minutes of `ImagePullBackOff`.
- **Dex hairpin:** oauth2-proxy in an ambient namespace reaching `dex.arigsela.com` should work
  (dispatcher does), but it's unverified for this namespace.
- **Unverified stream shape:** the `artifactUpdate` inner shape is unverified (expect one artifact
  per ADK event). Build the progress parser against a recorded stream, not the docs.
