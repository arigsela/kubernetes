# tests/dispatch/test_gen_configmap.py
"""base-apps/dispatcher/code-configmap.yaml is generated from the two scripts; CI fails on drift."""
from pathlib import Path
import subprocess
import sys

import yaml

REPO = Path(__file__).resolve().parents[2]
GEN = REPO / "scripts" / "gen-dispatcher-configmap.py"
OUT = REPO / "base-apps" / "dispatcher" / "code-configmap.yaml"


def test_generated_configmap_is_in_sync():
    result = subprocess.run([sys.executable, str(GEN), "--check", "--repo-root", str(REPO)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_configmap_carries_both_scripts_verbatim():
    doc = yaml.safe_load(OUT.read_text())
    assert doc["metadata"] == {"name": "dispatcher-code", "namespace": "dispatcher", "labels": {"app": "dispatcher"}}
    assert doc["data"]["dispatch.py"] == (REPO / "scripts" / "dispatch.py").read_text()
    assert doc["data"]["kagent_a2a.py"] == (REPO / "scripts" / "kagent_a2a.py").read_text()
