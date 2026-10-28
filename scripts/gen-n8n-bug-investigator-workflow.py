#!/usr/bin/env python3
"""Generate the n8n bug-investigator POC workflows (§T.152) and wire them into the n8n Deployment.

    ./scripts/gen-n8n-bug-investigator-workflow.py --repo-root . --write   # regenerate
    ./scripts/gen-n8n-bug-investigator-workflow.py --repo-root . --check   # CI: fail if stale

Writes base-apps/n8n/bug-investigator-workflow-configmap.yaml (two workflows) and, in
base-apps/n8n/deployments.yaml, the checksum/bug-investigator-workflow annotation and the init
script's block between `# BEGIN bug-investigator POC` and `# END bug-investigator POC`.

- Parent `bug-investigator-n8n-poc`: Every 2 minutes → Pick issue → Investigate issue (Execute
  Workflow, waits). Its successful runs are not saved, so 720 idle runs a day leave nothing.
- Child `bug-investigator-n8n-poc-run`: Gather facts → Gathered? → Investigate (AI Agent, the
  AgentTemplate's systemPrompt and tools) → Verify → Render and post → Finish; the agent's error
  output → Agent failed → Finish. Every run saved.

Each Code node's jsCode is a bundle: the scripts/n8n-bug-investigator/lib modules its snippet
(scripts/n8n-bug-investigator/nodes/) requires, transitively, each wrapped in a module function,
full-line comments left out, then `const CONFIG = …` and the snippet. The prompt, the tool lists
and the model settings come from base-apps/kagent/agent-templates/bug-investigator.yaml, so the
n8n agent cannot drift from kagent's. Kill switch: ACTIVE (and NOT_BEFORE, required with it).
Plan: docs/superpowers/plans/2026-10-28-n8n-bug-investigator-poc.md.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path

import yaml

# ------------------------------------------------------------------------------- settings

WORKFLOW_ID = "bug-investigator-n8n-poc"
RUN_WORKFLOW_ID = "bug-investigator-n8n-poc-run"
ACTIVE = True                  # the kill switch: publish both workflows (on since the activation PR, 2026-10-28)
NOT_BEFORE = "2026-10-28T17:21:00Z"  # issues created earlier are never picked (#30, #37); required with ACTIVE
ANTHROPIC_CREDENTIAL = {"id": "GZNrd4FlwwbyDA44", "name": "Anthropic — bug investigator POC"}  # created in the n8n UI
CONFIGMAP_BUDGET = 196_608     # bytes; client-side apply copies the object into a 256 KiB annotation
MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 16000
PROMPT_CACHING = "5m"        # one run's calls re-send prompt, tools and history seconds apart (Anthropic prompt caching)
MAX_ITERATIONS = 40
CHILD_TIMEOUT = 900
PARENT_TIMEOUT = 960
EVERY_MINUTES = 2
DEADLINE_MS = 240_000          # n8n's task runner kills a Code node at 300 s (Task 1)
GITHUB_MCP = "http://n8n-github-read-mcp.n8n.svc.cluster.local:3000/mcp"
TELEMETRY_MCP = "http://taskboard-telemetry-mcp.kagent.svc.cluster.local:3000/mcp"
MCP_NODES = {"implementer": ("github", GITHUB_MCP), "taskboard-telemetry": ("telemetry", TELEMETRY_MCP)}
CONFIG = {
    "repo": "arigsela/taskboard", "deployRepo": "arigsela/kubernetes", "deployPath": "base-apps/taskboard",
    "namespace": "taskboard", "days": 14, "deadlineMs": DEADLINE_MS, "telemetryUrl": TELEMETRY_MCP,
    "grafanaUrl": "https://grafana.arigsela.com", "model": MODEL,
}
# Latest typeVersions in the pinned n8n (tests/n8n_bug_investigator/fixtures/n8n-<version>-nodes.json).
TYPE_VERSIONS = {
    "n8n-nodes-base.scheduleTrigger": 1.4, "n8n-nodes-base.code": 2, "n8n-nodes-base.if": 2.4,
    "n8n-nodes-base.executeWorkflow": 1.4, "n8n-nodes-base.executeWorkflowTrigger": 1.3,
    "@n8n/n8n-nodes-langchain.agent": 3.1, "@n8n/n8n-nodes-langchain.lmChatAnthropic": 1.6,
    "@n8n/n8n-nodes-langchain.mcpClientTool": 1.4,
}

LIB = "scripts/n8n-bug-investigator/lib"
NODES = "scripts/n8n-bug-investigator/nodes"
TEMPLATE = "base-apps/kagent/agent-templates/bug-investigator.yaml"
OUT = "base-apps/n8n/bug-investigator-workflow-configmap.yaml"
DEPLOYMENT = "base-apps/n8n/deployments.yaml"
KEYS = {"bug-investigator.json": WORKFLOW_ID, "bug-investigator-run.json": RUN_WORKFLOW_ID}
BEGIN, END = "# BEGIN bug-investigator POC", "# END bug-investigator POC"

HEADER = """\
---
# The n8n bug-investigator POC workflows (§T.152): a parent that picks a Taskboard bug issue
# every 2 minutes and a child that investigates it with n8n's AI Agent, beside
# kagent/bug-investigator, so their comments can be compared.
#
# !!! GENERATED FILE — DO NOT EDIT BY HAND !!!
# Sources:    scripts/n8n-bug-investigator/ (lib, nodes), base-apps/kagent/agent-templates/bug-investigator.yaml
# Regenerate: ./scripts/gen-n8n-bug-investigator-workflow.py --repo-root . --write
# CI fails if this file drifts (tests/n8n_bug_investigator/test_workflow.py).
#
# The n8n Deployment's import-workflows initContainer imports both from here (the block between
# `# BEGIN bug-investigator POC` and `# END bug-investigator POC`, also generated).
"""

REQUIRE = """\
const __cache = {};
function __require(name) {
  const key = String(name).replace(/\\.js$/, '');
  if (!__modules[key]) throw new Error(`this bundle has no module ${key}`);
  if (!__cache[key]) {
    const module = { exports: {} };
    __cache[key] = module;
    __modules[key](module, module.exports, __require);
  }
  return __cache[key].exports;
}
"""


class _Literal(str):
    pass


yaml.add_representer(_Literal, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style="|"))


# --------------------------------------------------------------------------------- bundles


def _requires(text: str, fn: str) -> list[str]:
    return re.findall(rf"\b{fn}\('\./([a-z0-9]+)'\)", text)


def _lib(repo: Path, name: str) -> str:
    return (repo / LIB / f"{name}.js").read_text()


def _closure(repo: Path, names) -> list[str]:
    seen, todo = set(), list(names)
    while todo:
        n = todo.pop()
        if n not in seen:
            seen.add(n)
            todo += _requires(_lib(repo, n), "require")
    return sorted(seen)


def _strip_comments(src: str) -> str:
    return "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("//")).strip("\n")


def bundle(modules, body: str, config: dict | None, repo: Path | None = None) -> str:
    """The jsCode of one Code node: the modules (and what they require), CONFIG, then the body."""
    repo = repo or Path(__file__).resolve().parents[1]
    names = _closure(repo, set(modules) | set(_requires(body, "__require")))
    parts = ["// Generated by scripts/gen-n8n-bug-investigator-workflow.py from scripts/n8n-bug-investigator/ (§T.152).",
             "const __modules = {"]
    for n in names:
        parts.append(f"  './{n}': function (module, exports, require) {{\n{_strip_comments(_lib(repo, n))}\n  }},")
    parts.append("};")
    text = "\n".join(parts) + "\n" + REQUIRE
    if config is not None:
        text += f"const CONFIG = {json.dumps(config, ensure_ascii=False)};\n"
    return text + body


# ------------------------------------------------------------------------------- workflows


def _node(wf_id: str, name: str, type_: str, pos, params: dict, **extra) -> dict:
    n = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{wf_id}/{name}")), "name": name, "type": type_,
         "typeVersion": TYPE_VERSIONS[type_], "position": list(pos), "parameters": params}
    n.update(extra)
    return n


def _code(repo: Path, wf_id: str, name: str, snippet: str, pos, config: dict) -> dict:
    body = (repo / NODES / snippet).read_text()
    return _node(wf_id, name, "n8n-nodes-base.code", pos,
                 {"mode": "runOnceForAllItems", "jsCode": bundle([], body, config, repo)})


def _main(*targets):
    return {"main": [[{"node": t, "type": "main", "index": 0}] if t else [] for t in targets]}


def _template(repo: Path) -> tuple[str, dict[str, list[str]]]:
    t = yaml.safe_load((repo / TEMPLATE).read_text())
    tools = {x["mcp"]["server"]["name"]: list(x["mcp"]["tools"]) for x in t["spec"]["tools"] if "mcp" in x}
    if set(tools) != set(MCP_NODES):
        raise ValueError(f"{TEMPLATE} binds {sorted(tools)}; the generator maps {sorted(MCP_NODES)}")
    return t["spec"]["systemPrompt"], tools


def parent_workflow(repo: Path, config: dict) -> dict:
    w = WORKFLOW_ID
    return {
        "id": w, "name": "Bug investigator (n8n POC): pick", "active": False,
        "settings": {"executionOrder": "v1", "executionTimeout": PARENT_TIMEOUT,
                     "saveDataSuccessExecution": "none", "saveDataErrorExecution": "all"},
        "nodes": [
            _node(w, "Every 2 minutes", "n8n-nodes-base.scheduleTrigger", (0, 0),
                  {"rule": {"interval": [{"field": "minutes", "minutesInterval": EVERY_MINUTES}]}}),
            _code(repo, w, "Pick issue", "pick.js", (220, 0), config),
            _node(w, "Investigate issue", "n8n-nodes-base.executeWorkflow", (440, 0),
                  {"source": "database", "workflowId": {"__rl": True, "mode": "id", "value": RUN_WORKFLOW_ID},
                   "workflowInputs": {"mappingMode": "defineBelow", "value": {}}, "mode": "each",
                   "options": {"waitForSubWorkflow": True}}),
        ],
        "connections": {"Every 2 minutes": _main("Pick issue"), "Pick issue": _main("Investigate issue")},
    }


def child_workflow(repo: Path, config: dict) -> dict:
    w = RUN_WORKFLOW_ID
    prompt, tools = _template(repo)
    gathered = {"conditions": {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 4},
        "conditions": [{"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{w}/Gathered?/0")), "leftValue": "={{ $json.failed }}",
                        "rightValue": "", "operator": {"type": "boolean", "operation": "false", "singleValue": True}}],
        "combinator": "and"}, "options": {}}
    mcp = [_node(w, node, "@n8n/n8n-nodes-langchain.mcpClientTool", (560 + 140 * i, 220),
                 {"endpointUrl": url, "serverTransport": "httpStreamable", "authentication": "none",
                  "include": "selected", "includeTools": tools[server], "options": {}})
           for i, (server, (node, url)) in enumerate(sorted(MCP_NODES.items()))]
    return {
        "id": w, "name": "Bug investigator (n8n POC): investigate", "active": False,
        "settings": {"executionOrder": "v1", "executionTimeout": CHILD_TIMEOUT,
                     "saveDataSuccessExecution": "all", "saveDataErrorExecution": "all"},
        "nodes": [
            _node(w, "Called by the picker", "n8n-nodes-base.executeWorkflowTrigger", (0, 0), {"inputSource": "passthrough"}),
            _code(repo, w, "Gather facts", "gather.js", (220, 0), config),
            _node(w, "Gathered?", "n8n-nodes-base.if", (440, 0), gathered),
            _node(w, "Investigate", "@n8n/n8n-nodes-langchain.agent", (660, 0),
                  {"promptType": "define", "text": "={{ $json.message }}",
                   "options": {"systemMessage": prompt, "maxIterations": MAX_ITERATIONS, "returnIntermediateSteps": True}},
                  onError="continueErrorOutput"),
            _node(w, "Claude Sonnet 5.5", "@n8n/n8n-nodes-langchain.lmChatAnthropic", (560, 220),
                  {"model": {"__rl": True, "mode": "id", "value": MODEL}, "options": {"maxTokensToSample": MAX_TOKENS, "promptCaching": PROMPT_CACHING}},
                  credentials={"anthropicApi": dict(ANTHROPIC_CREDENTIAL)}),
            *mcp,
            _code(repo, w, "Verify", "verify.js", (900, -100), config),
            _code(repo, w, "Render and post", "render-post.js", (1120, -100), config),
            _code(repo, w, "Agent failed", "agent-failed.js", (900, 100), config),
            _code(repo, w, "Finish", "finish.js", (1340, 0), config),
        ],
        "connections": {
            "Called by the picker": _main("Gather facts"),
            "Gather facts": _main("Gathered?"),
            "Gathered?": _main("Investigate", "Finish"),
            "Claude Sonnet 5.5": {"ai_languageModel": [[{"node": "Investigate", "type": "ai_languageModel", "index": 0}]]},
            **{node: {"ai_tool": [[{"node": "Investigate", "type": "ai_tool", "index": 0}]]} for node, _ in MCP_NODES.values()},
            "Investigate": _main("Verify", "Agent failed"),
            "Verify": _main("Render and post"),
            "Render and post": _main("Finish"),
            "Agent failed": _main("Finish"),
        },
    }


def build(repo: Path, active: bool = ACTIVE, not_before: str | None = NOT_BEFORE) -> dict[str, dict]:
    if active and not not_before:
        raise ValueError("ACTIVE needs NOT_BEFORE: issues opened before the POC was switched on are never picked")
    config = {**CONFIG, "notBefore": not_before}
    return {"bug-investigator.json": parent_workflow(repo, config), "bug-investigator-run.json": child_workflow(repo, config)}


# ---------------------------------------------------------------------------------- output


def render_configmap(repo: Path) -> str:
    data = {}
    for key, wf in build(repo).items():
        text = json.dumps(wf, indent=2, ensure_ascii=False) + "\n"
        if any(c in text for c in "\u2028\u2029\x85"):
            raise ValueError(f"{key}: a raw line-break character would break the YAML block")
        data[key] = _Literal(text)
    doc = {"apiVersion": "v1", "kind": "ConfigMap",
           "metadata": {"name": "n8n-workflow-bug-investigator", "namespace": "n8n", "labels": {"app": "n8n"}},
           "data": data}
    return HEADER + yaml.dump(doc, sort_keys=False, width=1_000_000, allow_unicode=True)


def checksum(configmap: str) -> str:
    data = [d for d in yaml.safe_load_all(configmap) if d][0]["data"]
    return hashlib.sha256("".join(data[k] for k in sorted(data)).encode()).hexdigest()[:16]


def init_block(indent: str) -> str:
    publish = [f'{indent}    n8n publish:workflow --id="$id" || exit 1'] if ACTIVE else []
    state = "imported and published" if ACTIVE else "imported, not published (ACTIVE = False)"
    lines = [
        f"{BEGIN} (generated by scripts/gen-n8n-bug-investigator-workflow.py, §T.152)",
        "# The n8n bug-investigator POC, the child first so it exists when the parent calls it. In a",
        "# subshell with `|| exit 1` per step: a failure is reported and skipped, so it can stop",
        f"# neither the workflows above nor the pod. State: {state}.",
        "(",
        "  for wf in \\",
        f"    bug-investigator-run:{RUN_WORKFLOW_ID} \\",
        f"    bug-investigator:{WORKFLOW_ID}; do",
        "    file=${wf%%:*}",
        "    id=${wf#*:}",
        '    n8n unpublish:workflow --id="$id" || echo "$id: not in the database yet (first import)"',
        '    n8n import:workflow --input="/workflows-bug-investigator/$file.json" || exit 1',
    ]
    lines = [indent + x for x in lines] + publish + [indent + x for x in [
        "  done",
        ') || echo "bug-investigator POC import failed; the other workflows are unaffected" >&2',
        END,
    ]]
    return "\n".join(lines)


def render_deployment(repo: Path, configmap: str) -> str:
    text = (repo / DEPLOYMENT).read_text()
    text, n = re.subn(r'(checksum/bug-investigator-workflow: )"[0-9a-f]*"', rf'\g<1>"{checksum(configmap)}"', text)
    if n != 1:
        raise SystemExit(f"{DEPLOYMENT}: expected one checksum/bug-investigator-workflow annotation, found {n}")
    m = re.search(rf"^([ \t]*){re.escape(BEGIN)}.*?^[ \t]*{re.escape(END)}[^\n]*$", text, re.M | re.S)
    if not m:
        raise SystemExit(f"{DEPLOYMENT}: no `{BEGIN}` … `{END}` block in the init script")
    return text[:m.start()] + init_block(m.group(1)) + text[m.end():]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the files")
    mode.add_argument("--check", action="store_true", help="exit 1 if the committed files are stale")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    repo = Path(args.repo_root).resolve()
    try:
        cm = render_configmap(repo)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    wanted = {OUT: cm, DEPLOYMENT: render_deployment(repo, cm)}
    size = len(cm.encode())
    if size > CONFIGMAP_BUDGET:
        print(f"error: {OUT} would be {size} bytes, over the {CONFIGMAP_BUDGET}-byte budget", file=sys.stderr)
        return 2
    stale = []
    for rel, want in wanted.items():
        path = repo / rel
        have = path.read_text() if path.exists() else ""
        if have == want:
            continue
        if args.check:
            sys.stdout.writelines(list(difflib.unified_diff(have.splitlines(True), want.splitlines(True),
                                                            fromfile=rel, tofile=rel + " (regenerated)"))[:200])
            stale.append(rel)
        else:
            path.write_text(want)
            print(f"wrote {rel}")
    if args.check:
        if stale:
            print(f"stale: {', '.join(stale)}; run scripts/gen-n8n-bug-investigator-workflow.py --repo-root . --write", file=sys.stderr)
            return 1
        print(f"{OUT} ({size} bytes) and the {DEPLOYMENT} wiring are in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
