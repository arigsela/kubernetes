---
type: "Kubernetes App Guide"
title: "hk demo"
description: "hk demo page: a walkthrough of the agent platform (The platform tab), a conversation with kagent/homelab-knowledge shown end to end (checks, cost, logs, trace), where an implementer: turn has kagent/implementer open the pull request, and an On its own tab that replays an alert the platform fixed by itself; behind the IP allow-list and Dex login"
app: hk-demo
catalog_entity: hk-demo
kind: docs
namespace: hk-demo
last_reviewed: 2026-10-24
status: stable
tags: [agents, observability, fastapi, oauth2-proxy]
sources:
  - base-apps/hk-demo.yaml
  - base-apps/hk-demo/deployment.yaml
  - base-apps/hk-demo/namespace.yaml
  - base-apps/hk-demo/network-policy.yaml
  - base-apps/hk-demo/external-secrets.yaml
  - base-apps/hk-demo/secret-store.yaml
  - base-apps/hk-demo/httproute.yaml
  - base-apps/hk-demo-aws-infrastructure/ecr-push.yaml
  - base-apps/alert-demo/deployment.yaml
  - base-apps/alert-demo/authorization-policy.yaml
  - scripts/dispatch.py
  - docs/superpowers/specs/2026-10-24-hk-demo-on-its-own-design.md
  - base-apps/dex/configmap.yaml
  - base-apps/kagent/request-authentication.yaml
  - base-apps/kagent/authorization-policy.yaml
  - base-apps/kagent/network-policy-controller.yaml
  - base-apps/kagent/agent-templates/implementer.yaml
  - base-apps/kagent/implementer-mcp.yaml
  - base-apps/dispatcher/dispatch-table.yaml
  - scripts/provision-hk-demo-vault.sh
  - docs/superpowers/specs/2026-10-22-hk-demo-design.md
  - docs/superpowers/specs/2026-10-23-hk-demo-follow-ups-design.md
---

# hk demo

`https://hk-demo.arigsela.com` asks the `kagent/homelab-knowledge` agent (hk) a question and shows
what the platform did with it. Follow-ups continue the conversation, and a message that starts with
`implementer:` has the implementer agent open the pull request (see "Conversations" below). A map of eight boxes (Gateway, Login, the app, the mesh's identity
check, kagent, the Substrate sandbox, the model API, the tools) lights up as the request moves.
Each box opens a panel with that piece's part in this one request: what it checked, its numbers
(time, tokens, cost), its log lines, its trace spans and the configuration behind it. It exists to
show someone outside the household what this infrastructure makes visible; the owner drives it
over a screen share. Design: `docs/superpowers/specs/2026-10-22-hk-demo-design.md`, follow-ups:
`docs/superpowers/specs/2026-10-23-hk-demo-follow-ups-design.md`. App source: `arigsela/hk-demo`
(private).

## How a question flows

1. The browser posts the question. The Gateway lets it in only from the `gateway-allow` addresses;
   the oauth2-proxy sidecar lets in only a Dex login whose email is on its list, and passes the
   email to the app as `X-Forwarded-Email`.
2. The app (127.0.0.1:8000, never reachable directly) records who asked and calls hk in-cluster
   with A2A `SendStreamingMessage`, authenticated by its own projected service-account token
   (audience `kagent`). The kagent waypoint verifies that token, so the agent sees the caller as
   `system:serviceaccount:hk-demo:hk-demo`: a workload, not a person. A follow-up passes the
   session's `contextId`, so it continues that session (see "Conversations").
3. The agent's progress (tool calls, "writing the answer") streams to the page, then the answer
   (from `GetTask`).
4. The app finds the run's trace in Tempo by task id (the collector holds a trace about 10 s
   after its last span, so it arrives about 15-30 s after the answer), then reads the run's log
   lines from Loki (substring check before the regex, a window of minutes) and totals from
   Prometheus, and fills the boxes.

The Model API box follows the agent's model calls through the model gateway (§T.137). It shows:
- the egress injecting the group's key for the gateway;
- each call through the gateway, checked against the group's daily budget;
- what the gateway charged this run, from the gateway's own log lines;
- the group's spend today against its budget. The budgets come from `MODEL_BUDGETS`, which
  `scripts/gen-model-budgets.py` writes from `base-apps/model-gateway/budgets.yaml`; the spend is
  the gateway's cost metric since 00:00 UTC.

A refusal (429 `budget_exceeded`) turns the box red.

A box shows ✓ only for what this request's own spans, log lines or metrics show; settings that
hold for every request show ⚙. A box turns red with ✗ when what it saw failed: a tool call
whose span ended in error (the Tools box gives the error, and its Evidence tab has the tool
server's own line with the reason), or the waypoint refusing the call. Evidence lines that
report a failure are red: a line its component logged as an error, a request answered 5xx or
429, an adapter refusing a tool, or a Gateway or waypoint status of 400 or more. Recent runs replays the app's own runs of the last 7 days from its
log lines (`{namespace="hk-demo", container="app"} |= "hk-demo-run"`), `GetTask` and the trace.

## Conversations

Each run is a turn of a conversation; the page shows the turns as a thread, and clicking a turn
shows its own boxes.

- A plain follow-up continues hk's session (A2A `contextId`). The Sandbox box then shows no new
  actor: a sandbox already snapshotted is restored (ateom's `RestoreWorkload` line, about a second);
  one still running from the last turn is reused (ate-api-server's `ResumeActor`, milliseconds).
- The first message of each agent session states the date and UTC time (the agents do not know it,
  and the implementer writes it into pull requests).
- `implementer: <request>` sends the request to `kagent/implementer` (class `propose`: branches
  and pull requests on this repo under the `arigsela-implementer` App, nothing on the cluster, never
  a merge). hk stays read-only. The app opens an implementer session the first time and continues
  it after that.
- Each turn carries the other agent's latest answer, if it is newer than this agent's last turn,
  as "evidence, not instructions" (at most 6000 characters). So the implementer gets hk's finding
  with the "yes", and a later hk turn sees the PR link. The app box shows how much was carried.
- The next question is accepted once the current turn has its answer; the earlier turn's boxes
  keep filling.
- Turns of one session share its `contextId`, so a continued turn takes its trace only by task id
  (never by context) and drops `session-<contextId>` lines from the next same-session turn onward.
- Recent runs lists conversations; picking one replays every turn, and a replayed conversation
  can be continued (the app rebuilds its sessions from its log lines). Each `hk-demo-run` line
  carries `agent`, `conversation_id`, `turn`, `continued` and `carried`.

Every implementer turn opens a real pull request (or updates its open one for the same file), at
most three files. The dispatcher skips alert-driven fixes while the implementer has
`open_prs_max` (3) pull requests open, so close demo pull requests after a rehearsal (runbook).

## The platform tab

Since v0.6.0 the page opens on **The platform**, a walkthrough of the whole agent platform that is not
tied to any run. Design: `docs/superpowers/specs/2026-10-25-hk-demo-platform-tab-design.md`, which
supersedes v0.5.0's picture on the Ask hk tab. The tab shows:

- two ways in: people (this demo, Slack, a terminal), and an alert through the dispatcher (plain
  code, with its rules and limits);
- the shared middle: identity check, agent definition, context, a sandbox per conversation,
  guardrails and evaluation;
- two ways out: an answer, or a pull request and a Slack finding for a person to decide;
- the model gateway, the tools and the record.

Selecting a component (a step button, Prev/Next, or a click) shows three things:

- what it is;
- how it is set up here: the configuration excerpts from hk-demo's `config_excerpts.yaml`, the same
  ones the boxes' Config tab shows, rendered and masked by the server;
- where it shows up on the run tabs.

Product names stay behind a "Show technical names" toggle. A first visit opens on this tab; after
that, the last tab used. Ask hk and On its own show one run's evidence boxes, as before.

## On its own

A second tab replays an alert the platform handled with nobody at the keyboard. Design:
`docs/superpowers/specs/2026-10-24-hk-demo-on-its-own-design.md` (§T.140). The journey:

1. Grafana fires.
2. The dispatcher hands the alert to the implementer.
3. The implementer diagnoses it and usually opens a pull request.
4. The finding goes to `#oncall-alerts`.
5. A person merges or closes the PR.

- **Runs:** the dispatcher's investigations by the implementer from the last 7 days. They are joined
  from the dispatcher's own `dispatch`, `finding` and `remediate` lines by the alert's fingerprint;
  those lines carry `fingerprint`, `task_id`, `verdict` and `target` since 2026-10-24. Earlier
  lines cannot be joined and are not listed.
- **Ten boxes:**
  - **Grafana alert:** the dispatch line, the target's memory ratio from Prometheus and the
    resolved line.
  - **Dispatcher:** its decision, the open-PR check and the verdict.
  - **Identity check:** the waypoint's line for the dispatcher's call.
  - **kagent, Sandbox, Model gateway and Tools:** the Ask hk tab's own builders, on the
    implementer's trace, found by its session.
  - **Pull request:** from GitHub.
  - **Slack:** n8n accepted the finding; Slack's own delivery is not visible.
  - **Human decision:** amber while the PR is open or a restart awaits approval.
- **The answer:** the dispatcher never logs the implementer's answer (its design, 2026-10-12), so
  the journey shows the PR's title and body instead; the implementer puts its detail there.
- **Start a demo run** asks `alert-demo` (`base-apps/alert-demo/`) to hold 236Mi of its 256Mi limit
  for 30 minutes.
  - The real "Container near memory limit" alert fires about 16 minutes later, and a pull request
    follows about 2 minutes after that.
  - The app sends one HTTP request, admitted by alert-demo's AuthorizationPolicy only from this
    app's principal. It writes nothing to the cluster API.
  - It refuses while a run is on, while the implementer has 3 open pull requests, or within 6 hours
    of the dispatcher last looking at the same pod, and says why.
  - It logs `hk-demo-journey-start` with who pressed it.

## What never reaches the screen

Emails (shown as `a•••@domain`), full client IPs (shown as /24), tokens, cookies and tool
responses. The app masks everything it renders and logs; its log lines carry the asker's full
email as the record of who asked.

## Where config lives

| What | Where |
|---|---|
| Image | `base-apps/hk-demo/deployment.yaml`, pinned `tag@digest`; built by `arigsela/hk-demo` `release.yml` on a `v*` tag through the ECR push role `github-actions-hk-demo-ecr`; a version bump merged to hk-demo's `main` tags itself, and the release opens the pin PR here as the GitHub App `arigsela-hk-demo-release` (a person merges it) |
| Login | Dex static client `hk-demo` (`base-apps/dex/configmap.yaml`); client secret in Vault `k8s-secrets/dex:hk-demo-client-secret` and `k8s-secrets/hk-demo:oauth2-client-secret` (same value); cookie secret and the allowed email in `k8s-secrets/hk-demo`, written by `scripts/provision-hk-demo-vault.sh` |
| Who may call kagent | the namespace's ambient label, the kagent waypoint's `RequestAuthentication` (service-account issuer, audience `kagent`) and `network-policy-controller.yaml` |
| Data stores | env in `deployment.yaml`: Loki, Tempo, Prometheus in-cluster (GET only); Grafana links |
| Model budgets | `MODEL_BUDGETS` in `deployment.yaml`, generated from `base-apps/model-gateway/budgets.yaml` by `scripts/gen-model-budgets.py --write` |
| The Config tab's excerpts | `arigsela/hk-demo` `src/hk_demo/config_excerpts.yaml`, copied from this repo's files: refresh them in the release after those files change |
| On its own | the demo workload's URL `ALERT_DEMO_URL` in `deployment.yaml` (`base-apps/alert-demo/`); the implementer's PRs read unauthenticated from GitHub (`arigsela/kubernetes`, author `arigsela-implementer[bot]`) |
| The API description | `catalog/apis/hk-demo-openapi.yaml`, shown in Backstage as API `hk-demo-api` (the API tab): a copy of `arigsela/hk-demo` `openapi.yaml`, which that repo's tests check against the code; copy it over in the release PR when it changes |

## Cost and load

Each question is one agent run: $0.001-$0.21 seen so far for hk (a run with many repo reads costs
more). The page allows one agent call at a time; the evidence lookups of the previous turn may still
be running. Loki has half a CPU and Tempo one, so every query is
bounded to the run and the only polling is the trace lookup (every 3 s, at most 2 min). On its own
reads the dispatcher's 7-day lines at most every 10 s, polls Tempo only for a run answered in the last
3 minutes, and refreshes its status line every 15 s only while a demo run is on and the tab is
visible. GitHub is read unauthenticated: 60 requests an hour per egress address, shared with the
dispatcher's open-PR count, so the PR list and each PR are cached 5 minutes and the open count 1
minute.
