# Agent observability dashboards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One "Agent Platform" landing dashboard (Grafana home) plus five deep dives and the MCP dashboard,
all in the Agents folder, showing tokens, traces, logs, metrics and traffic of the kagent agents.

**Architecture:** The collector gains small agent-platform pipelines (UpdateTask noise dropped, tool output
scrubbed, an estimated cost stamped on model spans, a service graph and a run-start gauge on :8889). Tokens,
cost, model and tool numbers come from Tempo TraceQL metrics, which sum span attributes exactly (verified:
152,541 input tokens for trace 3ace5cd9…, the same as its spans). One generator writes every Agents
dashboard as its own ConfigMap; Grafana mounts them through one projected volume and opens the landing
page as home.

**Tech Stack:** OpenTelemetry Collector contrib 0.162.0 (filter, transform, service_graph,
signal_to_metrics), Tempo 3.1.0 (TraceQL metrics, max 24 h per query), Grafana 11.6.16, Prometheus 3.0.1,
Loki, Python 3 generator + pytest, bash demo script.

**Spec:** `docs/superpowers/specs/2026-10-21-agent-observability-dashboards-design.md`

## Executed 2026-10-21, with these deviations

Tasks ran in this session (the owner asked to continue without them). Loading the dashboards into
Grafana 11.6.16 showed it cannot run TraceQL range metrics against Tempo 3.1 nor `sum_over_time`,
so Task 1 grew `signal_to_metrics` counters (tokens, cost, model and tool calls, runs) and Task 4
reads them with `window_total()`; TraceQL stays instant-only. See the design doc's *Changed while
building* and SPEC §V.105, §V.106.

## Global Constraints

- Prometheus panels use `{"type": "prometheus"}` (unpinned); Loki panels `{"type": "loki", "uid": "loki"}`;
  Tempo panels `{"type": "tempo", "uid": "tempo"}`.
- Every traces pipeline starts with `memory_limiter` (beacon §V.41); every traces pipeline that exports spans
  runs `transform/scrub` before `batch` (beacon §V.42); tracing files cite beacon sections only (beacon §V.27);
  `checksum/config` = sha256 of the ConfigMap `data` (json.dumps, sort_keys).
- `tests/beacon/test_collector.py` replaces every exporter with `file/out` + `prometheus`: new pipelines may
  export only to connectors or to `prometheus`.
- Rare events (eval gate, dispatcher, crashes, restarts) use a fixed 7-day window and show 0, never
  "No data" (`or vector(0)` / `noValue: "0"`).
- Tool counts exclude the wrapper span `execute_tool (merged)` (`span.gen_ai.tool.name != "(merged tools)"`).
- Token and cost panels say "kagent agents"; cost panels say "estimate". Prices ($/MTok, input / cache write
  5 m / cache read / output, Claude API reference cached 2026-10-06): claude-sonnet-5-5 2 / 2.5 / 0.2 / 10,
  claude-opus-5-5 4 / 5 / 0.2 / 20, claude-haiku-5-5 0.1 / 0.125 / 0.01 / 0.5.
- No prompt, answer or tool output on screen: `gcp.vertex.agent.tool_response` is deleted in the collector.
- Default range `now-24h` (Tempo metrics answer at most 24 h), refresh 30s. Folder Agents.
- PRs carry label `claude-generated`; push once; commits end with the session's attribution lines.

## Review Focus

1. No agent run in the last 7 days → `trace_id` variable empty → the trace and logs panels must say so
   (panel `noValue` text), not fail; Task 4 test asserts `noValue` on both.
2. An empty `session` variable must not make the logs query match every line; Task 4 test pins the regex
   `session-${session}(?:[^0-9a-f-]|$)` and checks with Python `re` that an empty session matches no
   `session-<uuid>` line.
3. A model with no price row (a new model) must not break cost: spans get no cost attribute; the tokens
   panels still count it. Task 1 running-collector test sends one unknown-model span and asserts no
   `agent.cost.estimate_usd`.
4. A collector restart must not double count: no counter is used for tokens (Tempo sums spans); the run
   gauge is a gauge. Task 4 test asserts no panel uses `increase(`/`rate(` on `agent_run_start_seconds`.
5. Grafana must still start: datasource YAML parses, the projected volume names every generated ConfigMap,
   and the home path is a file that volume provides (Task 3 tests).

---

### Task 1: Collector agent-platform pipelines

**Files:**
- Modify: `base-apps/tracing/collector.yaml` (drafted already; finish + cost transform)
- Test: `tests/agent-capability/test_agent_traces.py`

**Interfaces:**
- Produces (Prometheus, via :8889): `agent_run_start_seconds{service_name, trace_id, gen_ai_conversation_id}`
  (gauge, Unix seconds), `traces_service_graph_request_total{client, server, connection_type, failed}`,
  `traces_service_graph_request_failed_total`, `traces_service_graph_request_{server,client}_seconds_*`.
- Produces (Tempo span attribute): `agent.cost.estimate_usd` (double) on `generate_content` spans of priced
  models.

- [ ] **Step 1: Write failing tests** in `test_agent_traces.py`:
  `test_v105_taskstore_update_spans_never_reach_tempo` (main `traces` pipeline has `filter/noise` before
  `tail_sampling`, condition on `kagent.runtime.v1.TaskStoreService/UpdateTask`),
  `test_v105_tool_output_is_scrubbed` (`delete_key(attributes, "gcp.vertex.agent.tool_response")` in
  `transform/scrub`), `test_v105_cost_prices_match_the_spec` (parse the three model prices out of
  `transform/genai-cost` statements, compare to the table above),
  `test_v105_agent_platform_pipelines_export_only_metrics` (`traces/agent-platform` exporters ⊆ connectors;
  `metrics/agent-platform` exporters == `["prometheus"]`), and a running-collector test
  `test_v105_running_collector` (Docker or `OTELCOL_BIN`, like `tests/beacon/test_collector.py`; skips
  otherwise unless `CI`): post a synthetic run (ingress SERVER→controller, controller CLIENT→agentgateway
  SERVER→agent, `invoke_agent`, 2 `generate_content claude-sonnet-5-5` with tokens, one
  `generate_content some-new-model`, `execute_tool` carrying `gcp.vertex.agent.tool_response`, UpdateTask
  CLIENT/SERVER, `HTTP POST` to `api.anthropic.com`); assert exported spans have no UpdateTask, no
  `tool_response`, sonnet spans carry `agent.cost.estimate_usd` = exact sum, the unknown model none;
  `/metrics` has `agent_run_start_seconds{…trace_id="<id>"…}` and
  `traces_service_graph_request_total{client="<agent>",…server="api.anthropic.com"}`.
- [ ] **Step 2: Run** `python3 -m pytest tests/agent-capability/test_agent_traces.py -q` → new tests FAIL.
- [ ] **Step 3: Implement** `transform/genai-cost` (span context, `generate_content` spans; uncached input =
  input − cache_read − cache_write, missing buckets = 0 via `cache[...]`) in the `traces` pipeline after
  `filter/noise`; add `gen_ai.conversation.id` (optional) to the run gauge; update header comment and
  checksum.
- [ ] **Step 4: Run** the file plus `OTELCOL_BIN=… python3 -m pytest tests/beacon -q` → all PASS.
- [ ] **Step 5: Commit** `feat(tracing): agent-platform pipelines for the Agents dashboards (§V.105, §B.38)`.

### Task 2: Scrape kagent-tools

**Files:**
- Modify: `base-apps/kagent.yaml` (`kagent-tools.podAnnotations`)
- Test: `tests/agent-capability/test_kagent_chart_config.py`

- [ ] **Step 1:** test `test_kagent_tools_metrics_are_scraped` on the rendered chart: Deployment
  `kagent-tools` pod annotations `prometheus.io/scrape: "true"`, `prometheus.io/port: "8085"`,
  `prometheus.io/path: /metrics`; no `initContainers` (§V.103).
- [ ] **Step 2:** run → FAIL. **Step 3:** add the annotations with a comment. **Step 4:** run → PASS.
- [ ] **Step 5: Commit** `feat(kagent): scrape kagent-tools' tool-call metrics`.

### Task 3: Grafana datasources, mount, home

**Files:**
- Modify: `base-apps/logging/grafana-deployment.yaml`
- Test: `tests/dashboards/test_grafana_datasources.py`

**Interfaces:**
- Consumes: ConfigMap names from Task 4 (`AGENT_CONFIGMAPS` in `scripts/gen-agent-dashboards.py`).
- Produces: mount `/var/lib/grafana/dashboards/agents` (projected), home
  `/var/lib/grafana/dashboards/agents/agent-platform.json`.

- [ ] **Step 1: Tests:** derived field regex captures the id from `trace_id=<32hex>`,
  `"trace_id":"<32hex>"` and `trace.id=<32hex>` and nothing from `trace_id=-`; Tempo `serviceMap.datasourceUid
  == "PBFA97CFB590B2093"`; `tracesToLogsV2.customQuery` true with a query on `${__trace.traceId}` across
  all namespaces; checksum test unchanged (recompute).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement; keep the single derived field, `$$` escapes.
- [ ] **Step 4:** run → PASS. **Step 5:** commit with Task 4 (the mount needs the ConfigMaps).

### Task 4: Dashboard generator and the seven dashboards

**Files:**
- Create: `scripts/gen-agent-dashboards.py`, output `base-apps/logging/grafana-dashboards-agents.yaml`
- Delete: `base-apps/logging/grafana-dashboard-mcp.yaml` (its ConfigMap name and uid move into the output)
- Create: `tests/dashboards/test_agent_dashboards.py`; modify `tests/dashboards/test_mcp_dashboard.py`
- Modify: `.github/workflows/validate.yaml` (dashboards job: `--check` step)

**Interfaces:**
- Produces: `AGENT_CONFIGMAPS: dict[str, str]` (ConfigMap name → JSON file name),
  `build_all() -> dict[str, dict]` (file name → dashboard), `render() -> str`, CLI `--repo-root`, `--check`
  (exit 1 + "out of date" on drift).
- Dashboards (uid → ConfigMap): `agent-platform` → `grafana-dashboard-agent-platform`,
  `agent-quality`, `agent-tokens`, `agent-traffic`, `agent-sandboxes`, `agent-alerts` (same pattern),
  `mcp-tool-calls` → `grafana-dashboard-mcp` (unchanged name and uid).

- [ ] **Step 1: Tests** (`test_agent_dashboards.py`): `--check` passes and catches a hand edit; ids unique,
  grid ≤ 24; datasource convention per type; every dashboard has tag `agents` and a link back to
  `agent-platform`; every landing tile links to an existing uid; every rare-event panel has `timeFrom: "7d"`
  and a zero fallback; no Tempo tool query counts the merged wrapper; `trace_id` and `session` variables read
  `agent_run_start_seconds` (the metric name the collector config defines) without `rate`/`increase`;
  logs-regex empty-session check (Review Focus 2); trace/logs panels have `noValue`; every ConfigMap in the
  output is in the Grafana projected volume and the home path file exists; MCP dashboard keeps uid
  `mcp-tool-calls` and its 7 queries.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement the generator** (Builder like `gen-cluster-overview-dashboard.py`, per-panel
  datasource). Panels per the spec's *Data sources per dashboard*; TraceQL metrics targets
  `{"queryType": "traceql", "query": "<q> | <fn>", "metricsQueryType": "range"}`; Tempo service map
  `{"queryType": "serviceMap"}`; recent runs = TraceQL search table with a data link
  `/d/agent-platform?var-trace_id=${__value.raw}&${__url_time_range}`.
- [ ] **Step 4:** run generator, tests → PASS; load every dashboard into local Grafana 11.6.16 wired to
  port-forwarded Prometheus/Loki/Tempo and confirm each panel returns data (script in scratchpad).
- [ ] **Step 5: Commit** `feat(logging): Agent Platform home and agent dashboards (§T.135, §V.106)`.

### Task 5: Demo script

**Files:**
- Create: `scripts/agent-demo.sh`; test `tests/dashboards/test_agent_demo.py`

- [ ] **Step 1: Test** with a stub `ASK_HK`: runs three read-only questions (homelab-knowledge,
  k8s-reader, istio-agent), stops on a non-zero exit, prints `https://grafana.arigsela.com/d/agent-platform`.
- [ ] **Step 2–4:** fail, implement, pass. **Step 5:** commit `feat(scripts): agent demo warm-up`.

### Task 6: Docs, SPEC, PR

**Files:** `base-apps/logging/docs.md`, `base-apps/logging/runbook.md`, `base-apps/tracing/docs.md`,
TechDocs copies (`scripts/gen-techdocs.py`), `SPEC.md` (T135, V105, V106, B38), the design doc's calls.

- [ ] Run `gen-techdocs.py --check`, `gen-okf.py --check`, `validate-agent-docs.py`, all touched suites.
- [ ] One PR, label `claude-generated`, pushed once.
