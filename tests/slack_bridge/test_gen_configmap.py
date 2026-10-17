# tests/slack_bridge/test_gen_configmap.py
"""base-apps/slack-bridge/code-configmap.yaml is generated from the two scripts; CI fails on drift."""
from pathlib import Path
import shutil
import subprocess
import sys

import yaml

REPO = Path(__file__).resolve().parents[2]
GEN = REPO / "scripts" / "gen-slack-bridge-configmap.py"
OUT = REPO / "base-apps" / "slack-bridge" / "code-configmap.yaml"


def test_generated_configmap_is_in_sync():
    result = subprocess.run([sys.executable, str(GEN), "--check", "--repo-root", str(REPO)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_configmap_carries_both_scripts_verbatim():
    doc = yaml.safe_load(OUT.read_text())
    assert doc["metadata"] == {"name": "slack-bridge-code", "namespace": "slack-bridge", "labels": {"app": "slack-bridge"}}
    assert doc["data"]["slack_bridge.py"] == (REPO / "scripts" / "slack_bridge.py").read_text()
    assert doc["data"]["kagent_a2a.py"] == (REPO / "scripts" / "kagent_a2a.py").read_text()


def test_check_fails_on_a_drifted_copy(tmp_path):
    for rel in ("scripts/slack_bridge.py", "scripts/kagent_a2a.py", "scripts/eval_release.py", "base-apps/slack-bridge/code-configmap.yaml",
                "base-apps/slack-bridge/deployment.yaml"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, tmp_path / rel)
    (tmp_path / "scripts/slack_bridge.py").write_text("# drifted\n")
    result = subprocess.run([sys.executable, str(GEN), "--check", "--repo-root", str(tmp_path)],
                            capture_output=True, text=True)
    assert result.returncode == 1 and "stale" in result.stderr
