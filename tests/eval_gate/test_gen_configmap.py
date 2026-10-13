# tests/eval_gate/test_gen_configmap.py
import importlib.util
import json
import subprocess
import sys
import yaml
from conftest import REPO

GEN = REPO / "scripts" / "gen-eval-gate-configmap.py"


def _gen():
    spec = importlib.util.spec_from_file_location("gen_eval_gate", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_render_bundles_the_scripts_and_the_corpus_as_json():
    cm = yaml.safe_load(_gen().render(REPO))
    assert cm["kind"] == "ConfigMap" and cm["metadata"]["name"] == "eval-gate-code"
    keys = set(cm["data"])
    assert {"eval_gate.py", "kagent_a2a.py", "score_eval.py", "capture_eval_answers.py"} <= keys
    corpus = json.loads(cm["data"]["corpus__homelab-knowledge.json"])
    assert corpus["agent"] == "homelab-knowledge" and corpus["entries"]
    assert cm["data"]["score_eval.py"] == (REPO / "scripts/score-eval.py").read_text()


def test_check_passes_on_the_committed_output():
    rc = subprocess.run([sys.executable, str(GEN), "--repo-root", str(REPO), "--check"]).returncode
    assert rc == 0, "run scripts/gen-eval-gate-configmap.py --repo-root ."
