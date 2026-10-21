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
