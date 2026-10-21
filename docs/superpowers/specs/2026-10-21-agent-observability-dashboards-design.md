# Agent observability dashboards: design

Date: 2026-10-21. Status: approved to build. The owner asked to continue without them ("its monday so
lets get this done today please continue without me"), so the open questions below were settled as
calls, not answers.

## Intent

A live demo of what this platform lets you see about its AI agents, from Grafana alone. The owner's
takeaway for the audience, in their words: *"they will have visibility towards tokens/traces/logs/
metrics related to our agents and the traffic between them"*. Venue and date: open (see *Open
questions*). An earlier guess, the ATL DevCon talk, was wrong: that brief is a recap of the 17 Oct
conference for the owner's engineering team at CHG. Date: not given; build all of it now.

The owner's other complaint: Grafana shows several folders and no single place to start. The demo
needs one page that opens first and leads everywhere else.

## Goals

1. **One landing dashboard, "Agent Platform"**, in the `Agents` folder, set as Grafana's home
   dashboard. Layout chosen by the owner: one page, two halves.
   - **Top half: six tiles.** Each is a headline number, a sparkline, and a link to a deep-dive
     dashboard:
     | Tile | Headline | Opens |
     |---|---|---|
     | Agents | agent runs today; agents held by the eval gate | Agent Quality |
     | Tools / MCP | tool calls; refused calls | MCP Tool Calls (exists) |
     | Gateway + tokens | tokens today (and an estimated cost) | Tokens & Models |
     | Agent traffic | callers into kagent; error rate | Agent Traffic |
     | Sandboxes | session starts; crashes | Sandboxes |
     | Alerts → agents | findings; restarts | Alerts to Agents |
   - **Bottom half: follow one request.** A strip of the hops a request takes (entry → kagent
     controller → Substrate sandbox → agent → model and tools), each hop with a live rate and
     latency; the latest agent run as a trace (Tempo); that run's logs (Loki).
2. **Five deep-dive dashboards** (plus the existing MCP Tool Calls) in the same folder, each
   linking back to the landing page.
3. **Tokens without a new LLM gateway.** The owner prefers not to build one ("no it shouldnt if
   possible but if its necessary im ok wih that too"). It is not necessary: see *Data sources*.
4. **Data is there when the demo starts.** A small script the owner runs before going on stage
   starts a few agent runs, so every panel and the "latest run" have fresh data.

## Non-goals

- The small LLM gateway (an agentgateway in front of the model API). Revisit only if trace-derived
  tokens turn out wrong.
- A Grafana Postgres datasource over kagent's database. kagent 1.0 stores events as protobuf
  bytes (`scripts/agent-audit.py` decodes them in Python), so SQL can only count rows.
- Reorganising the non-agent folders (Kubernetes, Istio, Security). They stay; the landing page
  links to them only where a tile needs it. (Builder's call to add a small "other dashboards" link
  row.)
- New alert rules.
- Triggering real alerts or eval-gate scoring from the demo script. Both have side effects (Slack
  posts, a restart request against the cap of 2 per day, model spend, possibly a held agent).

## Anti-goals (a failure even if it works)

- Empty or "No data" panels during the demo.
- Fake or seeded numbers on the real dashboards. The demo script makes real agent runs.
- Double counting. §B.36 was exactly this; every sum must count each sample once.
- Prompt or answer text on screen. kagent's content capture
  (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`) stays off.
- A trace view buried in noise (see *Collector changes*).

## What exists (verified 2026-10-21)

- **Traces:** since #926 (§V.104), kagent's controller, every session, Substrate (`ateapi`,
  `atelet`, `ateom-gvisor`, `agentgateway` = atenet-router/egress) and the MCP adapters send OTLP to
  `otel-collector.tracing`; every trace with `service.namespace=kagent` is kept. One `ask hk` run
  (trace `3ace5cd912380b069823402928b147e5`, 78 s, 617 spans, 7 services) showed the whole path:
  `main-istio.istio-ingress` → `kagent-controller` (A2A `SendMessage`) → `ateapi`
  (`CreateActor`, `CreateActorEgressPolicy`) → `homelab-knowledge` (`invoke_agent homelab_knowledge`)
  → 5 × `generate_content claude-sonnet-5-5` and 9 × `execute_tool <tool>` → `agent-docs-mcp`
  `tools/call` (agentgateway) and `agentgateway` (Substrate egress).
- **Tokens on spans:** each `generate_content <model>` span carries `gen_ai.request.model`,
  `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`
  (included in input), `gen_ai.usage.reasoning.output_tokens`. The agent is the span's resource
  `service.name` (also `gen_ai.main_agent.name`); `kagent.runtime=adk-go`. That run: 152.5k input
  (149k cache reads), 3.1k output. kagent's contract: `docs/architecture/telemetry-contract.md`
  @ v1.0.0-alpha14.
- **Noise:** that run also had 524 `kagent.runtime.v1.TaskStoreService/UpdateTask` spans (262 on
  the controller, 262 on the session), one per streaming status update.
- **Metrics in Prometheus:**
  - MCP adapters: `agentgateway_mcp_requests_total{server,resource,method}`,
    `agentgateway_request_duration_seconds_*` (§T.134; MCP Tool Calls dashboard).
  - kagent waypoint (Istio ambient, `reporter="waypoint"`): `istio_requests_total` and
    `istio_request_duration_milliseconds_*` with `source_workload` (eval-gate, kagent-controller,
    kagent-ui, main-istio, slack-bridge) → `destination_workload` (kagent-controller, kagent-tools,
    kagent-ui, the three MCP adapters), `response_code`, `response_flags`.
  - Substrate (`namespace="ate-system"`): `ate_actor_lifecycle_operation_duration_seconds_*`
    (`ate_actor_operation_name` create/resume/suspend/delete, `ate_template_name` =
    `<agent>-<hash>`, `error_type`), `ate_actor_crashes_total`, `ate_actor_restore_duration_seconds_*`,
    `ate_actor_checkpoint_duration_seconds_*`, `ate_scheduler_assignment_duration_seconds_*`,
    `ate_workerpool_workers{ate_worker_state}`, `atelet_snapshot_size_bytes_*`,
    `ate_imagecache_requests_total`, `ate_actor_stats_cpu_time_seconds_total`.
  - Collector: `tracing_spans_received_total{service_name}` (before sampling).
- **Not scraped yet:** `kagent-tools` serves `kagent_tools_mcp_invocations_total`,
  `kagent_tools_mcp_invocations_failure_total`, `kagent_tools_mcp_registered_tools` on :8085. The
  chart takes `kagent-tools.podAnnotations`.
- **Logs in Loki:**
  - eval gate: `{namespace="kagent", container="eval-gate"}`, JSON `event: eval-gate` with `agent`,
    `pass_rate`, `baseline`, `held`, `regression`, `failed`, `failed_detail`, `hard`, `judge`,
    `passed`, `entries`, `revision`, `skipped` (unchanged runs). 277 lines in 14 days.
  - dispatcher: `{namespace="dispatcher"}`, JSON events `dispatch` (`alertname`, `rule`, `route`,
    `decision`, `status`, `source`), `finding` (`agent`, `rule`, `session`, `state`, `error`),
    `remediate` (`rule`, `verdict`, `decision`), `decision` (`approved`, `approved_by`, `session`).
    14 days: 144 dispatch, 31 finding, 9 remediate, 1 decision.
  - kagent controller JSON logs carry `"trace_id":"<32 hex>"`; Substrate's agentgateway logs
    (`app=atenet-router|atenet-egress`) carry the trace id too; a session's sandbox lines come
    through `container="ateom"` labelled `ate.actor.name: session-<id>` and
    `ate.template.name: <agent>-<hash>`.
  - MCP adapters log `trace.id=<hex>`; Grafana's Loki derived field matches only `trace_id=`, so
    neither the adapters' nor the controller's JSON ids link to Tempo today.
- **Grafana:** 11.6.16; providers `Kubernetes`, `Istio`, `Security`, `Agents`
  (`/var/lib/grafana/dashboards/agents`, ConfigMap `grafana-dashboard-mcp`). Datasources: Prometheus
  (default, unpinned in dashboards), Loki uid `loki`, Tempo uid `tempo` (tracesToLogsV2 →
  Loki, filterByTraceID, tag `service.name`→`app`; nodeGraph enabled; no `serviceMap`).

## Data sources per dashboard

### Agent Platform (landing)
- Tiles: agent runs = Substrate `create` operations (count, by template → agent name via
  `label_replace`); held agents = latest eval-gate `held` per agent; tool calls = MCP adapter +
  kagent-tools invocations; refused = MCP 400s; tokens = the new token metric; callers/error rate =
  waypoint metrics; session starts/crashes = Substrate; findings/restarts = dispatcher logs.
- Bottom half: the strip uses waypoint (entry, kagent), Substrate (sandbox start), span metrics
  (agent and model latency), MCP/kagent-tools (tools). Latest run = a Tempo panel on the newest
  trace with `{resource.service.namespace="kagent" && name=~"invoke_agent.*"}`; logs = Loki on that
  trace id plus its session.

### Agent Quality (eval gate)
Pass rate per agent over time against its baseline, agents held now, regressions, the failed
entries of the latest scored run, runs per day (scored vs unchanged).

### Tokens & Models
Input/output/cache-read tokens per agent and per model over time; model calls and their latency
(span metrics on `generate_content`); estimated cost per agent and model from a price table;
largest runs by tokens (Tempo, linking to the trace).

### Agent Traffic
Who calls whom: a service graph from traces (collector `service_graph` → Prometheus, shown as a
node graph) plus the waypoint view: requests/min by source → destination, error rate (4xx/5xx,
response flags), p95 latency.

### Sandboxes (Substrate)
Session lifecycle operations per agent (create/resume/suspend/delete), their p95 and errors,
crashes, restore and checkpoint p95, scheduler delay, worker pool states, snapshot sizes, image
cache, CPU per agent.

### Alerts to Agents (dispatcher)
Alerts received by rule and status, routed vs not, findings per agent and outcome, verdicts,
remediation decisions, restarts per day against the cap of 2 (`limits.restarts_per_day`),
approvals with who approved, a recent-events log panel.

### MCP Tool Calls (exists)
Gains a kagent-tools row (built-in Kubernetes tools: calls and failures by tool) once scraped.

## Platform changes the dashboards need

1. **Collector (`base-apps/tracing/collector.yaml`):**
   - Token metrics: a traces → metrics connector over `generate_content` spans that sums
     `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`
     with dimensions agent (`service.name`) and `gen_ai.request.model`, exported through the existing
     Prometheus exporter (:8889, already scraped). `sum` (alpha) or `signal_to_metrics` both exist in
     0.162.0; builder's choice. It must count every model call once, so it reads spans before
     sampling, like `traces/count`.
   - Span metrics for latency of agent runs, model calls and tool calls (`span_metrics`), and the
     service graph (`service_graph`), bounded to the kagent namespace's services to keep series
     counts small.
   - Drop `TaskStoreService/UpdateTask` spans before Tempo (a `filter` processor). Decided by the
     owner after seeing the run.
   - Keep the beacon rules: `memory_limiter` first in every traces pipeline (beacon §V.41),
     `transform/scrub` before every exporter (beacon §V.42), checksum annotation current,
     `tests/beacon/test_collector.py` still passing (it swaps exporters; new pipelines must survive
     that swap), and tracing files cite beacon sections only (beacon §V.27).
2. **Grafana datasources (`base-apps/logging/grafana-deployment.yaml`):** Tempo `serviceMap` →
   Prometheus, so the service graph renders; tracesToLogs query by trace id across kagent and
   Substrate; Loki derived field matching `trace_id=`, `"trace_id":"…"` and `trace.id=`.
3. **kagent chart (`base-apps/kagent.yaml`):** `kagent-tools.podAnnotations` for
   `prometheus.io/scrape|port|path` (:8085). One container, no init container (§V.103).
4. **Grafana home:** the landing page as the default home dashboard (`GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH`
   or the org preference; builder's choice, in git).
5. **Demo script (`scripts/`):** runs a few agent sessions with `scripts/ask-hk` (different agents,
   one that calls an MCP tool), then prints the Grafana URL. Read-only questions only.

## Calls made without the owner (check these)

- **Rare events never show "No data".** Dispatcher, eval-gate, crash and restart panels and
  headlines use their own longer window (last 7 days, labelled on the panel; Loki keeps 30 d,
  Prometheus 15 d, Tempo 7 d) and show 0 when nothing happened. They fire about once a day or less
  (31 findings and 9 remediations in 14 days; the eval gate scores only agents whose definition
  changed, in a PostSync hook), so a 6 h window would be empty during the demo.
- **Cost estimate keeps four token buckets:** uncached input, cache write, cache read, output.
  Cache writes cost more than plain input. kagent's contract defines
  `gen_ai.usage.cache_write.input_tokens`, but the sample run carried none; the bucket is summed
  when present and shown as 0 otherwise.

- Dashboard names and the six tiles' exact headline numbers.
- Estimated cost per model: the owner approved a playback that suggested it; prices come from
  Anthropic's published pricing at build time and are labelled "estimate".
- Default range last 24 h (was 6 h; see *Changed while building*), refresh 30 s, dark theme.
- Dashboards generated by one script in the repo with a `--check` mode in CI (the pattern of
  `scripts/gen-cluster-overview-dashboard.py`), and the MCP Tool Calls dashboard moves into it.
- Folder stays `Agents`; the landing page is Grafana's home.
- The demo script uses `ask-hk` with the owner's own login.

## Open questions (settled as calls; the owner did not answer)

1. **When and how the audience sees it.** Call: the owner presents from their own screen. Grafana's
   login stays as it is (GitHub OAuth for the owner only, §V.69); widening it is a security change
   for the owner to make. Everything ships now, not in stages. The demo script runs from the home
   network (off it, `ask-hk` gets HTTP 403 at the Gateway allow-list), so it is run before the demo
   if the venue is elsewhere; panels with a 7-day window keep showing those runs.
2. **Tokens for docs-checker** (LangGraph, no OpenTelemetry). Call: not instrumented here. Token and
   cost panels say "kagent agents" in their titles so no one reads them as all model spend.
   Instrumenting docs-checker is a follow-up in the claude-agents repo.
3. **The "latest run" panel.** Call: no new plugin. A recent-runs table (Tempo TraceQL search) sits
   above the trace; clicking a run sets the dashboard's `trace_id` variable, which loads the trace
   and its logs. The demo script prints a landing-page link with its own run's trace id already
   set, so the demo opens on a fresh run.

## Changed while building (2026-10-21; the owner was away, so these are calls too)

- **Tokens, cost and call counts come from collector counters in Prometheus, not from Tempo.**
  Tempo 3.1 sums span attributes exactly with TraceQL metrics, but Grafana 11.6.16 cannot use
  that: it cannot read Tempo 3.1's range responses (`unknown field "step"`) and does not send
  `sum_over_time` at all. So the collector counts each model call's tokens, cost, model and tool
  calls and runs before sampling, and the dashboards read each counter as "last value minus
  the value before the window" (`window_total`), because `increase()` loses the first value of
  every new series and most series here are new. Tempo still answers instant questions
  (per-hop p95, per-run counts), searches and the trace view. No `span_metrics`.
- **The estimated cost is stamped on each model-call span by the collector**
  (`agent.cost.estimate_usd`), so a trace shows each call's cost and the counters sum it.
- **The latest run needs no click** (open question 3): the collector exports each run's start
  time with its trace id, and the landing page's run picker defaults to the newest. The demo
  script therefore just prints the landing page's address.
- **Tool output is scrubbed.** kagent's runtime puts each tool's whole output on its
  `execute_tool` span even with content capture off (§B.38). The anti-goal "no prompt or answer
  text on screen" covers it, so the collector deletes it.
- **Default range is 24 h**, not 6 h: Tempo answers at most 24 h, and a demo usually follows a
  warm-up run by minutes to hours.
- **Rare events:** the eval gate's "held now" looks 30 days back (a hold lasts until the next
  score); event charts from Loki are hourly, because Grafana floors a range query's end to its
  step and a daily bar would never show today.
- **Verified before merge** in a local Grafana 11.6.16 (same image) against the cluster's
  Prometheus, Loki and Tempo, and against a local collector and Prometheus fed the real ask-hk
  trace three times: the token tiles read 457,623 input tokens, 3 × the trace's 152,541.

## Left to the builder

Exact PromQL/LogQL/TraceQL, panel types and styling, connector configuration, the series-bounding
rules, how the generator is laid out, test names, SPEC rows (§T, §V) and docs per the repo's
conventions, and the order of PRs.

## How we'll know it works

- Every panel shows data after the demo script runs once, with no "No data" panel.
- The tokens tile for a run equals the sum of its `generate_content` spans' tokens in Tempo.
- Agent runs today matches the Substrate creates; tool calls match the MCP adapters' counters.
- Clicking any tile reaches its deep dive, and from a trace you reach its logs and back.
- Repo checks pass: the generator's `--check`, the dashboard tests, `tests/beacon`, and the
  collector config run by `tests/beacon/test_collector.py`.
