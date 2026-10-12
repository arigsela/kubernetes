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
import sys
from pathlib import Path

import yaml

SOURCES = {"dispatch.py": "scripts/dispatch.py", "kagent_a2a.py": "scripts/kagent_a2a.py"}
OUT = "base-apps/dispatcher/code-configmap.yaml"

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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed manifest is stale")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    repo = Path(args.repo_root).resolve()
    want = render(repo)
    out = repo / OUT
    if args.check:
        have = out.read_text() if out.exists() else ""
        if have != want:
            sys.stdout.writelines(difflib.unified_diff(have.splitlines(True), want.splitlines(True),
                                                       fromfile=OUT, tofile=OUT + " (regenerated)"))
            print(f"{OUT} is stale; run scripts/gen-dispatcher-configmap.py", file=sys.stderr)
            return 1
        print(f"{OUT} in sync")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(want)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
