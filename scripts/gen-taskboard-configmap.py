#!/usr/bin/env python3
"""Generate base-apps/taskboard/code-configmap.yaml (and the browser's copy) from an arigsela/taskboard commit.

    ./scripts/gen-taskboard-configmap.py --source ../taskboard --commit "$(git -C ../taskboard rev-parse HEAD)"
    ./scripts/gen-taskboard-configmap.py --source ../taskboard --commit <sha> --check   # stale? exit 1

Taskboard (§T.145) is the bug investigator's target app. Its code lives in the private repo
arigsela/taskboard and runs here like alert-demo: python:3.12-slim, no image build, the files from a
ConfigMap. This script copies every file in <source>/taskboard/ (flat: the server, its sibling
modules, the page) plus <source>/traffic/simulate.py into the ConfigMap, and writes in
base-apps/taskboard/deployment.yaml:

- both Deployments' checksum/code (the alert-demo formula), so a code change rolls both pods;
- the app's taskboard.arigsela.com/source-commit, which the app reports as TASKBOARD_COMMIT.

It also writes the taskboard-browser tool server's copy of the same code (§T.153), so the browser
replays exactly what is live: base-apps/kagent/taskboard-browser-code-configmap.yaml (ConfigMap
kagent/taskboard-browser-code: the same data plus key .source-commit = the commit, which the server
reports with every result; a pod mounts ConfigMaps from its own namespace only), and in
base-apps/kagent/taskboard-browser-mcp.yaml that Deployment's checksum/code (over the copy's data,
.source-commit included, so a commit-only deploy rolls it too) and its source-commit annotation.

The commit that changes those annotations is the deploy: its merge time is when the code went live.
Both Deployment files are edited in place (by regex) so their comments stay. When <source> is a git
checkout, the commit must be its HEAD and the copied files must be committed, so the annotation
never names code the pod does not run.

CI cannot read the private repo, so CI checks the committed files for consistency instead
(tests/taskboard/test_gen_configmap.py): each checksum matches its ConfigMap's data, the browser's
copy matches the app's ConfigMap, and the commit annotations agree.
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

OUT = "base-apps/taskboard/code-configmap.yaml"
DEPLOYMENT = "base-apps/taskboard/deployment.yaml"
APP_DIR = "taskboard"
EXTRA = "traffic/simulate.py"
SOURCE_REPO = "arigsela/taskboard"
COMMIT_ANNOTATION = "taskboard.arigsela.com/source-commit"
REPO_ANNOTATION = "taskboard.arigsela.com/source-repo"
DEPLOYMENTS = 2   # taskboard and taskboard-traffic both mount the ConfigMap
BROWSER_OUT = "base-apps/kagent/taskboard-browser-code-configmap.yaml"
BROWSER_DEPLOYMENT = "base-apps/kagent/taskboard-browser-mcp.yaml"
SOURCE_COMMIT_KEY = ".source-commit"   # a dotfile in the mount, never a file collect() copies

MAX_FILE_BYTES = 200 * 1024
# Argo CD applies client-side, which copies the whole object into the last-applied-configuration
# annotation, and all of an object's annotations together must stay under 256 KiB.
MAX_TOTAL_BYTES = 240 * 1024
# Left out of the copy: editor and interpreter litter, never part of a commit.
IGNORED = re.compile(r"^(\..*|__pycache__|.*\.pyc)$")
CONFIGMAP_KEY = re.compile(r"^[-._a-zA-Z0-9]+$")
SHA = re.compile(r"^[0-9a-f]{40}$")

HEADER = """\
---
# Taskboard code (the bug investigator's target app, §T.145).
#
# !!! GENERATED FILE — DO NOT EDIT BY HAND !!!
# Generator:  scripts/gen-taskboard-configmap.py
# Source:     {repo}@{commit}
#             ({app_dir}/* and {extra})
# Regenerate: ./scripts/gen-taskboard-configmap.py --source <taskboard checkout> --commit <sha>
# tests/taskboard/ fails if this file and deployment.yaml's checksum/code or source-commit disagree.
#
# Both Deployments (deployment.yaml) mount this at /opt/taskboard with python:3.12-slim and no pip
# install: the app is standard-library only. The app runs server.py, the traffic one simulate.py.
"""

BROWSER_HEADER = """\
---
# Taskboard code for the taskboard-browser tool server (§T.153): the live code it replays UI steps on.
#
# !!! GENERATED FILE — DO NOT EDIT BY HAND !!!
# Generator:  scripts/gen-taskboard-configmap.py
# Source:     {repo}@{commit}
#             ({app_dir}/* and {extra}, plus .source-commit)
# Regenerate: ./scripts/gen-taskboard-configmap.py --source <taskboard checkout> --commit <sha>
# tests/taskboard/ fails if this file differs from base-apps/taskboard/code-configmap.yaml (apart
# from .source-commit) or taskboard-browser-mcp.yaml's checksum/code or source-commit disagree.
#
# Written in the same commit as the app's ConfigMap, so it is the deployed code. A copy because a
# pod mounts ConfigMaps from its own namespace only. taskboard-browser-mcp.yaml mounts it read-only
# at /taskboard; each replay_ui_steps call starts server.py from it on a fresh database.
# .source-commit is the commit, which the server reports with every result.
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
    status = _git(source, "status", "--porcelain", "--untracked-files=all", "--", APP_DIR, EXTRA)
    if status is None:
        raise SystemExit(f"{source}: git status failed")
    dirty = [line for line in status.splitlines()
             if not any(IGNORED.match(part) for part in Path(line[3:]).parts)]
    if dirty:
        raise SystemExit(f"{source} has uncommitted changes to the deployed files:\n" + "\n".join(dirty))


def collect(source: Path) -> dict[str, str]:
    """ConfigMap data: every file in <source>/taskboard/ plus traffic/simulate.py, by basename."""
    app_dir = source / APP_DIR
    if not app_dir.is_dir():
        raise SystemExit(f"{app_dir}: not a directory")
    paths = []
    for path in sorted(app_dir.iterdir()):
        if IGNORED.match(path.name):
            continue
        if path.is_dir():
            raise SystemExit(f"{path}: {APP_DIR}/ must be flat (a ConfigMap has no directories)")
        paths.append(path)
    extra = source / EXTRA
    if not extra.is_file():
        raise SystemExit(f"{extra}: missing")
    if any(p.name == extra.name for p in paths):
        raise SystemExit(f"{app_dir / extra.name}: clashes with {EXTRA} (both would be key {extra.name})")
    paths.append(extra)
    data, total = {}, 0
    for path in paths:
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


def _why_not_block(name: str, text: str) -> str:
    for number, line in enumerate(text.split("\n"), 1):
        if line != line.rstrip(" "):
            return f"line {number} ends in a space"
        if "\t" in line:
            return f"line {number} has a tab"
        if any(ord(ch) < 32 or ch in "\x7f﻿" for ch in line):
            return f"line {number} has a control character (CR, BOM, ...)"
    return "PyYAML would not write it as a block literal"


def _render_configmap(out: str, header: str, metadata: dict, data: dict[str, str]) -> str:
    doc = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata,
           "data": {key: _Literal(value) for key, value in data.items()}}
    body = yaml.dump(doc, sort_keys=False, width=10_000, allow_unicode=True)
    # Every non-empty file must come out as a readable block literal with no trailing whitespace
    # (yamllint's trailing-spaces is an error); PyYAML silently double-quotes what it cannot.
    for key, value in data.items():
        if value and not re.search(rf"^  {re.escape(key)}: \|[-+0-9]*$", body, re.M):
            raise SystemExit(f"{APP_DIR}/{key} or {EXTRA}: {_why_not_block(key, value)}; fix it in {SOURCE_REPO}")
    text = header + body
    bad = [n for n, line in enumerate(text.splitlines(), 1) if line != line.rstrip()]
    if bad:
        raise SystemExit(f"{out}: trailing whitespace on lines {bad[:5]}")
    return text


def render(data: dict[str, str], commit: str) -> str:
    """The app's ConfigMap, taskboard/taskboard-code."""
    return _render_configmap(
        OUT, HEADER.format(repo=SOURCE_REPO, commit=commit, app_dir=APP_DIR, extra=EXTRA),
        {"name": "taskboard-code", "namespace": "taskboard", "labels": {"app": "taskboard"},
         "annotations": {COMMIT_ANNOTATION: commit, REPO_ANNOTATION: SOURCE_REPO}},
        data)


def render_browser(data: dict[str, str], commit: str) -> str:
    """The browser's copy, kagent/taskboard-browser-code: the same data plus .source-commit."""
    return _render_configmap(
        BROWSER_OUT,
        BROWSER_HEADER.format(repo=SOURCE_REPO, commit=commit, app_dir=APP_DIR, extra=EXTRA),
        {"name": "taskboard-browser-code", "namespace": "kagent",
         "labels": {"app.kubernetes.io/part-of": "kagent", "app.kubernetes.io/name": "taskboard-browser-code"},
         "annotations": {COMMIT_ANNOTATION: commit, REPO_ANNOTATION: SOURCE_REPO}},
        dict(sorted({**data, SOURCE_COMMIT_KEY: commit}.items())))


def configmap_checksum(text: str) -> str:
    """sha256 of a ConfigMap's data as sorted JSON (scripts/gen-alert-demo-configmap.py's formula)."""
    doc = [d for d in yaml.safe_load_all(text) if d and d.get("kind") == "ConfigMap"][0]
    return hashlib.sha256(json.dumps(doc["data"], sort_keys=True).encode()).hexdigest()


def _set_annotations(rel: str, text: str, code_configmap: str, commit: str, checksums: int) -> str:
    value = configmap_checksum(code_configmap)
    text, count = re.subn(r'(checksum/code: )"[0-9a-f]*"', rf'\g<1>"{value}"', text)
    if count != checksums:
        raise SystemExit(f"{rel}: expected {checksums} checksum/code annotations, found {count}")
    text, count = re.subn(rf'({re.escape(COMMIT_ANNOTATION)}: )"[0-9a-f]*"', rf'\g<1>"{commit}"', text)
    if count != 1:
        raise SystemExit(f"{rel}: expected one {COMMIT_ANNOTATION} annotation, found {count}")
    return text


def render_deployment(text: str, code_configmap: str, commit: str) -> str:
    """deployment.yaml with both checksum/code annotations and the app's source-commit set."""
    return _set_annotations(DEPLOYMENT, text, code_configmap, commit, DEPLOYMENTS)


def render_browser_deployment(text: str, browser_configmap: str, commit: str) -> str:
    """taskboard-browser-mcp.yaml with its checksum/code (over the copy's data) and source-commit set."""
    return _set_annotations(BROWSER_DEPLOYMENT, text, browser_configmap, commit, 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="an arigsela/taskboard checkout")
    ap.add_argument("--commit", required=True, help="the full commit SHA the checkout is at")
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed manifests are stale")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    repo = Path(args.repo_root).resolve()
    source = Path(args.source).resolve()
    if not SHA.match(args.commit):
        raise SystemExit(f"--commit must be a full 40-character lowercase SHA, got {args.commit!r}")
    check_source_commit(source, args.commit)
    data = collect(source)
    app, browser = render(data, args.commit), render_browser(data, args.commit)
    # Everything is rendered before anything is written: a refusal leaves all four files as they were.
    wanted = {OUT: app,
              DEPLOYMENT: render_deployment((repo / DEPLOYMENT).read_text(), app, args.commit),
              BROWSER_OUT: browser,
              BROWSER_DEPLOYMENT: render_browser_deployment((repo / BROWSER_DEPLOYMENT).read_text(), browser,
                                                            args.commit)}
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
                  "run scripts/gen-taskboard-configmap.py without --check", file=sys.stderr)
            return 1
        print(f"{', '.join(wanted)} match {SOURCE_REPO}@{args.commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
