# tests/beacon/test_collector.py
"""beacon §V.42 against the real collector: the config in base-apps/tracing/collector.yaml,
run by the pinned image (Docker), or by a local binary named in OTELCOL_BIN.

Its exporters are swapped for a file exporter and the sampling wait is shortened; every
processor runs as deployed. Spans carrying an OAuth callback query, a token query and a
Slack-style webhook path go in; none of the secret parts may come out.

Without Docker or OTELCOL_BIN the test skips, unless BEACON_REQUIRE_DOCKER=1 (CI), where a
skip fails.
"""
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

import pytest
import yaml

from conftest import one

COLLECTOR = "base-apps/tracing/collector.yaml"
CANARY = "s3cr3t"


def collector_image() -> str:
    deploy = one(COLLECTOR, "Deployment", "otel-collector")
    return deploy["spec"]["template"]["spec"]["containers"][0]["image"]


def deployed_config() -> dict:
    return yaml.safe_load(one(COLLECTOR, "ConfigMap", "otel-collector-config")["data"]["collector.yaml"])


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def scrubbing_config(out: str, ports: dict) -> dict:
    cfg = deployed_config()
    cfg["exporters"] = {"file/out": {"path": out}, "prometheus": {"endpoint": f"0.0.0.0:{ports['prom']}"}}
    traces = cfg["service"]["pipelines"]["traces"]
    traces["exporters"] = ["file/out"]
    cfg["processors"]["tail_sampling"]["decision_wait"] = "1s"
    cfg["processors"]["batch"]["timeout"] = "200ms"
    cfg["receivers"]["otlp"]["protocols"]["grpc"]["endpoint"] = f"0.0.0.0:{ports['grpc']}"
    cfg["receivers"]["otlp"]["protocols"]["http"]["endpoint"] = f"0.0.0.0:{ports['http']}"
    cfg["extensions"]["health_check"]["endpoint"] = f"0.0.0.0:{ports['health']}"
    return cfg


def attr(key, value):
    return {"key": key, "value": {"stringValue": value}}


def span(name, kind, attributes, error=False):
    now = time.time_ns()
    return {
        "traceId": uuid.uuid4().hex,
        "spanId": uuid.uuid4().hex[:16],
        "name": name,
        "kind": kind,
        "startTimeUnixNano": str(now - 1_000_000),
        "endTimeUnixNano": str(now),
        "status": {"code": 2 if error else 0},
        "attributes": attributes,
    }


def resource_spans(service, spans):
    return {"resource": {"attributes": [attr("service.name", service)]}, "scopeSpans": [{"spans": spans}]}


PAYLOAD = {"resourceSpans": [
    # The Gateway (Envoy): http.url keeps the query (beacon §R.42). An error, so kept.
    resource_spans("istio-ingress", [span("dex.arigsela.com:443/*", 2, [
        attr("http.url", f"https://dex.arigsela.com/callback?code={CANARY}-code&state={CANARY}-state"),
    ], error=True)]),
    # The notifier's httpx client span: a Slack webhook path is the credential.
    resource_spans("beacon-notifier", [span("POST", 3, [
        attr("url.full", f"https://93.184.215.14/services/T000/B000/{CANARY}-path?x={CANARY}"),
        attr("url.path", f"/services/T000/B000/{CANARY}-path"),
    ])]),
    # A server span with a token in its query.
    resource_spans("beacon-api", [span("GET /public/v1/status/{slug}", 2, [
        attr("http.target", f"/public/v1/status/homelab?token={CANARY}-q"),
        attr("url.query", f"token={CANARY}-q"),
        attr("url.full", f"https://beacon.arigsela.com/public/v1/status/homelab?token={CANARY}-q"),
    ])]),
]}


@pytest.fixture
def collector(tmp_path):
    ports = {k: free_port() for k in ("grpc", "http", "prom", "health")}
    tmp_path.chmod(0o777)  # the image runs as uid 10001
    binary = os.environ.get("OTELCOL_BIN")
    if binary:
        out = tmp_path / "spans.json"
        (tmp_path / "c.yaml").write_text(yaml.safe_dump(scrubbing_config(str(out), ports)))
        proc = subprocess.Popen([binary, f"--config={tmp_path / 'c.yaml'}"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        stop = proc.terminate
    elif shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
        out = tmp_path / "spans.json"
        (tmp_path / "c.yaml").write_text(yaml.safe_dump(scrubbing_config("/work/spans.json", ports)))
        cid = subprocess.run(
            ["docker", "run", "-d", "--rm", "--network", "host", "-v", f"{tmp_path}:/work",
             collector_image(), "--config=/work/c.yaml"],
            capture_output=True, text=True, check=True).stdout.strip()
        stop = lambda: subprocess.run(["docker", "rm", "-f", cid], capture_output=True)  # noqa: E731
    else:
        if os.environ.get("BEACON_REQUIRE_DOCKER") == "1":
            pytest.fail("Docker is required here (BEACON_REQUIRE_DOCKER=1) and is not available")
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
        yield ports, out
    finally:
        stop()


def test_v42_running_collector_scrubs_urls(collector):
    ports, out = collector
    req = urllib.request.Request(f"http://127.0.0.1:{ports['http']}/v1/traces",
                                 data=json.dumps(PAYLOAD).encode(),
                                 headers={"Content-Type": "application/json"})
    assert urllib.request.urlopen(req, timeout=5).status == 200
    deadline = time.time() + 30
    spans = {}
    while time.time() < deadline and len(spans) < 3:
        time.sleep(0.5)
        if out.exists():
            for line in out.read_text().splitlines():
                for rs in json.loads(line)["resourceSpans"]:
                    svc = {a["key"]: a["value"]["stringValue"] for a in rs["resource"]["attributes"]}["service.name"]
                    for ss in rs["scopeSpans"]:
                        for s in ss["spans"]:
                            spans[svc] = {a["key"]: a["value"]["stringValue"] for a in s.get("attributes", [])}
    assert set(spans) == {"istio-ingress", "beacon-notifier", "beacon-api"}, spans
    assert CANARY not in out.read_text(), out.read_text()
    assert spans["istio-ingress"]["http.url"] == "https://dex.arigsela.com/callback"
    assert spans["beacon-notifier"]["url.full"] == "https://93.184.215.14"
    assert "url.path" not in spans["beacon-notifier"]
    assert spans["beacon-api"]["http.target"] == "/public/v1/status/homelab"
    assert spans["beacon-api"]["url.full"] == "https://beacon.arigsela.com/public/v1/status/homelab"
    assert "url.query" not in spans["beacon-api"]


def post(ports, service, n):
    body = {"resourceSpans": [resource_spans(service, [span("tick", 1, []) for _ in range(n)])]}
    req = urllib.request.Request(f"http://127.0.0.1:{ports['http']}/v1/traces", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    assert urllib.request.urlopen(req, timeout=5).status == 200


def span_count(ports, service):
    text = urllib.request.urlopen(f"http://127.0.0.1:{ports['prom']}/metrics", timeout=5).read().decode()
    for line in text.splitlines():
        if line.startswith("tracing_spans_received_total") and f'service_name="{service}"' in line:
            return float(line.rsplit(" ", 1)[1])
    return None


def wait_for(fn, want, seconds=15):
    deadline = time.time() + seconds
    got = None
    while time.time() < deadline:
        got = fn()
        if got == want:
            return got
        time.sleep(0.5)
    return got


def test_v9_span_count_is_cumulative(collector):
    """beacon §B.14: the count connector emits deltas; the Prometheus exporter alone shows only
    the latest one, so a rate or absence alert on it means nothing. 3 spans, then 2: 5."""
    ports, _ = collector
    post(ports, "beacon-notifier", 3)
    assert wait_for(lambda: span_count(ports, "beacon-notifier"), 3.0) == 3.0
    post(ports, "beacon-notifier", 2)
    assert wait_for(lambda: span_count(ports, "beacon-notifier"), 5.0) == 5.0
