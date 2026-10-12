#!/usr/bin/env python3
"""Generate base-apps/dispatcher/code-configmap.yaml from the real source files.

    ./scripts/gen-dispatcher-configmap.py            # regenerate
    ./scripts/gen-dispatcher-configmap.py --check    # CI: fail if stale

Same pattern and reasoning as gen-agent-audit-cronjob.py: the dispatcher has to run
scripts/dispatch.py in the cluster; an image build per edit is a drift trap, a hand-copied
ConfigMap is a worse one. So the ConfigMap is generated from the one tested file (and the
A2A client it imports), and CI fails if the committed manifest falls behind.
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

SOURCES = {"dispatch.py": "scripts/dispatch.py", "kagent_a2a.py": "scripts/kagent_a2a.py"}
OUT = "base-apps/dispatcher/code-configmap.yaml"
TABLE = "base-apps/dispatcher/dispatch-table.yaml"
DEPLOYMENT = "base-apps/dispatcher/deployment.yaml"

HEADER = """\
---
# Dispatcher code (ADP path 1, dispatch work).
#
# !!! GENERATED FILE — DO NOT EDIT BY HAND !!!
# Sources:    scripts/dispatch.py
#             scripts/kagent_a2a.py
# Regenerate: ./scripts/gen-dispatcher-configmap.py
# CI fails if this file drifts from either source (tests/dispatch/test_gen_configmap.py).
#
# The Deployment (deployment.yaml) mounts this at /opt/dispatch and runs dispatch.py with
# python:3.12-slim and no pip install: both files are standard-library only.
"""


class _Literal(str):
    pass


def _literal(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.add_representer(_Literal, _literal)


def render(repo: Path) -> str:
    data = {key: _Literal((repo / path).read_text()) for key, path in SOURCES.items()}
    doc = {"apiVersion": "v1", "kind": "ConfigMap",
           "metadata": {"name": "dispatcher-code", "namespace": "dispatcher", "labels": {"app": "dispatcher"}},
           "data": data}
    return HEADER + yaml.dump(doc, sort_keys=False, width=10_000, allow_unicode=True)


def configmap_checksum(text: str) -> str:
    """sha256 of a ConfigMap's data, the same formula tests/dispatch/test_manifests.py checks."""
    doc = [d for d in yaml.safe_load_all(text) if d and d.get("kind") == "ConfigMap"][0]
    return hashlib.sha256(json.dumps(doc["data"], sort_keys=True).encode()).hexdigest()


def render_deployment(repo: Path, code_configmap: str) -> str:
    """deployment.yaml with its checksum/table and checksum/code annotations set.

    The pod reads both ConfigMaps once at start; Argo CD updates a ConfigMap in place and
    nothing restarts the pod. A changed checksum changes the pod template, so the sync rolls
    the pod (Grafana's checksum/alerting pattern)."""
    text = (repo / DEPLOYMENT).read_text()
    values = {"checksum/table": configmap_checksum((repo / TABLE).read_text()),
              "checksum/code": configmap_checksum(code_configmap)}
    for key, value in values.items():
        text, count = re.subn(rf'({re.escape(key)}: )"[0-9a-f]*"', rf'\g<1>"{value}"', text)
        if count != 1:
            raise SystemExit(f"{DEPLOYMENT}: expected exactly one {key} annotation, found {count}")
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
            print(f"stale: {', '.join(stale)}; run scripts/gen-dispatcher-configmap.py", file=sys.stderr)
            return 1
        print(f"{OUT} and {DEPLOYMENT} checksums in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
