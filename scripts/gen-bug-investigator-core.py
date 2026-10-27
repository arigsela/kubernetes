#!/usr/bin/env python3
"""Generate base-apps/kagent/bug-investigator-core-configmap.yaml from an arigsela/claude-agents commit.

    ./scripts/gen-bug-investigator-core.py --source ../claude-agents --commit "$(git -C ../claude-agents rev-parse HEAD)"
    ./scripts/gen-bug-investigator-core.py --source ../claude-agents --commit <sha> --check   # stale? exit 1

The bug investigator's shared core (the scope check, gather, verify, render, the telemetry adapter
and the stdio MCP server) lives in arigsela/claude-agents `bug-investigator/`, where the GitHub
Action uses it too. Here it runs like alert-demo: python:3.12-slim, standard library only, no image
build, the files from one ConfigMap. This script copies every
`bug-investigator/src/bug_investigator/*.py` plus `bug-investigator/prompt.md` and
`bug-investigator/findings.schema.json` into ConfigMap `bug-investigator-core` (keys are the
basenames), and writes the ConfigMap's checksum into every consumer listed in CONSUMERS (pod
template annotation `checksum/core`), so a core change rolls those pods.

The mount contract (every consumer): the package at /core/src/bug_investigator/ and the two data
files at /core/prompt.md and /core/findings.schema.json, with PYTHONPATH=/core/src. The package's
own code finds the data files two levels above itself. Either mount the ConfigMap whole at
/core/src/bug_investigator (the two data files appear there too, harmlessly) plus two subPath mounts
for /core/prompt.md and /core/findings.schema.json, or list `items` key by key.

When <source> is a git checkout, the commit must be its HEAD and the copied files committed, so the
annotation never names code the pods do not run. A `git archive` export (no .git) is accepted as is.
CI cannot regenerate (it has no claude-agents checkout at that commit), so CI checks the committed
files for consistency instead (tests/taskboard/test_bug_investigator_core.py).
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

OUT = "base-apps/kagent/bug-investigator-core-configmap.yaml"
# Every manifest whose pod template mounts the core and must roll when it changes. Each needs at
# least one `checksum/core: "<hex>"` annotation. A CronJob's pods start fresh every run, so a
# CronJob may stay out of this list.
CONSUMERS = ["base-apps/kagent/taskboard-telemetry-mcp.yaml"]
SUBDIR = "bug-investigator"
PACKAGE = "bug-investigator/src/bug_investigator"
DATA_FILES = ["bug-investigator/prompt.md", "bug-investigator/findings.schema.json"]
SOURCE_REPO = "arigsela/claude-agents"
COMMIT_ANNOTATION = "bug-investigator.arigsela.com/source-commit"
REPO_ANNOTATION = "bug-investigator.arigsela.com/source-repo"

MAX_FILE_BYTES = 200 * 1024
# The kagent-secrets Argo app (base-apps/kagent-secrets.yaml) applies server-side, so no
# last-applied-configuration annotation copies the data (the 256 KiB annotation cap that limits
# gen-taskboard-configmap.py does not apply). The cap is the 1 MiB object limit, with headroom.
MAX_TOTAL_BYTES = 900 * 1024
CONFIGMAP_KEY = re.compile(r"^[-._a-zA-Z0-9]+$")
SHA = re.compile(r"^[0-9a-f]{40}$")

HEADER = """\
---
# The bug investigator's shared core (scope check, gather, verify, render, telemetry, MCP server).
#
# !!! GENERATED FILE — DO NOT EDIT BY HAND !!!
# Generator:  scripts/gen-bug-investigator-core.py
# Source:     {repo}@{commit}
#             ({package}/*.py, {data})
# Regenerate: ./scripts/gen-bug-investigator-core.py --source <claude-agents checkout> --commit <sha>
# tests/taskboard/test_bug_investigator_core.py fails if this file and the consumers' checksum/core disagree.
#
# Mount contract: the package at /core/src/bug_investigator/, prompt.md and findings.schema.json at
# /core/, PYTHONPATH=/core/src. Standard library only, on python:3.12-slim. Consumers: the
# taskboard-telemetry MCP server (taskboard-telemetry-mcp.yaml) and the bug-investigator trigger.
"""


class _Literal(str):
    pass


def _literal(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.add_representer(_Literal, _literal)


def _git(source: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(source), *args], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return result.stdout.rstrip("\n") if result.returncode == 0 else None


def check_source_commit(source: Path, commit: str) -> None:
    """A git checkout must be at the commit and have the copied files committed."""
    if not (source / ".git").exists():
        return
    head = _git(source, "rev-parse", "HEAD")
    if head is None:
        raise SystemExit(f"{source}: git rev-parse HEAD failed (no commits yet?)")
    if head != commit:
        raise SystemExit(f"{source} is at {head}, not --commit {commit}; check out the commit first")
    status = _git(source, "status", "--porcelain", "--untracked-files=all", "--", PACKAGE, *DATA_FILES)
    if status is None:
        raise SystemExit(f"{source}: git status failed")
    dirty = [line for line in status.splitlines() if not line[3:].endswith((".pyc", "/"))
             and "__pycache__" not in line]
    if dirty:
        raise SystemExit(f"{source} has uncommitted changes to the core files:\n" + "\n".join(dirty))


def collect(source: Path) -> dict[str, str]:
    """ConfigMap data: every <PACKAGE>/*.py plus the two data files, by basename."""
    package = source / PACKAGE
    if not package.is_dir():
        raise SystemExit(f"{package}: not a directory")
    paths = sorted(p for p in package.glob("*.py") if p.is_file())
    if not any(p.name == "__init__.py" for p in paths):
        raise SystemExit(f"{package}: no __init__.py")
    for rel in DATA_FILES:
        path = source / rel
        if not path.is_file():
            raise SystemExit(f"{path}: missing")
        paths.append(path)
    data, total = {}, 0
    for path in paths:
        if path.name in data:
            raise SystemExit(f"{path}: two core files are both key {path.name!r}")
        if not CONFIGMAP_KEY.match(path.name):
            raise SystemExit(f"{path}: {path.name!r} is not a valid ConfigMap key")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise SystemExit(f"{path}: {size} bytes, over the {MAX_FILE_BYTES}-byte limit per file")
        total += size
        try:
            data[path.name] = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raise SystemExit(f"{path}: not UTF-8 text (the ConfigMap holds text files only)") from None
    if total > MAX_TOTAL_BYTES:
        raise SystemExit(f"{total} bytes in all, over {MAX_TOTAL_BYTES} (Argo CD's last-applied annotation)")
    return dict(sorted(data.items()))


def _why_not_block(text: str) -> str:
    for number, line in enumerate(text.split("\n"), 1):
        if line != line.rstrip(" "):
            return f"line {number} ends in a space"
        if "\t" in line:
            return f"line {number} has a tab"
        if any(ord(ch) < 32 or ch in "\x7f﻿" for ch in line):
            return f"line {number} has a control character (CR, BOM, ...)"
    return "PyYAML would not write it as a block literal"


def render(data: dict[str, str], commit: str) -> str:
    doc = {"apiVersion": "v1", "kind": "ConfigMap",
           "metadata": {"name": "bug-investigator-core", "namespace": "kagent",
                        "labels": {"app.kubernetes.io/part-of": "kagent",
                                   "app.kubernetes.io/name": "bug-investigator-core"},
                        "annotations": {COMMIT_ANNOTATION: commit, REPO_ANNOTATION: SOURCE_REPO}},
           "data": {key: _Literal(value) for key, value in data.items()}}
    body = yaml.dump(doc, sort_keys=False, width=10_000, allow_unicode=True)
    # Every non-empty file must come out as a readable block literal with no trailing whitespace
    # (yamllint's trailing-spaces is an error); PyYAML silently double-quotes what it cannot.
    for key, value in data.items():
        if value and not re.search(rf"^  {re.escape(key)}: \|[-+0-9]*$", body, re.M):
            raise SystemExit(f"{key}: {_why_not_block(value)}; fix it in {SOURCE_REPO}")
    text = HEADER.format(repo=SOURCE_REPO, commit=commit, package=PACKAGE,
                         data=", ".join(DATA_FILES)) + body
    bad = [n for n, line in enumerate(text.splitlines(), 1) if line != line.rstrip()]
    if bad:
        raise SystemExit(f"{OUT}: trailing whitespace on lines {bad[:5]}")
    return text


def configmap_checksum(text: str) -> str:
    """sha256 of a ConfigMap's data as sorted JSON (scripts/gen-alert-demo-configmap.py's formula)."""
    doc = [d for d in yaml.safe_load_all(text) if d and d.get("kind") == "ConfigMap"][0]
    return hashlib.sha256(json.dumps(doc["data"], sort_keys=True).encode()).hexdigest()


def render_consumer(rel: str, text: str, core_configmap: str) -> str:
    """A consumer manifest with every checksum/core annotation set."""
    value = configmap_checksum(core_configmap)
    text, count = re.subn(r'(checksum/core: )"[0-9a-f]*"', rf'\g<1>"{value}"', text)
    if count < 1:
        raise SystemExit(f"{rel}: no checksum/core annotation to set")
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="an arigsela/claude-agents checkout (or git archive export)")
    ap.add_argument("--commit", required=True, help="the full commit SHA the source is at")
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed manifests are stale")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    repo = Path(args.repo_root).resolve()
    source = Path(args.source).resolve()
    if not SHA.match(args.commit):
        raise SystemExit(f"--commit must be a full 40-character lowercase SHA, got {args.commit!r}")
    check_source_commit(source, args.commit)
    wanted = {OUT: render(collect(source), args.commit)}
    for rel in CONSUMERS:
        wanted[rel] = render_consumer(rel, (repo / rel).read_text(), wanted[OUT])
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
            print(f"stale for {SOURCE_REPO}@{args.commit}: {', '.join(stale)}; "
                  "run scripts/gen-bug-investigator-core.py without --check", file=sys.stderr)
            return 1
        print(f"{OUT} and the consumers match {SOURCE_REPO}@{args.commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
