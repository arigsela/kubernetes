#!/usr/bin/env python3
"""Generate the Grafana dashboards of the Agents folder (§T.135).

Writes base-apps/logging/grafana-dashboards-agents.yaml: one ConfigMap per dashboard, all mounted
into /var/lib/grafana/dashboards/agents through one projected volume (grafana-deployment.yaml).
Edit the panels here and regenerate; never hand-edit the JSON in the ConfigMaps.

    python scripts/gen-agent-dashboards.py --repo-root .          # write
    python scripts/gen-agent-dashboards.py --repo-root . --check  # CI

The landing page, Agent Platform, is Grafana's home dashboard. Every other dashboard links back to it.

Where the numbers come from:
  - Tokens, cost, model calls, tool calls, agent runs over a time range: the collector's counters
    (signal_to_metrics over the kagent agents' generate_content / execute_tool / invoke_agent
    spans), read with window_total(). The per-hop counts and p95s: Tempo TraceQL instant metrics
    (at most 24 h per query).
  - The picked run's tokens, cost, calls and turn: its trace, as the trace panel loads it (the
    Dashboard datasource), so a picked run costs one Tempo lookup.
  - The estimated cost: agent.cost.estimate_usd, stamped on each model call by the collector
    (base-apps/tracing/collector.yaml, transform/genai-cost).
  - The latest run: agent_run_start_seconds{trace_id} from the collector (signal_to_metrics).
  - The service graph: traces_service_graph_* from the collector (service_graph), read through the
    Tempo datasource's service map.
  - Mesh traffic: Istio waypoint metrics; sandboxes: Substrate's ate_* metrics; MCP: agentgateway
    and kagent-tools metrics; eval gate and dispatcher: their JSON event lines in Loki.
  - Agent KPIs (§T.144): scripts/agent-kpis.py's hourly lines in Loki (task outcomes, approval waits,
    pull request lead times), scripts/agent-capability-map.yaml (read here, at generation time), and
    the series above.

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
DASHBOARD_DS = {"type": "datasource", "uid": "-- Dashboard --"}  # another panel's results
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
    "grafana-dashboard-model-gateway": ("model-gateway.json", "model-gateway"),
    "grafana-dashboard-agent-kpis": ("agent-kpis.json", "agent-kpis"),
    "grafana-dashboard-bug-investigation-live": ("bug-investigation-live.json", "bug-investigation-live"),
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


def loki(expr, legend="", ref="A", instant=False, interval=None, max_lines=None):
    t = {"datasource": LOKI, "refId": ref, "expr": expr, "legendFormat": legend,
         "queryType": "instant" if instant else "range"}
    if interval:
        t["interval"] = interval
    if max_lines:
        t["maxLines"] = max_lines   # a log query: only the newest N lines
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
    {"title": "Agent KPIs", "type": "link", "url": "/d/agent-kpis", "icon": "dashboard",
     "keepTime": False, "targetBlank": False, "tooltip": "The playbook's eight agent-scenario KPIs (§T.144)"},
    {"title": "Bug Investigation · live", "type": "link", "url": "/d/bug-investigation-live", "icon": "dashboard",
     "keepTime": False, "targetBlank": False,
     "tooltip": "A Taskboard bug report or alert, step by step, as the platform handles it (§T.149)"},
]


def dashboard(uid, title, description, layout, templating=None, time_from="now-24h", timezone="browser",
              refresh="1m"):
    templating = [PROM_VARIABLE] + (templating or [])
    return {"uid": uid, "title": title, "description": description, "tags": ["agents"],
            "timezone": timezone, "schemaVersion": 39, "version": 1, "editable": True, "graphTooltip": 1,
            "refresh": refresh, "time": {"from": time_from, "to": "now"}, "links": LINKS,
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
# The substring check comes first: Loki then runs the regex only on lines carrying either id, not
# on every kagent and Substrate line of the day (18 s → 3 s on its half CPU, §B.41). Tempo's form
# of the id (leading zeros stripped) is still a substring of the line's.
RUN_LOGS = ('{namespace=~"kagent|ate-system"} |= "${trace_id}" or "session-${session}" |~ `' + RUN_LOG_REGEX + '`'
            ' != "TaskStoreService/UpdateTask" != "request before normalization"'
            ' != "request after normalization" ' + MASK_EMAILS)
NO_RUN = "No agent run in the last 2 days: run scripts/agent-demo.sh"

# The run's tiles read the trace the trace panel loads (Grafana's Dashboard datasource): one Tempo
# lookup per picked run, and the tiles change with the trace. A TraceQL query per tile scanned a
# day of S3 blocks each, four at once beside that lookup on a 1-CPU Tempo, and the tiles kept the
# last run's numbers for about 10 s (§B.41). Spans are picked by name as the trace view has them.
THE_TRACE = "the trace panel's id"  # a tile target's panelId until landing() has laid out the trace
MODEL_SPANS = "^generate_content "
TOOL_SPANS = "^execute_tool (?!\\(merged\\))"  # not kagent's parallel-call wrapper, "execute_tool (merged)"
TURN_SPANS = "^invoke_agent "
# (tag on the model call, field name, shown as). The regex reads each from a model call's tags,
# which Grafana 11.6.16 stringifies as [{"value":…,"key":…}, …], value first.
RUN_FIELDS = [("gen_ai.usage.input_tokens", "input_tokens", "input tokens"),
              ("gen_ai.usage.output_tokens", "output_tokens", "output tokens"),
              ("agent.cost.estimate_usd", "cost_usd", "est. cost")]


def _tag_number(tag, field):
    """A lookahead capturing the tag's number as `field`, or nothing when the call lacks the tag."""
    key = tag.replace(".", "\\.")
    return f'(?=(?:.*?"value":"?(?<{field}>[0-9.eE+-]+)"?,"key":"{key}")?)'


RUN_TAGS_REGEX = "/^" + "".join(_tag_number(tag, field) for tag, field, _ in RUN_FIELDS) + "/"


def of_the_trace(title, desc, span_names, *, transformations=(), **kw):
    """A tile over the spans of the run's trace whose name matches span_names."""
    p = stat(title, [{"datasource": DASHBOARD_DS, "refId": "A", "panelId": THE_TRACE, "withTransforms": False}],
             desc, no_value="-", **kw)
    # The trace comes with the node graph's two frames, so Grafana names its fields "Trace
    # operationName" and the filter (by display name) found no span: one frame first, plain names.
    one_frame = {"id": "concatenate", "options": {"frameNameMode": "drop"}}
    pick = {"id": "filterByValue", "options": {"type": "include", "match": "any", "filters": [
        {"fieldName": "operationName", "config": {"id": "regex", "options": {"value": span_names}}}]}}
    return dict(p, transformations=[one_frame, pick, *(transformations or [
        {"id": "filterFieldsByName", "options": {"include": {"names": ["duration"]}}}])])


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

    b.row("Each hop of an agent request, all runs in the time range: ingress → kagent → Substrate sandbox "
          "→ agent → model and tools")
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

    b.row("One run, picked with Run at the top or a trace id below: its tokens, trace and logs")
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

    b.line(4, [
        (12, of_the_trace(
            "This run: tokens and estimated cost",
            "The run's model calls, summed: input tokens (cache reads included), output tokens, and the "
            "estimated cost at list prices. Read from the trace below.", MODEL_SPANS,
            field_names={f: shown for _, f, shown in RUN_FIELDS}, field_units={"cost_usd": "currencyUSD"},
            field_decimals={"cost_usd": 3}, calcs="sum",
            # Tags as text, the three numbers out of each model call's tags, numbers again.
            transformations=[
                {"id": "convertFieldType", "options": {"fields": {}, "conversions": [
                    {"targetField": "tags", "destinationType": "string"}]}},
                {"id": "extractFields", "options": {"source": "tags", "format": "regexp",
                                                    "regExp": RUN_TAGS_REGEX, "replace": True}},
                {"id": "convertFieldType", "options": {"fields": {}, "conversions": [
                    {"targetField": f, "destinationType": "number"} for _, f, _ in RUN_FIELDS]}}])),
        (4, of_the_trace("Model calls", "Calls to the model API in this run.", MODEL_SPANS, calcs="count")),
        (4, of_the_trace("Tool calls", "Tool calls in this run.", TOOL_SPANS, calcs="count")),
        (4, of_the_trace("Agent turn", "How long the agent's turn took.", TURN_SPANS, calcs="max",
                         field_units={"duration": "ms"}, decimals=1)),
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

    (trace,) = [p for p in b.panels if p["type"] == "traces"]
    for p in b.panels:
        for t in p.get("targets", []):
            if t.get("panelId") == THE_TRACE:
                t["panelId"] = trace["id"]

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


# --- Model Gateway (§T.137) --------------------------------------------------------------------

def _budgets():
    """scripts/model_budgets.py: the budget table, so this dashboard follows budgets.yaml."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import model_budgets
    return model_budgets


GW = 'namespace="model-gateway"'
GW_COST = f"agentgateway_gen_ai_client_cost_usd_total{{{GW}}}"
GW_CALLS = f'agentgateway_requests_total{{{GW}, listener="llm"}}'
GW_REFUSED = f'agentgateway_requests_total{{{GW}, listener="llm", status="429", reason="RateLimit"}}'
GW_LATENCY = f"agentgateway_gen_ai_server_request_duration_bucket{{{GW}}}"
GW_LOGS = '{namespace="model-gateway"} |= "listener=llm"'


def model_gateway():
    mb = _budgets()
    table_ = mb.load()
    vector = mb.budget_vector_expr(table_)
    b = Layout()

    def today(metric, by=""):
        return prom(window_total(metric, by, "$__range"), instant=True)

    def per_interval(metric, by, legend):
        return prom(window_total(metric, by, "$__interval", forward=True), legend, interval="15m")

    rows = "\n".join(f"| {name} | {', '.join(g['agents']) or mb.CHART_DEFAULT_AGENTS} | ${g['daily_usd']:.2f} | "
                     f"{'yes' if g['gateway'] else 'not yet'} |" for name, g in table_["groups"].items())
    b.row("Today, 00:00 UTC to now: the gateway's own counts (a budget day is a UTC day)")
    b.line(5, [
        (5, stat("Spent today", [today(GW_COST)], "What the gateway charged every group today, priced like the "
                 "collector's estimate from Anthropic's reported usage. Only groups on the gateway count here.",
                 units={"A": "currencyUSD"}, decimals=4)),
        (5, stat("Model calls today", [today(GW_CALLS)], "Calls the gateway handled today, answered or refused.")),
        (5, stat("Refused today", [today(GW_REFUSED)], "Calls refused because the group's daily budget was spent "
                 "(429 budget_exceeded). The group's runs fail until 00:00 UTC.", steps=[("green", None), ("red", 1)])),
        (4, stat("Gateway up", [prom(f"sum(up{{{GW}}}) or vector(0)", instant=True)],
                 "Replicas Prometheus can scrape. 0 means every group on the gateway is failing its model calls.",
                 steps=[("red", None), ("green", 1)])),
        (5, text("Where the budgets live", "Budgets: `base-apps/model-gateway/budgets.yaml`, then "
                 "`scripts/gen-model-budgets.py --write` and a pull request. Runbook: "
                 "`base-apps/model-gateway/runbook.md`. The gateway's own UI: `kubectl -n model-gateway "
                 "port-forward deploy/model-gateway 15000:15000`, then http://localhost:15000/ui (Keys).")),
    ])
    share = bargauge("Today's spend against the daily budget",
                     [prom(f"({window_total(GW_COST, 'group', '$__range')})\n/ on(group) group_left(agents, budget_usd) (\n"
                           f"{vector}\n)", instant=True) | {"legendFormat": "{{group}}"}],
                     "Each group's spend today as a share of its daily budget. The gateway refuses the group's "
                     "calls at 100%; Slack hears at the warning line.", unit="percentunit", decimals=1)
    share["fieldConfig"]["defaults"]["max"] = 1
    share["fieldConfig"]["defaults"]["color"] = {"mode": "thresholds"}
    share["fieldConfig"]["defaults"]["thresholds"] = _thr([("green", None), ("orange", table_["warn_at"]), ("red", 1)])
    b.line(9, [
        (12, share),
        (12, text("Groups", "| group | agents | daily budget | on the gateway |\n|---|---|---|---|\n" + rows +
                  "\n\nGroups not on the gateway yet are only measured, as an estimate, on Tokens & Models.")),
    ])
    b.row("Over time")
    b.line(8, [
        (12, ts("Spend per group", [per_interval(GW_COST, "group", "{{group}}")],
                "What the gateway charged each group per interval.", unit="currencyUSD", bars=True, stack=True,
                calcs=("sum",), no_value="No calls through the gateway in this time range")),
        (12, ts("Calls per group and status", [per_interval(GW_CALLS, "group, status", "{{group}} {{status}}")],
                "Every call on the budget listener: 200 answered, 429 refused, 401/403 a wrong or missing key or a "
                "model outside the group's.", bars=True, stack=True, calcs=("sum",),
                no_value="No calls through the gateway in this time range")),
    ])
    b.line(8, [
        (12, ts("Refusals per group", [per_interval(GW_REFUSED, "group", "{{group}}")],
                "Calls refused for a spent budget.", bars=True, calcs=("sum",), no_value="No refusals in this time range")),
        (12, bargauge("Model call latency p95 per group",
                      [prom(f"histogram_quantile(0.95, {window_total(GW_LATENCY, 'le, group', '$__range')})", instant=True)
                       | {"legendFormat": "{{group}}"}],
                      "Request to last byte at the gateway, Anthropic's time included.", unit="s", decimals=1,
                      no_value="No calls in this time range")),
    ])
    b.row("Calls")
    b.line(10, [(24, logs("Gateway log (budget listener)", {"refId": "A", "datasource": LOKI, "expr": GW_LOGS,
                                                            "queryType": "range"},
                          "One line per call: group, model, status, and for a refusal reason=RateLimit. No keys.",
                          no_value="No calls through the gateway in this time range"))])
    return dashboard("model-gateway", "Model Gateway & Budgets",
                     "Each agent group's daily model budget and what the gateway charged it today, its refusals "
                     "and calls (§T.137).", b, time_from="now/d", timezone="utc")


# --- Agent KPIs (§T.144) ---------------------------------------------------------------------------
#
# The eight agent-scenario KPIs of the playbook "Agent-first IDP" (platformengineering.org,
# 2026-10-16, practice 9), measured. Three come from scripts/agent-kpis.py's hourly lines (how tasks
# ended, how long approvals waited, how long pull requests took); the rest from what Prometheus and
# Loki already hold. The first tile is computed here from scripts/agent-capability-map.yaml.

ARTICLE = "https://platformengineering.org/blog/agent-first-idp-how-big-tech-and-the-clouds-are-preparing-platforms-for-agents"
REPO = Path(__file__).resolve().parent.parent
CAPABILITY_MAP = "scripts/agent-capability-map.yaml"
EVAL_CORPUS = "tests/eval-corpus"
AREAS = ("provision", "deploy", "observe", "secure", "govern", "approve", "remediate", "explain")
MODE_CLASS = {"read": "read", "recommend": "propose", "act": "admin"}

KPI = '{namespace="postgresql", app="agent-kpis"} | json | __error__="" | check="agent-kpi"'
PROD = 'caller!="eval"'   # the eval gate's own test sessions are not scenarios
DECISIVE = "limit-too-low|leak|contention|rollout-pending"   # dispatch.py VERDICTS minus inconclusive
VAP_DENY = 'apiserver_validating_admission_policy_check_total{policy=~"agent-.*", enforcement_action="deny"}'
NO_KPI = "No data yet: the agent-kpis job prints each hour at :05"


def capability_areas(root=REPO):
    """The capability map with each area's verdict. open: some agent serves it. safe: open, and every
    agent in it has golden tasks of its own (tests/eval-corpus/<agent>.yaml)."""
    import yaml
    doc = yaml.safe_load((root / CAPABILITY_MAP).read_text())
    corpus = {p.stem for p in (root / EVAL_CORPUS).glob("*.yaml") if not p.name.startswith("_")}
    out = []
    for a in doc["areas"]:
        agents = list(dict.fromkeys(ag for m in a["modes"] for ag in m["agents"]))
        out.append({**a, "agents": agents, "open": bool(agents),
                    "safe": bool(agents) and all(ag in corpus for ag in agents),
                    "untested": [ag for ag in agents if ag not in corpus]})
    return out


def _capability_table(areas):
    rows = []
    for a in sorted(areas, key=lambda a: (not a["safe"], not a["open"], AREAS.index(a["area"]))):
        modes = "; ".join(f"{m['mode']}: {', '.join(m['agents'])}" for m in a["modes"]) or "closed"
        if a["safe"]:
            verdict = "safely open"
        elif a["open"]:
            verdict = "open, no golden tasks for " + ", ".join(a["untested"])
        else:
            verdict = "closed: " + a["why"].split(". ")[0].rstrip(".")
        rows.append(f"| {a['area']} | {modes} | {verdict} |")
    return "| area | how agents serve it | verdict |\n|---|---|---|\n" + "\n".join(rows)


def kpis():
    areas = capability_areas()
    safe, open_ = sum(a["safe"] for a in areas), sum(a["open"] for a in areas)
    b = Layout()

    def lsum(sel):
        return f"sum(count_over_time({sel} [$__range]))"

    def median(sel, field, q="0.5"):
        return f"quantile_over_time({q}, {sel} | unwrap {field} | __error__=\"\" [$__range]) by ()"

    tasks = f'{KPI} | kind="task" | {PROD}'
    completed = tasks + ' | state="completed"'
    approvals = f'{KPI} | kind="approval" | {PROD}'
    merged = f'{KPI} | kind="change" | outcome="merged"'
    windows_ = f'{KPI} | kind="window" | db="ok"'
    remediate = f'{DISP} | event="remediate"'
    diagnosed = remediate + f" | verdict=~`{DECISIVE}`"
    investigated, firing = 'decision="investigate"', 'status="firing"'
    ungated = f'sum(sum_over_time({windows_} | unwrap ungated_writes | __error__="" [$__range]))'
    calls = f'sum(sum_over_time({windows_} | unwrap scenario_tool_calls | __error__="" [$__range]))'

    def distinct_alerts(sel):
        return f"count(sum by (fingerprint) (count_over_time({DISP} | event=\"dispatch\" | {sel} [$__range])))"

    b.row("The playbook's eight agent-scenario KPIs, over the dashboard's time range (default 7 days)")
    b.line(5, [
        (6, stat(f"1 · Capabilities open to agents (of {len(areas)})", [
            prom(f"vector({safe})", ref="A", instant=True), prom(f"vector({open_})", ref="B", instant=True)],
            "The playbook's eight capability areas (provision, deploy, observe, secure, govern, approve, "
            "remediate, explain), from scripts/agent-capability-map.yaml. Open: an agent serves the area. "
            "Safely open: every agent in it also has golden tasks of its own in the eval corpus. Changes "
            "with a pull request; see the table below.",
            names={"A": "safely open", "B": "open"})),
        (6, stat("2 · Intent success rate", [
            loki(f"(({lsum(completed)} or vector(0)) / {lsum(tasks)})", ref="A", instant=True),
            loki(f"{lsum(tasks)} or vector(0)", ref="B", instant=True)],
            "Agent tasks that ended completed, of all that ended (completed, failed, canceled, rejected), "
            "for every caller but the eval gate. A task that paused for an approval and then finished is "
            "one success. From kagent's task states (agent-kpis job).",
            names={"A": "completed", "B": "tasks"}, units={"A": "percentunit"}, no_value="—")),
        (6, stat("3 · Time to a safe change", [
            loki(median(f'{KPI} | kind="task" | agent="implementer" | caller="dispatcher" | state="completed"',
                        "duration_s"), ref="A", instant=True),
            loki(median(merged + ' | author="implementer"', "lead_s"), ref="B", instant=True),
            loki(median(merged + ' | author="owner"', "lead_s"), ref="C", instant=True)],
            "Medians. Alert to diagnosis: the implementer's investigation of an alert, from the alert's "
            "session to its finished answer (a pull request, a restart request or a diagnosis). Agent PR to "
            "merge: an implementer pull request until a person merges it (CI and review). People's PRs: the "
            "owner's own changes, for comparison. Task states and GitHub's merge times (agent-kpis job).",
            names={"A": "alert → diagnosis", "B": "agent PR → merge", "C": "people's PRs"},
            units={"A": "s", "B": "s", "C": "s"}, decimals=0, no_value="—")),
        (6, stat("4 · Cost per scenario", [
            prom(f'{window_total("agent_cost_estimate_usd_total", "", "$__range")} / '
                 f'{window_total("agent_runs_total", "", "$__range")}', ref="A", instant=True),
            prom(window_total("agent_cost_estimate_usd_total", "", "$__range"), ref="B", instant=True)],
            "Estimated model cost of one agent run (one turn: a question answered, an alert investigated), "
            "averaged over every run, and the total. List prices, from the collector; per agent below.",
            names={"A": "per run", "B": "total"}, units={"A": "currencyUSD", "B": "currencyUSD"}, decimals=3,
            no_value="—")),
    ])
    b.line(5, [
        (6, stat("5 · Approval cycle time", [
            loki(median(approvals, "wait_s"), ref="A", instant=True),
            loki(median(approvals, "wait_s", "0.9"), ref="B", instant=True),
            loki(f"{lsum(approvals)} or vector(0)", ref="C", instant=True)],
            "How long a gated call waited for a person's decision: median and 90th percentile, and the "
            "number of decisions (the eval gate's automatic rejections of its own tests left out). From "
            "the approval request and decision kagent stores (agent-kpis job).",
            names={"A": "median", "B": "p90", "C": "decisions"}, units={"A": "s", "B": "s"}, decimals=0,
            no_value="—")),
        (6, stat("6 · Policy violation rate", [
            loki(f"(({ungated} or vector(0)) / {calls})", ref="A", instant=True),
            loki(f"{ungated} or vector(0)", ref="B", instant=True),
            loki(f"{lsum(REFUSED)} or vector(0)", ref="C", instant=True),
            prom(window_total(VAP_DENY, "", "$__range") + " or vector(0)", ref="D", instant=True)],
            "Rate: tool calls that broke a rule and ran (a write or destructive tool without an approved "
            "decision, the audit's rule) of all tool calls, both from the hours the agent-kpis job covered, "
            "eval gate left out. Beside it, what the guardrails stopped before it ran: calls an MCP adapter "
            "refused (a tool off its allow-list; its log does not say who asked, so the eval gate's are "
            "included) and agent definitions the API server denied at admission.",
            names={"A": "violation rate", "B": "writes w/o approval", "C": "refused before running",
                   "D": "denied at admission"},
            units={"A": "percentunit"}, no_value="—")),
        (6, stat("7 · Support load absorbed", [
            loki(f"(({distinct_alerts(investigated)} or vector(0)) / {distinct_alerts(firing)})", ref="A", instant=True),
            loki(distinct_alerts(firing) + " or vector(0)", ref="B", instant=True)],
            "Firing alerts (distinct) that reached a person already investigated by an agent, and all "
            "firing alerts. 2 of the 14 alert rules route to an agent (base-apps/dispatcher/"
            "dispatch-table.yaml); the rest go to people only, some by design (no agent investigates the "
            "agents).",
            names={"A": "investigated first", "B": "alerts fired"}, units={"A": "percentunit"}, decimals=0,
            no_value="—")),
        (6, stat("8 · Completed without escalation", [
            loki(f"(({lsum(diagnosed)} or vector(0)) / {lsum(remediate)})", ref="A", instant=True),
            loki(f"{lsum(remediate)} or vector(0)", ref="B", instant=True)],
            "Alert investigations that ended in a diagnosis the platform acts on (limit too low → a pull "
            "request, leak or pending rollout → a restart a person approves, contention), of all of them. "
            "Inconclusive, failed or timed out: a person has to look. From the dispatcher's remediate lines.",
            names={"A": "no escalation", "B": "investigations"}, units={"A": "percentunit"}, decimals=0,
            no_value="—")),
    ])

    b.row("Capability map: the playbook's eight areas (scripts/agent-capability-map.yaml)")
    b.line(9, [
        (16, text(f"{safe} of {len(areas)} areas safely open, {open_} open", _capability_table(areas))),
        (8, text("Where these numbers come from",
                 f"The KPIs are the ones the playbook [Agent-first IDP]({ARTICLE}) (platformengineering.org, "
                 "2026-10-16, practice 9) asks an agent platform to report instead of portal usage.\n\n"
                 "- **agent-kpis** (CronJob in `postgresql`, hourly at :05, the SELECT-only audit role): how "
                 "each agent task ended, how long each approval waited, how long each pull request took "
                 "to merge (GitHub). Argument-free; callers as a class, never an identity.\n"
                 "- **Prometheus**: model cost and runs (the collector), admission denials (the API server).\n"
                 "- **Loki**: the dispatcher's alerts and verdicts, the MCP adapters' refusals.\n\n"
                 "The eval gate's own test sessions are left out of every scenario KPI; only the count of "
                 "calls an adapter refused includes them, because the adapter's log does not say who asked. "
                 "A rate shows a dash when nothing is behind it, and 0 % when nothing succeeded.")),
    ])

    b.row("Over time")
    b.line(8, [
        (12, ts("Agent tasks by how they ended", [
            loki(f"sum by (state) (count_over_time({tasks} [$__interval]))", "{{state}}")],
            "Every caller but the eval gate. Per interval.", bars=True, stack=True, calcs=("sum",),
            interval="1h", no_value=NO_KPI)),
        (12, ts("Agent tasks by caller", [
            loki(f'sum by (caller) (count_over_time({KPI} | kind="task" [$__interval]))', "{{caller}}")],
            "Who asks: person (CLI), slack, dispatcher (alerts), demo, backstage, docs-check, eval (the "
            "eval gate's tests), ui.", bars=True, stack=True, calcs=("sum",), interval="1h", no_value=NO_KPI)),
    ])
    b.line(8, [
        (8, ts("Approval wait, each decision", [
            loki(f'max_over_time({approvals} | unwrap wait_s | __error__="" [$__interval]) by (agent)', "{{agent}}")],
            "Seconds from the approval request to the decision.", unit="s", interval="1h", no_value=NO_KPI)),
        (8, ts("Pull request lead time by author", [
            loki(f'quantile_over_time(0.5, {merged} | unwrap lead_s | __error__="" [$__interval]) by (author)',
                 "{{author}}")],
            "Median opened → merged per interval. owner = a person, implementer = the agent, renovate = "
            "dependency bumps (the promotion rule merges some unattended).", unit="s", interval="6h", no_value=NO_KPI)),
        (8, bargauge("Cost per run, by agent", [
            prom(f'{window_total("agent_cost_estimate_usd_total", "service_name", "$__range")} / '
                 f'{window_total("agent_runs_total", "service_name", "$__range")}', instant=True)
            | {"legendFormat": "{{service_name}}"}],
            "Estimated model cost of one run of each agent (list prices).", unit="currencyUSD", decimals=3)),
    ])
    b.line(8, [
        (12, ts("Rule-breaking tool calls", [
            loki(f'sum(sum_over_time({windows_} | unwrap ungated_writes | __error__="" [$__interval]))', "writes w/o approval", ref="A"),
            loki(f"sum(count_over_time({REFUSED} [$__interval]))", "refused by an adapter", ref="B")],
            "Per interval. A write without an approval also raises the audit's own alert.", bars=True,
            calcs=("sum",), interval="1h", no_value="0")),
        (12, ts("Alerts: fired, investigated, diagnosed", [
            loki(f'sum(count_over_time({DISP} | event="dispatch" | status="firing" [$__interval]))', "fired", ref="A"),
            loki(f'sum(count_over_time({DISP} | event="dispatch" | decision="investigate" [$__interval]))', "investigated", ref="B"),
            loki(f"sum(count_over_time({diagnosed} [$__interval]))", "diagnosed", ref="C")],
            "Alert notifications the dispatcher received, the ones it sent to an agent, and the "
            "investigations that ended in a diagnosis.", bars=True, calcs=("sum",), interval="6h",
            no_value="No alerts in this time range")),
    ])

    b.row("The facts")
    b.line(8, [
        (12, table("Approvals", [loki(f'{KPI} | kind="approval"')],
                   "Every decision on a gated call, newest first (the eval gate's included, caller eval).",
                   transformations=[{"id": "extractFields", "options": {"source": "Line", "format": "json"}},
                                    _organize(["at", "agent", "tool", "caller", "decision", "wait_s"],
                                              {"wait_s": "waited (s)"})],
                   no_value=NO_KPI, sort=[{"displayName": "at", "desc": True}])),
        (12, table("Pull requests merged or closed", [loki(f'{KPI} | kind="change"')],
                   "Newest first; lead time = opened → merged or closed.",
                   transformations=[{"id": "extractFields", "options": {"source": "Line", "format": "json"}},
                                    _organize(["at", "number", "author", "outcome", "lead_s"],
                                              {"lead_s": "lead time (s)", "number": "PR"})],
                   no_value=NO_KPI, sort=[{"displayName": "at", "desc": True}])),
    ])
    b.line(8, [(24, table("Agent tasks that did not complete", [loki(f'{KPI} | kind="task" | state!="completed"')],
                          "Failed, canceled or rejected tasks, newest first. task_id finds the session in the "
                          "audit UI.",
                          transformations=[{"id": "extractFields", "options": {"source": "Line", "format": "json"}},
                                           _organize(["at", "agent", "caller", "state", "paused", "duration_s", "task_id"],
                                                     {"duration_s": "ran (s)"})],
                          no_value="Every task completed in this time range",
                          sort=[{"displayName": "at", "desc": True}]))])
    return dashboard("agent-kpis", "Agent KPIs",
                     "The agent-first IDP playbook's eight agent-scenario KPIs, measured: capabilities open to "
                     "agents, intent success, time to a safe change, cost per scenario, approval cycle time, "
                     "policy violations, support load absorbed, completed without escalation.",
                     b, time_from="now-7d")


# --- Bug Investigation · live (§T.149) ----------------------------------------------------------
# One Taskboard bug report (or a Taskboard 5xx alert) as the platform handles it, for a live demo:
# the trigger's own JSON step lines (scripts/bug_investigator_trigger.py, §T.146-§T.148), the
# collector's per-agent counters for the model and tool calls, the MCP adapters' access lines and
# the agents' traces. Every lane lights only from that data; nothing is inferred.

BIT = '{namespace="kagent", app="bug-investigator-trigger"}'   # the trigger's JSON lines, one per step
BIT_STEPS = BIT + ' != `"event": "idle"`'                       # every step but "nothing to do"


def bit_event(name):
    """The trigger's lines for one step; the substring check runs before any JSON parsing."""
    return BIT + ' |= `"event": "' + name + '"`'


# The agent's tools (base-apps/kagent/agent-templates/bug-investigator.yaml), by the server they run on.
GITHUB_READS = "get_file_contents|search_code|list_commits|get_commit|list_pull_requests|pull_request_read"
TELEMETRY_TOOLS = "query_loki_logs|query_loki_stats|query_prometheus|list_loki_label_names|list_loki_label_values"
BROWSER_TOOLS = "replay_ui_steps"
TB_5XX = 'taskboard_http_requests_total{namespace="taskboard", status=~"5.."}'   # the trigger's ALERT_EXPR counter
TB_MCP_CALLS = ('{namespace="kagent", container="mcp-server", pod=~"(taskboard-telemetry|taskboard-browser|implementer)-mcp-.*"} '
                '|= "mcp.method.name=tools/call"')
NO_INVESTIGATION = "No investigation in this time range: label a Taskboard issue bug"

# The step names the trigger logs, as a viewer reads them, and their colour on the Step now tile.
STEP_NAMES = [("alert_issue_filed", "Alert filed an issue", "orange"), ("investigating", "Picked the issue", "blue"),
              ("gathered", "Facts gathered", "blue"), ("recalled", "Memory recalled", "blue"),
              ("answered", "Agent answered", "blue"), ("screenshots_posted", "Screenshots uploaded", "blue"),
              ("screenshots_failed", "Screenshots not uploaded", "orange"), ("posted", "Comment posted", "green"),
              ("fix_handoff", "Fix PR handed off", "green"), ("remembered", "Outcome remembered", "green"),
              ("investigation_failed", "Failed: nothing posted", "red")]
VERDICT_NAMES = [("likely_cause", "Likely cause", "green"), ("not_a_bug", "Not a bug", "blue"),
                 ("needs_info", "Needs info", "orange"), ("unclear", "Unclear", "orange")]

# One readable line per trigger step (Loki line_format over the step's JSON fields).
STEP_LINE = (
    '| line_format `{{ if eq .event "alert_issue_filed" }}ALERT   {{ .route }} answered 5xx ({{ .count }} in 10 min): filed #{{ .issue }}'
    '{{ else if eq .event "investigating" }}PICKED  #{{ .issue }} "{{ .title }}"{{ if eq .alert "true" }} (filed by the alert){{ end }}'
    '{{ else if eq .event "gathered" }}FACTS   {{ .prs }} merged PRs, {{ .deploys }} deploys ({{ .seconds }} s)'
    '{{ else if eq .event "recalled" }}MEMORY  {{ .count }} of {{ .remembered }} remembered outcomes match'
    '{{ else if eq .event "answered" }}AGENT   {{ .verdict }} after {{ .tool_calls }} tool calls ({{ .seconds }} s)'
    '{{ else if eq .event "screenshots_posted" }}SHOTS   {{ .count }} browser replay screenshots uploaded for the comment'
    '{{ else if eq .event "screenshots_failed" }}SHOTS   not uploaded (the comment goes out without them): {{ .error }}'
    '{{ else if eq .event "posted" }}POSTED  {{ .verdict }}: {{ .verified }} of {{ .total }} evidence links verified'
    '{{ else if eq .event "fix_handoff" }}FIX     {{ if .pr_url }}PR #{{ .pr_number }} opened by kagent/implementer'
    '{{ else if .existing_pr_url }}PR #{{ .existing_pr_number }} already open: no duplicate{{ else }}{{ .outcome }}{{ end }}'
    '{{ else if eq .event "remembered" }}MEMORY  {{ if eq .ok "true" }}outcome saved ({{ .entries }} kept){{ else }}not saved: {{ .reason }}{{ .error }}{{ end }}'
    '{{ else if eq .event "investigation_failed" }}FAILED  at {{ .stage }}: {{ .error }}'
    '{{ else }}{{ .event }}{{ end }}` ')


def _text_mappings(pairs):
    return [{"type": "value", "options": {k: {"text": label, "color": color, "index": i}
                                          for i, (k, label, color) in enumerate(pairs)}}]


def textstat(title, target, desc, *, mappings=None, no_value="—"):
    """One line of text: the newest line of a log query (maxLines 1; its line_format is the value),
    shown by a stat over the string field Line."""
    defaults = {"noValue": no_value, "color": {"mode": "thresholds"}, "thresholds": _thr([("text", None)]),
                "mappings": mappings or []}
    return {"type": "stat", "title": title, "description": desc, "datasource": target["datasource"],
            "targets": [target], "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^Line$/", "values": False},
                        "textMode": "value", "colorMode": "value" if mappings else "none", "graphMode": "none",
                        "justifyMode": "center", "orientation": "vertical", "wideLayout": True}}


def timeline(title, targets, desc, *, colors=None, interval="30s"):
    """One lane per target, in target order: coloured for each step in which its stage happened (a
    count above zero), empty otherwise. colors: refId -> colour (default blue)."""
    defaults = {"color": {"mode": "thresholds"}, "thresholds": _thr([("transparent", None), ("blue", 0.001)]),
                "mappings": [{"type": "value", "options": {"0": {"text": "-", "color": "transparent", "index": 0}}}],
                "custom": {"fillOpacity": 90, "lineWidth": 0, "spanNulls": False, "insertNulls": False}}
    # After the join every lane is a field of one frame, so colours follow the lane's name.
    names = {t["refId"]: t["legendFormat"] for t in targets}
    overrides = [{"matcher": {"id": "byName", "options": names[ref]},
                  "properties": [{"id": "thresholds", "value": _thr([("transparent", None), (color, 0.001)])}]}
                 for ref, color in sorted((colors or {}).items())]
    # Grafana returns a mixed panel's frames grouped by datasource, not in target order: join them on
    # time (outer, so a lane with no point is empty there) and order the lanes by the targets.
    order = [t["legendFormat"] for t in targets]
    transformations = [{"id": "joinByField", "options": {"byField": "Time", "mode": "outer"}},
                       {"id": "organize", "options": {"indexByName": {"Time": 0, **{n: i + 1 for i, n in enumerate(order)}},
                                                      "excludeByName": {}, "renameByName": {}}}]
    return {"type": "state-timeline", "title": title, "description": desc, "datasource": _ds(targets),
            "targets": targets, "interval": interval, "transformations": transformations,
            "fieldConfig": {"defaults": defaults, "overrides": overrides},
            "options": {"mergeValues": False, "showValue": "never", "alignValue": "center", "rowHeight": 0.85,
                        "legend": {"showLegend": False}, "tooltip": {"mode": "single", "sort": "none"}}}


def _latest(stream, field, *, window="$__range"):
    """The newest value of a numeric field across the trigger's lines (by (app) merges its streams)."""
    return (f'last_over_time({stream} | json {field} | __error__="" | {field}!="" '
            f"| unwrap {field} [{window}]) by (app)")


def bug_investigation_live():
    b = Layout()
    b.line(3, [(24, text("", (
        "**A Taskboard bug report, from issue to verified comment and fix pull request, as the platform "
        "runs it.** Read the journey top to bottom, left to right: the alert signal; the trigger (a "
        "CronJob, plain code, no model) picking the issue and gathering facts; its memory of verified "
        "outcomes; the read-only agent's model calls and tool calls (GitHub reads, Loki and Prometheus "
        "queries held to namespace `taskboard`); the script that verifies every evidence link before "
        "anything is posted; the fix pull request from `kagent/implementer`; the outcome remembered for "
        "the next report. Refreshes every 10 s.")))])

    b.row("Now: the latest investigation in the time range")
    recalled = ('| json count, remembered, first="issues[0]", second="issues[1]" '
                '| line_format `{{ if eq .count "0" }}no match ({{ .remembered }} kept){{ else }}#{{ .first }}'
                '{{ if .second }}, #{{ .second }}{{ end }} ({{ .remembered }} kept){{ end }}`')
    fix = ('| json | line_format `{{ if .pr_url }}#{{ .pr_number }} opened{{ else if .existing_pr_url }}'
           '#{{ .existing_pr_number }} (existing){{ else }}{{ regexReplaceAll ".*/pull/" .outcome "#" }}{{ end }}`')
    b.line(4, [
        (2, stat("Issue", [loki(_latest(BIT_STEPS, "issue"), instant=True)],
                 "The Taskboard issue of the trigger's latest step (its JSON lines in Loki).",
                 units={"A": "prefix:#"}, decimals=0, no_value="-")),
        (4, textstat("Step now", loki(BIT_STEPS + " | json event | line_format `{{ .event }}`", max_lines=1),
                     "The trigger's latest step: picked, facts gathered, memory recalled, agent answered, "
                     "browser screenshots uploaded, comment posted, fix PR handed off, outcome remembered "
                     "(or failed).",
                     mappings=_text_mappings(STEP_NAMES))),
        (3, textstat("Verdict", loki(bit_event("posted") + " | json verdict | line_format `{{ .verdict }}`",
                                     max_lines=1),
                     "The verdict of the latest comment, after verify: a claim it could not re-check is "
                     "dropped, and a cause left without verified evidence becomes `unclear`.",
                     mappings=_text_mappings(VERDICT_NAMES))),
        (3, textstat("Evidence verified", loki(bit_event("posted") + " | json verified, total "
                                               "| line_format `{{ .verified }} of {{ .total }}`", max_lines=1),
                     "Evidence links the script re-checked (commits, PRs, file lines and quotes, doc quotes, "
                     "every Loki/Prometheus query re-run) out of those the agent cited.")),
        (4, textstat("Memory recalled", loki(bit_event("recalled") + " " + recalled, max_lines=1),
                     "Verified outcomes of earlier investigations the trigger matched to this report and "
                     "added to the facts (§T.148): leads for the agent, never evidence.")),
        (3, textstat("Fix PR", loki(bit_event("fix_handoff") + " " + fix, max_lines=1),
                     "The pull request kagent/implementer opened for the latest verified cause, or the open "
                     "one it found instead of opening a duplicate. A person merges.")),
        (2, stat("Agent time", [loki(_latest(bit_event("answered"), "seconds"), instant=True)],
                 "How long the agent took on the latest report, from the A2A request to its answer.",
                 units={"A": "suffix: s"}, decimals=0, no_value="-")),
        (3, stat("Tool calls", [loki(_latest(bit_event("answered"), "tool_calls"), instant=True)],
                 "Tool calls the agent made for the latest report: GitHub reads and Loki/Prometheus "
                 "queries; read-only, with no credential in its sandbox.", decimals=0, no_value="-")),
    ])

    b.row("The journey: each lane lights up in the 30 s its stage happens")

    def agent_calls(metric, agent, tools=None):
        sel = f'service_name="{agent}"' + (f', gen_ai_tool_name=~"{tools}"' if tools else "")
        return window_total(f"{metric}{{{sel}}}", "", "$__interval", forward=True)

    # `or vector(0)`: an explicit 0 in every step without the stage. After the join a lane with no
    # point at a step is null there, and a state timeline carries the last state across nulls.
    def steps(name):
        return f"sum(count_over_time({bit_event(name)} [$__interval])) or vector(0)"

    lanes = [
        prom(f"sum(rate({TB_5XX}[$__rate_interval])) * 60", "Alert signal: Taskboard 5xx", ref="A"),
        loki(steps("alert_issue_filed"), "Alert check files an issue", ref="B"),
        loki(steps("investigating"), "Trigger picks the issue", ref="C"),
        loki(steps("gathered"), "Gather facts: PRs, deploys, docs", ref="D"),
        loki(steps("recalled"), "Recall verified outcomes", ref="E"),
        prom(agent_calls("agent_model_calls_total", "bug-investigator"), "Agent: model calls", ref="F"),
        prom(agent_calls("agent_tool_calls_total", "bug-investigator", GITHUB_READS), "Agent: GitHub reads", ref="G"),
        prom(agent_calls("agent_tool_calls_total", "bug-investigator", TELEMETRY_TOOLS),
             "Agent: Loki + Prometheus (taskboard only)", ref="H"),
        prom(agent_calls("agent_tool_calls_total", "bug-investigator", BROWSER_TOOLS),
             "Agent: browser replays (taskboard copy)", ref="I"),
        loki(f'sum(count_over_time({TB_MCP_CALLS} |= "Unknown tool" [$__interval])) or vector(0)',
             "Refused tool call", ref="J"),
        loki(steps("posted"), "Verify every link, post the comment", ref="K"),
        prom(agent_calls("agent_tool_calls_total", "implementer"), "Fix PR: kagent/implementer", ref="L"),
        loki(steps("remembered"), "Remember the verified outcome", ref="M"),
        loki(steps("investigation_failed"), "Failed: nothing posted", ref="N"),
    ]
    b.line(15, [(24, timeline("Journey", lanes, (
        "One lane per stage, top to bottom in the order a report travels; a lane is coloured in each 30 s "
        "its stage happened. Trigger stages: its JSON step lines (Loki). Agent and implementer calls: the "
        "collector's per-agent counters (agent_model_calls_total, agent_tool_calls_total; the browser lane is replay_ui_steps). Alert signal: "
        "Taskboard's 5xx counter, the trigger's alert expression. Refused tool call: an MCP adapter's "
        "`Unknown tool` (a tool off its allowlist); a Loki/Prometheus query outside namespace taskboard "
        "comes back to the agent as a tool error instead (`ok: false` in the trigger's AGENT line). The "
        "agent lanes also show the eval gate's test sessions, and the implementer lane its work for other "
        "callers, such as resource alerts."),
        colors={"A": "orange", "B": "orange", "J": "red", "K": "green", "L": "green", "M": "green", "N": "red"}))])

    b.row("Behind the scenes, line by line")
    b.line(11, [
        (12, logs("What the trigger did", loki(BIT_STEPS + ' | json | __error__="" ' + STEP_LINE + MASK_EMAILS),
                  "The trigger's step lines, newest first, one readable line each. Expand a line for every "
                  "field, including the agent's tool trail on the AGENT line.", no_value=NO_INVESTIGATION)),
        (12, logs("Tool calls through the MCP adapters", loki(
            TB_MCP_CALLS + " | logfmt | line_format `{{ .mcp_target }}  {{ .gen_ai_tool_name }}  "
            "HTTP {{ .http_status }}  {{ .duration }}`"),
            "Every tools/call through the three servers these agents use: implementer-mcp (GitHub: the "
            "agent's reads, the implementer's branch and PR), taskboard-telemetry-mcp (Loki and "
            "Prometheus, held to namespace taskboard) and taskboard-browser-mcp (UI replays on a "
            "copy of Taskboard). Arguments and results are never shown.",
            no_value="No tool calls in this time range")),
    ])

    b.row("Agent sessions: open one for every model and tool call")
    b.line(8, [(24, table("Turns of bug-investigator and implementer",
                          [tsearch('{resource.service.name=~"bug-investigator|implementer" && name=~"invoke_agent .*"}',
                                   limit=20, spss=5, table_type="spans")],
                          "One row per agent turn in the time range (Tempo), newest first, including the eval "
                          "gate's test turns. Click its trace id for the sandbox, each model call through the "
                          "gateway and each tool call, in order."))])

    b.row("Signal, spend and score")
    eval_score = ('last_over_time({namespace="kagent", container="eval-gate"} | json | __error__="" '
                  '| event="eval-gate" | agent="bug-investigator" | pass_rate!="" | unwrap pass_rate [30d]) by (agent)')
    b.line(7, [
        (12, ts("Taskboard 5xx per minute, by route",
                [prom(f"sum by (route) (rate({TB_5XX}[$__rate_interval])) * 60", "{{route}}")],
                "Taskboard's own server errors (taskboard_http_requests_total): the signal the trigger's "
                "alert check and the Grafana rule \"Taskboard server errors\" watch.", no_value="0")),
        (4, stat("Agent spend", [prom(window_total('agent_cost_estimate_usd_total{service_name="bug-investigator"}',
                                                   "", "$__range") + " or vector(0)", instant=True)],
                 "Estimated model cost of the bug-investigator's calls in the time range (list prices, the "
                 "collector's estimate). Its daily budget at the model gateway is $8.",
                 units={"A": "currencyUSD"}, decimals=2)),
        (4, stat("Agent tokens", [
            prom(window_total('agent_tokens_input_total{service_name="bug-investigator"}', "", "$__range")
                 + " or vector(0)", instant=True, ref="A"),
            prom(window_total('agent_tokens_cache_read_total{service_name="bug-investigator"}', "", "$__range")
                 + " or vector(0)", instant=True, ref="B")],
            "Input tokens of its model calls in the time range, and how many came from the prompt cache.",
            names={"A": "input", "B": "from cache"})),
        (4, stat("Eval gate score", [loki(eval_score, instant=True)],
                 "The eval gate's latest score for bug-investigator: its corpus (smoke test, bug 2, bug 3) "
                 "and the shared adversarial cases, scored on every change to the agent.",
                 units={"A": "percentunit"}, decimals=0, steps=[("orange", None), ("green", 0.8)], no_value="-")),
    ])
    return dashboard("bug-investigation-live", "Bug Investigation · live",
                     "A Taskboard bug report (or a Taskboard 5xx alert), step by step, as the platform handles "
                     "it: trigger, memory, the read-only agent's model and tool calls, verify, the comment, the "
                     "implementer's fix PR. For a live demo: 30 minutes, refreshing every 10 s.",
                     b, time_from="now-30m", refresh="10s")


BUILDERS = {"agent-platform": landing, "agent-quality": quality, "agent-tokens": tokens,
            "agent-traffic": traffic, "agent-sandboxes": sandboxes, "agent-alerts": alerts,
            "mcp-tool-calls": mcp, "model-gateway": model_gateway, "agent-kpis": kpis,
            "bug-investigation-live": bug_investigation_live}


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
