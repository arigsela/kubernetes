# tests/n8n_bug_investigator/test_workflow.py
"""The generated n8n workflows and their wiring into the n8n Deployment (§T.152, Task 9).

The Docker tests run the image the Deployment pins: both workflows must import and export
unchanged, the bundled code must run in n8n's task-runner sandbox, and the child workflow must
take its failure path to Finish. A missing docker fails them; it never skips.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml

import world as W
from conftest import REPO

GEN_PATH = REPO / "scripts/gen-n8n-bug-investigator-workflow.py"
CM = REPO / "base-apps/n8n/bug-investigator-workflow-configmap.yaml"
DEP = REPO / "base-apps/n8n/deployments.yaml"
TEMPLATE = REPO / "base-apps/kagent/agent-templates/bug-investigator.yaml"
FIXTURES = Path(__file__).parent / "fixtures"
PARENT, CHILD = "bug-investigator-n8n-poc", "bug-investigator-n8n-poc-run"


def gen():
    spec = importlib.util.spec_from_file_location("gen_n8n_bug_investigator_workflow", GEN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def workflows():
    data = yaml.safe_load(CM.read_text())["data"]
    return json.loads(data["bug-investigator.json"]), json.loads(data["bug-investigator-run.json"])


def nodes(wf):
    return {n["name"]: n for n in wf["nodes"]}


def deployment():
    return next(d for d in yaml.safe_load_all(DEP.read_text()) if d and d.get("kind") == "Deployment")


def template():
    return yaml.safe_load(TEMPLATE.read_text())


def template_tools(server):
    binds = [x["mcp"] for x in template()["spec"]["tools"] if "mcp" in x and x["mcp"]["server"]["name"] == server]
    assert len(binds) == 1
    return binds[0]["tools"]


def deployed_image():
    return deployment()["spec"]["template"]["spec"]["containers"][0]["image"]


def code_nodes():
    for wf in workflows():
        for n in wf["nodes"]:
            if n["type"] == "n8n-nodes-base.code":
                yield n


# ---------------------------------------------------------------------------- generation


def test_generated_files_are_current():
    r = subprocess.run(["python3", str(GEN_PATH), "--repo-root", str(REPO), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]


def test_system_prompt_is_the_agenttemplates():
    _, child = workflows()
    assert nodes(child)["Investigate"]["parameters"]["options"]["systemMessage"] == template()["spec"]["systemPrompt"]


def test_agent_tools_are_the_agenttemplates():
    _, child = workflows()
    n = nodes(child)
    gh, tel = n["github"]["parameters"], n["telemetry"]["parameters"]
    assert gh["includeTools"] == template_tools("implementer") and tel["includeTools"] == template_tools("taskboard-telemetry")
    assert gh["endpointUrl"] == "http://n8n-github-read-mcp.n8n.svc.cluster.local:3000/mcp"
    assert tel["endpointUrl"] == "http://taskboard-telemetry-mcp.kagent.svc.cluster.local:3000/mcp"
    for p in (gh, tel):
        assert p["include"] == "selected" and p["serverTransport"] == "httpStreamable" and p["authentication"] == "none"
    tools_in = {c["node"] for c in child["connections"]["github"]["ai_tool"][0]} | {c["node"] for c in child["connections"]["telemetry"]["ai_tool"][0]}
    assert tools_in == {"Investigate"}


def test_model_and_limits():
    parent, child = workflows()
    n = nodes(child)
    model = n["Claude Sonnet 5.5"]["parameters"]
    assert model["model"] == {"__rl": True, "mode": "id", "value": "claude-sonnet-5-5"}
    assert model["options"]["maxTokensToSample"] == 16000
    # One investigation's model calls re-send the system prompt, tools and history seconds apart:
    # cached for 5 minutes, they bill at the cache-read price (the rehearsal ran uncached).
    assert model["options"]["promptCaching"] == "5m"
    assert n["Claude Sonnet 5.5"]["credentials"] == {"anthropicApi": {"id": gen().ANTHROPIC_CREDENTIAL["id"], "name": "Anthropic — bug investigator POC"}}
    opts = n["Investigate"]["parameters"]["options"]
    assert opts["maxIterations"] == 40 and opts["returnIntermediateSteps"] is True
    assert n["Investigate"]["parameters"]["promptType"] == "define" and n["Investigate"]["parameters"]["text"] == "={{ $json.message }}"
    assert n["Investigate"]["onError"] == "continueErrorOutput"
    assert child["settings"]["executionTimeout"] == 900 and parent["settings"]["executionTimeout"] == 960
    assert nodes(parent)["Every 2 minutes"]["parameters"]["rule"] == {"interval": [{"field": "minutes", "minutesInterval": 2}]}


def test_graph_wiring():
    parent, child = workflows()
    edges = lambda wf: {(src, kind, i, c["node"])  # noqa: E731
                        for src, by_kind in wf["connections"].items() for kind, outs in by_kind.items()
                        for i, out in enumerate(outs) for c in out}
    assert edges(parent) == {("Every 2 minutes", "main", 0, "Pick issue"), ("Pick issue", "main", 0, "Investigate issue")}
    assert edges(child) == {
        ("Called by the picker", "main", 0, "Gather facts"), ("Gather facts", "main", 0, "Gathered?"),
        ("Gathered?", "main", 0, "Investigate"), ("Gathered?", "main", 1, "Finish"),
        ("Claude Sonnet 5.5", "ai_languageModel", 0, "Investigate"),
        ("github", "ai_tool", 0, "Investigate"), ("telemetry", "ai_tool", 0, "Investigate"),
        ("Investigate", "main", 0, "Verify"), ("Investigate", "main", 1, "Agent failed"),
        ("Verify", "main", 0, "Render and post"), ("Render and post", "main", 0, "Finish"), ("Agent failed", "main", 0, "Finish"),
    }
    sub = nodes(parent)["Investigate issue"]["parameters"]
    assert sub["workflowId"]["value"] == CHILD and sub["options"]["waitForSubWorkflow"] is True and sub["source"] == "database"
    cond = nodes(child)["Gathered?"]["parameters"]["conditions"]["conditions"][0]
    assert cond["leftValue"] == "={{ $json.failed }}" and cond["operator"]["operation"] == "false"


def test_parent_does_not_save_idle_runs():
    parent, child = workflows()
    assert parent["settings"]["saveDataSuccessExecution"] == "none" and parent["settings"]["saveDataErrorExecution"] == "all"
    assert child["settings"]["saveDataSuccessExecution"] == "all" and child["settings"]["saveDataErrorExecution"] == "all"
    assert parent["id"] == PARENT and child["id"] == CHILD


def test_every_code_node_parses(tmp_path):
    for n in code_nodes():
        f = tmp_path / f"{uuid.uuid4().hex}.js"
        f.write_text("async function f() {\n" + n["parameters"]["jsCode"] + "\n}\n")
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, (n["name"], r.stderr[-1500:])


def test_each_node_bundles_only_what_it_requires():
    want = {
        "Pick issue": {"util", "github", "n8n", "pick", "settings"},
        "Gather facts": {"util", "github", "n8n", "gather", "settings"},
        "Verify": {"util", "github", "n8n", "mcp", "verify", "settings"},
        "Render and post": {"util", "github", "n8n", "render", "settings"},
        "Agent failed": {"util"},
        "Finish": {"util", "github", "n8n", "pick", "finish", "settings"},
    }
    got = {n["name"]: set(re.findall(r"^  '\./([a-z0-9]+)': function \(module, exports, require\)", n["parameters"]["jsCode"], re.M))
           for n in code_nodes()}
    assert got == want


def test_configmap_size_budget():
    size = len(CM.read_bytes())
    assert size <= gen().CONFIGMAP_BUDGET, size


def test_node_types_match_spike_fixture():
    version = re.search(r"n8nio/n8n:([0-9.]+)@", deployed_image()).group(1)
    fixture = json.loads((FIXTURES / f"n8n-{version}-nodes.json").read_text())
    for wf in workflows():
        for n in wf["nodes"]:
            assert n["typeVersion"] == fixture[n["type"]]["typeVersion"], (n["name"], n["type"])


def test_no_secret_in_generated_json():
    text = CM.read_text()
    assert not re.search(r"ghp_[A-Za-z0-9]{10}|github_pat_[A-Za-z0-9]{10}|sk-ant-[A-Za-z0-9]{6}", text)


def test_code_reads_the_gathered_item_and_the_token():
    n = {x["name"]: x["parameters"]["jsCode"] for x in code_nodes()}
    for name in ("Verify", "Agent failed"):
        assert "$('Gather facts').first().json" in n[name], name
    assert "$('Investigate').first().json" in n["Render and post"]
    for name in ("Pick issue", "Gather facts", "Verify", "Render and post", "Finish"):
        assert "httpFromHelpers(this.helpers)" in n[name], name
    for name, code in n.items():
        # The token: the n8n Variable, else the environment variable (BUG_INVESTIGATOR_GH_TOKEN here).
        assert "const TOKEN = CONFIG.tokenName || 'BUG_INVESTIGATOR_GH_TOKEN';" in code and "__secret(TOKEN)" in code, name
        assert "$vars[name]" in code and "$env[name]" in code and "$env." not in code, name


def test_active_requires_not_before():
    g = gen()
    with pytest.raises(ValueError):
        g.build(REPO, active=True, not_before=None)
    parent, _ = workflows()
    pick = nodes(parent)["Pick issue"]["parameters"]["jsCode"]
    assert f'"notBefore": {json.dumps(g.NOT_BEFORE)}' in pick


# -------------------------------------------------------------------- the n8n Deployment


def test_checksum_annotation_is_current():
    data = yaml.safe_load(CM.read_text())["data"]
    want = hashlib.sha256("".join(data[k] for k in sorted(data)).encode()).hexdigest()[:16]
    assert deployment()["spec"]["template"]["metadata"]["annotations"]["checksum/bug-investigator-workflow"] == want


def test_workflow_volume_is_mounted_read_only():
    spec = deployment()["spec"]["template"]["spec"]
    vol = next(v for v in spec["volumes"] if v["name"] == "bug-investigator-workflow")
    assert vol["configMap"]["name"] == "n8n-workflow-bug-investigator"
    mount = next(m for m in spec["initContainers"][0]["volumeMounts"] if m["name"] == "bug-investigator-workflow")
    assert mount == {"name": "bug-investigator-workflow", "mountPath": "/workflows-bug-investigator", "readOnly": True}


def test_gh_token_env_is_optional():
    env = deployment()["spec"]["template"]["spec"]["containers"][0]["env"]
    var = next(e for e in env if e["name"] == "BUG_INVESTIGATOR_GH_TOKEN")
    assert var["valueFrom"] == {"secretKeyRef": {"name": "n8n-bug-investigator-gh", "key": "token", "optional": True}}


def init_script():
    return deployment()["spec"]["template"]["spec"]["initContainers"][0]["args"][0]


def test_init_container_imports_and_sets_active():
    script = init_script()
    block = script[script.index("# BEGIN bug-investigator POC"):script.index("# END bug-investigator POC")]
    assert script.index("# BEGIN bug-investigator POC") > script.index("agent-findings:agent-findings")
    assert f"bug-investigator-run:{CHILD}" in block and f"bug-investigator:{PARENT}" in block
    assert block.index(CHILD) < block.index(f"bug-investigator:{PARENT}")  # the child exists before the parent calls it
    assert '--input="/workflows-bug-investigator/$file.json"' in block
    assert ('n8n publish:workflow --id="$id"' in block) == gen().ACTIVE


def _run_init(tmp_path, fail_poc: bool):
    """The init script under sh, with a stub `n8n` that logs its arguments (and fails POC imports if asked)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True)
    log = tmp_path / "n8n.log"
    stub = bindir / "n8n"
    stub.write_text("#!/bin/sh\n"
                    f'echo "$*" >> "{log}"\n'
                    f'case "$*" in *workflows-bug-investigator*) [ "{int(fail_poc)}" = 1 ] && exit 1 ;; esac\n'
                    'case "$*" in unpublish*) exit 1 ;; esac\n'
                    "exit 0\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    r = subprocess.run(["sh", "-c", init_script()], capture_output=True, text=True, env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"})
    return r, log.read_text().splitlines()


def test_poc_import_cannot_block_the_pod(tmp_path):
    r, calls = _run_init(tmp_path / "fail", fail_poc=True)
    assert r.returncode == 0, r.stderr
    assert sum(1 for c in calls if c.startswith("import:workflow --input=/workflows/")) == 6
    assert "bug-investigator POC import failed" in r.stderr
    r, calls = _run_init(tmp_path / "ok", fail_poc=False)
    assert r.returncode == 0 and "bug-investigator POC import failed" not in r.stderr
    assert [c for c in calls if "workflows-bug-investigator" in c] == [
        "import:workflow --input=/workflows-bug-investigator/bug-investigator-run.json",
        "import:workflow --input=/workflows-bug-investigator/bug-investigator.json"]


# ------------------------------------------------------------------- n8n itself (Docker)

SPARSE_FACTS = {
    "mode": "full", "generated_at": "2026-10-28T12:00:00Z", "repo": W.REPO, "namespace": "taskboard",
    "ticket": {"number": 41, "title": "Broke | badly", "body": "x <b> @y\r\nz", "author": "app/arigsela-implementer",
               "created_at": "2026-10-28T10:00:00Z", "state": "OPEN", "url": "u", "labels": ["bug"], "comments": []},
    "problems": ["past_bugs: GitHub GraphQL: down"], "deploy_repo": W.DEPLOY_REPO, "deploy_path": W.DEPLOY_PATH,
    "deploys": [], "merged_prs": [], "past_bugs": [], "docs": [],
}


@pytest.fixture(scope="module")
def n8n_session(tmp_path_factory, core):
    if not shutil.which("docker"):
        pytest.fail("docker is required for tests/n8n_bug_investigator (the n8n import and runtime tests); the suite never skips")
    g = gen()
    facts = {**SPARSE_FACTS, "telemetry": core.gather.telemetry_pointers("taskboard")}
    steps = json.loads((FIXTURES / "intermediate-steps.json").read_text())["intermediateSteps"]
    probe_code = g.bundle(["gather", "render", "util"], (
        "const gth = __require('./gather');\nconst r = __require('./render');\nconst u = __require('./util');\n"
        f"const facts = {json.dumps(facts)};\nconst steps = {json.dumps(steps)};\n"
        "return [{ json: { issue: { number: 41 }, md: gth.toMarkdown(facts), dec: gth.b64decode('aGVsbG8g8J+YgA=='), lines: u.splitLines('a\\r\\nb\\u2028c'),\n"
        "  rule: gth.ruleId('- **P4** x\\n'), trail: r.trailFromSteps(steps),\n"
        "  secret: __secret('N8N_PROBE_SECRET'), unset: __secret('N8N_PROBE_UNSET'),\n"
        "  globals: { Buffer: typeof Buffer, atob: typeof atob, TextDecoder: typeof TextDecoder, URL: typeof URL } } }];\n"), None)
    probe = {"id": "probe", "name": "probe", "active": False, "settings": {"executionOrder": "v1"},
             "nodes": [{"id": str(uuid.uuid4()), "name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0], "parameters": {}},
                       {"id": str(uuid.uuid4()), "name": "Probe", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [200, 0],
                        "parameters": {"mode": "runOnceForAllItems", "jsCode": probe_code}},
                       {"id": str(uuid.uuid4()), "name": "Call the child", "type": "n8n-nodes-base.executeWorkflow", "typeVersion": 1.4, "position": [400, 0],
                        "parameters": {"source": "database", "workflowId": {"__rl": True, "mode": "id", "value": CHILD},
                                       "workflowInputs": {"mappingMode": "defineBelow", "value": {}}, "mode": "each",
                                       "options": {"waitForSubWorkflow": True}}}],
             "connections": {"Start": {"main": [[{"node": "Probe", "type": "main", "index": 0}]]},
                             "Probe": {"main": [[{"node": "Call the child", "type": "main", "index": 0}]]}}}
    d = tmp_path_factory.mktemp("n8n")
    data = yaml.safe_load(CM.read_text())["data"]
    for k, v in data.items():
        (d / k).write_text(v)
    (d / "probe.json").write_text(json.dumps(probe))
    pick_node = {**nodes(workflows()[0])["Pick issue"], "position": [200, 0]}
    pickprobe = {"id": "pickprobe", "name": "pickprobe", "active": False, "settings": {"executionOrder": "v1"},
                 "nodes": [{"id": str(uuid.uuid4()), "name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
                            "position": [0, 0], "parameters": {}}, pick_node],
                 "connections": {"Start": {"main": [[{"node": "Pick issue", "type": "main", "index": 0}]]}}}
    (d / "pickprobe.json").write_text(json.dumps(pickprobe))
    # The image runs as `node`; docker cp keeps modes, and pytest's temp dirs are 0700.
    d.chmod(0o755)
    for f in d.iterdir():
        f.chmod(0o644)
    script = "\n".join([
        "set -e",
        f"n8n import:workflow --input=/w/bug-investigator-run.json",
        f"n8n import:workflow --input=/w/bug-investigator.json",
        "n8n import:workflow --input=/w/probe.json",
        "n8n import:workflow --input=/w/pickprobe.json",
        f"n8n publish:workflow --id={CHILD}",
        f"n8n export:workflow --id={CHILD} --output=/tmp/child.json",
        f"n8n export:workflow --id={PARENT} --output=/tmp/parent.json",
        # As in the pod: the server starts after the CLI import. Its start records the published
        # versions a sub-workflow call reads (a CLI publish alone leaves them empty), so the
        # child is called with the server up, from a second CLI process on its own broker port.
        "n8n start >/tmp/server.log 2>&1 &",
        "i=0; until wget -q -O- http://127.0.0.1:5678/healthz/readiness >/dev/null 2>&1 || [ $i -ge 90 ]; do sleep 2; i=$((i+1)); done",
        "echo '==RUN=='", "N8N_RUNNERS_BROKER_PORT=5681 n8n execute --id=probe --rawOutput || true",
        # The parent's own Pick issue code with no token, behind a manual trigger (the CLI cannot
        # start a schedule-only workflow): it must idle (a successful run, not saved), not fail.
        "echo '==PICK=='", "N8N_RUNNERS_BROKER_PORT=5681 n8n execute --id=pickprobe --rawOutput || true",
        "echo '==CHILD=='", "cat /tmp/child.json", "echo", "echo '==PARENT=='", "cat /tmp/parent.json", "echo", "echo '==END=='",
    ])
    name = f"n8n-poc-test-{uuid.uuid4().hex[:8]}"
    # N8N_BLOCK_ENV_ACCESS_IN_NODE=false as the Deployment sets it: the Code nodes read $env.
    subprocess.run(["docker", "create", "--name", name, "-e", "N8N_BLOCK_ENV_ACCESS_IN_NODE=false", "-e", "N8N_PROBE_SECRET=s3cret",
                    "--entrypoint", "/bin/sh",
                    deployed_image(), "-c", script], check=True, capture_output=True)
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
        return json.JSONDecoder().raw_decode(text[i:])[0]

    run_text = section("==RUN==", "==PICK==")
    run = json.JSONDecoder().raw_decode(run_text[run_text.index("{\n"):])[0]
    pick_text = section("==PICK==", "==CHILD==")
    # A failed CLI execution may print only an error, no JSON.
    pick = (json.JSONDecoder().raw_decode(pick_text[pick_text.index("{\n"):])[0] if "{\n" in pick_text
            else {"status": "no JSON", "data": {"resultData": {"error": pick_text[-1500:], "runData": {}}}})
    return {"run": run, "pick": pick, "child": first_json(section("==CHILD==", "==PARENT==")), "parent": first_json(section("==PARENT==", "==END==")),
            "facts": facts, "steps": steps}


def _subset(a, b, path=""):
    bad = []
    if isinstance(a, dict):
        if not isinstance(b, dict):
            return [path]
        for k, v in a.items():
            bad += [f"{path}.{k} missing"] if k not in b else _subset(v, b[k], f"{path}.{k}")
    elif a != b:
        bad.append(f"{path}: {str(a)[:80]!r} != {str(b)[:80]!r}")
    return bad


def test_n8n_accepts_the_workflows(n8n_session):
    for mine, exported in zip(workflows(), (n8n_session["parent"], n8n_session["child"])):
        exported = exported[0] if isinstance(exported, list) else exported
        by = {n["name"]: n for n in exported["nodes"]}
        problems = []
        for n in mine["nodes"]:
            e = by.get(n["name"])
            if not e:
                problems.append(f"{n['name']} missing")
                continue
            for k in ("type", "typeVersion", "onError", "credentials"):
                if k in n and n[k] != e.get(k):
                    problems.append(f"{n['name']}.{k}")
            problems += _subset(n["parameters"], e["parameters"], n["name"])
        problems += _subset(mine["settings"], exported.get("settings") or {}, "settings")
        problems += _subset(mine["connections"], exported.get("connections") or {}, "connections")
        assert not problems, problems


def _node_items(run, name):
    rd = run["data"]["resultData"]["runData"]
    return [it["json"] for r in rd.get(name, []) for out in (r.get("data") or {}).get("main") or [] for it in out or []]


def test_bundles_run_in_n8ns_sandbox(core, js, n8n_session):
    probe = _node_items(n8n_session["run"], "Probe")[0]
    assert probe["md"] == core.gather.to_markdown(n8n_session["facts"])
    assert probe["dec"] == "hello 😀" and probe["lines"] == ["a", "b", "c"] and probe["rule"] == "P4"
    assert probe["trail"] == js("render", "trailFromSteps", n8n_session["steps"])
    # __secret: no n8n Variable here (unlicensed), so the environment variable; unset is "".
    assert probe["secret"] == "s3cret" and probe["unset"] == ""


def test_child_takes_its_failure_path_to_finish(n8n_session):
    # No token in the test container: Gather fails before any request (a prepare failure, not
    # spent), Gathered? routes to Finish, and Finish reports the lock released.
    run = n8n_session["run"]
    child_out = _node_items(run, "Call the child")
    rd = run["data"]["resultData"]
    errors = [rd.get("error")] + [r.get("error") for runs in rd["runData"].values() for r in runs if r.get("error")]
    assert child_out and child_out[0]["outcome"] == "released", json.dumps([e for e in errors if e], default=str)[:3000]
    assert "BUG_INVESTIGATOR_GH_TOKEN" in json.dumps(child_out[0])


def test_pick_without_a_token_is_idle_not_an_error(n8n_session):
    # A token problem recurs every 2 minutes; as an error each run would be saved (720 a day).
    pick = n8n_session["pick"]
    rd = pick["data"]["resultData"]
    assert pick["status"] == "success" and not rd.get("error"), json.dumps(rd.get("error"), default=str)[:1500]
    assert _node_items(pick, "Pick issue") == [] and "Pick issue" in rd["runData"]


def test_progress_deadline_covers_the_init():
    # The init container makes 3 n8n CLI calls per workflow (~47 s each on these nodes) and the
    # probes wait 240 s, so a rollout takes ~20 minutes; the default 600 s deadline marks every
    # one ProgressDeadlineExceeded and Argo shows the app Degraded until the pod is Ready.
    assert deployment()["spec"]["progressDeadlineSeconds"] >= 1800
