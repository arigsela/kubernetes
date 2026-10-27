"""scripts/gen-bug-investigator-core.py: the bug investigator's core ConfigMap and every consumer's
checksum/core come from one arigsela/claude-agents commit. CI has no checkout at that commit, so
the committed files are checked for consistency, and the generator runs on a small fake source.
(Taskboard's suite: the bug investigator exists to investigate Taskboard, §T.145.)"""
from pathlib import Path
import hashlib
import importlib.util
import json
import subprocess
import sys

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
GEN = REPO / "scripts" / "gen-bug-investigator-core.py"
SHA = "0123456789abcdef0123456789abcdef01234567"

_spec = importlib.util.spec_from_file_location("gen_bug_investigator_core", GEN)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def _checksum(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _committed():
    text = (REPO / gen.OUT).read_text()
    return text, yaml.safe_load(text)


def _pod_annotations(rel):
    docs = [d for d in yaml.safe_load_all((REPO / rel).read_text()) if d]
    out = []
    for d in docs:
        if d["kind"] == "Deployment":
            out.append(d["spec"]["template"]["metadata"]["annotations"])
        elif d["kind"] == "CronJob":
            out.append(d["spec"]["jobTemplate"]["spec"]["template"]["metadata"].get("annotations") or {})
    return out


# --- the committed files ------------------------------------------------------------------------

def test_every_consumer_carries_the_committed_checksum():
    _, cm = _committed()
    want = _checksum(cm["data"])
    for rel in gen.CONSUMERS:
        anns = [a for a in _pod_annotations(rel) if "checksum/core" in a]
        assert anns, f"{rel}: no pod template with checksum/core"
        for a in anns:
            assert a["checksum/core"] == want, f"{rel}: re-run scripts/gen-bug-investigator-core.py"


def test_committed_source_commit_agrees():
    text, cm = _committed()
    commit = cm["metadata"]["annotations"][gen.COMMIT_ANNOTATION]
    assert gen.SHA.match(commit)
    assert cm["metadata"]["annotations"][gen.REPO_ANNOTATION] == "arigsela/claude-agents"
    assert f"arigsela/claude-agents@{commit}" in text, "the header names the commit"


def test_committed_configmap_holds_the_package_and_its_data():
    _, cm = _committed()
    assert cm["metadata"]["name"] == "bug-investigator-core" and cm["metadata"]["namespace"] == "kagent"
    keys = set(cm["data"])
    assert {"__init__.py", "scope.py", "verify.py", "render.py", "prompt.md", "findings.schema.json"} <= keys
    json.loads(cm["data"]["findings.schema.json"])


def test_committed_configmap_regenerates_from_its_own_data(tmp_path):
    """Rebuilding a source tree from the committed data reproduces the file byte for byte, so
    nobody hand-edited it."""
    text, cm = _committed()
    pkg = tmp_path / "bug-investigator" / "src" / "bug_investigator"
    pkg.mkdir(parents=True)
    for key, value in cm["data"].items():
        dest = tmp_path / "bug-investigator" / key if key in ("prompt.md", "findings.schema.json") else pkg / key
        dest.write_text(value)
    assert gen.render(gen.collect(tmp_path), cm["metadata"]["annotations"][gen.COMMIT_ANNOTATION]) == text


def test_the_telemetry_server_mounts_the_package_where_it_imports_from():
    docs = [d for d in yaml.safe_load_all((REPO / "base-apps/kagent/taskboard-telemetry-mcp.yaml").read_text()) if d]
    dep = [d for d in docs if d["kind"] == "Deployment"][0]["spec"]["template"]["spec"]
    vols = {v["name"]: v for v in dep["volumes"]}
    mounts = {m["name"]: m["mountPath"] for m in dep["containers"][0]["volumeMounts"]}
    assert vols["core"]["configMap"]["name"] == "bug-investigator-core"
    assert mounts["core"] == "/core/src/bug_investigator"
    local = yaml.safe_load([d for d in docs if d["kind"] == "ConfigMap"][0]["data"]["local.yaml"])
    stdio = local["binds"][0]["listeners"][0]["routes"][0]["backends"][0]["mcp"]["targets"][0]["stdio"]
    assert stdio["args"][-1] == "/core/src/bug_investigator/mcp_server.py"


# --- the generator on a fake source -------------------------------------------------------------

def _fake(tmp_path, extra=None):
    root = tmp_path / "src"
    pkg = root / "bug-investigator" / "src" / "bug_investigator"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""core"""\n')
    (pkg / "scope.py").write_text("NAMESPACE = 'taskboard'\n")
    (root / "bug-investigator" / "prompt.md").write_text("You are investigating.\n")
    (root / "bug-investigator" / "findings.schema.json").write_text('{"type": "object"}\n')
    for name, body in (extra or {}).items():
        (pkg / name).write_text(body)
    return root


def _repo(tmp_path):
    repo = tmp_path / "repo"
    for rel in gen.CONSUMERS:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text('kind: Deployment\n  checksum/core: "00"\n')
    return repo


def _run(*args):
    return subprocess.run([sys.executable, str(GEN), *args], capture_output=True, text=True)


def test_generator_writes_the_configmap_and_the_consumer_checksums(tmp_path):
    src, repo = _fake(tmp_path), _repo(tmp_path)
    r = _run("--source", str(src), "--commit", SHA, "--repo-root", str(repo))
    assert r.returncode == 0, r.stderr
    cm = yaml.safe_load((repo / gen.OUT).read_text())
    assert set(cm["data"]) == {"__init__.py", "scope.py", "prompt.md", "findings.schema.json"}
    assert cm["metadata"]["annotations"][gen.COMMIT_ANNOTATION] == SHA
    for rel in gen.CONSUMERS:
        assert f'checksum/core: "{_checksum(cm["data"])}"' in (repo / rel).read_text()
    assert _run("--source", str(src), "--commit", SHA, "--repo-root", str(repo), "--check").returncode == 0


def test_check_fails_when_the_source_changed(tmp_path):
    src, repo = _fake(tmp_path), _repo(tmp_path)
    assert _run("--source", str(src), "--commit", SHA, "--repo-root", str(repo)).returncode == 0
    (src / "bug-investigator" / "src" / "bug_investigator" / "scope.py").write_text("NAMESPACE = 'other'\n")
    r = _run("--source", str(src), "--commit", SHA, "--repo-root", str(repo), "--check")
    assert r.returncode == 1 and "stale" in r.stderr


def test_a_short_sha_is_refused(tmp_path):
    r = _run("--source", str(_fake(tmp_path)), "--commit", "4ad231a", "--repo-root", str(_repo(tmp_path)))
    assert r.returncode != 0 and "40-character" in r.stderr


@pytest.mark.parametrize("body,why", [("x = 1 \n", "ends in a space"), ("x = 1\n\ty = 2\n", "has a tab")])
def test_a_file_that_cannot_be_a_block_literal_is_refused(tmp_path, body, why):
    r = _run("--source", str(_fake(tmp_path, {"bad.py": body})), "--commit", SHA,
             "--repo-root", str(_repo(tmp_path)))
    assert r.returncode != 0 and why in r.stderr
