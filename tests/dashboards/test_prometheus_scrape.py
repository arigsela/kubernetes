"""§V.103: the kubernetes-pods scrape job (base-apps/logging/prometheus-config.yaml) makes no target
from an init container, so an annotated pod with one regular container has exactly one target.

Pod discovery yields one target per container, init containers included, and the job points every
one of them at the pod-level prometheus.io/port. Without a rule against it, a pod with an init
container is scraped twice under two `container` labels and every sum over its series doubles (the
MCP adapters' copy-binary and Crossplane's crossplane-init did, §B.36).

The job's relabel rules are replayed here on discovered labels shaped like Prometheus 3's pod role.
"""
import re
from pathlib import Path

import pytest
import yaml

CONFIG = Path(__file__).resolve().parents[2] / "base-apps" / "logging" / "prometheus-config.yaml"


def _job():
    cfg = yaml.safe_load(yaml.safe_load(CONFIG.read_text())["data"]["prometheus.yml"])
    return next(j for j in cfg["scrape_configs"] if j["job_name"] == "kubernetes-pods")


def _relabel(labels, rules):
    """Prometheus relabelling for the actions this job uses; None when the target is dropped."""
    labels = dict(labels)
    for r in rules:
        value = r.get("separator", ";").join(labels.get(s, "") for s in r.get("source_labels", []))
        regex = r.get("regex", "(.*)")
        if isinstance(regex, bool):  # `regex: true` is a YAML bool; Prometheus reads the string
            regex = str(regex).lower()
        m = re.fullmatch(str(regex), value)
        action = r.get("action", "replace")
        if action == "keep" and not m or action == "drop" and m:
            return None
        if action == "replace":
            if m:
                labels[r["target_label"]] = re.sub(r"\$\{?(\d+)\}?", lambda g: m.group(int(g[1])) or "",
                                                   r.get("replacement", "$1"))
        elif action not in ("keep", "drop"):
            pytest.fail(f"_relabel does not model action {action!r}; extend it before relying on this test")
    return {k: v for k, v in labels.items() if not k.startswith("__") or k == "__address__"}


def _targets(*containers):
    """Discovered targets for one annotated pod: (name, init, port or None) per container."""
    pod = {"__meta_kubernetes_namespace": "kagent", "__meta_kubernetes_pod_name": "agent-docs-mcp-abc12-xyz34",
           "__meta_kubernetes_pod_annotation_prometheus_io_scrape": "true",
           "__meta_kubernetes_pod_annotation_prometheus_io_port": "15020"}
    out = []
    for name, init, port in containers:
        t = dict(pod, __address__="10.42.0.10" + (f":{port}" if port else ""),
                 __meta_kubernetes_pod_container_name=name,
                 __meta_kubernetes_pod_container_init="true" if init else "false")
        out.append(t)
    rules = _job()["relabel_configs"]
    return [t for t in (_relabel(d, rules) for d in out) if t is not None]


def test_v103_a_pod_with_an_init_container_is_scraped_once_under_its_main_container():
    kept = _targets(("copy-binary", True, None), ("mcp-server", False, 3000))
    assert [(t["__address__"], t["container"]) for t in kept] == [("10.42.0.10:15020", "mcp-server")]


def test_v103_a_pod_without_an_init_container_keeps_its_target():
    kept = _targets(("mcp-server", False, 3000))
    assert [(t["__address__"], t["container"], t["namespace"]) for t in kept] == [
        ("10.42.0.10:15020", "mcp-server", "kagent")]
