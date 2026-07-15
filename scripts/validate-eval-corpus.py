#!/usr/bin/env python3
"""Validate the golden-answer evaluation corpus (Evaluation E1).

    ./scripts/validate-eval-corpus.py

The corpus is committed to git and will be fed to an LLM judge (E2). Two failure
modes matter, and this gate covers both:

  1. A malformed entry that the scorer cannot use — missing question, no golden
     criteria, an unknown category, a refusal with no must_not_include.
  2. A SECRET checked into the corpus. Refusal entries exist precisely because the
     questions ask for secrets; a careless golden could paste the very value it is
     meant to keep out. Every string in the corpus is scanned with the same
     secret-shape detector the audit redactor uses.

Exit 1 on any error.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import yaml

_AUDIT = Path(__file__).resolve().parent / "agent-audit.py"
_spec = importlib.util.spec_from_file_location("agent_audit", _AUDIT)
aa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aa)

CORPUS_DIR = "tests/eval-corpus"
CATEGORIES = {"repo-factual", "security-refusal", "memory", "procedural"}
BEHAVIORS = {"answer", "refuse"}


def _walk_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _walk_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_strings(v)


def check_entry(path: Path, i: int, entry: dict) -> list[str]:
    errs = []
    where = f"{path.name}[{i}] (id={entry.get('id', '?')})"

    for req in ("id", "question", "category", "golden", "source"):
        if not entry.get(req):
            errs.append(f"{where}: missing required key '{req}'")

    cat = entry.get("category")
    if cat and cat not in CATEGORIES:
        errs.append(f"{where}: category {cat!r} not in {sorted(CATEGORIES)}")

    golden = entry.get("golden") or {}
    behavior = golden.get("behavior")
    if behavior not in BEHAVIORS:
        errs.append(f"{where}: golden.behavior must be one of {sorted(BEHAVIORS)}")

    if behavior == "answer" and not golden.get("must_include"):
        errs.append(f"{where}: an 'answer' golden must list must_include facts to score")
    if behavior == "refuse" and not golden.get("must_not_include"):
        errs.append(f"{where}: a 'refuse' golden must list must_not_include "
                    f"(the thing the answer must never leak)")

    # No secret may be committed anywhere in the entry — including, especially, a
    # refusal entry that is supposed to keep secrets OUT.
    for s in _walk_strings(entry):
        if aa._looks_like_secret_value(s):
            errs.append(f"{where}: a field contains a secret-shaped value — the "
                        f"corpus is committed to git and must never hold a real "
                        f"secret. Describe it, do not paste it.")
            break
    return errs


def validate(repo_root: Path) -> list[str]:
    corpus_dir = repo_root / CORPUS_DIR
    files = sorted(corpus_dir.glob("*.yaml"))
    if not files:
        return [f"{CORPUS_DIR}/ has no corpus files"]

    errors: list[str] = []
    total = 0
    ids: set[str] = set()
    for path in files:
        doc = yaml.safe_load(path.read_text()) or {}
        if doc.get("version") != 1:
            errors.append(f"{path.name}: missing or unsupported 'version: 1'")
        if not doc.get("agent"):
            errors.append(f"{path.name}: missing 'agent'")
        for i, entry in enumerate(doc.get("entries") or []):
            total += 1
            eid = entry.get("id")
            if eid in ids:
                errors.append(f"{path.name}[{i}]: duplicate id {eid!r}")
            ids.add(eid)
            errors.extend(check_entry(path, i, entry))

    if not total:
        errors.append("corpus has no entries")
    if not errors:
        print(f"eval-corpus: {total} entries across {len(files)} file(s), "
              f"{len(ids)} unique ids — OK")
    return errors


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args(argv)
    errors = validate(args.repo_root)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        print(f"\neval-corpus validation FAILED with {len(errors)} error(s).",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
