# tests/n8n_bug_investigator/conftest.py
"""Fixtures for the n8n bug-investigator POC (§T.152).

`core` is the Python bug-investigator package, loaded from the data of
base-apps/kagent/bug-investigator-core-configmap.yaml (what kagent's trigger runs), so the
JavaScript port is checked against the exact code it mirrors. `js(...)` calls an export of
scripts/n8n-bug-investigator/lib/<module>.js through run.js under node. A missing node fails
the suite: a parity suite that skips is a parity suite that never ran.
"""
from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CORE_CONFIGMAP = REPO / "base-apps/kagent/bug-investigator-core-configmap.yaml"
CORE_MODULES = ("util", "scope", "grafana", "tickets", "trail", "gather", "verify", "render")
FROZEN_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)


class JsError(Exception):
    """An exception thrown by the JavaScript under test (name and message kept)."""

    def __init__(self, name: str, message: str, extra: dict | None = None):
        super().__init__(f"{name}: {message}")
        self.name = name
        self.message = message
        self.extra = extra or {}


@pytest.fixture(scope="session")
def core(tmp_path_factory):
    data = yaml.safe_load(CORE_CONFIGMAP.read_text())["data"]
    pkg = tmp_path_factory.mktemp("core") / "src" / "bug_investigator"
    pkg.mkdir(parents=True)
    for name, text in data.items():
        (pkg / name).write_text(text)
    sys.path.insert(0, str(pkg.parent))
    mods = {m: importlib.import_module(f"bug_investigator.{m}") for m in CORE_MODULES}
    return SimpleNamespace(**mods)


@pytest.fixture
def frozen_now(core, monkeypatch):
    """A fixed `now`, patched into every core module that reads the clock (they import utc_now by name)."""
    for mod in vars(core).values():
        if hasattr(mod, "utc_now"):
            monkeypatch.setattr(mod, "utc_now", lambda: FROZEN_NOW)
    return FROZEN_NOW


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.fail("node (22 or newer) is required for tests/n8n_bug_investigator; install it, the suite never skips")
    major = int(subprocess.run([node, "--version"], capture_output=True, text=True, check=True).stdout.strip().lstrip("v").split(".")[0])
    if major < 22:
        pytest.fail(f"node {major} found; tests/n8n_bug_investigator needs node 22 or newer")
    return node


@pytest.fixture(scope="session")
def js():
    """js(module, fn, *args, ctx=None, full=False) → the JSON result of lib/<module>.js's export `fn`.

    `ctx` is {"fake": <name>, "spec": {...}}: run.js builds that fake and passes it as the first
    argument. With full=True the whole reply comes back, including the fake's recorded `calls`.
    A JavaScript exception is raised here as JsError.
    """
    node = _node()

    def call(module, fn, *args, ctx=None, full=False):
        req = {"module": module, "fn": fn, "args": list(args), "ctx": ctx}
        r = subprocess.run([node, str(HERE / "run.js")], input=json.dumps(req), capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or not r.stdout.strip():
            raise RuntimeError(f"run.js failed (exit {r.returncode}): {r.stderr.strip()[-2000:]}")
        reply = json.loads(r.stdout)
        if not reply["ok"]:
            e = reply["error"]
            raise JsError(e.get("name", "Error"), e.get("message", ""), e)
        return reply if full else reply["value"]

    return call
