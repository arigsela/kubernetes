"""Guards on the generated Cluster Overview dashboard (scripts/gen-cluster-overview-dashboard.py)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
GEN = REPO / "scripts" / "gen-cluster-overview-dashboard.py"
OUT = REPO / "base-apps" / "logging" / "grafana-dashboard-cluster-overview.yaml"


def _dashboard():
    cm = yaml.safe_load(OUT.read_text())
    return json.loads(cm["data"]["cluster-overview.json"])


def _gen(root, *extra):
    return subprocess.run([sys.executable, str(GEN), "--repo-root", str(root), *extra],
                          capture_output=True, text=True)


def test_checked_in_dashboard_matches_generator():
    r = _gen(REPO, "--check")
    assert r.returncode == 0, r.stderr


def test_check_catches_a_hand_edit(tmp_path):
    """The point of --check: editing the JSON instead of the generator must fail CI."""
    dst = tmp_path / OUT.relative_to(REPO)
    dst.parent.mkdir(parents=True)
    shutil.copy(OUT, dst)
    dst.write_text(dst.read_text().replace('"title": "Cluster Overview"', '"title": "Hand edited"'))
    r = _gen(tmp_path, "--check")
    assert r.returncode == 1 and "out of date" in r.stderr


def test_panel_ids_unique_and_layout_fits_grid():
    panels = _dashboard()["panels"]
    ids = [p["id"] for p in panels]
    assert len(ids) == len(set(ids))
    for p in panels:
        g = p["gridPos"]
        assert g["x"] + g["w"] <= 24, f"{p['title']} overflows the 24-column grid"


def test_every_query_uses_the_default_prometheus_datasource():
    """Alert rules pin the Prometheus datasource by uid; dashboards must not, so that a
    datasource uid change cannot silently break them (the Istio dashboard's convention)."""
    for p in _dashboard()["panels"]:
        for t in p.get("targets", []):
            assert t["datasource"] == {"type": "prometheus"}, p["title"]
            assert t["expr"].strip(), f"{p['title']} has an empty query"


def test_configmap_name_is_the_one_grafana_mounts():
    """grafana-deployment.yaml mounts this exact ConfigMap name; renaming it would restart
    Grafana and orphan the dashboard."""
    cm = yaml.safe_load(OUT.read_text())
    assert cm["metadata"]["name"] == "grafana-dashboard-k8s-basic"
    deploy = (REPO / "base-apps" / "logging" / "grafana-deployment.yaml").read_text()
    assert "name: grafana-dashboard-k8s-basic" in deploy


@pytest.mark.parametrize("needle", ["container_pressure_cpu_waiting_seconds_total",
                                    "container_cpu_cfs_throttled_periods_total",
                                    "container_spec_memory_limit_bytes",
                                    "kubernetes_build_info"])
def test_key_signals_are_on_the_dashboard(needle):
    exprs = [t["expr"] for p in _dashboard()["panels"] for t in p.get("targets", [])]
    assert any(needle in e for e in exprs), f"no panel queries {needle}"
