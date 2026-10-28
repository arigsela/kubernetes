# tests/n8n_bug_investigator/test_package.py
"""The handover package (§T.152): a zip someone else builds their own workflows from.

scripts/n8n-bug-investigator/make-package.py assembles it: package/ (README, ADAPTERS, the example
config, the prompt, build.py, docker-compose.yml) plus engine/ (builder.py, lib/ with the neutral
package/settings.js in place of this homelab's, nodes/). These tests build from the example
config in an unzipped copy, the way the recipient will, and run the result in n8n.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import uuid
import zipfile
from pathlib import Path

import pytest

import test_compare as TC
import test_workflow as TW
import world as W
from conftest import REPO, _node

PKG = REPO / "scripts/n8n-bug-investigator/package"
LIB = REPO / "scripts/n8n-bug-investigator/lib"
MAKE = REPO / "scripts/n8n-bug-investigator/make-package.py"
HOMELAB = ["arigsela", "taskboard", "Taskboard", "kagent", "svc.cluster.local", "PBFA97CFB590B2093",
           "GZNrd4FlwwbyDA44", "n8nagent", "POC", "§T."]
EXAMPLE = tomllib.loads((PKG / "config.example.toml").read_text())


@pytest.fixture(scope="module")
def unpacked(tmp_path_factory):
    d = tmp_path_factory.mktemp("pkg")
    z = d / "n8n-bug-investigator.zip"
    r = subprocess.run([sys.executable, str(MAKE), "--out", str(z)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    with zipfile.ZipFile(z) as f:
        f.extractall(d)
    return d / "n8n-bug-investigator"


def build(root: Path, config: str | None = None, tmp: Path | None = None):
    cfg = root / "config.example.toml"
    if config is not None:
        cfg = (tmp or root) / f"config-{uuid.uuid4().hex[:6]}.toml"
        cfg.write_text(config)
        if tmp is not None:
            shutil.copy(root / "prompt.md", tmp / "prompt.md")
    out = cfg.parent / f"out-{uuid.uuid4().hex[:6]}"
    r = subprocess.run([sys.executable, "-I", str(root / "build.py"), str(cfg), "--out", str(out)], capture_output=True, text=True)
    files = {p.name: json.loads(p.read_text()) for p in out.glob("*.json")} if out.exists() else {}
    return r, files


def code_nodes(wf):
    return {n["name"]: n["parameters"]["jsCode"] for n in wf["nodes"] if n["type"] == "n8n-nodes-base.code"}


def config_of(js: str) -> dict:
    return json.loads(re.search(r"^const CONFIG = (.*);$", js, re.M).group(1))


# ------------------------------------------------------------------------------- the zip


def test_zip_holds_the_package_and_the_engine(unpacked):
    names = {str(p.relative_to(unpacked)) for p in unpacked.rglob("*") if p.is_file()}
    assert {"README.md", "ADAPTERS.md", "config.example.toml", "prompt.md", "build.py", "docker-compose.yml",
            "engine/builder.py", "engine/lib/settings.js", "engine/nodes/pick.js"} <= names
    assert {f"engine/lib/{p.name}" for p in LIB.glob("*.js")} <= names
    assert {f"engine/nodes/{p.name}" for p in (LIB.parent / "nodes").glob("*.js")} <= names


def test_zip_carries_the_neutral_settings(unpacked):
    assert (unpacked / "engine/lib/settings.js").read_text() == (PKG / "settings.js").read_text()
    for p in LIB.glob("*.js"):
        if p.name != "settings.js":
            assert (unpacked / "engine/lib" / p.name).read_text() == p.read_text(), p.name


def test_package_settings_export_what_lib_settings_exports():
    node = _node()
    keys = lambda path: json.loads(subprocess.run([node, "-e", f"console.log(JSON.stringify(Object.keys(require({json.dumps(str(path))})).sort()))"],  # noqa: E731
                                                  capture_output=True, text=True, check=True).stdout)
    assert keys(PKG / "settings.js") == keys(LIB / "settings.js")


def test_zip_is_reproducible(tmp_path):
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    for z in (a, b):
        subprocess.run([sys.executable, str(MAKE), "--out", str(z)], check=True, capture_output=True)
    assert a.read_bytes() == b.read_bytes()


# ------------------------------------------------------------------------------ building


def test_example_config_builds(unpacked):
    r, files = build(unpacked)
    assert r.returncode == 0, r.stderr
    assert sorted(files) == ["bug-investigator-run.json", "bug-investigator.json"]
    parent, child = files["bug-investigator.json"], files["bug-investigator-run.json"]
    assert parent["id"] == EXAMPLE["workflows"]["parent_id"] and child["id"] == EXAMPLE["workflows"]["child_id"]
    call = next(n for n in parent["nodes"] if n["name"] == "Investigate issue")
    assert call["parameters"]["workflowId"]["value"] == child["id"]
    sched = next(n for n in parent["nodes"] if n["type"] == "n8n-nodes-base.scheduleTrigger")
    assert sched["parameters"]["rule"]["interval"][0]["minutesInterval"] == EXAMPLE["schedule"]["every_minutes"]


def test_built_workflows_carry_no_homelab_values(unpacked):
    _, files = build(unpacked)
    text = json.dumps(files, ensure_ascii=False)
    assert not [s for s in HOMELAB if s in text]


def test_config_reaches_every_code_node(unpacked):
    _, files = build(unpacked)
    nodes = {**code_nodes(files["bug-investigator.json"]), **code_nodes(files["bug-investigator-run.json"])}
    assert set(nodes) == {"Pick issue", "Gather facts", "Verify", "Render and post", "Agent failed", "Finish"}
    gh = EXAMPLE["github"]
    for name, js in nodes.items():
        c = config_of(js)
        assert c["repo"] == gh["repo"] and c["notBefore"] == gh["not_before"] and c["tokenName"] == gh["token_name"], name
        assert c["labels"] == EXAMPLE["labels"] and c["comment"] == EXAMPLE["comment"] and c["trustedBots"] == [], name
        assert c["telemetryUrl"] is None and c["deployRepo"] is None and c["watchBranches"] is None, name
        assert c["tools"] == EXAMPLE["mcp"][0]["tools"], name


def test_bundles_are_valid_javascript(unpacked, tmp_path):
    _, files = build(unpacked)
    for wf in files.values():
        for name, js in code_nodes(wf).items():
            f = tmp_path / "node.js"
            f.write_text("async function f() {\n" + js + "\n}\n")
            r = subprocess.run([_node(), "--check", str(f)], capture_output=True, text=True)
            assert r.returncode == 0, (name, r.stderr[-1500:])


def test_credentials_are_left_to_choose_after_import(unpacked):
    _, files = build(unpacked)
    child = {n["name"]: n for n in files["bug-investigator-run.json"]["nodes"]}
    assert "credentials" not in child["Anthropic model"] and "credentials" not in child["github"]
    assert child["github"]["parameters"]["authentication"] == "bearerAuth"
    assert child["github"]["parameters"]["includeTools"] == EXAMPLE["mcp"][0]["tools"]


def test_credential_ids_are_used_when_given(unpacked, tmp_path):
    text = (unpacked / "config.example.toml").read_text()
    text = text.replace('credential_id = ""                         # your Anthropic', 'credential_id = "abc123"  # your Anthropic', 1)
    text = text.replace('credential_id = ""                         # blank: choose the credential', 'credential_id = "ghr456"  # blank', 1)
    r, files = build(unpacked, text, tmp_path)
    assert r.returncode == 0, r.stderr
    child = {n["name"]: n for n in files["bug-investigator-run.json"]["nodes"]}
    assert child["Anthropic model"]["credentials"] == {"anthropicApi": {"id": "abc123", "name": "Anthropic"}}
    assert child["github"]["credentials"] == {"httpBearerAuth": {"id": "ghr456", "name": "GitHub read-only"}}


def test_prompt_is_filled_in(unpacked):
    _, files = build(unpacked)
    agent = next(n for n in files["bug-investigator-run.json"]["nodes"] if n["name"] == "Investigate")
    prompt = agent["parameters"]["options"]["systemMessage"]
    assert "{{" not in prompt and EXAMPLE["github"]["repo"] in prompt and EXAMPLE["app"]["name"] in prompt
    assert "No telemetry: you have no log or metric tools." in prompt and "`logql`" not in prompt


def test_telemetry_choices_change_the_prompt(unpacked, tmp_path):
    base = (unpacked / "config.example.toml").read_text()
    loki = base.replace('url = ""                                   # the MCP server', 'url = "http://grafana-mcp:8000/mcp"  #', 1)
    loki = loki.replace('loki_uid = ""', 'loki_uid = "L1"').replace('prometheus_uid = ""', 'prometheus_uid = "P1"')
    r, files = build(unpacked, loki, tmp_path)
    assert r.returncode == 0, r.stderr
    prompt = next(n for n in files["bug-investigator-run.json"]["nodes"] if n["name"] == "Investigate")["parameters"]["options"]["systemMessage"]
    assert "datasourceUid `L1`" in prompt and "`logql` / `promql`" in prompt
    c = config_of(code_nodes(files["bug-investigator-run.json"])["Verify"])
    assert c["telemetryUrl"] == "http://grafana-mcp:8000/mcp" and c["datasources"] == {"loki": "L1", "prometheus": "P1"}

    dd = base.replace("notes = []", 'notes = ["Datadog: service `checkout`, env `prod`."]')
    r, files = build(unpacked, dd, tmp_path)
    prompt = next(n for n in files["bug-investigator-run.json"]["nodes"] if n["name"] == "Investigate")["parameters"]["options"]["systemMessage"]
    assert "The script cannot\n  re-check telemetry" in prompt and "`logql`" not in prompt


@pytest.mark.parametrize("old,new,message", [
    ('repo = "your-org/your-app"', 'repo = "your-app"', "owner/name"),
    ('not_before = "2026-11-01T00:00:00Z"', 'not_before = "next week"', "RFC 3339"),
    ('node = "github"', 'node = "Verify"', "node names"),
    ('auth = "bearerAuth"', 'auth = "oauth"', "auth must be one of"),
    ('prompt_caching = "5m"', 'prompt_caching = "10m"', "prompt_caching"),
    ('repo = ""\npath = ""', 'repo = "your-org/gitops"\npath = ""', "set both repo and path"),
    ('lock = "ai-investigating"', 'locked = "ai-investigating"', "unknown key"),
])
def test_bad_config_is_refused(unpacked, tmp_path, old, new, message):
    text = (unpacked / "config.example.toml").read_text()
    assert text.count(old) == 1, old
    r, files = build(unpacked, text.replace(old, new), tmp_path)
    assert r.returncode == 2 and message in r.stderr and not files, r.stderr


# --------------------------------------------------------------- the neutral settings in use


def js_pkg(unpacked, module, fn, *args, ctx=None):
    req = {"module": module, "fn": fn, "args": list(args), "ctx": ctx}
    r = subprocess.run([_node(), str(Path(__file__).parent / "run.js")], input=json.dumps(req), capture_output=True, text=True,
                       env={**os.environ, "N8N_BI_LIB": str(unpacked / "engine/lib")}, timeout=60)
    reply = json.loads(r.stdout)
    assert reply["ok"], reply
    return reply["value"]


def test_neutral_defaults_in_the_libs(unpacked, core, frozen_now, tmp_path):
    v = TC.verified(core, tmp_path, frozen_now)
    body = js_pkg(unpacked, "render", "renderComment", v, {"model": "m"})
    assert body.startswith("<!-- bug-investigator mode=n8n-ai -->\n## 🔎 Bug investigation — AI agent\n")
    assert js_pkg(unpacked, "finish", "outcome", {"issue": {"number": 1}, "failed": False})["add"] == ["ai-investigated"]
    w = W.rich_world(frozen_now)
    cfg = {"repo": W.REPO, "namespace": "shop", "days": 14, "telemetryUrl": "http://grafana-mcp/mcp", "datasources": {"loki": "L1", "prometheus": "P1"}}
    facts = js_pkg(unpacked, "gather", "gatherVia", cfg, w.issue, W.iso(frozen_now), {}, ctx=w.http())
    assert facts["telemetry"] == {"notes": ['Loki datasourceUid `L1`, Prometheus datasourceUid `P1`. Every Loki selector and Prometheus query needs namespace="shop".']}


# ------------------------------------------------------------------------------ n8n itself


@pytest.fixture(scope="module")
def n8n_package_run(unpacked, tmp_path_factory):
    if not shutil.which("docker"):
        pytest.fail("docker is required for tests/n8n_bug_investigator; the suite never skips")
    r, files = build(unpacked)
    assert r.returncode == 0, r.stderr
    parent, child = files["bug-investigator.json"], files["bug-investigator-run.json"]
    probe = {"id": "pkgprobe", "name": "pkgprobe", "active": False, "settings": {"executionOrder": "v1"},
             "nodes": [{"id": str(uuid.uuid4()), "name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0], "parameters": {}},
                       {"id": str(uuid.uuid4()), "name": "Issue", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [200, 0],
                        "parameters": {"mode": "runOnceForAllItems", "jsCode": "return [{ json: { issue: { number: 41 } } }];"}},
                       {**next(n for n in parent["nodes"] if n["name"] == "Investigate issue"), "position": [400, 0]}],
             "connections": {"Start": {"main": [[{"node": "Issue", "type": "main", "index": 0}]]},
                             "Issue": {"main": [[{"node": "Investigate issue", "type": "main", "index": 0}]]}}}
    d = tmp_path_factory.mktemp("n8npkg")
    for name, wf in {"child.json": child, "parent.json": parent, "probe.json": probe}.items():
        (d / name).write_text(json.dumps(wf))
    d.chmod(0o755)
    for f in d.iterdir():
        f.chmod(0o644)
    script = "\n".join([
        "set -e",
        "n8n import:workflow --input=/w/child.json", "n8n import:workflow --input=/w/parent.json", "n8n import:workflow --input=/w/probe.json",
        f"n8n publish:workflow --id={child['id']}",
        f"n8n export:workflow --id={child['id']} --output=/tmp/child.json",
        f"n8n export:workflow --id={parent['id']} --output=/tmp/parent.json",
        "n8n start >/tmp/server.log 2>&1 &",
        "i=0; until wget -q -O- http://127.0.0.1:5678/healthz/readiness >/dev/null 2>&1 || [ $i -ge 90 ]; do sleep 2; i=$((i+1)); done",
        "echo '==RUN=='", "N8N_RUNNERS_BROKER_PORT=5681 n8n execute --id=pkgprobe --rawOutput || true",
        "echo '==CHILD=='", "cat /tmp/child.json", "echo", "echo '==PARENT=='", "cat /tmp/parent.json", "echo", "echo '==END=='",
    ])
    name = f"n8n-pkg-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "create", "--name", name, "-e", "N8N_BLOCK_ENV_ACCESS_IN_NODE=false", "--entrypoint", "/bin/sh",
                    TW.deployed_image(), "-c", script], check=True, capture_output=True)
    try:
        subprocess.run(["docker", "cp", str(d), f"{name}:/w"], check=True, capture_output=True)
        r = subprocess.run(["docker", "start", "-a", name], capture_output=True, text=True, timeout=900)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    out = r.stdout
    assert r.returncode == 0 and "==END==" in out, (out[-3000:], r.stderr[-3000:])

    def section(a, b):
        return out[out.index(a) + len(a):out.index(b)]

    def first_json(text):
        i = min(x for x in (text.find("["), text.find("{")) if x >= 0)
        v = json.JSONDecoder().raw_decode(text[i:])[0]
        return v[0] if isinstance(v, list) else v

    run_text = section("==RUN==", "==CHILD==")
    return {"run": json.JSONDecoder().raw_decode(run_text[run_text.index("{\n"):])[0], "files": files,
            "child": first_json(section("==CHILD==", "==PARENT==")), "parent": first_json(section("==PARENT==", "==END=="))}


def test_n8n_accepts_the_package_workflows(n8n_package_run):
    files = n8n_package_run["files"]
    for mine, exported in ((files["bug-investigator.json"], n8n_package_run["parent"]), (files["bug-investigator-run.json"], n8n_package_run["child"])):
        by = {n["name"]: n for n in exported["nodes"]}
        problems = []
        for n in mine["nodes"]:
            e = by.get(n["name"])
            if not e:
                problems.append(f"{n['name']} missing")
                continue
            for k in ("type", "typeVersion", "onError"):
                if k in n and n[k] != e.get(k):
                    problems.append(f"{n['name']}.{k}")
            problems += TW._subset(n["parameters"], e["parameters"], n["name"])
        problems += TW._subset(mine["connections"], exported.get("connections") or {}, "connections")
        assert not problems, problems


def test_package_child_without_a_token_releases_the_lock(n8n_package_run):
    run = n8n_package_run["run"]
    out = TW._node_items(run, "Investigate issue")
    assert out and out[0]["outcome"] == "released", json.dumps(run["data"]["resultData"].get("error"), default=str)[:2000]
    assert f"{EXAMPLE['github']['token_name']} is not set" in json.dumps(out[0])
