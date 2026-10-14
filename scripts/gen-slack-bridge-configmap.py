#!/usr/bin/env python3
"""Generate base-apps/slack-bridge/code-configmap.yaml from the real source files.

    ./scripts/gen-slack-bridge-configmap.py            # regenerate
    ./scripts/gen-slack-bridge-configmap.py --check    # CI: fail if stale

Same pattern and reasoning as gen-dispatcher-configmap.py: the bridge runs
scripts/slack_bridge.py in the cluster; an image build per edit is a drift trap, a hand-copied
ConfigMap a worse one. The ConfigMap is generated from the tested file (and the A2A client it
imports), the Deployment's checksum/code annotation is set from it, and CI fails if either
falls behind.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

SOURCES = {"slack_bridge.py": "scripts/slack_bridge.py", "kagent_a2a.py": "scripts/kagent_a2a.py"}
OUT = "base-apps/slack-bridge/code-configmap.yaml"
DEPLOYMENT = "base-apps/slack-bridge/deployment.yaml"

HEADER = """\
---
# Slack bridge code (§T.120).
#
# !!! GENERATED FILE — DO NOT EDIT BY HAND !!!
# Sources:    scripts/slack_bridge.py
#             scripts/kagent_a2a.py
# Regenerate: ./scripts/gen-slack-bridge-configmap.py
# CI fails if this file drifts from either source (tests/slack_bridge/test_gen_configmap.py).
#
# The Deployment (deployment.yaml) mounts this at /opt/slack-bridge and runs slack_bridge.py
# with python:3.12-slim and no pip install: both files are standard-library only.
"""


class _Literal(str):
    pass


def _literal(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.add_representer(_Literal, _literal)


def render(repo: Path) -> str:
    data = {key: _Literal((repo / path).read_text()) for key, path in SOURCES.items()}
    doc = {"apiVersion": "v1", "kind": "ConfigMap",
           "metadata": {"name": "slack-bridge-code", "namespace": "slack-bridge", "labels": {"app": "slack-bridge"}},
           "data": data}
    return HEADER + yaml.dump(doc, sort_keys=False, width=10_000, allow_unicode=True)


def configmap_checksum(text: str) -> str:
    """sha256 of the ConfigMap's data, the formula tests/slack_bridge/test_manifests.py checks."""
    doc = [d for d in yaml.safe_load_all(text) if d and d.get("kind") == "ConfigMap"][0]
    return hashlib.sha256(json.dumps(doc["data"], sort_keys=True).encode()).hexdigest()


def render_deployment(repo: Path, code_configmap: str) -> str:
    text = (repo / DEPLOYMENT).read_text()
    value = configmap_checksum(code_configmap)
    text, count = re.subn(r'(checksum/code: )"[0-9a-f]*"', rf'\g<1>"{value}"', text)
    if count != 1:
        raise SystemExit(f"{DEPLOYMENT}: expected exactly one checksum/code annotation, found {count}")
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed manifests are stale")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    repo = Path(args.repo_root).resolve()
    wanted = {OUT: render(repo)}
    wanted[DEPLOYMENT] = render_deployment(repo, wanted[OUT])
    stale = []
    for rel, want in wanted.items():
        path = repo / rel
        have = path.read_text() if path.exists() else ""
        if have == want:
            continue
        if args.check:
            sys.stdout.writelines(difflib.unified_diff(have.splitlines(True), want.splitlines(True),
                                                       fromfile=rel, tofile=rel + " (regenerated)"))
            stale.append(rel)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(want)
            print(f"wrote {rel}")
    if args.check:
        if stale:
            print(f"stale: {', '.join(stale)}; run scripts/gen-slack-bridge-configmap.py", file=sys.stderr)
            return 1
        print(f"{OUT} and {DEPLOYMENT} checksum in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
