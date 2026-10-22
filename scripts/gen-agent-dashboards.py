#!/usr/bin/env python3
"""Generate the Grafana dashboards of the Agents folder (§T.135).

Writes base-apps/logging/grafana-dashboards-agents.yaml: one ConfigMap per dashboard, all mounted
into /var/lib/grafana/dashboards/agents through one projected volume (grafana-deployment.yaml).
Edit the panels here and regenerate; never hand-edit the JSON in the ConfigMaps.

    python scripts/gen-agent-dashboards.py --repo-root .          # write
    python scripts/gen-agent-dashboards.py --repo-root . --check  # CI

The landing page, Agent Platform, is Grafana's home dashboard. Every other dashboard links back to it.

Where the numbers come from:
  - Tokens, cost, model calls, tool calls, agent runs: Tempo TraceQL metrics over the kagent agents'
    spans (generate_content / execute_tool / invoke_agent). Tempo sums span attributes exactly; a
    Prometheus counter fed per span would lose the first value of every new series. Tempo answers
    at most 24 h per metrics query, so these panels follow a time range of up to 24 h.
  - The estimated cost: agent.cost.estimate_usd, stamped on each model call by the collector
    (base-apps/tracing/collector.yaml, transform/genai-cost).
  - The latest run: agent_run_start_seconds{trace_id} from the collector (signal_to_metrics).
  - The service graph: traces_service_graph_* from the collector (service_graph), read through the
    Tempo datasource's service map.
  - Mesh traffic: Istio waypoint metrics; sandboxes: Substrate's ate_* metrics; MCP: agentgateway
    and kagent-tools metrics; eval gate and dispatcher: their JSON event lines in Loki.

Datasources: Prometheus unpinned ({"type": "prometheus"}), Loki uid `loki`, Tempo uid `tempo`, the
uids grafana-deployment.yaml keeps stable.
"""
import argparse
import json
import sys
from pathlib import Path

OUT = Path("base-apps/logging/grafana-dashboards-agents.yaml")
MOUNT = "/var/lib/grafana/dashboards/agents"

PROM = {"type": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
TEMPO = {"type": "tempo", "uid": "tempo"}
MIXED = {"type": "datasource", "uid": "-- Mixed --"}
# A panel mixing datasources cannot resolve {"type": "prometheus"} (Grafana sends no query for it);
# its Prometheus targets name this variable instead, which defaults to the default Prometheus, so
# no uid is pinned either way.
PROM_IN_MIXED = {"type": "prometheus", "uid": "${prometheus}"}
PROM_VARIABLE = {"type": "datasource", "name": "prometheus", "label": "Prometheus", "query": "prometheus",
                 "hide": 2, "refresh": 1, "current": {}, "options": [], "regex": ""}

# ConfigMap name -> (file name, uid). grafana-dashboard-mcp and mcp-tool-calls predate this generator
# (§T.134) and keep their names, so existing links and the mount do not change.
DASHBOARDS = {
    "grafana-dashboard-agent-platform": ("agent-platform.json", "agent-platform"),
    "grafana-dashboard-agent-quality": ("agent-quality.json", "agent-quality"),
    "grafana-dashboard-agent-tokens": ("agent-tokens.json", "agent-tokens"),
    "grafana-dashboard-agent-traffic": ("agent-traffic.json", "agent-traffic"),
    "grafana-dashboard-agent-sandboxes": ("agent-sandboxes.json", "agent-sandboxes"),
    "grafana-dashboard-agent-alerts": ("agent-alerts.json", "agent-alerts"),
    "grafana-dashboard-mcp": ("mcp-tool-calls.json", "mcp-tool-calls"),
}
HOME = f"{MOUNT}/agent-platform.json"

# TraceQL selectors. execute_tool (merged) wraps parallel tool calls whose own spans are counted.
RUNS = '{name=~"invoke_agent .*"}'
MODEL = '{name=~"generate_content .*"}'
TOOLS = '{name=~"execute_tool .*" && span.gen_ai.tool.name != "(merged tools)"}'
AGENT = "resource.service.name"
MODEL_NAME = "span.gen_ai.request.model"

# LogQL streams.
# `__error__=""` drops a line that is not JSON (a traceback); a metric query fails on one otherwise.
EVAL = '{namespace="kagent", container="eval-gate"} | json | __error__="" | event="eval-gate"'
DISP = '{namespace="dispatcher"} | json | __error__=""'
REFUSED = '{namespace="kagent", container="mcp-server"} |= "mcp.method.name=tools/call" |= "Unknown tool"'

# Substrate templates are <agent>-<12 hex>; one label per agent across template revisions.
def by_agent(expr):
    return f'label_replace({expr}, "agent", "$1", "ate_template_name", "(.+)-[0-9a-f]{{12}}")'


WAYPOINT = 'reporter="waypoint", destination_workload_namespace="kagent"'
SESSIONS = "ate_actor_lifecycle_operation_duration_seconds_count"  # Substrate: one sample per operation


def window_total(metric, by, window, forward=False):
    """What a counter of rare events added within `window`: the collector's (agent_tokens_input_total,
    ...), Substrate's sessions and crashes, kagent-tools' calls.

    Not increase(): a model call, a session or a crash is a rare event, so many series are new
    inside the window (a new agent or agent version, tool, model or pod) and increase() never
    counts a series' first value. Per series: its last value in the window minus its value just
    before, or all of its last value when it has none before (a new series) or when it fell (it
    restarted from zero: the container restarted in place, or it sat idle past the collector's 47
    hours). The collector exposes each series for 48 hours after its last update, and Substrate and
    kagent-tools for as long as their pod runs, so the value before a window is there; a scrape
    gap longer than the lookback counts that series' whole total once.
    tests/dashboards/test_agent_dashboards.py runs this through promtool.

    forward=True counts the window that starts at the evaluation time, for range queries: Grafana
    floors a range query's end to the step, so a window ending at each step would never show the
    last few minutes, which is where the demo's runs are. Its lookback is 1 hour, as it runs once
    per step; an instant total looks back a day.
    """
    if forward:
        last = f"last_over_time({metric}[{window}] offset -{window})"
        before = f"last_over_time({metric}[1h])"
    else:
        last = f"last_over_time({metric}[{window}])"
        before = f"last_over_time({metric}[1d] offset {window})"
    return f"sum by ({by}) ((({last} - {before}) >= 0) or {last})"


# Agents whose latest score held them. A hold lasts until the agent is scored again, so 30 days
# (Loki's retention). `by (agent)` merges an agent's lines before taking the last one: | json makes
# every field a label, so per-line series would turn "latest" into "any in the window".
HELD_NOW = ('sum(last_over_time({namespace="kagent", container="eval-gate"} | json | __error__="" | event="eval-gate" '
            '| pass_rate!="" | label_format held_n=`{{ if eq .held "true" }}1{{ else }}0{{ end }}` '
            '| unwrap held_n [30d]) by (agent)) or vector(0)')

# Log panels mask email addresses: Substrate's router logs the user's, the dispatcher an approver's.
MASK_EMAILS = ('| line_format `{{ regexReplaceAll "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}" '
               '__line__ "<email>" }}`')

RARE = "7d"  # rare events: their own window, never "No data" (design: calls made without the owner)


# --- targets ------------------------------------------------------------------------------------

def prom(expr, legend="", ref="A", instant=False, interval=None, fmt=None):
    # Explicit: Grafana fills in range=true only for a panel with one datasource, not a mixed one.
    t = {"datasource": PROM, "refId": ref, "expr": expr, "legendFormat": legend,
         "instant": instant, "range": not instant}
    if interval:
        t["interval"] = interval
    if fmt:
        t["format"] = fmt
    return t


def loki(expr, legend="", ref="A", instant=False, interval=None):
    t = {"datasource": LOKI, "refId": ref, "expr": expr, "legendFormat": legend,
         "queryType": "instant" if instant else "range"}
    if interval:
        t["interval"] = interval
    return t


def tql(query, ref="A"):
    """A TraceQL metrics query, instant only (one value over the panel's time range, max 24 h).

    Grafana 11.6 cannot read Tempo 3.1's range responses (`unknown field "step" in
    tempopb.QueryRangeResponse`) and sends only rate, count/avg/min/max/quantile/histogram_over_time
    and compare as metrics queries (pkg/tsdb/tempo/traceql_query.go, isMetricsQuery): sum_over_time
    goes nowhere. Sums and time series come from the collector's counters in Prometheus instead.
    """
    return {"datasource": TEMPO, "refId": ref, "queryType": "traceql", "query": query, "filters": [],
            "metricsQueryType": "instant", "limit": 20, "tableType": "traces"}


def tsearch(query, ref="A", limit=20, spss=3, table_type="traces"):
    """A TraceQL search (Grafana runs these in the browser)."""
    return {"datasource": TEMPO, "refId": ref, "queryType": "traceql", "query": query, "filters": [],
            "limit": limit, "spss": spss, "tableType": table_type}


# --- panels -------------------------------------------------------------------------------------

def _ds(targets):
    """The panel's datasource; for a mixed panel, also points its Prometheus targets at PROM_IN_MIXED."""
    kinds = {json.dumps(t["datasource"], sort_keys=True) for t in targets}
    if len(kinds) == 1:
        return targets[0]["datasource"]
    for t in targets:
        if t["datasource"] == PROM:
            t["datasource"] = PROM_IN_MIXED
    return MIXED


def _thr(steps):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps]}


def _by_ref(ref, props):
    return {"matcher": {"id": "byFrameRefID", "options": ref}, "properties": props}


def _link(uid, title):
    return {"title": title, "url": f"/d/{uid}?${{__url_time_range}}", "targetBlank": False}


def stat(title, targets, desc, *, names=None, units=None, calcs="lastNotNull", graph=False,
         link=None, steps=None, no_value="0", decimals=None, time_from=None, series_names=False,
         field_names=None, field_units=None, fields="", field_decimals=None):
    """One or more headline numbers. names/units: refId -> display name / unit; field_names /
    field_units: field name -> display name / unit (for a table result such as a span search)."""
    overrides = []
    for field in sorted(set(field_names or {}) | set(field_units or {}) | set(field_decimals or {})):
        props = []
        if field_names and field in field_names:
            props.append({"id": "displayName", "value": field_names[field]})
        if field_units and field in field_units:
            props.append({"id": "unit", "value": field_units[field]})
        if field_decimals and field in field_decimals:
            props.append({"id": "decimals", "value": field_decimals[field]})
        overrides.append({"matcher": {"id": "byName", "options": field}, "properties": props})
    for ref in sorted(set(names or {}) | set(units or {})):
        props = []
        if names and ref in names:
            props.append({"id": "displayName", "value": names[ref]})
        if units and ref in units:
            props.append({"id": "unit", "value": units[ref]})
        overrides.append(_by_ref(ref, props))
    defaults = {"unit": "short", "noValue": no_value, "color": {"mode": "thresholds"},
                "thresholds": _thr(steps or [("blue", None)])}
    if decimals is not None:
        defaults["decimals"] = decimals
    p = {"type": "stat", "title": title, "description": desc, "datasource": _ds(targets), "targets": targets,
         "fieldConfig": {"defaults": defaults, "overrides": overrides},
         "options": {"reduceOptions": {"calcs": [calcs], "fields": fields, "values": False},
                     "textMode": ("value_and_name" if len(targets) > 1 or names or series_names or field_names
                                  else "value"),
                     "colorMode": "value", "graphMode": "area" if graph else "none",
                     "justifyMode": "center", "orientation": "vertical", "wideLayout": True}}
    if link:
        p["links"] = [_link(*link)]
        defaults["links"] = [_link(*link)]
    if time_from:
        p["timeFrom"] = time_from
    return p


def ts(title, targets, desc, *, unit="short", bars=False, stack=False, time_from=None, no_value=None,
       calcs=("lastNotNull", "max"), display_names=None, axis_min=None, interval=None):
    custom = {"drawStyle": "bars" if bars else "line", "lineWidth": 1, "fillOpacity": 60 if bars else 10,
              "showPoints": "auto", "spanNulls": False,
              "stacking": {"group": "A", "mode": "normal" if stack else "none"}}
    defaults = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"}}
    if no_value:
        defaults["noValue"] = no_value
    if axis_min is not None:
        defaults["min"] = axis_min
    overrides = [_by_ref(ref, [{"id": "displayName", "value": name}]) for ref, name in (display_names or {}).items()]
    p = {"type": "timeseries", "title": title, "description": desc, "datasource": _ds(targets), "targets": targets,
         "fieldConfig": {"defaults": defaults, "overrides": overrides},
         "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": list(calcs)},
                     "tooltip": {"mode": "multi", "sort": "desc"}}}
    if time_from:
        p["timeFrom"] = time_from
    if interval:
        p["interval"] = interval  # the panel's minimum step; Loki ignores a target's
    return p


def bargauge(title, targets, desc, *, unit="short", time_from=None, no_value="0", decimals=None):
    """Horizontal bars. A TraceQL result is named by its label set ({p=0.95, span.gen_ai.tool.name=...});
    each bar is renamed to the label's value."""
    defaults = {"unit": unit, "min": 0, "noValue": no_value, "color": {"mode": "continuous-BlPu"},
                "thresholds": _thr([("blue", None)])}
    if decimals is not None:
        defaults["decimals"] = decimals
    p = {"type": "bargauge", "title": title, "description": desc, "datasource": _ds(targets), "targets": targets,
         "fieldConfig": {"defaults": defaults, "overrides": []},
         "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                     "valueMode": "color", "namePlacement": "left",
                     "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}}
    if time_from:
        p["timeFrom"] = time_from
    if any(t["datasource"] == TEMPO for t in targets):
        p["transformations"] = [{"id": "renameByRegex", "options": {
            "regex": '^\\{.*(?:service\\.name|request\\.model|tool\\.name)="?([^",}]+)"?\\}$', "renamePattern": "$1"}}]
    return p


def table(title, targets, desc, *, transformations=None, overrides=None, time_from=None, no_value=None,
          sort=None):
    defaults = {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}}
    if no_value:
        defaults["noValue"] = no_value
    p = {"type": "table", "title": title, "description": desc, "datasource": _ds(targets), "targets": targets,
         "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
         "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False},
                     "sortBy": sort or []},
         "transformations": transformations or []}
    if time_from:
        p["timeFrom"] = time_from
    return p


def logs(title, target, desc, *, time_from=None, no_value=None):
    p = {"type": "logs", "title": title, "description": desc, "datasource": target["datasource"],
         "targets": [target],
         "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending",
                     "enableLogDetails": True, "dedupStrategy": "none", "prettifyLogMessage": False}}
    if no_value:
        p["fieldConfig"] = {"defaults": {"noValue": no_value}, "overrides": []}
    if time_from:
        p["timeFrom"] = time_from
    return p


def text(title, content):
    return {"type": "text", "title": title, "options": {"mode": "markdown", "content": content}}


def _organize(keep, rename):
    return {"id": "organize", "options": {"excludeByName": {}, "includeByName": {k: True for k in keep},
                                          "indexByName": {k: i for i, k in enumerate(keep)},
                                          "renameByName": rename}}


# --- layout -------------------------------------------------------------------------------------

class Layout:
    """Rows and lines on Grafana's 24-column grid; each line starts where the last one ended."""

    def __init__(self):
        self.panels, self.y, self.next_id = [], 0, 0

    def _id(self):
        self.next_id += 1
        return self.next_id

    def row(self, title):
        self.panels.append({"type": "row", "title": title, "id": self._id(), "collapsed": False,
                            "gridPos": {"h": 1, "w": 24, "x": 0, "y": self.y}, "panels": []})
        self.y += 1

    def line(self, height, cells):
        x = 0
        for width, panel in cells:
            panel = dict(panel, id=self._id(), gridPos={"h": height, "w": width, "x": x, "y": self.y})
            self.panels.append(panel)
            x += width
        assert x <= 24, f"line overflows the grid: {x}"
        self.y += height


LINKS = [
    {"title": "Agent Platform", "type": "link", "url": "/d/agent-platform", "icon": "dashboard",
     "keepTime": True, "targetBlank": False, "tooltip": "The landing page"},
    {"title": "Agents", "type": "dashboards", "tags": ["agents"], "asDropdown": True, "keepTime": True,
     "includeVars": False, "targetBlank": False},
    {"title": "Cluster Overview", "type": "link", "url": "/d/cluster-overview", "icon": "dashboard",
     "keepTime": True, "targetBlank": False},
]


def dashboard(uid, title, description, layout, templating=None, time_from="now-24h"):
    templating = [PROM_VARIABLE] + (templating or [])
    return {"uid": uid, "title": title, "description": description, "tags": ["agents"],
            "timezone": "browser", "schemaVersion": 39, "version": 1, "editable": True, "graphTooltip": 1,
            "refresh": "1m", "time": {"from": time_from, "to": "now"}, "links": LINKS,
            "panels": layout.panels, "templating": {"list": templating}, "annotations": {"list": []}}


# --- Agent Platform (landing) -------------------------------------------------------------------

# The collector exposes each run's start time for 48 hours: an instant query lists them cheaply.
LATEST_RUNS = 'sort_desc(topk(20, max by (service_name, trace_id) (agent_run_start_seconds)))'

# A run's log lines: the controller's and Substrate's router lines carry its trace id, the sandbox's
# carry its session. Each id is anchored, so an empty variable (no run in 2 days) matches no line
# that carries a real id; it could match one whose trace id is empty, which kagent and Substrate
# do not write.
RUN_LOG_REGEX = ('(?:trace_id":"|trace\\.id=|trace_id=)0{0,31}${trace_id}(?:[^0-9a-f]|$)'
                 '|session-${session}(?:[^0-9a-f-]|$)')
RUN_LOGS = ('{namespace=~"kagent|ate-system"} |~ `' + RUN_LOG_REGEX + '`'
            ' != "TaskStoreService/UpdateTask" != "request before normalization"'
            ' != "request after normalization" ' + MASK_EMAILS)
RUN_FIELDS = [("gen_ai.usage.input_tokens", "input tokens"), ("gen_ai.usage.output_tokens", "output tokens"),
              ("agent.cost.estimate_usd", "est. cost")]
NO_RUN = "No agent run in the last 2 days: run scripts/agent-demo.sh"


def landing():
    b = Layout()
    b.row("Everything at a glance: the last 24 h (rare events: 7 d). Click a tile for its dashboard.")
    b.line(5, [
        (8, stat("Agents", [
            prom(window_total("agent_runs_total", "", "$__interval", forward=True), ref="A", interval="5m"),
            loki(HELD_NOW,
                 ref="B", instant=True)],
            "Agent runs (one per agent turn, kagent agents) and agents the eval gate holds now (their "
            "latest score regressed or failed a hard check).",
            names={"A": "runs", "B": "held by eval gate"}, calcs="sum", graph=True,
            link=("agent-quality", "Agent Quality"))),
        (8, stat("Tools / MCP", [
            prom(window_total("agent_tool_calls_total", "", "$__interval", forward=True), ref="A", interval="5m"),
            loki(f"sum(count_over_time({REFUSED} [7d])) or vector(0)", ref="B", instant=True)],
            "Tool calls the agents made and calls the MCP adapters refused in 7 days (a tool off the "
            "adapter's allowlist: `Unknown tool`).",
            names={"A": "tool calls", "B": "refused (7 d)"}, calcs="sum", graph=True,
            link=("mcp-tool-calls", "MCP Tool Calls"))),
        (8, stat("Gateway + tokens", [
            prom(window_total("agent_tokens_input_total", "", "$__interval", forward=True), ref="A", interval="5m"),
            prom(window_total("agent_tokens_output_total", "", "$__interval", forward=True), ref="B", interval="5m"),
            prom(window_total("agent_cost_estimate_usd_total", "", "$__interval", forward=True), ref="C", interval="5m")],
            "Tokens of the kagent agents' model calls and their estimated cost at list prices (see "
            "Tokens & Models). docs-checker sends no traces and is not counted.",
            names={"A": "input tokens", "B": "output tokens", "C": "est. cost"},
            units={"A": "short", "B": "short", "C": "currencyUSD"}, calcs="sum", graph=True,
            link=("agent-tokens", "Tokens & Models"))),
    ])
    b.line(5, [
        (8, stat("Agent traffic", [
            prom(f"sum(rate(istio_requests_total{{{WAYPOINT}}}[5m])) * 60", ref="A"),
            prom(f'100 * (sum(rate(istio_requests_total{{{WAYPOINT}, response_code=~"5.."}}[$__range])) or vector(0))'
                 f" / sum(rate(istio_requests_total{{{WAYPOINT}}}[$__range]))", ref="B", instant=True)],
            "Requests per minute into kagent through its Istio waypoint (callers: the ingress Gateway, "
            "kagent's UI, Slack, the dispatcher, the eval gate) and the share answered 5xx.",
            names={"A": "requests / min", "B": "5xx"}, units={"A": "short", "B": "percent"},
            graph=True, decimals=1, link=("agent-traffic", "Agent Traffic"))),
        (8, stat("Sandboxes", [
            prom(window_total(f'{SESSIONS}{{ate_actor_operation_name="create"}}', "", "$__interval", forward=True),
                 ref="A", interval="5m"),
            prom(window_total("ate_actor_crashes_total", "", RARE) + " or vector(0)", ref="B", instant=True)],
            "Agent sessions Substrate started (each a gVisor sandbox) and sandbox crashes in 7 days.",
            names={"A": "sessions started", "B": "crashes (7 d)"}, calcs="sum", graph=True,
            link=("agent-sandboxes", "Sandboxes"))),
        (8, stat("Alerts → agents", [
            loki(f'sum(count_over_time({DISP} | event="finding" [{RARE}])) or vector(0)', ref="A", instant=True),
            loki(f'sum(count_over_time({DISP} | event="remediate" | decision="remediate" [{RARE}])) or vector(0)',
                 ref="B", instant=True)],
            "Last 7 days: findings agents returned for alerts the dispatcher sent them, and restarts it "
            "asked for (capped at 2 a day, each approved by a person).",
            names={"A": "findings", "B": "restart requests"},
            link=("agent-alerts", "Alerts to Agents"))),
    ])

    b.row("Follow one request: ingress → kagent → Substrate sandbox → agent → model and tools")
    hops = [
        ("Entry: Istio Gateway", [
            prom(f'round(sum(increase(istio_requests_total{{{WAYPOINT}, source_workload="main-istio", destination_workload="kagent-controller"}}[$__range])))',
                 ref="A", instant=True),
            prom('histogram_quantile(0.95, sum by (le) (rate(istio_request_duration_milliseconds_bucket'
                 f'{{{WAYPOINT}, source_workload="main-istio", destination_workload="kagent-controller"}}[$__range])))',
                 ref="B", instant=True)],
         "A2A calls from the internet (ingress Gateway) to kagent's controller, and their p95: an agent "
         "run is one long A2A call.", {"B": "ms"}),
        ("kagent controller (A2A)", [
            prom(f'round(sum(increase(istio_requests_total{{{WAYPOINT}, destination_workload="kagent-controller"}}[$__range])))',
                 ref="A", instant=True),
            prom('histogram_quantile(0.95, sum by (le) (rate(istio_request_duration_milliseconds_bucket'
                 f'{{{WAYPOINT}, destination_workload="kagent-controller"}}[$__range])))', ref="B", instant=True)],
         "Every call into kagent's controller (people, Slack, the dispatcher, the eval gate, kagent's UI) "
         "and its p95.", {"B": "ms"}),
        ("Sandbox start (Substrate)", [
            prom(window_total(f'{SESSIONS}{{ate_actor_operation_name=~"create|resume"}}', "", "$__range"),
                 ref="A", instant=True),
            prom('histogram_quantile(0.95, sum by (le) (rate(ate_actor_lifecycle_operation_duration_seconds_bucket'
                 '{ate_actor_operation_name=~"create|resume"}[$__range])))', ref="B", instant=True)],
         "Sessions Substrate created or resumed (a session sleeps between turns, snapshotted) and the "
         "p95 time to get its sandbox running.", {"B": "s"}),
        ("Agent run", [tql(f"{RUNS} | count_over_time()", "A"),
                       tql(f"{RUNS} | quantile_over_time(duration, .95)", "B")],
         "invoke_agent spans: one per agent turn, from the first model call to the answer.", {"B": "s"}),
        ("Model calls", [tql(f"{MODEL} | count_over_time()", "A"),
                         tql(f"{MODEL} | quantile_over_time(duration, .95)", "B")],
         "generate_content spans: one per call to the model API.", {"B": "s"}),
        ("Tool calls", [tql(f"{TOOLS} | count_over_time()", "A"),
                        tql(f"{TOOLS} | quantile_over_time(duration, .95)", "B")],
         "execute_tool spans: kagent-tools (Kubernetes, Istio) and the MCP adapters.", {"B": "s"}),
    ]
    def hop(title, targets, desc, units):
        if targets[0]["datasource"] == TEMPO:
            # Grafana 11.6 names a TraceQL instant result by its function, not its refId.
            return stat(title, targets, desc, field_names={"count_over_time": "calls", "0.95": "p95"},
                        field_units={"0.95": "s"}, field_decimals={"count_over_time": 0, "0.95": 1})
        return stat(title, targets, desc, names={"A": "calls", "B": "p95"}, units=units,
                    field_decimals={"Value #A": 0})
    b.line(4, [(4, hop(t, tg, d, u)) for t, tg, d, u in hops])

    b.line(7, [(24, table(
        "Recent agent runs: click a trace id to follow it below", [tsearch(RUNS, limit=20)],
        "The newest agent runs in the dashboard's time range. Clicking a trace id loads that run's "
        "trace and logs below; the page opens on the newest run.",
        overrides=[{"matcher": {"id": "byName", "options": "traceID"}, "properties": [
            {"id": "links", "value": [{"title": "Follow this run",
                                       "url": "/d/agent-platform?var-trace_id=${__value.raw}&${__url_time_range}"}]}]},
                   # A run still in flight: its root span (the ingress call) ends last.
                   {"matcher": {"id": "byName", "options": "traceName"}, "properties": [{"id": "noValue", "value": "—"}]}],
        no_value="No agent runs in this time range"))])

    run = 'trace:id = "${trace_id}"'
    calls = f'{{{run} && name=~"generate_content .*"}} | select(span.gen_ai.usage.input_tokens, ' \
            'span.gen_ai.usage.output_tokens, span.agent.cost.estimate_usd)'
    b.line(4, [
        (12, dict(stat("This run: tokens and estimated cost", [tsearch(calls, limit=1, spss=100, table_type="spans")],
                       "The run's model calls (up to 100), summed: input tokens (cache reads included), output "
                       "tokens, and the estimated cost at list prices.",
                       field_names={k: v for k, v in RUN_FIELDS}, field_units={"agent.cost.estimate_usd": "currencyUSD"},
                       field_decimals={"agent.cost.estimate_usd": 3}, calcs="sum", no_value="-"),
                  # Keep the three attributes (not the span's duration), and make them numbers: a span
                  # search returns attribute values as text.
                  transformations=[
                      {"id": "filterFieldsByName", "options": {"include": {"names": [k for k, _ in RUN_FIELDS]}}},
                      {"id": "convertFieldType", "options": {"fields": {}, "conversions": [
                          {"targetField": k, "destinationType": "number"} for k, _ in RUN_FIELDS]}}])),
        (4, stat("Model calls", [tql(f"{{{run} && name=~\"generate_content .*\"}} | count_over_time()")],
                 "Calls to the model API in this run.", no_value="-")),
        (4, stat("Tool calls", [tql(f"{{{run} && name=~\"execute_tool .*\" && span.gen_ai.tool.name != \"(merged tools)\"}} | count_over_time()")],
                 "Tool calls in this run.", no_value="-")),
        (4, stat("Agent turn", [tql(f"{{{run} && name=~\"invoke_agent .*\"}} | max_over_time(duration)")],
                 "How long the agent's turn took.", field_units={"max_over_time": "s"},
                 no_value="-", decimals=1)),
    ])
    b.line(16, [(24, {"type": "traces", "title": "The run, end to end (Tempo)",
                      "description": "One agent run: ingress Gateway, kagent's controller, Substrate's router, "
                                     "the agent's turn with each model and tool call. Span attributes hold "
                                     "tokens and the estimated cost; tool output is scrubbed.",
                      "datasource": TEMPO,
                      "targets": [{"datasource": TEMPO, "refId": "A", "queryType": "traceql",
                                   "query": "${trace_id}", "filters": [], "limit": 20, "tableType": "traces"}],
                      "fieldConfig": {"defaults": {"noValue": NO_RUN}, "overrides": []}, "options": {}})])
    b.line(10, [(24, logs("Its logs: kagent's controller, Substrate and the sandbox", loki(RUN_LOGS),
                          "Lines carrying this run's trace id (controller, Substrate router and egress) or "
                          "its session (Substrate, the sandbox). The TaskStore UpdateTask stream and "
                          "routing debug lines are left out.", no_value=NO_RUN))])

    templating = [
        {"type": "query", "name": "trace_id", "label": "Run", "datasource": PROM,
         "query": {"query": f"query_result({LATEST_RUNS})", "refId": "trace_id"},
         "definition": f"query_result({LATEST_RUNS})",
         "regex": '/service_name="(?<text>[^"]+)".*trace_id="0*(?<value>[0-9a-f]{1,32})"/',
         "refresh": 1, "sort": 0, "includeAll": False, "multi": False, "allowCustomValue": True,
         "current": {}, "options": [], "hide": 0,
         "description": "The newest agent runs of the last 2 days, newest first (agent_run_start_seconds). "
                        "Read when the page loads."},
        {"type": "query", "name": "session", "label": "Session", "datasource": PROM,
         "query": {"query": 'query_result(max by (gen_ai_conversation_id) '
                            '(last_over_time(agent_run_start_seconds{trace_id=~"0*$trace_id"}[2d])))',
                   "refId": "session"},
         "definition": 'query_result(max by (gen_ai_conversation_id) '
                       '(last_over_time(agent_run_start_seconds{trace_id=~"0*$trace_id"}[2d])))',
         "regex": '/gen_ai_conversation_id="([^"]+)"/', "refresh": 2, "sort": 0, "includeAll": False,
         "multi": False, "current": {}, "options": [], "hide": 2},
    ]
    return dashboard("agent-platform", "Agent Platform",
                     "Start here: every agent-platform signal at a glance, then one request followed end to end.",
                     b, templating)


# --- Agent Quality (eval gate + runs) -----------------------------------------------------------

def quality():
    b = Layout()
    scored = f'{EVAL} | pass_rate!=""'
    b.row("Eval gate: scores an agent when its definition changes (last 7 days)")
    b.line(4, [
        (6, stat("Agents scored", [loki(f"count(count by (agent) (count_over_time({scored} [{RARE}]))) or vector(0)",
                                        instant=True)],
                 "Agents the eval gate scored in 7 days (it skips agents whose definition did not change).",
                 )),
        (6, stat("Held now", [loki(HELD_NOW, instant=True)],
                 "Agents whose latest score (30 days) held their release: a regression against the baseline "
                 "or a hard failure. A held agent keeps its previous version.",
                 steps=[("green", None), ("red", 1)])),
        (6, stat("Regressions", [loki(f'sum(count_over_time({scored} | regression="true" [{RARE}])) or vector(0)',
                                      instant=True)],
                 "Scored runs whose pass rate fell below the agent's baseline.",
                 steps=[("green", None), ("orange", 1)])),
        (6, stat("Scored runs with a hard failure", [
            loki(f'sum(count_over_time({{namespace="kagent", container="eval-gate"}} |= "pass_rate" != "\\"hard\\": []" [{RARE}])) or vector(0)',
                 instant=True)],
                 "A hard failure (for example a leaked canary) holds the agent whatever its pass rate.",
                 steps=[("green", None), ("red", 1)])),
    ])
    b.line(8, [
        (14, ts("Pass rate per agent", [loki(f"max_over_time({scored} | unwrap pass_rate [$__interval]) by (agent)",
                                              "{{agent}}")],
                "Each point is one scored run (0 to 1).", unit="percentunit", time_from=RARE,
                no_value="No agent scored in 7 days", axis_min=0, interval="1h")),
        (10, ts("Eval-gate runs", [loki(
            f'sum by (result) (count_over_time({EVAL} | label_format result=`{{{{ if .pass_rate }}}}scored{{{{ else }}}}unchanged, skipped{{{{ end }}}}` [$__interval]))',
            "{{result}}", )],
                "Per hour. The gate runs after every sync of kagent's agents; most agents are unchanged and skipped.",
                bars=True, stack=True, time_from=RARE, calcs=("sum",), interval="1h")),
    ])
    b.line(8, [(24, table("Scored runs", [loki(scored)],
                          "Newest first: pass rate against baseline, whether it held the agent, and the git "
                          "revision it scored.",
                          transformations=[{"id": "extractFields", "options": {"source": "Line", "format": "json"}},
                                           _organize(["Time", "agent", "pass_rate", "baseline", "passed", "total",
                                                      "held", "regression", "judge", "revision"],
                                                     {"pass_rate": "pass rate", "Time": "time"})],
                          time_from=RARE, no_value="No agent scored in 7 days", sort=[{"displayName": "time", "desc": True}]))])

    b.row("Agent runs (kagent agents)")
    b.line(8, [
        (12, ts("Runs per agent", [prom(window_total("agent_runs_total", "service_name", "$__interval", forward=True),
                                                  "{{service_name}}", interval="15m")],
                "Agent turns (invoke_agent spans), counted by the collector.", bars=True, stack=True,
                calcs=("sum",), no_value="No agent runs in this time range")),
        (12, bargauge("Run duration p95 per agent", [tql(f"{RUNS} | quantile_over_time(duration, .95) by ({AGENT})")],
                      "How long an agent turn takes, model and tool calls included (Tempo, up to 24 h).",
                      unit="s", decimals=1, no_value="No agent runs in this time range")),
    ])
    b.line(8, [
        (12, bargauge("Failed runs per agent", [tql(f"{{name=~\"invoke_agent .*\" && status = error}} | count_over_time() by ({AGENT})")],
                      "Agent turns that ended in an error (Tempo, up to 24 h).", no_value="0 failed runs")),
        (12, table("Recent runs", [tsearch(RUNS)], "Click a trace id to open it in Explore.",
                   no_value="No agent runs in this time range")),
    ])
    return dashboard("agent-quality", "Agent Quality",
                     "Is each agent still good? The eval gate's scores and holds, and every agent's runs.", b)


# --- Tokens & Models ----------------------------------------------------------------------------

def tokens():
    b = Layout()

    def total(metric, by=""):
        return prom(window_total(metric, by, "$__range"), instant=True)

    def per_interval(metric, by, legend):
        return prom(window_total(metric, by, "$__interval", forward=True), legend, interval="15m")

    b.row("Tokens of the kagent agents, counted per model call by the collector")
    b.line(4, [
        (4, stat("Input tokens", [total("agent_tokens_input_total")], "All input tokens, cache reads included.")),
        (4, stat("Cache reads", [prom("100 * " + window_total("agent_tokens_cache_read_total", "", "$__range")
                                      + " / " + window_total("agent_tokens_input_total", "", "$__range"), instant=True)],
                 "Share of input tokens served from the prompt cache, billed at a tenth of the input price "
                 "or less.", units={"A": "percent"}, decimals=0)),
        (4, stat("Output tokens", [total("agent_tokens_output_total")], "Output tokens, reasoning included.")),
        (4, stat("Model calls", [total("agent_model_calls_total")], "Calls to the model API.")),
        (4, stat("Agent runs", [total("agent_runs_total")], "Agent turns those calls belong to.")),
        (4, stat("Est. cost", [total("agent_cost_estimate_usd_total")],
                 "Estimated from Anthropic's list prices (2026-10-06), per call: uncached input, cache "
                 "writes, cache reads and output each at their own rate. An estimate, not the bill.",
                 units={"A": "currencyUSD"}, decimals=2)),
    ])
    b.line(8, [
        (12, ts("Input tokens per agent", [per_interval("agent_tokens_input_total", "service_name", "{{service_name}}")],
                "Cache reads included.", bars=True, stack=True, calcs=("sum",), no_value="No model calls in this time range")),
        (12, ts("Output tokens per agent", [per_interval("agent_tokens_output_total", "service_name", "{{service_name}}")],
                "Reasoning included.", bars=True, stack=True, calcs=("sum",), no_value="No model calls in this time range")),
    ])
    b.line(8, [
        (8, bargauge("Est. cost per agent", [total("agent_cost_estimate_usd_total", "service_name") | {"legendFormat": "{{service_name}}"}],
                     "Estimated cost of each agent's model calls in the time range.", unit="currencyUSD", decimals=3)),
        (8, bargauge("Input tokens per model", [total("agent_tokens_input_total", "gen_ai_request_model") | {"legendFormat": "{{gen_ai_request_model}}"}],
                     "Input tokens per model.")),
        (8, bargauge("Est. cost per model", [total("agent_cost_estimate_usd_total", "gen_ai_request_model") | {"legendFormat": "{{gen_ai_request_model}}"}],
                     "A model without a price row gets no estimate; its tokens still count.",
                     unit="currencyUSD", decimals=3)),
    ])
    b.row("Models and tools")
    b.line(8, [
        (12, ts("Model calls per model", [per_interval("agent_model_calls_total", "gen_ai_request_model", "{{gen_ai_request_model}}")],
                "Calls per model.", bars=True, stack=True, calcs=("sum",), no_value="No model calls in this time range")),
        (12, bargauge("Model call latency p95", [tql(f"{MODEL} | quantile_over_time(duration, .95) by ({MODEL_NAME})")],
                      "One model call, first byte to last, as the agent saw it (Tempo, up to 24 h).", unit="s", decimals=1,
                      no_value="No model calls in this time range")),
    ])
    b.line(8, [
        (12, bargauge("Tool calls per tool", [total("agent_tool_calls_total", "gen_ai_tool_name") | {"legendFormat": "{{gen_ai_tool_name}}"}],
                      "Tool calls the agents made, per tool.", no_value="No tool calls in this time range")),
        (12, bargauge("Tool call latency p95", [tql(f"{TOOLS} | quantile_over_time(duration, .95) by (span.gen_ai.tool.name)")],
                      "Per tool (Tempo, up to 24 h).", unit="s", decimals=1, no_value="No tool calls in this time range")),
    ])
    b.line(8, [
        (16, table("Runs over 100k input tokens", [tsearch(f"{MODEL} | sum(span.gen_ai.usage.input_tokens) > 100000")],
                   "Agent runs whose model calls read more than 100k tokens in total. Click to open the trace.",
                   no_value="No run over 100k input tokens")),
        (8, text("How tokens and cost are counted",
                 "The collector counts every model call's tokens (`gen_ai.usage.*` on its span) before "
                 "sampling, so each call counts once.\n\n"
                 "Cost is an **estimate** stamped on each call at Anthropic's list prices on 2026-10-06 "
                 "($ per million tokens):\n\n"
                 "| model | input | cache write | cache read | output |\n|---|---|---|---|---|\n"
                 "| claude-sonnet-5-5 | 2 | 2.5 | 0.2 | 10 |\n| claude-opus-5-5 | 4 | 5 | 0.2 | 20 |\n"
                 "| claude-haiku-5-5 | 0.1 | 0.125 | 0.01 | 0.5 |\n\n"
                 "Haiku over 100K-token prompts: 0.5 / 0.625 / 0.05 / 2.5.\n\n"
                 "Only the kagent agents are traced; docs-checker (LangGraph) sends no traces.")),
    ])
    return dashboard("agent-tokens", "Tokens & Models",
                     "What the agents spend: tokens per agent and model, model and tool calls, estimated cost.", b)


# --- Agent Traffic ------------------------------------------------------------------------------

def traffic():
    b = Layout()
    b.row("Who calls whom: the service graph from traces (agent platform only)")
    b.line(14, [(24, {"type": "nodeGraph", "title": "Service graph",
                      "description": "From the collector's service graph over the agent platform's traces: the "
                                     "ingress Gateway, kagent's controller, Substrate's router (agentgateway), "
                                     "each agent, and what it calls (the model API, kagent-tools, the MCP "
                                     "adapters). Edge numbers are requests in the time range.",
                      "datasource": TEMPO,
                      "targets": [{"datasource": TEMPO, "refId": "A", "queryType": "serviceMap", "filters": [],
                                   "limit": 20, "tableType": "traces"}],
                      "options": {}, "fieldConfig": {"defaults": {"noValue": "No agent traffic in this time range"},
                                                     "overrides": []}})])
    b.line(8, [(24, table("Edges: requests and failures", [
        prom(window_total("traces_service_graph_request_total", "client, server", "$__range") + " > 0",
             ref="A", instant=True, fmt="table"),
        prom(window_total("traces_service_graph_request_failed_total", "client, server", "$__range") + " or "
             + window_total("traces_service_graph_request_total", "client, server", "$__range") + " * 0",
             ref="B", instant=True, fmt="table")],
        "Each edge of the graph above: requests and failed requests in the time range.",
        transformations=[{"id": "merge", "options": {}},
                         _organize(["client", "server", "Value #A", "Value #B"],
                                   {"client": "from", "server": "to", "Value #A": "requests", "Value #B": "failed"})],
        sort=[{"displayName": "requests", "desc": True}]))])

    b.row("Mesh traffic into kagent (Istio waypoint)")
    b.line(8, [
        (12, ts("Requests per second, caller → destination",
                [prom(f"sum by (source_workload, destination_workload) (rate(istio_requests_total{{{WAYPOINT}}}[$__rate_interval]))",
                      "{{source_workload}} → {{destination_workload}}")],
                "Every request the kagent waypoint saw, by calling and called workload.", unit="reqps")),
        (12, ts("p95 latency by destination",
                [prom("histogram_quantile(0.95, sum by (destination_workload, le) (rate("
                      f"istio_request_duration_milliseconds_bucket{{{WAYPOINT}}}[$__rate_interval])))",
                      "{{destination_workload}}")],
                "Agent runs are long A2A calls, so kagent-controller's p95 is a run's length.", unit="ms")),
    ])
    b.line(8, [
        (12, ts("Error responses by destination and code",
                [prom(f'sum by (destination_workload, response_code) (rate(istio_requests_total{{{WAYPOINT}, response_code=~"[45].."}}[$__rate_interval]))',
                      "{{destination_workload}} {{response_code}}")],
                "4xx and 5xx answers. A 403 at kagent-controller is the waypoint refusing a caller without a "
                "verified login.", unit="reqps", no_value="No errors")),
        (12, table("Callers in the time range", [
            prom(f"round(sum by (source_workload, destination_workload) (increase(istio_requests_total{{{WAYPOINT}}}[$__range]))) > 0",
                 ref="A", instant=True, fmt="table")],
            "Every caller of a kagent workload and how many requests it made.",
            transformations=[_organize(["source_workload", "destination_workload", "Value"],
                                       {"source_workload": "caller", "destination_workload": "destination",
                                        "Value": "requests"})],
            sort=[{"displayName": "requests", "desc": True}])),
    ])
    return dashboard("agent-traffic", "Agent Traffic",
                     "The traffic between agents and everything they call: a service graph from traces and the mesh's view.", b)


# --- Sandboxes (Substrate) ----------------------------------------------------------------------

def sandboxes():
    b = Layout()
    op = "ate_actor_lifecycle_operation_duration_seconds"
    creates = window_total(f'{SESSIONS}{{ate_actor_operation_name="create"}}', "ate_template_name", "$__interval",
                           forward=True)

    def count(operation):
        return prom(window_total(f'{SESSIONS}{{ate_actor_operation_name="{operation}"}}', "", "$__range")
                    + " or vector(0)", instant=True)
    b.row("Sessions: each agent conversation is a Substrate actor in a gVisor sandbox, snapshotted between turns")
    b.line(4, [
        (5, stat("Sessions started", [count("create")],
                 "New sessions (CreateActor).")),
        (5, stat("Resumed", [count("resume")],
                 "A sleeping session woken from its snapshot for the next turn.")),
        (5, stat("Suspended", [count("suspend")],
                 "Sessions snapshotted and stopped after a turn.")),
        (4, stat("Crashes (7 d)", [prom(window_total("ate_actor_crashes_total", "", RARE) + " or vector(0)", instant=True)],
                 "Sandboxes that died while running.", steps=[("green", None), ("red", 1)])),
        (5, stat("Workers", [prom("sum by (ate_worker_state) (ate_workerpool_workers)", "{{ate_worker_state}}", instant=True)],
                 "Worker pods in the pool by state: idle, partial (some sandboxes), at capacity, unschedulable.",
                 series_names=True)),
    ])
    b.line(8, [
        (12, ts("Lifecycle operations", [prom(window_total(SESSIONS, "ate_actor_operation_name", "$__interval", forward=True),
                                                       "{{ate_actor_operation_name}}", interval="15m")],
                "create, resume, suspend, delete.", bars=True, stack=True, calcs=("sum",))),
        (12, ts("Sessions started per agent", [prom("sum by (agent) (" + by_agent(creates) + ")",
                                                    "{{agent}}", interval="15m")],
                "Every agent, docs-checker included (it runs in Substrate though it sends no traces).",
                bars=True, stack=True, calcs=("sum",))),
    ])
    b.line(8, [
        (12, ts("Operation p95", [prom(f"histogram_quantile(0.95, sum by (ate_actor_operation_name, le) (rate({op}_bucket[1h])))",
                                       "{{ate_actor_operation_name}}", interval="5m")],
                "Over the previous hour.", unit="s")),
        (12, ts("Operation errors", [prom(window_total(f'{SESSIONS}{{error_type!=""}}', "ate_actor_operation_name, error_type",
                                                    "$__interval", forward=True),
                                          "{{ate_actor_operation_name}} {{error_type}}", interval="15m")],
                "Lifecycle operations that failed, by error type.", bars=True, no_value="No errors", calcs=("sum",))),
    ])
    b.row("Snapshots, scheduling and resources")
    b.line(8, [
        (8, ts("Restore p95 by phase", [prom("histogram_quantile(0.95, sum by (ate_snapshot_phase, le) (rate(ate_actor_restore_duration_seconds_bucket[1h])))",
                                             "{{ate_snapshot_phase}}", interval="5m")],
               "Waking a session: fetch and unpack its snapshot, restore the sandbox.", unit="s")),
        (8, ts("Checkpoint p95 by phase", [prom("histogram_quantile(0.95, sum by (ate_snapshot_phase, le) (rate(ate_actor_checkpoint_duration_seconds_bucket[1h])))",
                                                "{{ate_snapshot_phase}}", interval="5m")],
               "Putting a session to sleep: checkpoint the sandbox and persist it.", unit="s")),
        (8, ts("Scheduler: time to a worker, p95", [prom("histogram_quantile(0.95, sum by (le) (rate(ate_scheduler_assignment_duration_seconds_bucket[1h])))",
                                                         "p95", interval="5m")],
               "How long a new session waits for a worker.", unit="s")),
    ])
    b.line(8, [
        (8, ts("Sandbox CPU per agent", [prom(f"sum by (agent) ({by_agent('rate(ate_actor_stats_cpu_time_seconds_total[$__rate_interval])')})", "{{agent}}")],
               "CPU cores the agents' sandboxes use.", unit="short")),
        (8, ts("Snapshot size p95 per agent", [prom(f"histogram_quantile(0.95, sum by (agent, le) ({by_agent('rate(atelet_snapshot_size_bytes_bucket[1h])')}))",
                                                    "{{agent}}", interval="5m")],
               "Size of the snapshot files a session writes.", unit="bytes")),
        (8, stat("Image cache", [prom(window_total("ate_imagecache_requests_total", "ate_imagecache_outcome", "$__range"),
                                      "{{ate_imagecache_outcome}}", instant=True)],
                 "Sandbox image lookups by outcome (hit or miss) in the time range.", series_names=True)),
    ])
    return dashboard("agent-sandboxes", "Sandboxes",
                     "Substrate, where every agent session runs: lifecycle, snapshots, scheduling, resources.", b)


# --- Alerts to Agents (dispatcher) --------------------------------------------------------------

def alerts():
    b = Layout()
    b.row("The dispatcher sends Grafana alerts to agents and asks a person before any restart (last 7 days)")

    def count(sel):
        return loki(f"sum(count_over_time({DISP} | {sel} [{RARE}])) or vector(0)", instant=True)
    b.line(4, [
        (4, stat("Alerts received", [count('event="dispatch" | status="firing"')], "Firing alerts the dispatcher received.")),
        (4, stat("Sent to an agent", [count('event="dispatch" | decision="investigate"')],
                 "Alerts routed to an agent (k8s-reader, implementer) to investigate.")),
        (4, stat("Skipped", [count('event="dispatch" | decision=~"skip-.*"')],
                 "Not sent again: a repeat inside 6 hours, or the agent is held by the eval gate.")),
        (4, stat("Findings", [count('event="finding"')], "Answers agents returned.")),
        (4, stat("Restart requests", [count('event="remediate" | decision="remediate"')],
                 "Restarts the dispatcher asked restarter for (capped at 2 a day).")),
        (4, stat("Approved by a person", [count('event="decision" | approved="true"')],
                 "Restarts a person approved on the dispatcher's approvals page.")),
    ])
    b.line(8, [
        (12, ts("Alerts by rule (firing)", [loki(f'sum by (rule) (count_over_time({DISP} | event="dispatch" | status="firing" [$__interval]))',
                                                  "{{rule}}", )],
                "Per hour.", bars=True, stack=True, time_from=RARE, calcs=("sum",), interval="1h", no_value="No alerts in 7 days")),
        (12, ts("Dispatch decisions", [loki(f'sum by (decision) (count_over_time({DISP} | event="dispatch" [$__interval]))',
                                             "{{decision}}", )],
                "investigate = sent to an agent; resolved = the alert cleared; none = no route for it; skip-* "
                "= not sent again.", bars=True, stack=True, time_from=RARE, calcs=("sum",), interval="1h", no_value="No alerts in 7 days")),
    ])
    b.line(8, [
        (12, ts("Findings per agent and outcome", [loki(f'sum by (agent, state) (count_over_time({DISP} | event="finding" [$__interval]))',
                                                        "{{agent}} {{state}}", )],
                "TASK_STATE_INPUT_REQUIRED = restarter waiting for a person's approval.", bars=True, stack=True,
                time_from=RARE, calcs=("sum",), interval="1h", no_value="No findings in 7 days")),
        (6, table("Remediation verdicts", [loki(f'sum by (verdict, decision) (count_over_time({DISP} | event="remediate" [{RARE}]))',
                                                 instant=True)],
                  "What the investigating agent concluded and what the dispatcher did with it.",
                  transformations=[{"id": "labelsToFields", "options": {"mode": "columns"}},
                                   _organize(["verdict", "decision", "Value"], {"Value": "count"})],
                  time_from=RARE, no_value="No remediation in 7 days")),
        (6, stat("Restart requests today", [loki(f'sum(count_over_time({DISP} | event="remediate" | decision="remediate" [24h])) or vector(0)',
                                                 instant=True)],
                 "Against the cap of 2 a day (dispatcher limits.restarts_per_day).",
                 steps=[("green", None), ("orange", 2), ("red", 3)])),
    ])
    b.line(10, [(24, logs("Dispatcher events", loki(f'{DISP} | event=~"dispatch|finding|remediate|decision" {MASK_EMAILS}'),
                          "Every alert, finding, remediation and approval, newest first (emails masked).", time_from=RARE,
                          no_value="No dispatcher events in 7 days"))])
    return dashboard("agent-alerts", "Alerts to Agents",
                     "Alerts handed to agents: what came in, which agent looked, what it found, which restarts a person approved.", b)


# --- MCP Tool Calls (§T.134) + kagent-tools -----------------------------------------------------

def mcp():
    b = Layout()
    dep = 'label_replace({expr}, "deployment", "$1", "pod", "(.+)-[a-z0-9]+-[a-z0-9]+")'
    b.row("MCP adapters: agent-docs-mcp, brave-search-mcp, implementer-mcp (agentgateway, §T.134)")
    b.line(9, [
        (12, ts("Tool calls per minute, by tool",
                [prom('sum by (server, resource) (rate(agentgateway_mcp_requests_total{namespace="kagent", method="tools/call"}[$__rate_interval])) * 60',
                      "{{server}} {{resource}}")],
                "tools/call requests per server and tool, from agentgateway_mcp_requests_total. Counts refused "
                "calls too (a tool off the allowlist is counted under the name the agent asked for); see the "
                "panel on the right.", stack=True)),
        (12, ts("Refused tool calls (not on the allowlist)",
                [loki(f"sum by (pod) (count_over_time({REFUSED} [$__interval]))", "{{pod}}")],
                "tools/call requests the adapter refused with `Unknown tool`: the agent asked for a tool its "
                "allowlist does not hold.", bars=True, no_value="0", calcs=("sum",))),
    ])
    b.line(8, [
        (12, ts("MCP requests per minute, by method",
                [prom("sum by (deployment, method) (" + dep.format(expr='rate(agentgateway_mcp_requests_total{namespace="kagent"}[$__rate_interval])') + ") * 60",
                      "{{deployment}} {{method}}")],
                "initialize, tools/list, tools/call and the rest, per adapter.")),
        (12, ts("HTTP responses per minute, by status",
                [prom("sum by (deployment, status) (" + dep.format(expr='rate(agentgateway_request_duration_seconds_count{namespace="kagent", protocol="mcp"}[$__rate_interval])') + ") * 60",
                      "{{deployment}} {{status}}")],
                "A refused tool call answers 400.")),
    ])
    b.line(8, [
        (12, ts("Request latency p95",
                [prom("histogram_quantile(0.95, sum by (deployment, le) (" + dep.format(expr='rate(agentgateway_request_duration_seconds_bucket{namespace="kagent", protocol="mcp"}[$__rate_interval])') + "))",
                      "{{deployment}}")],
                "Per adapter, through to its stdio server.", unit="s")),
        (12, ts("Memory working set (container mcp-server)",
                [prom("sum by (deployment) (" + dep.format(expr='container_memory_working_set_bytes{namespace="kagent", container="mcp-server"}') + ")",
                      "{{deployment}}")],
                "agentgateway plus its stdio server.", unit="bytes")),
    ])
    b.line(8, [(24, logs("Refused tool calls (log lines)", loki(REFUSED), "The adapter's access line for each refused call.",
                         no_value="No refused calls in this time range"))])
    b.row("kagent-tools: the built-in Kubernetes, Istio and Helm tools")
    KT_CALLS = 'kagent_tools_mcp_invocations_total{namespace="kagent"}'
    KT_FAILURES = 'kagent_tools_mcp_invocations_failure_total{namespace="kagent"}'
    b.line(4, [
        (12, stat("Calls", [prom(window_total(KT_CALLS, "", "$__range") + " or vector(0)", instant=True)],
                  "Tool calls kagent-tools served in the time range.")),
        (12, stat("Failures", [prom(window_total(KT_FAILURES, "", "$__range") + " or vector(0)", instant=True)],
                  "Calls that returned an error (for example a resource that does not exist).",
                  steps=[("green", None), ("orange", 1)])),
    ])
    b.line(8, [
        (12, ts("Calls by tool", [prom(window_total(KT_CALLS, "tool_name", "$__interval", forward=True),
                                       "{{tool_name}}", interval="15m")],
                "kagent_tools_mcp_invocations_total per tool, counted per interval.", bars=True, stack=True,
                calcs=("sum",))),
        (12, ts("Failures by tool", [prom(window_total(KT_FAILURES, "tool_name", "$__interval", forward=True),
                                          "{{tool_name}}", interval="15m")],
                "kagent_tools_mcp_invocations_failure_total per tool, counted per interval.", bars=True,
                stack=True, calcs=("sum",), no_value="No failures")),
    ])
    d = dashboard("mcp-tool-calls", "MCP Tool Calls",
                  "Every tool call the agents make: the MCP adapters (allowlists, refusals, latency) and kagent-tools.", b)
    d["tags"] = ["agents", "mcp", "agentgateway", "kagent"]
    return d


BUILDERS = {"agent-platform": landing, "agent-quality": quality, "agent-tokens": tokens,
            "agent-traffic": traffic, "agent-sandboxes": sandboxes, "agent-alerts": alerts,
            "mcp-tool-calls": mcp}


def build_all():
    """file name -> dashboard."""
    return {fname: BUILDERS[uid]() for fname, uid in DASHBOARDS.values()}


HEADER = '''# The Agents folder's dashboards (§T.135), one ConfigMap each.
#
# GENERATED by scripts/gen-agent-dashboards.py. Do not hand-edit the JSON; change the generator and
# regenerate. CI runs it with --check.
#
# grafana-deployment.yaml mounts every ConfigMap here into /var/lib/grafana/dashboards/agents (one
# projected volume; provider "agents" in grafana-dashboard-configmap.yaml) and opens
# agent-platform.json as Grafana's home dashboard.
'''


def render():
    docs = []
    built = build_all()
    for name, (fname, _uid) in DASHBOARDS.items():
        body = json.dumps(built[fname], indent=2, sort_keys=False)
        indented = "\n".join("    " + line for line in body.split("\n"))
        docs.append(f"apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: {name}\n  namespace: logging\n"
                    f"  labels:\n    grafana_dashboard: \"1\"\ndata:\n  {fname}: |\n{indented}\n")
    return HEADER + "---\n" + "---\n".join(docs)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--check", action="store_true", help="verify only; exit 1 on drift")
    args = ap.parse_args()
    out = Path(args.repo_root) / OUT
    want = render()
    if args.check:
        have = out.read_text() if out.exists() else ""
        if have != want:
            print(f"{OUT} is out of date — run scripts/gen-agent-dashboards.py --repo-root .", file=sys.stderr)
            return 1
        print("Agents dashboards in sync.")
        return 0
    out.write_text(want)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
