"""The release gate's one file: which agents the eval gate holds back from automations (V99).

The eval gate (scripts/eval_gate.py) writes `release.json` into ConfigMap `eval-release` in the
`dispatcher` and `slack-bridge` namespaces after every scored run. Those pods mount it (optional)
and read it here before opening a session; neither talks to the API server, by design, so the
file is the whole interface. Shape:

    {"version": 1, "written_at": "<iso8601>", "revision": "<git sha or null>",
     "agents": {"<agent>": {"held": true|false, "hash": "<surface hash>",
                            "reason": "<text>", "scored_at": "<iso8601>"}}}

`held` is true when the agent's last scored run was a regression (a hard fail or a pass rate
below its baseline). An agent absent from the file, or a file that is absent or unreadable, is
UNKNOWN: the caller proceeds and logs it (a fresh cluster must not fall silent); only a recorded
regression holds. Humans are never gated: ask-hk and the UI do not read this file.
"""
from __future__ import annotations

import json
from pathlib import Path

VERSION = 1
DEFAULT_PATH = "/etc/eval-release/release.json"
CONFIGMAP = "eval-release"
KEY = "release.json"
CONSUMER_NAMESPACES = ("dispatcher", "slack-bridge")


def render(agents: dict, *, revision: str | None, written_at: str) -> str:
    """The file the eval gate writes. `agents` maps name -> {held, hash, reason, scored_at}."""
    return json.dumps({"version": VERSION, "written_at": written_at, "revision": revision,
                       "agents": {k: agents[k] for k in sorted(agents)}}, sort_keys=False, indent=1)


def load(path: str | Path = DEFAULT_PATH) -> dict | None:
    """The parsed file, or None when it is absent or unreadable (unknown, never a hold)."""
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("version") != VERSION or not isinstance(doc.get("agents"), dict):
        return None
    return doc


def hold(agent: str, doc: dict | None) -> str | None:
    """The reason `agent` is held, or None (released or unknown)."""
    if not doc:
        return None
    entry = doc["agents"].get(agent)
    if not isinstance(entry, dict) or not entry.get("held"):
        return None
    return entry.get("reason") or "eval regression"
