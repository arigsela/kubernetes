"""Guards on the MCP Tool Calls dashboard (base-apps/logging/grafana-dashboard-mcp.yaml, §T.134)."""
import json
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
LOGGING = REPO / "base-apps" / "logging"


def _cm(path):
    return next(d for d in yaml.safe_load_all(path.read_text()) if d and d["kind"] == "ConfigMap")


def _dashboard():
    cm = _cm(LOGGING / "grafana-dashboard-mcp.yaml")
    assert cm["metadata"]["labels"]["grafana_dashboard"] == "1"
    (body,) = cm["data"].values()
    return json.loads(body)


def test_panel_ids_unique_and_queries_scoped_to_the_adapters():
    panels = _dashboard()["panels"]
    ids = [p["id"] for p in panels]
    assert len(ids) == len(set(ids))
    for p in panels:
        for t in p.get("targets", []):
            assert 'namespace="kagent"' in t["expr"], (p["title"], t["expr"])


def test_prometheus_uses_the_default_datasource_and_loki_its_pinned_uid():
    """Same split as the other dashboards: Prometheus unpinned (a uid change cannot break the
    panels), Loki by the uid that grafana-deployment.yaml keeps stable."""
    ds = yaml.safe_load(_cm(LOGGING / "grafana-deployment.yaml")["data"]["datasources.yaml"])
    loki_uids = {d["uid"] for d in ds["datasources"] if d["type"] == "loki"}
    for p in _dashboard()["panels"]:
        src = p.get("datasource")
        if src is None:  # row
            continue
        if src["type"] == "prometheus":
            assert src == {"type": "prometheus"}, p["title"]
        else:
            assert src["type"] == "loki" and src["uid"] in loki_uids, p["title"]


def test_grafana_provides_and_mounts_it():
    provider = yaml.safe_load(_cm(LOGGING / "grafana-dashboard-configmap.yaml")["data"]["dashboards.yaml"])
    path = next(p["options"]["path"] for p in provider["providers"] if p["name"] == "agents")
    dep = next(d for d in yaml.safe_load_all((LOGGING / "grafana-deployment.yaml").read_text())
               if d and d["kind"] == "Deployment")
    pod = dep["spec"]["template"]["spec"]
    vol = next(v["name"] for v in pod["volumes"] if v.get("configMap", {}).get("name") == "grafana-dashboard-mcp")
    mounts = {m["name"]: m["mountPath"] for c in pod["containers"] for m in c.get("volumeMounts", [])}
    assert mounts[vol] == path
