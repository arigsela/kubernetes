# tests/alert_demo/test_gen_configmap.py
"""base-apps/alert-demo/code-configmap.yaml is generated from scripts/alert_demo.py; CI fails on drift."""
from pathlib import Path
import subprocess
import sys

import yaml

REPO = Path(__file__).resolve().parents[2]
GEN = REPO / "scripts" / "gen-alert-demo-configmap.py"
OUT = REPO / "base-apps" / "alert-demo" / "code-configmap.yaml"


def test_generated_configmap_is_in_sync():
    result = subprocess.run([sys.executable, str(GEN), "--check", "--repo-root", str(REPO)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_configmap_carries_the_script_verbatim():
    doc = yaml.safe_load(OUT.read_text())
    assert doc["metadata"] == {"name": "alert-demo-code", "namespace": "alert-demo", "labels": {"app": "alert-demo"}}
    assert doc["data"]["alert_demo.py"] == (REPO / "scripts" / "alert_demo.py").read_text()
