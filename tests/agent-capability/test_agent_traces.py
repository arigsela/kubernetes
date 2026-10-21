"""§V.104: every agent trace goes through the cluster collector (base-apps/tracing/collector.yaml).

kagent's controller and its sessions sent OTLP straight to Coroot, which refuses gzip, so no agent
trace ever reached Coroot or Tempo (§B.37). The collector accepts gzip, keeps every kagent trace
whole, and forwards to Tempo and Coroot (uncompressed for Coroot). The controller/runtime side is
checked on the rendered chart in test_kagent_chart_config.py.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COLLECTOR = "otel-collector.tracing.svc.cluster.local:4317"
COROOT_OTLP = re.compile(r"coroot-coroot\.coroot(\.svc(\.cluster\.local)?)?:4317")


def _values(app: str) -> dict:
    return yaml.safe_load((ROOT / "base-apps" / app).read_text())["spec"]["source"]["helm"]["valuesObject"]


def _collector_config() -> dict:
    cm = next(d for d in yaml.safe_load_all((ROOT / "base-apps/tracing/collector.yaml").read_text())
              if d and d["kind"] == "ConfigMap")
    return yaml.safe_load(cm["data"]["collector.yaml"])


def test_v104_substrate_sends_traces_to_the_collector():
    otel = _values("substrate.yaml")["otel"]
    assert otel["traces"] == {"enabled": True, "endpoint": f"http://{COLLECTOR}"}
    assert otel["metrics"] == {"enabled": False}


def test_v104_collector_keeps_every_kagent_trace():
    """kagent sets service.namespace=kagent on the controller and on every session (kagent
    go/core/pkg/app/app.go, go/core/internal/translator/otel.go @ v1.0.0-alpha14). Tail sampling
    keeps a trace when any span matches, so the MCP adapter and Substrate spans of a run stay too."""
    policies = {p["name"]: p for p in _collector_config()["processors"]["tail_sampling"]["policies"]}
    assert policies["kagent"] == {"name": "kagent", "type": "string_attribute",
                                  "string_attribute": {"key": "service.namespace", "values": ["kagent"]}}


def test_v104_only_the_collector_exports_otlp_to_coroot():
    hits = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "base-apps").rglob("*.yaml")
                  if COROOT_OTLP.search(p.read_text()))
    assert hits == ["base-apps/tracing/collector.yaml"]


# --- §V.105: the collector's agent-platform pipelines (the Agents dashboards) ---------------------

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
import uuid

import pytest

UPDATE_TASK = "kagent.runtime.v1.TaskStoreService/UpdateTask"
# $/MTok: base input, cache write (5 min), cache read, output (Anthropic API pricing, 2026-10-06).
PRICES = {"claude-sonnet-5-5": (2.0, 2.5, 0.2, 10.0), "claude-opus-5-5": (4.0, 5.0, 0.2, 20.0),
          "claude-haiku-5-5": (0.1, 0.125, 0.01, 0.5)}


def _pipelines() -> dict:
    return _collector_config()["service"]["pipelines"]


def test_v105_taskstore_update_spans_never_reach_tempo():
    """kagent writes one UpdateTask call per streamed status update (524 of a 617-span run)."""
    cfg = _collector_config()
    procs = _pipelines()["traces"]["processors"]
    assert procs.index("filter/noise") < procs.index("tail_sampling")
    assert cfg["processors"]["filter/noise"]["trace_conditions"] == [f'span.name == "{UPDATE_TASK}"']


def test_v105_tool_output_is_scrubbed():
    """ADK copies each tool's whole output onto its execute_tool span even with content capture off,
    and can write the whole prompt and response (llm_request, llm_response)."""
    statements = [s for block in _collector_config()["processors"]["transform/scrub"]["trace_statements"]
                  for s in block["statements"]]
    [stmt] = [s for s in statements if "gcp[.]vertex" in s]
    pattern = re.search(r'delete_matching_keys\(attributes, "(.+)"\)', stmt).group(1).replace("$$", "$")
    for key in ("gcp.vertex.agent.tool_response", "gcp.vertex.agent.llm_request", "gcp.vertex.agent.llm_response"):
        assert re.match(pattern, key), key
    assert not re.match(pattern, "gcp.vertex.agent.tool_call_args")


def test_v105_cost_prices_match_the_published_table():
    block, = _collector_config()["processors"]["transform/genai-cost"]["trace_statements"]
    found = {}
    for s in block["statements"]:
        m = re.search(r'\* ([\d.]+) \+ cache\["cache_write"\] \* ([\d.]+) \+ cache\["cache_read"\] \* ([\d.]+)'
                      r' \+ cache\["output"\] \* ([\d.]+)\) / 1000000\.0\) where attributes\["gen_ai\.request\.model"\]'
                      r' == "([^"]+)"$', s)
        if m:
            found[m.group(5)] = tuple(float(x) for x in m.group(1, 2, 3, 4))
    assert found == PRICES
    # Haiku's second rate, for a prompt over 100K tokens, is set last so it wins.
    long = [s for s in block["statements"] if 'cache["input"] > 100000.0' in s]
    assert len(long) == 1 and long[0] == block["statements"][-1]
    assert '* 0.5 + cache["cache_write"] * 0.625 + cache["cache_read"] * 0.05 + cache["output"] * 2.5)' in long[0]
    procs = _pipelines()["traces"]["processors"]
    assert procs.index("transform/genai-cost") < procs.index("tail_sampling")


def test_v105_agent_platform_pipelines_export_only_metrics():
    """They read spans before sampling and must never export a span (beacon §V.42 covers those)."""
    cfg = _collector_config()
    traces = _pipelines()["traces/agent-platform"]
    assert traces["processors"][0] == "memory_limiter"
    assert set(traces["exporters"]) <= set(cfg["connectors"])
    assert _pipelines()["metrics/agent-platform"]["exporters"] == ["prometheus"]


# A running collector, fed one synthetic agent run shaped like the real one (trace 3ace5cd9…).

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _attrs(**kv):
    out = []
    for k, v in kv.items():
        key = k.replace("__", ".")
        val = {"intValue": str(v)} if isinstance(v, int) else {"stringValue": v}
        out.append({"key": key, "value": val})
    return out


def _span(trace, name, kind, parent=None, **attrs):
    now = time.time_ns()
    s = {"traceId": trace, "spanId": uuid.uuid4().hex[:16], "name": name, "kind": kind,
         "startTimeUnixNano": str(now - 2_000_000_000), "endTimeUnixNano": str(now),
         "attributes": _attrs(**attrs)}
    if parent:
        s["parentSpanId"] = parent["spanId"]
    return s


def _resource(attrs, spans):
    return {"resource": {"attributes": _attrs(**attrs)}, "scopeSpans": [{"spans": spans}]}


def _synthetic_run(trace):
    SERVER, CLIENT, INTERNAL = 2, 3, 1
    ingress_srv = _span(trace, "kagent-controller.kagent.svc.cluster.local:8083/*", SERVER,
                        upstream_cluster="outbound|8083||kagent-controller.kagent.svc.cluster.local")
    ingress_cli = _span(trace, "router outbound|8083||kagent-controller.kagent.svc.cluster.local; egress", CLIENT,
                        ingress_srv, upstream_cluster="outbound|8083||kagent-controller.kagent.svc.cluster.local")
    ctl_srv = _span(trace, "POST /agents/", SERVER, ingress_cli)
    ctl_cli = _span(trace, "lf.a2a.v1.A2AService/SendMessage", CLIENT, ctl_srv)
    ctl_update = _span(trace, UPDATE_TASK, SERVER)
    router_srv = _span(trace, "POST /*", SERVER, ctl_cli)
    router_cli = _span(trace, "POST", CLIENT, router_srv)
    agent_srv = _span(trace, "lf.a2a.v1.A2AService/SendMessage", SERVER, router_cli)
    invoke = _span(trace, "invoke_agent demo_agent", INTERNAL, agent_srv,
                   gen_ai__conversation__id="01a16026-4ed5-7a36-b89e-b7f00405e755")
    model1 = _span(trace, "generate_content claude-sonnet-5-5", INTERNAL, invoke,
                   gen_ai__request__model="claude-sonnet-5-5", gen_ai__usage__input_tokens=10000,
                   gen_ai__usage__output_tokens=400, gen_ai__usage__cache_read__input_tokens=8000)
    model2 = _span(trace, "generate_content claude-sonnet-5-5", INTERNAL, invoke,
                   gen_ai__request__model="claude-sonnet-5-5", gen_ai__usage__input_tokens=20000,
                   gen_ai__usage__output_tokens=1000, gen_ai__usage__cache_read__input_tokens=15000,
                   gen_ai__usage__cache_write__input_tokens=4000)
    unknown = _span(trace, "generate_content some-new-model", INTERNAL, invoke,
                    gen_ai__request__model="some-new-model", gen_ai__usage__input_tokens=500,
                    gen_ai__usage__output_tokens=50)
    http = _span(trace, "HTTP POST", CLIENT, model1, server__address="api.anthropic.com")
    tool = _span(trace, "execute_tool k8s_get_resources", INTERNAL, invoke, gen_ai__tool__name="k8s_get_resources",
                 gcp__vertex__agent__tool_response="NAME READY STATUS secret-looking-output",
                 gcp__vertex__agent__llm_request="secret-looking-prompt",
                 gcp__vertex__agent__tool_call_args='{"namespace":"kagent"}')
    update = _span(trace, UPDATE_TASK, CLIENT, invoke, rpc__method=UPDATE_TASK, server__address="10.43.156.252")
    return {"resourceSpans": [
        _resource({"service.name": "main-istio.istio-ingress"}, [ingress_srv, ingress_cli]),
        _resource({"service.name": "kagent-controller", "service.namespace": "kagent"}, [ctl_srv, ctl_cli, ctl_update]),
        _resource({"service.name": "agentgateway"}, [router_srv, router_cli]),
        _resource({"service.name": "demo-agent", "service.namespace": "kagent"},
                  [agent_srv, invoke, model1, model2, unknown, http, tool, update]),
    ]}


@pytest.fixture
def running_collector(tmp_path):
    cfg = _collector_config()
    ports = {k: _free_port() for k in ("grpc", "http", "prom", "health")}
    tmp_path.chmod(0o777)  # the image runs as uid 10001
    binary = os.environ.get("OTELCOL_BIN")
    docker = (not binary and shutil.which("docker")
              and subprocess.run(["docker", "info"], capture_output=True).returncode == 0)
    out = str(tmp_path / "spans.json") if binary else "/work/spans.json"
    cfg["exporters"] = {"file/out": {"path": out}, "prometheus": {"endpoint": f"0.0.0.0:{ports['prom']}"}}
    cfg["service"]["pipelines"]["traces"]["exporters"] = ["file/out"]
    cfg["processors"]["tail_sampling"]["decision_wait"] = "1s"
    cfg["processors"]["batch"]["timeout"] = "200ms"
    cfg["connectors"]["service_graph"]["metrics_flush_interval"] = "1s"
    cfg["connectors"]["service_graph"]["store"]["ttl"] = "3s"
    cfg["receivers"]["otlp"]["protocols"]["grpc"]["endpoint"] = f"0.0.0.0:{ports['grpc']}"
    cfg["receivers"]["otlp"]["protocols"]["http"]["endpoint"] = f"0.0.0.0:{ports['http']}"
    cfg["extensions"]["health_check"]["endpoint"] = f"0.0.0.0:{ports['health']}"
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(cfg))
    if binary:
        proc = subprocess.Popen([binary, f"--config={tmp_path / 'c.yaml'}"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stop = proc.terminate
    elif docker:
        image = next(d for d in yaml.safe_load_all((ROOT / "base-apps/tracing/collector.yaml").read_text())
                     if d and d["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]["image"]
        cid = subprocess.run(["docker", "run", "-d", "--rm", "--network", "host", "-v", f"{tmp_path}:/work",
                              image, "--config=/work/c.yaml"], capture_output=True, text=True, check=True).stdout.strip()
        stop = lambda: subprocess.run(["docker", "rm", "-f", cid], capture_output=True)  # noqa: E731
    else:
        if os.environ.get("CI"):
            pytest.fail("Docker is required in CI and is not available")
        pytest.skip("needs Docker or OTELCOL_BIN")
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{ports['health']}/", timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail("collector never became healthy")
        yield ports, tmp_path / "spans.json"
    finally:
        stop()


def test_v105_running_collector(running_collector):
    ports, out = running_collector
    trace = uuid.uuid4().hex
    req = urllib.request.Request(f"http://127.0.0.1:{ports['http']}/v1/traces",
                                 data=json.dumps(_synthetic_run(trace)).encode(),
                                 headers={"Content-Type": "application/json"})
    assert urllib.request.urlopen(req, timeout=5).status == 200

    def exported():
        spans = []
        if out.exists():
            for line in out.read_text().splitlines():
                for rs in json.loads(line)["resourceSpans"]:
                    for ss in rs["scopeSpans"]:
                        spans += ss["spans"]
        return spans

    def metrics():
        return urllib.request.urlopen(f"http://127.0.0.1:{ports['prom']}/metrics", timeout=5).read().decode()

    deadline = time.time() + 30
    while time.time() < deadline:
        text = metrics()
        if (len(exported()) >= 13 and 'server="api.anthropic.com"' in text and "agent_run_start_seconds" in text
                and "agent_runs_total" in text):
            break
        time.sleep(0.5)
    spans = exported()
    names = [s["name"] for s in spans]
    assert UPDATE_TASK not in names and "invoke_agent demo_agent" in names, names
    assert "secret-looking-output" not in out.read_text() and "secret-looking-prompt" not in out.read_text()
    assert "gcp.vertex.agent.tool_call_args" in out.read_text()
    cost = {}
    for s in spans:
        a = {x["key"]: x["value"] for x in s.get("attributes", [])}
        if s["name"].startswith("generate_content"):
            cost.setdefault(s["name"], []).append(a.get("agent.cost.estimate_usd", {}).get("doubleValue"))
    # 10000 in (8000 cached) + 400 out; 20000 in (15000 read, 4000 written) + 1000 out.
    want = sorted([(2000 * 2.0 + 8000 * 0.2 + 400 * 10.0) / 1e6,
                   (1000 * 2.0 + 4000 * 2.5 + 15000 * 0.2 + 1000 * 10.0) / 1e6])
    assert sorted(cost["generate_content claude-sonnet-5-5"]) == pytest.approx(want)
    assert cost["generate_content some-new-model"] == [None]
    text = metrics()
    run = [l for l in text.splitlines() if l.startswith("agent_run_start_seconds{")]
    assert len(run) == 1 and f'trace_id="{trace}"' in run[0] and 'service_name="demo-agent"' in run[0], run
    assert 'gen_ai_conversation_id="01a16026-4ed5-7a36-b89e-b7f00405e755"' in run[0]
    assert abs(float(run[0].rsplit(" ", 1)[1]) - time.time()) < 60
    edges = {(m.group(1), m.group(2)) for m in re.finditer(
        r'traces_service_graph_request_total\{client="([^"]+)".*?server="([^"]+)"', text)}
    assert {("main-istio.istio-ingress", "kagent-controller"), ("kagent-controller", "agentgateway"),
            ("agentgateway", "demo-agent"), ("demo-agent", "api.anthropic.com")} <= edges, edges
    assert not any(c == "demo-agent" and s == "kagent-controller" for c, s in edges), \
        "UpdateTask calls must stay off the service graph"

    def counter(name, **labels):
        total = 0.0
        for line in text.splitlines():
            if line.startswith(name + "{") and all(f'{k}="{v}"' in line for k, v in labels.items()):
                total += float(line.rsplit(" ", 1)[1])
        return total

    # Each span counted once, by agent and model; the merged tool wrapper and the unknown model's
    # cost are not counted.
    assert counter("agent_tokens_input_total", service_name="demo-agent") == 30500
    assert counter("agent_tokens_input_total", gen_ai_request_model="claude-sonnet-5-5") == 30000
    assert counter("agent_tokens_output_total", service_name="demo-agent") == 1450
    assert counter("agent_tokens_cache_read_total", service_name="demo-agent") == 23000
    assert counter("agent_tokens_cache_write_total", service_name="demo-agent") == 4000
    assert counter("agent_cost_estimate_usd_total", service_name="demo-agent") == pytest.approx(sum(want))
    assert counter("agent_model_calls_total", service_name="demo-agent") == 3
    assert counter("agent_tool_calls_total", service_name="demo-agent", gen_ai_tool_name="k8s_get_resources") == 1
    assert counter("agent_runs_total", service_name="demo-agent") == 1
