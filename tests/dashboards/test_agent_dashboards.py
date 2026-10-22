"""Guards on the Agents folder's dashboards (scripts/gen-agent-dashboards.py, §T.135, §V.106).

Most of what these check was found by loading the dashboards into Grafana 11.6.16 against the
cluster's Prometheus, Loki and Tempo 3.1 (and a local collector fed the real ask-hk trace): each
test names the failure it keeps out.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
GEN = REPO / "scripts" / "gen-agent-dashboards.py"
OUT = REPO / "base-apps" / "logging" / "grafana-dashboards-agents.yaml"
DEPLOY = REPO / "base-apps" / "logging" / "grafana-deployment.yaml"
COLLECTOR = REPO / "base-apps" / "tracing" / "collector.yaml"

spec = importlib.util.spec_from_file_location("gen_agent_dashboards", GEN)
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def _configmaps():
    return {d["metadata"]["name"]: d for d in yaml.safe_load_all(OUT.read_text()) if d}


def _dashboards():
    out = {}
    for cm in _configmaps().values():
        (body,) = cm["data"].values()
        d = json.loads(body)
        out[d["uid"]] = d
    return out


def _panels():
    for uid, d in _dashboards().items():
        for p in d["panels"]:
            if p["type"] != "row":
                yield uid, p


def _targets():
    for uid, p in _panels():
        for t in p.get("targets", []):
            yield uid, p, t


def _query(t):
    return t.get("expr") or t.get("query") or ""


def _deployment():
    return next(d for d in yaml.safe_load_all(DEPLOY.read_text()) if d and d["kind"] == "Deployment")


def _collector_config():
    cm = next(d for d in yaml.safe_load_all(COLLECTOR.read_text()) if d and d["kind"] == "ConfigMap")
    return yaml.safe_load(cm["data"]["collector.yaml"])


def _gen(root, *extra):
    return subprocess.run([sys.executable, str(GEN), "--repo-root", str(root), *extra], capture_output=True, text=True)


# --- generated, mounted, home ---------------------------------------------------------------------

def test_checked_in_dashboards_match_generator():
    r = _gen(REPO, "--check")
    assert r.returncode == 0, r.stderr


def test_check_catches_a_hand_edit(tmp_path):
    dst = tmp_path / OUT.relative_to(REPO)
    dst.parent.mkdir(parents=True)
    shutil.copy(OUT, dst)
    dst.write_text(dst.read_text().replace('"title": "Agent Platform"', '"title": "Hand edited"'))
    r = _gen(tmp_path, "--check")
    assert r.returncode == 1 and "out of date" in r.stderr


def test_every_dashboard_is_mounted_into_the_agents_folder():
    """One projected volume carries every generated ConfigMap; a ConfigMap left out is a dashboard
    that silently never appears."""
    pod = _deployment()["spec"]["template"]["spec"]
    vol = next(v for v in pod["volumes"] if v["name"] == "dashboards-agents")
    mounted = {s["configMap"]["name"] for s in vol["projected"]["sources"]}
    assert mounted == set(_configmaps()) == set(gen.DASHBOARDS)
    mounts = {m["name"]: m["mountPath"] for c in pod["containers"] for m in c.get("volumeMounts", [])}
    assert mounts["dashboards-agents"] == gen.MOUNT
    for name, cm in _configmaps().items():
        assert cm["metadata"]["labels"]["grafana_dashboard"] == "1"
        assert list(cm["data"]) == [gen.DASHBOARDS[name][0]]


def test_grafana_opens_on_the_agent_platform():
    env = {e["name"]: e.get("value") for e in _deployment()["spec"]["template"]["spec"]["containers"][0]["env"]}
    home = env["GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH"]
    assert home == gen.HOME == f"{gen.MOUNT}/agent-platform.json"
    assert "agent-platform.json" in _configmaps()["grafana-dashboard-agent-platform"]["data"]


# --- layout, links, datasources -------------------------------------------------------------------

def test_panel_ids_unique_and_layout_fits_grid():
    for uid, d in _dashboards().items():
        ids = [p["id"] for p in d["panels"]]
        assert len(ids) == len(set(ids)), uid
        for p in d["panels"]:
            g = p["gridPos"]
            assert g["x"] + g["w"] <= 24, (uid, p["title"])


def test_every_dashboard_is_tagged_and_links_home():
    for uid, d in _dashboards().items():
        assert "agents" in d["tags"], uid
        assert any(link.get("url") == "/d/agent-platform" for link in d["links"]), uid


def test_landing_tiles_open_existing_dashboards():
    uids = set(_dashboards())
    landing = _dashboards()["agent-platform"]
    tiles = [p for p in landing["panels"] if p.get("links")]
    assert len(tiles) == 6
    for p in tiles:
        target = re.match(r"/d/([a-z0-9-]+)", p["links"][0]["url"]).group(1)
        assert target in uids - {"agent-platform"}, p["title"]


def test_datasources_follow_the_repo_convention():
    """Prometheus unpinned ({"type": "prometheus"}), Loki and Tempo by the uids the datasource file
    keeps stable. A panel that mixes datasources cannot resolve an unpinned Prometheus target
    (Grafana sends no query for it), so there, and only there, it names the hidden ${prometheus}
    datasource variable, which defaults to the default Prometheus. The Dashboard datasource (another
    panel's results) only for the run's tiles (test_the_run_tiles_change_with_the_trace)."""
    ds = yaml.safe_load(next(d for d in yaml.safe_load_all(DEPLOY.read_text())
                             if d and d["kind"] == "ConfigMap")["data"]["datasources.yaml"])
    uids = {d["type"]: d["uid"] for d in ds["datasources"]}
    for uid, p, t in _targets():
        src = t["datasource"]
        mixed = p["datasource"] == gen.MIXED
        if src["type"] == "prometheus":
            assert src == (gen.PROM_IN_MIXED if mixed else {"type": "prometheus"}), (uid, p["title"])
        elif src == gen.DASHBOARD_DS:
            assert uid == "agent-platform" and p["datasource"] == src, p["title"]
        else:
            assert src["type"] in ("loki", "tempo") and src["uid"] == uids[src["type"]], (uid, p["title"])
    for uid, d in _dashboards().items():
        if any(p.get("datasource") == gen.MIXED for p in d["panels"]):
            names = {v["name"]: v for v in d["templating"]["list"]}
            assert names["prometheus"]["type"] == "datasource" and names["prometheus"]["query"] == "prometheus"


def test_prometheus_targets_say_range_or_instant():
    """Grafana fills in range=true only for a single-datasource panel; a mixed panel's target
    without it is never sent."""
    for uid, p, t in _targets():
        if t["datasource"]["type"] == "prometheus":
            assert t["range"] is (not t["instant"]), (uid, p["title"])


# --- Tempo: what Grafana 11.6 can ask Tempo 3.1 ---------------------------------------------------

# Grafana 11.6.16 pkg/tsdb/tempo/traceql_query.go, isMetricsQuery.
GRAFANA_11_6_METRICS = re.compile(
    r"\|\s*(rate|count_over_time|avg_over_time|max_over_time|min_over_time|quantile_over_time|histogram_over_time|compare)\s*\(")


def test_tempo_metrics_queries_are_instant_and_known_to_grafana():
    """Grafana 11.6 cannot read Tempo 3.1's range responses (unknown field "step") and sends
    sum_over_time nowhere: such panels error or stay empty."""
    for uid, p, t in _targets():
        if t["datasource"]["type"] != "tempo" or "metricsQueryType" not in t:
            continue
        assert t["metricsQueryType"] == "instant", (uid, p["title"])
        assert GRAFANA_11_6_METRICS.search(t["query"]), (uid, p["title"], t["query"])
    assert not any("sum_over_time" in _query(t) for _, _, t in _targets() if t["datasource"]["type"] == "tempo")


def test_tool_counts_skip_the_merged_wrapper():
    """kagent wraps parallel tool calls in an execute_tool span named "(merged tools)"; counting it
    turned 9 tool calls into 11."""
    for uid, p, t in _targets():
        if "execute_tool" in _query(t):
            assert '!= "(merged tools)"' in _query(t), (uid, p["title"])
    stm = {m["name"]: m for m in _collector_config()["connectors"]["signal_to_metrics"]["spans"]}
    assert '!= "(merged tools)"' in stm["agent.tool.calls"]["conditions"][0]


def test_span_search_limits_fit_tempo():
    """Tempo refuses more than 100 spans per span set."""
    for uid, p, t in _targets():
        if t["datasource"]["type"] == "tempo" and "spss" in t:
            assert t["spss"] <= 100, (uid, p["title"])


# --- the collector's counters ---------------------------------------------------------------------

def _collector_metric_names():
    names = set()
    for m in _collector_config()["connectors"]["signal_to_metrics"]["spans"]:
        base = m["name"].replace(".", "_")
        if "sum" in m:
            names.add(base + "_total")
        else:
            names.add(base + ("_seconds" if m.get("unit") == "s" else ""))
    return names


def test_counters_the_dashboards_read_exist_in_the_collector():
    known = _collector_metric_names()
    used = {m for _, _, t in _targets() for m in re.findall(r"\bagent_[a-z_]+", _query(t))}
    used |= {m for d in _dashboards().values() for v in d["templating"]["list"]
             for m in re.findall(r"\bagent_[a-z_]+", json.dumps(v.get("query", "")))}
    assert used and used <= known, used - known


# Counters of rare events: most of their series are new inside any window (a new agent or agent
# version, tool, model or pod), and increase() never counts a series' first value. Live on
# 2026-10-22 it read 0 sandbox crashes in 7 days for 2, 4 sessions started in 24 hours for 16
# and 2 kagent-tools calls for 6. Istio's request counters are busy and long-lived: increase()
# was within 1% there.
RARE_COUNTERS = (r"agent_[a-z_]+|traces_service_graph_[a-z_]+|ate_actor_crashes_total"
                 r"|ate_actor_lifecycle_operation_duration_seconds_count|ate_imagecache_requests_total"
                 r"|kagent_tools_mcp_invocations(?:_failure)?_total")


def test_rare_counters_are_never_read_with_increase_or_rate():
    """window_total() counts a new series' first value (tested through promtool below)."""
    for uid, p, t in _targets():
        q = _query(t)
        for m in re.findall(rf"(?:increase|rate)\(({RARE_COUNTERS})\b", q):
            pytest.fail(f"{uid} / {p['title']}: {m} read with increase/rate")


def test_range_panels_count_forward_so_the_last_minutes_show():
    """Grafana floors a range query's end to the step: a window ending at each step never shows
    the demo's runs of the last few minutes."""
    for uid, p, t in _targets():
        q = _query(t)
        if t["datasource"]["type"] == "prometheus" and t.get("range") and re.search(rf"\b(?:{RARE_COUNTERS})\b", q):
            assert "offset -$__interval" in q, (uid, p["title"])


# --- the landing page's run -----------------------------------------------------------------------

def test_the_landing_page_opens_on_the_newest_run():
    landing = _dashboards()["agent-platform"]
    v = {x["name"]: x for x in landing["templating"]["list"]}
    run = v["trace_id"]
    # Read on load, from an instant query: one sample per run exposed (48 h), not a 7-day range of
    # every run's samples on every refresh of everyone's home page.
    assert run["datasource"] == {"type": "prometheus"} and run["refresh"] == 1 and run["sort"] == 0
    assert "_over_time" not in run["definition"]
    assert "sort_desc(topk(20" in run["definition"] and "agent_run_start_seconds" in run["definition"]
    line = '{service_name="homelab-knowledge", trace_id="3ace5cd912380b069823402928b147e5"} 1792614488.4 1792622769000'
    rx = run["regex"].strip("/").replace("(?<", "(?P<")
    m = re.search(rx, line)
    assert m.group("text") == "homelab-knowledge" and m.group("value") == "3ace5cd912380b069823402928b147e5"
    assert v["session"]["hide"] == 2 and "$trace_id" in v["session"]["definition"]
    for p in landing["panels"]:
        if p["type"] in ("traces", "logs"):
            assert p["fieldConfig"]["defaults"]["noValue"] == gen.NO_RUN, p["title"]


SESSION_LINE = ('{"labels":{"ate.actor.name":"session-01a16026-4ed5-7a36-b89e-b7f00405e755",'
                '"ate.template.name":"homelab-knowledge-b7f3c951819e"},"message":"Actor checkpointed"}')
CONTROLLER_LINE = ('{"msg":"rpc completed","grpc_method":"/kagent.runtime.v1.TaskStoreService/GetTask",'
                   '"trace_id":"3ace5cd912380b069823402928b147e5"}')
ROUTER_LINE = "request gateway=default/default grpc.status=0 trace.id=3ace5cd912380b069823402928b147e5 span.id=71abe88b"


def _run_regex(trace_id, session):
    return gen.RUN_LOG_REGEX.replace("${trace_id}", trace_id).replace("${session}", session)


def test_run_logs_match_the_run_and_nothing_when_empty():
    """An empty variable must not turn the logs panel into every line of kagent and Substrate."""
    rx = _run_regex("3ace5cd912380b069823402928b147e5", "01a16026-4ed5-7a36-b89e-b7f00405e755")
    for line in (SESSION_LINE, CONTROLLER_LINE, ROUTER_LINE):
        assert re.search(rx, line), line
    empty = _run_regex("", "")
    for line in (SESSION_LINE, CONTROLLER_LINE, ROUTER_LINE):
        assert not re.search(empty, line), line
    other = _run_regex("0" * 32, "")
    assert not re.search(other, CONTROLLER_LINE)
    zero_id = 'request grpc.status=0 trace.id=' + "0" * 32 + ' span.id=0000000000000000'
    assert not re.search(empty, zero_id)


def test_run_logs_check_for_the_ids_before_the_regex():
    """Loki ran the regex on every kagent and Substrate line of the day: 18 s on its half CPU, the
    last run's logs up meanwhile. A substring check first leaves it the few lines with either id
    (3 s, the same lines, §B.41). Tempo's id (zeros stripped) is still a substring of the line's."""
    expr = gen.RUN_LOGS
    pre = '|= "${trace_id}" or "session-${session}"'
    assert expr.index(pre) < expr.index("|~")
    tid, sess = ZERO_LED.lstrip("0"), "01a16026-4ed5-7a36-b89e-b7f00405e755"
    rx = _run_regex(tid, sess)
    for line in (SESSION_LINE.replace("01a16026-4ed5-7a36-b89e-b7f00405e755", sess),
                 '{"msg":"rpc completed","trace_id":"' + ZERO_LED + '"}', "request trace.id=" + ZERO_LED + " span.id=1"):
        assert re.search(rx, line) and (tid in line or "session-" + sess in line), line


# Tempo's search drops a trace id's leading zeros: the k8s-reader run 03468923… is 3468923… in the
# recent-runs table, so a click on it loaded the trace but no logs, and the picker showed a raw id.
ZERO_LED = "03468923ed5e78314b6d90ddb2323617"


def test_a_run_whose_id_starts_with_zero_keeps_its_logs():
    v = {x["name"]: x for x in _dashboards()["agent-platform"]["templating"]["list"]}
    rx = v["trace_id"]["regex"].strip("/").replace("(?<", "(?P<")
    line = '{service_name="k8s-reader", trace_id="' + ZERO_LED + '"} 1792632623.8 1792632700000'
    assert re.search(rx, line).group("value") == ZERO_LED.lstrip("0")
    assert 'trace_id=~"0*$trace_id"' in v["session"]["definition"]
    rx = _run_regex(ZERO_LED.lstrip("0"), "")
    for line in ('{"msg":"rpc completed","trace_id":"' + ZERO_LED + '"}',
                 "request gateway=default/default trace.id=" + ZERO_LED + " span.id=71abe88b"):
        assert re.search(rx, line), line
    assert not re.search(rx, '{"trace_id":"1' + ZERO_LED[1:] + '"}')


def test_a_run_still_in_flight_is_not_called_no_runs():
    """Its root span (the ingress call) ends last, so for a minute the table has no name for it:
    the table's empty text must not fill that cell."""
    (table,) = [p for uid, p in _panels() if p["title"].startswith("Recent agent runs")]
    names = [o for o in table["fieldConfig"]["overrides"] if o["matcher"] == {"id": "byName", "options": "traceName"}]
    assert names and {"id": "noValue", "value": "—"} in names[0]["properties"]


def _run_tiles():
    landing = _dashboards()["agent-platform"]
    (trace,) = [p for p in landing["panels"] if p["type"] == "traces"]
    return trace, [p for p in landing["panels"] if p.get("datasource") == gen.DASHBOARD_DS]


def test_the_run_tiles_change_with_the_trace():
    """The run's tiles read the trace the trace panel loads: one Tempo lookup per picked run, and
    the tiles change with the trace. Each tile's own TraceQL query scanned a day of S3 blocks, four
    at once beside the lookup on a 1-CPU Tempo, so picking a run left the last run's numbers up for
    about 10 s (§B.41)."""
    trace, tiles = _run_tiles()
    assert [t["query"] for t in trace["targets"]] == ["${trace_id}"]
    assert sorted(p["title"] for p in tiles) == ["Agent turn", "Model calls", "This run: tokens and estimated cost",
                                                  "Tool calls"]
    for p in tiles:
        assert p["targets"] == [{"datasource": gen.DASHBOARD_DS, "refId": "A", "panelId": trace["id"],
                                 "withTransforms": False}], p["title"]
    for uid, p, t in _targets():
        assert "trace:id" not in _query(t), (uid, p["title"])


def _span_filter(panel):
    # Grafana filters by display name, and beside the node graph's frames the trace's field is
    # "Trace operationName": the tiles found no span until the frames became one.
    assert panel["transformations"][0] == {"id": "concatenate", "options": {"frameNameMode": "drop"}}
    (f,) = [t for t in panel["transformations"] if t["id"] == "filterByValue"]
    assert f["options"]["type"] == "include"
    (cond,) = f["options"]["filters"]
    assert cond["fieldName"] == "operationName" and cond["config"]["id"] == "regex"
    return re.compile(cond["config"]["options"]["value"])


def test_the_run_tiles_pick_their_spans_by_name():
    """Span names as Tempo's trace view has them; kagent's parallel-call wrapper is
    `execute_tool (merged)` (§B tool counts: 9 tool calls read 11 with it)."""
    tiles = {p["title"]: p for p in _run_tiles()[1]}
    model = "generate_content claude-sonnet-5-5"
    tool, merged, turn = "execute_tool k8s_get_resources", "execute_tool (merged)", "invoke_agent homelab-knowledge"
    for title, yes in (("This run: tokens and estimated cost", [model]), ("Model calls", [model]),
                       ("Tool calls", [tool, "execute_tool get_file_contents"]), ("Agent turn", [turn])):
        rx = _span_filter(tiles[title])
        assert all(rx.search(n) for n in yes), title
        assert not any(rx.search(n) for n in {model, tool, merged, turn} - set(yes)), title
    assert tiles["Model calls"]["options"]["reduceOptions"]["calcs"] == ["count"]
    assert tiles["Tool calls"]["options"]["reduceOptions"]["calcs"] == ["count"]
    assert tiles["Agent turn"]["options"]["reduceOptions"]["calcs"] == ["max"]


def _tags(*pairs):
    # A span's tags as Grafana 11.6.16 hands them to the regex: convertFieldType to string
    # JSON.stringify's the trace frame's [{value, key}] list, value first.
    return json.dumps([{"value": v, "key": k} for k, v in pairs], separators=(",", ":"))


def test_the_run_tokens_come_from_each_model_calls_tags():
    """Sums of the three numbers the collector and the model client put on each model call."""
    tile = next(p for p in _run_tiles()[1] if p["title"] == "This run: tokens and estimated cost")
    (ext,) = [t["options"] for t in tile["transformations"] if t["id"] == "extractFields"]
    assert ext["source"] == "tags" and ext["format"] == "regexp" and ext["replace"] is True
    body = re.fullmatch(r"/(.*)/", ext["regExp"]).group(1)
    assert "(?<=" not in body and "(?<!" not in body
    rx = re.compile(body.replace("(?<", "(?P<"))
    call = _tags(("gen_ai.operation.name", "generate_content"), ("gen_ai.usage.cache_read.input_tokens", 999),
                 ("gen_ai.usage.input_tokens", 10034), ("gen_ai.usage.output_tokens", 317),
                 ("gen_ai.usage.reasoning.output_tokens", 0), ("agent.cost.estimate_usd", 0.023238))
    assert rx.search(call).groupdict() == {"input_tokens": "10034", "output_tokens": "317", "cost_usd": "0.023238"}
    # No price for the model: no cost, the tokens still count. A number sent as text still reads.
    assert rx.search(_tags(("gen_ai.usage.input_tokens", "52"), ("gen_ai.usage.output_tokens", 7))).groupdict() == {
        "input_tokens": "52", "output_tokens": "7", "cost_usd": None}
    names = [t for t in tile["transformations"] if t["id"] == "convertFieldType"]
    assert names[0]["options"]["conversions"] == [{"targetField": "tags", "destinationType": "string"}]
    assert {c["targetField"] for c in names[1]["options"]["conversions"]} == set(rx.groupindex)
    assert tile["options"]["reduceOptions"]["calcs"] == ["sum"]


# --- rare events ----------------------------------------------------------------------------------

HELD_LINES = [  # (agent, held) oldest first: homelab-knowledge was held, then released
    ("homelab-knowledge", "true"), ("homelab-knowledge", "false"), ("istio-agent", "false")]


def test_held_now_takes_each_agents_latest_score():
    """| json made every field a label, so a per-line max said 4 agents were held when the gate held
    none: the query must group by agent before taking the last value."""
    assert "unwrap held_n [30d]) by (agent)" in gen.HELD_NOW
    assert "max by (agent)" not in gen.HELD_NOW


def test_rare_event_numbers_show_zero_not_no_data():
    for uid, p, t in _targets():
        q = _query(t)
        if t["datasource"]["type"] == "loki" and t.get("queryType") == "instant" and q.startswith("sum("):
            assert q.rstrip().endswith("or vector(0)"), (uid, p["title"])


def test_cost_prices_on_the_dashboard_match_the_collector():
    block, = _collector_config()["processors"]["transform/genai-cost"]["trace_statements"]
    prices = {}
    for s in block["statements"]:
        m = re.search(r'\* ([\d.]+) \+ cache\["cache_write"\] \* ([\d.]+) \+ cache\["cache_read"\] \* ([\d.]+) '
                      r'\+ cache\["output"\] \* ([\d.]+)\) / 1000000\.0\) where [^ ]+ == "([^"]+)"$', s)
        if m:
            prices[m.group(5)] = [float(x) for x in m.group(1, 2, 3, 4)]
    note = next(p for _, p in _panels() if p["type"] == "text" and "cost" in p["title"])["options"]["content"]
    shown = {}
    for row in re.findall(r"^\| (claude-[a-z0-9-]+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \|$", note, re.M):
        shown[row[0]] = [float(x) for x in row[1:]]
    assert prices and shown == prices
    assert "Haiku over 100K-token prompts: 0.5 / 0.625 / 0.05 / 2.5." in note


def test_log_panels_mask_email_addresses():
    """Substrate's router logs the user's email with each run; the dispatcher logs an approver's."""
    logs = [(uid, p) for uid, p in _panels() if p["type"] == "logs" and uid in ("agent-platform", "agent-alerts")]
    assert logs
    for uid, p in logs:
        assert gen.MASK_EMAILS in p["targets"][0]["expr"], (uid, p["title"])
    rx = re.search(r'regexReplaceAll "([^"]+)"', gen.MASK_EMAILS).group(1)
    line = 'route=default/route0 x-user-id=someone@example.com approved_by="ops.person+x@corp.co.uk"'
    masked = re.sub(rx, "<email>", line)
    assert "@" not in masked and masked.count("<email>") == 2
    assert re.sub(rx, "<email>", "golang.org/x/net v2@v2.5.0") == "golang.org/x/net v2@v2.5.0"


# --- window_total through promtool (Prometheus 3.0.1, the deployed version) -----------------------

def _values(points, n=60):
    out = ["_"] * n
    for (a, b), v in points.items():
        for m in range(a, b + 1):
            out[m] = str(v)
    return " ".join(out)


def _promtool_cases():
    m = "agent_tokens_input_total"
    series = [
        # one collector pod: runs at minute 10 (100 tokens) and 40 (+50), exposed in between
        {"series": f'{m}{{service_name="a", pod="p1"}}', "values": _values({(10, 39): 100, (40, 49): 150})},
        # the collector restarted at minute 50: the same agent's series starts again under a new pod
        {"series": f'{m}{{service_name="a", pod="p2"}}', "values": _values({(50, 59): 30})},
        # another agent, alive before every window, one run at minute 20 (+7)
        {"series": f'{m}{{service_name="b", pod="p1"}}', "values": _values({(0, 19): 5, (20, 49): 12})},
    ]

    def case(window, at, want, forward=False):
        return {"expr": gen.window_total(m, "service_name", window, forward=forward), "eval_time": at,
                "exp_samples": [{"labels": f'{{service_name="{k}"}}', "value": v} for k, v in want.items()]}

    tests = [
        case("60m", "59m", {"a": 180, "b": 12}),   # everything: 150 + 30 (both new series), b from its start
        case("30m", "59m", {"a": 80, "b": 0}),      # (29m, 59m]: a 150-100 + 30; b grew nothing
        case("15m", "59m", {"a": 80, "b": 0}),      # (44m, 59m]: p1 still exposed (150-150=0... ) see below
        case("40m", "40m", {"a": 150, "b": 7}),     # (0m, 40m]: a new (150); b 12-5
        case("10m", "35m", {"a": 50, "b": 0}, forward=True),  # (35m, 45m]: a 150-100
        case("10m", "15m", {"a": 0, "b": 7}, forward=True),   # (15m, 25m]: b 12-5
    ]
    # (44m, 59m]: p1's last is 150 and its value before the window is 150 (0); p2 is new (30).
    tests[2]["exp_samples"][0]["value"] = 30
    # The collector container restarted in place (same pod): the series falls to 0 and grows to 30.
    reset = [{"series": f'{m}{{service_name="c", pod="p1"}}', "values": _values({(0, 29): 100, (35, 59): 30})}]
    reset_tests = [
        {"expr": gen.window_total(m, "service_name", "30m"), "eval_time": "59m",
         "exp_samples": [{"labels": '{service_name="c"}', "value": 30}]},
        {"expr": gen.window_total(m, "service_name", "10m", forward=True), "eval_time": "29m",
         "exp_samples": [{"labels": '{service_name="c"}', "value": 30}]},
    ]
    return {"rule_files": [], "evaluation_interval": "1m",
            "tests": [{"interval": "1m", "input_series": series, "promql_expr_test": tests},
                      {"interval": "1m", "input_series": reset, "promql_expr_test": reset_tests}]}


def _prometheus_image():
    ss = next(d for d in yaml.safe_load_all((REPO / "base-apps/logging/prometheus-statefulset.yaml").read_text())
              if d and d["kind"] == "StatefulSet")
    return ss["spec"]["template"]["spec"]["containers"][0]["image"]


def test_window_total_counts_new_series_gaps_and_restarts_exactly(tmp_path):
    cases = tmp_path / "window_total.yaml"
    cases.write_text(yaml.safe_dump(_promtool_cases(), sort_keys=False))
    if shutil.which("promtool"):
        cmd = ["promtool", "test", "rules", str(cases)]
    elif shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
        name = f"promtool-{os.getpid()}"
        subprocess.run(["docker", "create", "--name", name, "--entrypoint", "promtool", _prometheus_image(),
                        "test", "rules", "/cases.yaml"], check=True, capture_output=True)
        subprocess.run(["docker", "cp", str(cases), f"{name}:/cases.yaml"], check=True, capture_output=True)
        cmd = ["docker", "start", "-a", name]
    else:
        if os.environ.get("CI"):
            pytest.fail("promtool or Docker is required in CI")
        pytest.skip("needs promtool or Docker")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    finally:
        if cmd[0] == "docker":
            subprocess.run(["docker", "rm", "-f", cmd[-1]], capture_output=True)
    assert r.returncode == 0, r.stdout + r.stderr
