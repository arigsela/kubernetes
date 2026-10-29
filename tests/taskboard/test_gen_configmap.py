"""scripts/gen-taskboard-configmap.py: the code ConfigMap and both checksums come from one
arigsela/taskboard commit (§T.145), and so do the taskboard-browser tool server's copy of that code
and its checksum (§T.153). CI cannot read that private repo, so the committed files are checked
for consistency, and the generator itself runs on a small fake checkout."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
GEN = REPO / "scripts" / "gen-taskboard-configmap.py"
APP = REPO / "base-apps" / "taskboard"
BROWSER_CM = REPO / "base-apps" / "kagent" / "taskboard-browser-code-configmap.yaml"
BROWSER_DEP = REPO / "base-apps" / "kagent" / "taskboard-browser-mcp.yaml"
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"

_spec = importlib.util.spec_from_file_location("gen_taskboard_configmap", GEN)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def _checksum(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _deployments(text):
    return {d["metadata"]["name"]: d for d in yaml.safe_load_all(text) if d and d["kind"] == "Deployment"}


def _run(*args):
    return subprocess.run([sys.executable, str(GEN), *args], capture_output=True, text=True)


# --- the committed pair -------------------------------------------------------------------------

def test_committed_checksums_match_the_committed_configmap():
    cm = yaml.safe_load((APP / "code-configmap.yaml").read_text())
    want = _checksum(cm["data"])
    deps = _deployments((APP / "deployment.yaml").read_text())
    assert set(deps) == {"taskboard", "taskboard-traffic"}
    for name, dep in deps.items():
        assert dep["spec"]["template"]["metadata"]["annotations"]["checksum/code"] == want, name


def test_committed_source_commit_agrees():
    text = (APP / "code-configmap.yaml").read_text()
    cm = yaml.safe_load(text)
    commit = cm["metadata"]["annotations"][gen.COMMIT_ANNOTATION]
    assert gen.SHA.match(commit)
    assert cm["metadata"]["annotations"][gen.REPO_ANNOTATION] == "arigsela/taskboard"
    assert f"arigsela/taskboard@{commit}" in text, "the header names the commit"
    app = _deployments((APP / "deployment.yaml").read_text())["taskboard"]
    assert app["spec"]["template"]["metadata"]["annotations"][gen.COMMIT_ANNOTATION] == commit


def test_committed_configmap_has_the_entry_points():
    cm = yaml.safe_load((APP / "code-configmap.yaml").read_text())
    assert {"server.py", "simulate.py"} <= set(cm["data"])
    assert cm["metadata"]["name"] == "taskboard-code" and cm["metadata"]["namespace"] == "taskboard"


def test_browser_copy_matches_app_configmap():
    """The browser replays the code that is live: the app ConfigMap's files, written in the same
    commit, plus .source-commit = the deployed commit (the server reports it with every result)."""
    app = yaml.safe_load((APP / "code-configmap.yaml").read_text())
    text = BROWSER_CM.read_text()
    browser = yaml.safe_load(text)
    assert browser["metadata"]["name"] == "taskboard-browser-code"
    assert browser["metadata"]["namespace"] == "kagent"
    assert browser["metadata"]["annotations"] == app["metadata"]["annotations"]
    commit = app["metadata"]["annotations"][gen.COMMIT_ANNOTATION]
    data = dict(browser["data"])
    assert data.pop(".source-commit") == commit
    assert data == app["data"]
    assert f"arigsela/taskboard@{commit}" in text, "the header names the commit"


def test_browser_deployment_checksum_code():
    browser = yaml.safe_load(BROWSER_CM.read_text())
    deps = _deployments(BROWSER_DEP.read_text())
    assert set(deps) == {"taskboard-browser-mcp"}
    ann = deps["taskboard-browser-mcp"]["spec"]["template"]["metadata"]["annotations"]
    assert ann["checksum/code"] == _checksum(browser["data"])
    assert ann[gen.COMMIT_ANNOTATION] == browser["data"][".source-commit"]


def test_committed_configmap_regenerates_from_its_own_data(tmp_path):
    """Rebuilding a checkout from the committed data reproduces all four files byte for byte, so
    nobody hand-edited them."""
    text = (APP / "code-configmap.yaml").read_text()
    cm = yaml.safe_load(text)
    src = tmp_path / "src"
    (src / "taskboard").mkdir(parents=True)
    (src / "traffic").mkdir()
    for key, value in cm["data"].items():
        dest = src / "traffic" / key if key == "simulate.py" else src / "taskboard" / key
        dest.write_text(value)
    commit = cm["metadata"]["annotations"][gen.COMMIT_ANNOTATION]
    rendered = gen.render(gen.collect(src), commit)
    assert rendered == text
    deployment = (APP / "deployment.yaml").read_text()
    assert gen.render_deployment(deployment, rendered, commit) == deployment
    browser = gen.render_browser(gen.collect(src), commit)
    assert browser == BROWSER_CM.read_text()
    browser_dep = BROWSER_DEP.read_text()
    assert gen.render_browser_deployment(browser_dep, browser, commit) == browser_dep


# --- the generator on a fake checkout -----------------------------------------------------------

@pytest.fixture
def fake(tmp_path):
    repo = tmp_path / "repo"
    (repo / "base-apps" / "taskboard").mkdir(parents=True)
    shutil.copy(APP / "deployment.yaml", repo / gen.DEPLOYMENT)
    (repo / "base-apps" / "kagent").mkdir(parents=True)
    shutil.copy(BROWSER_DEP, repo / gen.BROWSER_DEPLOYMENT)
    src = tmp_path / "taskboard"
    (src / "taskboard").mkdir(parents=True)
    (src / "traffic").mkdir()
    (src / "taskboard" / "server.py").write_text("import store\n\nprint('hi')\n")
    (src / "taskboard" / "store.py").write_text("  # leading spaces\nX = 1")
    (src / "taskboard" / "index.html").write_text("<!doctype html>\n<p>Taskboard — tasks</p>\n\n\n")
    (src / "taskboard" / "__init__.py").write_text("")
    (src / "taskboard" / "__pycache__").mkdir()
    (src / "taskboard" / "__pycache__" / "server.cpython-312.pyc").write_bytes(b"\x00")
    (src / "taskboard" / ".DS_Store").write_bytes(b"\x00\x01")
    (src / "traffic" / "simulate.py").write_text("print('traffic')\n")
    return repo, src


def _gen(repo, src, commit=SHA, *extra):
    return _run("--repo-root", str(repo), "--source", str(src), "--commit", commit, *extra)


def test_round_trip(fake):
    repo, src = fake
    result = _gen(repo, src)
    assert result.returncode == 0, result.stderr
    out = (repo / gen.OUT).read_text()
    cm = yaml.safe_load(out)
    assert cm["data"] == {
        "__init__.py": "",
        "index.html": "<!doctype html>\n<p>Taskboard — tasks</p>\n\n\n",
        "server.py": "import store\n\nprint('hi')\n",
        "simulate.py": "print('traffic')\n",
        "store.py": "  # leading spaces\nX = 1",
    }, "every file verbatim, litter skipped"
    assert cm["metadata"]["annotations"] == {gen.COMMIT_ANNOTATION: SHA, gen.REPO_ANNOTATION: "arigsela/taskboard"}
    for key in ("server.py", "store.py", "index.html", "simulate.py"):
        assert any(line.startswith(f"  {key}: |") for line in out.splitlines()), f"{key} is a block literal"
    assert all(line == line.rstrip() for line in out.splitlines()), "no trailing whitespace"
    assert f"arigsela/taskboard@{SHA}" in out

    text = (repo / gen.DEPLOYMENT).read_text()
    deps = _deployments(text)
    for dep in deps.values():
        assert dep["spec"]["template"]["metadata"]["annotations"]["checksum/code"] == _checksum(cm["data"])
    assert deps["taskboard"]["spec"]["template"]["metadata"]["annotations"][gen.COMMIT_ANNOTATION] == SHA
    assert gen.COMMIT_ANNOTATION not in deps["taskboard-traffic"]["spec"]["template"]["metadata"]["annotations"]
    # Edited in place: the comments survive and only the annotation lines changed.
    original = (APP / "deployment.yaml").read_text().splitlines()
    changed = [(a, b) for a, b in zip(original, text.splitlines()) if a != b]
    assert len(text.splitlines()) == len(original)
    assert all("checksum/code:" in a or "source-commit:" in a for a, _ in changed)
    assert "# No livenessProbe" in text

    # The browser's copy: the same files plus .source-commit, in kagent; its Deployment edited in place.
    browser_out = (repo / gen.BROWSER_OUT).read_text()
    browser = yaml.safe_load(browser_out)
    assert browser["data"] == {**cm["data"], ".source-commit": SHA}
    assert (browser["metadata"]["name"], browser["metadata"]["namespace"]) == ("taskboard-browser-code", "kagent")
    assert browser["metadata"]["annotations"] == cm["metadata"]["annotations"]
    assert all(line == line.rstrip() for line in browser_out.splitlines()), "no trailing whitespace"
    assert f"arigsela/taskboard@{SHA}" in browser_out
    browser_dep = (repo / gen.BROWSER_DEPLOYMENT).read_text()
    ann = _deployments(browser_dep)["taskboard-browser-mcp"]["spec"]["template"]["metadata"]["annotations"]
    assert ann["checksum/code"] == _checksum(browser["data"]) and ann[gen.COMMIT_ANNOTATION] == SHA
    original = BROWSER_DEP.read_text().splitlines()
    changed = [a for a, b in zip(original, browser_dep.splitlines()) if a != b]
    assert len(browser_dep.splitlines()) == len(original)
    assert all("checksum/code:" in a or "source-commit:" in a for a in changed)

    assert _gen(repo, src, SHA, "--check").returncode == 0


def test_check_reports_a_code_change(fake):
    repo, src = fake
    assert _gen(repo, src).returncode == 0
    (src / "taskboard" / "server.py").write_text("print('changed')\n")
    result = _gen(repo, src, SHA, "--check")
    assert result.returncode == 1
    assert "stale" in result.stderr
    assert "changed" in result.stdout, "the diff is shown"


def test_a_new_commit_changes_the_annotation_and_keeps_the_checksum(fake):
    repo, src = fake
    assert _gen(repo, src).returncode == 0
    before = _deployments((repo / gen.DEPLOYMENT).read_text())
    browser_before = _deployments((repo / gen.BROWSER_DEPLOYMENT).read_text())
    assert _gen(repo, src, OTHER_SHA, "--check").returncode == 1
    assert _gen(repo, src, OTHER_SHA).returncode == 0
    after = _deployments((repo / gen.DEPLOYMENT).read_text())
    meta = lambda d, n: d[n]["spec"]["template"]["metadata"]["annotations"]  # noqa: E731
    assert meta(after, "taskboard")[gen.COMMIT_ANNOTATION] == OTHER_SHA
    assert meta(after, "taskboard")["checksum/code"] == meta(before, "taskboard")["checksum/code"]
    # The browser's copy carries .source-commit, so a commit-only deploy rolls the browser too.
    browser = yaml.safe_load((repo / gen.BROWSER_OUT).read_text())
    assert browser["data"][".source-commit"] == OTHER_SHA
    after = _deployments((repo / gen.BROWSER_DEPLOYMENT).read_text())
    assert meta(after, "taskboard-browser-mcp")[gen.COMMIT_ANNOTATION] == OTHER_SHA
    assert meta(after, "taskboard-browser-mcp")["checksum/code"] == _checksum(browser["data"])
    assert meta(after, "taskboard-browser-mcp")["checksum/code"] != meta(browser_before, "taskboard-browser-mcp")["checksum/code"]


@pytest.mark.parametrize("breakage, message", [
    (lambda src: (src / "taskboard" / "static").mkdir(), "must be flat"),
    (lambda src: (src / "taskboard" / "big.js").write_text("x" * (200 * 1024 + 1)), "limit per file"),
    (lambda src: (src / "traffic" / "simulate.py").unlink(), "missing"),
    (lambda src: (src / "taskboard" / "simulate.py").write_text("x\n"), "clashes"),
    (lambda src: (src / "taskboard" / "logo.png").write_bytes(b"\x89PNG\xff\xfe"), "not UTF-8"),
    (lambda src: (src / "taskboard" / "a.py").write_text("x = 1   \n"), "ends in a space"),
    (lambda src: (src / "taskboard" / "a.js").write_text("if (x) {\n\treturn;\n}\n"), "has a tab"),
    (lambda src: (src / "taskboard" / "a.py").write_text("x = 1\r\n"), "control character"),
])
def test_refuses_what_a_configmap_cannot_carry_cleanly(fake, breakage, message):
    repo, src = fake
    breakage(src)
    result = _gen(repo, src)
    assert result.returncode != 0
    assert message in result.stderr
    assert not (repo / gen.OUT).exists(), "nothing written"
    assert not (repo / gen.BROWSER_OUT).exists(), "nothing written"


@pytest.mark.parametrize("commit", ["abc123", SHA.upper(), SHA + "0", "main"])
def test_commit_must_be_a_full_sha(fake, commit):
    repo, src = fake
    result = _gen(repo, src, commit)
    assert result.returncode != 0 and "full 40-character" in result.stderr


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
                        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_git_checkout_must_be_at_the_commit_and_clean(fake):
    repo, src = fake
    _git(src, "init", "-q")
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "init")
    head = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    result = _gen(repo, src, OTHER_SHA)
    assert result.returncode != 0 and "not --commit" in result.stderr
    (src / "taskboard" / "server.py").write_text("print('uncommitted')\n")
    result = _gen(repo, src, head)
    assert result.returncode != 0 and "uncommitted" in result.stderr
    _git(src, "commit", "-q", "-am", "two")
    head = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert _gen(repo, src, head).returncode == 0
