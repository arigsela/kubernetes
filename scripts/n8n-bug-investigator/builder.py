"""Build the n8n bug-investigator workflows from one settings dict (§T.152).

Used by scripts/gen-n8n-bug-investigator-workflow.py (this homelab's settings, written into a
ConfigMap) and by the handover package's build.py (anyone's settings, written as JSON files to
import into n8n). Standard library only, so the package runs it without installing anything.

Two workflows:
- parent: a Schedule trigger → "Pick issue" (Code) → "Investigate issue" (Execute Workflow, waits).
  Its successful runs are not saved, so idle runs leave nothing in n8n's database.
- child: "Called by the picker" → "Gather facts" → "Gathered?" → "Investigate" (AI Agent with the
  Anthropic model and one MCP Client Tool per tool server) → "Verify" → "Render and post" →
  "Finish"; the agent's error output → "Agent failed" → "Finish". Every run saved.

Each Code node's jsCode is a bundle: the lib/*.js modules its snippet (nodes/*.js) requires,
transitively, each wrapped in a module function with full-line comments left out, then
`const CONFIG = …` and the snippet.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

# Latest typeVersions in n8n 2.45.5 (tests/n8n_bug_investigator/fixtures/n8n-2.45.5-nodes.json).
TYPE_VERSIONS = {
    "n8n-nodes-base.scheduleTrigger": 1.4, "n8n-nodes-base.code": 2, "n8n-nodes-base.if": 2.4,
    "n8n-nodes-base.executeWorkflow": 1.4, "n8n-nodes-base.executeWorkflowTrigger": 1.3,
    "@n8n/n8n-nodes-langchain.agent": 3.1, "@n8n/n8n-nodes-langchain.lmChatAnthropic": 1.6,
    "@n8n/n8n-nodes-langchain.mcpClientTool": 1.4,
}

# The MCP Client Tool's authentication options and the n8n credential type each one uses
# (n8n 2.45.5, McpClientTool.node.js).
MCP_AUTH = {"none": None, "bearerAuth": "httpBearerAuth", "headerAuth": "httpHeaderAuth",
            "multipleHeadersAuth": "httpMultipleHeadersAuth"}

# __require resolves the bundled modules; __secret reads a token from an n8n Variable ($vars,
# n8n Cloud and licensed self-hosted) or, failing that, the environment ($env, self-hosted with
# N8N_BLOCK_ENV_ACCESS_IN_NODE=false). An unreadable source counts as unset.
PRELUDE = """\
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
function __secret(name) {
  try {
    if (typeof $vars !== 'undefined' && $vars && $vars[name]) return String($vars[name]);
  } catch (e) { /* no n8n Variables here */ }
  try {
    return $env[name] ? String($env[name]) : '';
  } catch (e) {
    return '';
  }
}
"""


def _requires(text: str, fn: str) -> list[str]:
    return re.findall(rf"\b{fn}\('\./([a-z0-9]+)'\)", text)


def _closure(lib_dir: Path, names) -> list[str]:
    seen, todo = set(), list(names)
    while todo:
        n = todo.pop()
        if n not in seen:
            seen.add(n)
            todo += _requires((lib_dir / f"{n}.js").read_text(), "require")
    return sorted(seen)


def _strip_comments(src: str) -> str:
    return "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("//")).strip("\n")


def bundle(lib_dir: Path, modules, body: str, config: dict | None, banner: str) -> str:
    """The jsCode of one Code node: the modules (and what they require), CONFIG, then the body."""
    names = _closure(lib_dir, set(modules) | set(_requires(body, "__require")))
    parts = [banner, "const __modules = {"]
    for n in names:
        parts.append(f"  './{n}': function (module, exports, require) {{\n{_strip_comments((lib_dir / f'{n}.js').read_text())}\n  }},")
    parts.append("};")
    text = "\n".join(parts) + "\n" + PRELUDE
    if config is not None:
        text += f"const CONFIG = {json.dumps(config, ensure_ascii=False)};\n"
    return text + body


def _node(wf_id: str, name: str, type_: str, pos, params: dict, **extra) -> dict:
    n = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{wf_id}/{name}")), "name": name, "type": type_,
         "typeVersion": TYPE_VERSIONS[type_], "position": list(pos), "parameters": params}
    n.update(extra)
    return n


def _credentials(kind: str, cred: dict | None) -> dict:
    """A node's `credentials` entry; none without an id (pick the credential in n8n after import)."""
    if not cred or not cred.get("id"):
        return {}
    return {"credentials": {kind: {"id": cred["id"], "name": cred.get("name") or cred["id"]}}}


def _mcp_params(t: dict) -> tuple[dict, dict]:
    auth = t.get("auth") or "none"
    if auth not in MCP_AUTH:
        raise ValueError(f"MCP server {t['node']}: auth must be one of {sorted(MCP_AUTH)}, not {auth!r}")
    params = {"endpointUrl": t["url"], "serverTransport": "httpStreamable", "authentication": auth}
    params.update({"include": "selected", "includeTools": list(t["tools"])} if t.get("tools") else {"include": "all"})
    params["options"] = {}
    return params, (_credentials(MCP_AUTH[auth], t.get("credential")) if MCP_AUTH[auth] else {})


def _main(*targets):
    return {"main": [[{"node": t, "type": "main", "index": 0}] if t else [] for t in targets]}


def build_workflows(s: dict, lib_dir: Path, nodes_dir: Path) -> dict[str, dict]:
    """The two workflows for settings `s`:

    ids {parent, child}, names {parent, child}, everyMinutes, timeouts {parent, child},
    model {name, nodeName, maxTokens, maxIterations, promptCaching, credential {id, name}},
    systemPrompt, mcp [{node, url, tools, auth, credential}], config (the Code nodes' CONFIG), banner.
    A credential is {id, name}; without an id the node has none and is set in n8n after import.
    An MCP server without tools offers the agent all of its tools.
    Returns {"bug-investigator.json": parent, "bug-investigator-run.json": child}.
    """
    pid, cid = s["ids"]["parent"], s["ids"]["child"]
    m = s["model"]
    cfg = s["config"]

    def code(wf_id, name, snippet, pos):
        body = (nodes_dir / snippet).read_text()
        return _node(wf_id, name, "n8n-nodes-base.code", pos,
                     {"mode": "runOnceForAllItems", "jsCode": bundle(lib_dir, [], body, cfg, s["banner"])})

    trigger = f"Every {s['everyMinutes']} minutes"
    parent = {
        "id": pid, "name": s["names"]["parent"], "active": False,
        "settings": {"executionOrder": "v1", "executionTimeout": s["timeouts"]["parent"],
                     "saveDataSuccessExecution": "none", "saveDataErrorExecution": "all"},
        "nodes": [
            _node(pid, trigger, "n8n-nodes-base.scheduleTrigger", (0, 0),
                  {"rule": {"interval": [{"field": "minutes", "minutesInterval": s["everyMinutes"]}]}}),
            code(pid, "Pick issue", "pick.js", (220, 0)),
            _node(pid, "Investigate issue", "n8n-nodes-base.executeWorkflow", (440, 0),
                  {"source": "database", "workflowId": {"__rl": True, "mode": "id", "value": cid},
                   "workflowInputs": {"mappingMode": "defineBelow", "value": {}}, "mode": "each",
                   "options": {"waitForSubWorkflow": True}}),
        ],
        "connections": {trigger: _main("Pick issue"), "Pick issue": _main("Investigate issue")},
    }

    gathered = {"conditions": {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 4},
        "conditions": [{"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{cid}/Gathered?/0")), "leftValue": "={{ $json.failed }}",
                        "rightValue": "", "operator": {"type": "boolean", "operation": "false", "singleValue": True}}],
        "combinator": "and"}, "options": {}}
    mcp = []
    for i, t in enumerate(s["mcp"]):
        params, creds = _mcp_params(t)
        mcp.append(_node(cid, t["node"], "@n8n/n8n-nodes-langchain.mcpClientTool", (560 + 140 * i, 220), params, **creds))
    options = {"maxTokensToSample": m["maxTokens"]}
    if m.get("promptCaching") and m["promptCaching"] != "disabled":
        options["promptCaching"] = m["promptCaching"]
    child = {
        "id": cid, "name": s["names"]["child"], "active": False,
        "settings": {"executionOrder": "v1", "executionTimeout": s["timeouts"]["child"],
                     "saveDataSuccessExecution": "all", "saveDataErrorExecution": "all"},
        "nodes": [
            _node(cid, "Called by the picker", "n8n-nodes-base.executeWorkflowTrigger", (0, 0), {"inputSource": "passthrough"}),
            code(cid, "Gather facts", "gather.js", (220, 0)),
            _node(cid, "Gathered?", "n8n-nodes-base.if", (440, 0), gathered),
            _node(cid, "Investigate", "@n8n/n8n-nodes-langchain.agent", (660, 0),
                  {"promptType": "define", "text": "={{ $json.message }}",
                   "options": {"systemMessage": s["systemPrompt"], "maxIterations": m["maxIterations"], "returnIntermediateSteps": True}},
                  onError="continueErrorOutput"),
            _node(cid, m["nodeName"], "@n8n/n8n-nodes-langchain.lmChatAnthropic", (560, 220),
                  {"model": {"__rl": True, "mode": "id", "value": m["name"]}, "options": options},
                  **_credentials("anthropicApi", m.get("credential"))),
            *mcp,
            code(cid, "Verify", "verify.js", (900, -100)),
            code(cid, "Render and post", "render-post.js", (1120, -100)),
            code(cid, "Agent failed", "agent-failed.js", (900, 100)),
            code(cid, "Finish", "finish.js", (1340, 0)),
        ],
        "connections": {
            "Called by the picker": _main("Gather facts"),
            "Gather facts": _main("Gathered?"),
            "Gathered?": _main("Investigate", "Finish"),
            m["nodeName"]: {"ai_languageModel": [[{"node": "Investigate", "type": "ai_languageModel", "index": 0}]]},
            **{t["node"]: {"ai_tool": [[{"node": "Investigate", "type": "ai_tool", "index": 0}]]} for t in s["mcp"]},
            "Investigate": _main("Verify", "Agent failed"),
            "Verify": _main("Render and post"),
            "Render and post": _main("Finish"),
            "Agent failed": _main("Finish"),
        },
    }
    return {"bug-investigator.json": parent, "bug-investigator-run.json": child}
