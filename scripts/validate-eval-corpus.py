#!/usr/bin/env python3
"""Validate the golden-answer evaluation corpus (Evaluation E1, V99).

    ./scripts/validate-eval-corpus.py

The corpus is committed to git and fed to the eval gate's judge cascade. Failure
modes this gate covers:

  1. A malformed entry that the scorer cannot use — missing question, no golden
     criteria, an unknown category, a refusal with nothing to refuse.
  2. A SECRET checked into the corpus. Refusal entries exist precisely because the
     questions ask for secrets; a careless golden could paste the very value it is
     meant to keep out. Every string in the corpus is scanned with the same
     secret-shape detector the audit redactor uses. The one exception is a value
     carrying the EVALCANARY marker: a deliberately credential-shaped fake that the
     canary fixtures plant (base-apps/kagent/eval-canary.yaml). It opens nothing, and
     this validator checks the corpus and the manifest agree on it, so neither can
     drift into a real token.
  3. An acting agent with no corpus. Every AgentTemplate of class write, admin or
     propose must have tests/eval-corpus/<name>.yaml with at least one `tool-use`
     entry: the shared corpus covers what an agent SAYS; what it DOES needs its own
     expectations (expect_tools / forbid_tools).

File shapes:
  <agent>.yaml   version 1, agent: <name>, entries
  _shared.yaml   version 1, agent: "*", generated (scripts/gen-eval-adversarial-corpus.py),
                 applies to every agent; entries may carry `requires_tools`.

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
TEMPLATES_DIR = "base-apps/kagent/agent-templates"
CANARY_MANIFEST = "base-apps/kagent/eval-canary.yaml"
CANARY_MARKER = "EVALCANARY"
CATEGORIES = {"repo-factual", "security-refusal", "memory", "procedural",
              "adversarial", "canary", "tool-use"}
BEHAVIORS = {"answer", "refuse"}
ACTING_CLASSES = {"write", "admin", "propose"}
TOOL_KEYS = ("requires_tools", "expect_tools", "forbid_tools")


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

    for key in TOOL_KEYS:
        val = entry.get(key)
        if val is not None and not (isinstance(val, list) and val and all(isinstance(t, str) and t for t in val)):
            errs.append(f"{where}: {key} must be a non-empty list of tool names")

    if behavior == "answer" and not golden.get("must_include") and cat != "tool-use":
        errs.append(f"{where}: an 'answer' golden must list must_include facts to score")
    # A refusal needs something concrete to refuse: the secret it must not leak, or (an
    # adversarial prompt) the judge's reference saying what the attack is.
    if behavior == "refuse" and not golden.get("must_not_include") and cat != "adversarial":
        errs.append(f"{where}: a 'refuse' golden must list must_not_include "
                    f"(the thing the answer must never leak)")
    if cat == "adversarial" and not entry.get("reference"):
        errs.append(f"{where}: an adversarial entry needs a reference (what the attack is, for the judge)")
    if cat == "canary":
        vals = golden.get("must_not_include") or []
        if not any(CANARY_MARKER in v for v in vals):
            errs.append(f"{where}: a canary entry's must_not_include must carry the {CANARY_MARKER} value")
        if not entry.get("requires_tools"):
            errs.append(f"{where}: a canary entry needs requires_tools (which tool reaches the value)")
    if cat == "tool-use" and not (entry.get("expect_tools") or entry.get("forbid_tools")):
        errs.append(f"{where}: a tool-use entry needs expect_tools and/or forbid_tools")

    # No secret may be committed anywhere in the entry — including, especially, a
    # refusal entry that is supposed to keep secrets OUT. The canary marker is the
    # one allowed credential shape (checked against the manifest in validate()).
    for s in _walk_strings(entry):
        if CANARY_MARKER in s:
            continue
        if aa._looks_like_secret_value(s):
            errs.append(f"{where}: a field contains a secret-shaped value — the "
                        f"corpus is committed to git and must never hold a real "
                        f"secret. Describe it, do not paste it.")
            break
    return errs


def canary_values(repo_root: Path) -> set[str]:
    """Every value carrying the marker in the canary manifest (ConfigMap data and pod env)."""
    path = repo_root / CANARY_MANIFEST
    if not path.exists():
        return set()
    out = set()
    for doc in yaml.safe_load_all(path.read_text()):
        for s in _walk_strings(doc or {}):
            for word in s.replace('"', " ").split():
                if CANARY_MARKER in word:
                    out.add(word.strip(",;:"))
    return out


def template_classes(repo_root: Path) -> dict[str, str]:
    """AgentTemplate name -> capability.homelab/class, from git."""
    out = {}
    for path in sorted((repo_root / TEMPLATES_DIR).glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if (doc or {}).get("kind") != "AgentTemplate":
                continue
            meta = doc.get("metadata") or {}
            out[meta.get("name")] = (meta.get("labels") or {}).get("capability.homelab/class")
    return out


def validate(repo_root: Path) -> list[str]:
    corpus_dir = repo_root / CORPUS_DIR
    files = sorted(corpus_dir.glob("*.yaml"))
    if not files:
        return [f"{CORPUS_DIR}/ has no corpus files"]

    errors: list[str] = []
    total = 0
    ids: set[str] = set()
    per_agent: dict[str, list[dict]] = {}
    corpus_canaries: set[str] = set()
    for path in files:
        doc = yaml.safe_load(path.read_text()) or {}
        if doc.get("version") != 1:
            errors.append(f"{path.name}: missing or unsupported 'version: 1'")
        agent = doc.get("agent")
        if not agent:
            errors.append(f"{path.name}: missing 'agent'")
        elif agent == "*" and path.stem != "_shared":
            errors.append(f"{path.name}: agent '*' is reserved for _shared.yaml")
        elif agent != "*" and path.stem != agent:
            errors.append(f"{path.name}: file name must equal its agent ({agent})")
        if agent == "*":
            sample = doc.get("sample") or {}
            if sample.get("category") not in CATEGORIES or not isinstance(sample.get("size"), int) or sample["size"] < 1:
                errors.append(f"{path.name}: sample needs a category and a positive size")
        for i, entry in enumerate(doc.get("entries") or []):
            total += 1
            eid = entry.get("id")
            if eid in ids:
                errors.append(f"{path.name}[{i}]: duplicate id {eid!r}")
            ids.add(eid)
            errors.extend(check_entry(path, i, entry))
            per_agent.setdefault(agent, []).append(entry)
            if entry.get("category") == "canary":
                corpus_canaries.update(v for v in ((entry.get("golden") or {}).get("must_not_include") or [])
                                       if CANARY_MARKER in v)
            elif agent != "*":
                for s in _walk_strings(entry):
                    if CANARY_MARKER in s:
                        errors.append(f"{path.name}[{i}]: the canary value belongs to _shared.yaml's canary entries only")
                        break

    # The canary value must be the SAME in the corpus and in the planted fixtures.
    manifest_canaries = canary_values(repo_root)
    if corpus_canaries and corpus_canaries != manifest_canaries:
        errors.append(f"canary mismatch: corpus has {sorted(corpus_canaries)}, "
                      f"{CANARY_MANIFEST} has {sorted(manifest_canaries)}")

    # Every acting agent has its own tool-use expectations.
    for name, cls in sorted(template_classes(repo_root).items()):
        if cls in ACTING_CLASSES:
            entries = per_agent.get(name) or []
            if not any(e.get("category") == "tool-use" for e in entries):
                errors.append(f"AgentTemplate {name} (class {cls}) has no tool-use entry in "
                              f"{CORPUS_DIR}/{name}.yaml: an acting agent needs expect_tools/forbid_tools")

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
