#!/usr/bin/env python3
"""Generate base-apps/alert-demo/code-configmap.yaml from scripts/alert_demo.py.

    ./scripts/gen-alert-demo-configmap.py            # regenerate
    ./scripts/gen-alert-demo-configmap.py --check    # CI: fail if stale

The dispatcher's pattern (gen-dispatcher-configmap.py): the Deployment runs the one tested file
from a ConfigMap with python:3.12-slim, and this generator keeps the ConfigMap and the pod's
checksum/code annotation in step with it, so a code change rolls the pod.
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

SOURCES = {"alert_demo.py": "scripts/alert_demo.py"}
OUT = "base-apps/alert-demo/code-configmap.yaml"
DEPLOYMENT = "base-apps/alert-demo/deployment.yaml"

HEADER = """\
---
# alert-demo code (the hk demo's workload, §T.139).
#
# !!! GENERATED FILE — DO NOT EDIT BY HAND !!!
# Source:     scripts/alert_demo.py
# Regenerate: ./scripts/gen-alert-demo-configmap.py
# CI fails if this file drifts from the source (tests/alert_demo/test_gen_configmap.py).
#
# The Deployment (deployment.yaml) mounts this at /opt/alert-demo and runs alert_demo.py with
# python:3.12-slim and no pip install: the file is standard-library only.
"""


class _Literal(str):
    pass


def _literal(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.add_representer(_Literal, _literal)


def render(repo: Path) -> str:
    data = {key: _Literal((repo / path).read_text()) for key, path in SOURCES.items()}
    doc = {"apiVersion": "v1", "kind": "ConfigMap",
           "metadata": {"name": "alert-demo-code", "namespace": "alert-demo", "labels": {"app": "alert-demo"}},
           "data": data}
    return HEADER + yaml.dump(doc, sort_keys=False, width=10_000, allow_unicode=True)


def configmap_checksum(text: str) -> str:
    """sha256 of a ConfigMap's data, the same formula tests/alert_demo/test_manifests.py checks."""
    doc = [d for d in yaml.safe_load_all(text) if d and d.get("kind") == "ConfigMap"][0]
    return hashlib.sha256(json.dumps(doc["data"], sort_keys=True).encode()).hexdigest()


def render_deployment(repo: Path, code_configmap: str) -> str:
    """deployment.yaml with its checksum/code annotation set (the pod reads the code once at start)."""
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
            print(f"stale: {', '.join(stale)}; run scripts/gen-alert-demo-configmap.py", file=sys.stderr)
            return 1
        print(f"{OUT} and {DEPLOYMENT} checksum in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
