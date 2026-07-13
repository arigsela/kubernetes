#!/usr/bin/env python3
"""Validate the agent-capability contract (L03 Security guardrails).

Four invariants, checked against the manifests in git:

  1. Declared class    every Agent carries capability.homelab/class in
                       {read, write, admin}. No default; missing is an error.
  2. Tools within class every bound tool is classified in the taxonomy, and its
                       classification is permitted by the agent's class. A tool
                       absent from the taxonomy is an error (FAIL-CLOSED).
  3. Mutating gated    every bound tool classified write/destructive appears in
                       that tool ref's requireApproval.
  4. No escalation     for every type: Agent delegation, the delegate's class is
                       <= this agent's class. Effective capability is transitive.

This is the authoritative gate for all four. Kyverno enforces 1-3 at admission;
invariant 4 needs a cross-object lookup, so CI is where it is actually decided.
The whole agent graph is in git, which is what makes that sound.

Exit 0 clean, 1 on any error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

CLASS_LABEL = "capability.homelab/class"

# read < write < admin
CLASS_RANK = {"read": 0, "write": 1, "admin": 2}

# What each class may bind. A class may bind any tool whose classification rank
# is <= its own binding ceiling.
CLASS_MAY_BIND = {
    "read": {"read"},
    "write": {"read", "write"},
    "admin": {"read", "write", "destructive"},
}

# Classifications that must be gated behind requireApproval wherever bound.
MUST_APPROVE = {"write", "destructive"}

TAXONOMY_PATH = "base-apps/kyverno-policies/agent-capability-taxonomy.yaml"


def _load_docs(path: Path) -> list[dict]:
    try:
        return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]
    except yaml.YAMLError:
        return []


def load_taxonomy(repo_root: Path) -> dict[str, str]:
    """Return {tool_name: classification}. Same content Kyverno reads."""
    path = repo_root / TAXONOMY_PATH
    if not path.exists():
        raise SystemExit(f"taxonomy not found: {TAXONOMY_PATH}")

    cm = next(
        (d for d in _load_docs(path) if d.get("kind") == "ConfigMap"),
        None,
    )
    if cm is None:
        raise SystemExit(f"no ConfigMap in {TAXONOMY_PATH}")

    tool_class: dict[str, str] = {}
    for classification in ("read", "write", "destructive"):
        raw = (cm.get("data") or {}).get(classification)
        if not raw:
            raise SystemExit(f"taxonomy missing '{classification}' key")
        for tool in json.loads(raw):
            if tool in tool_class:
                raise SystemExit(
                    f"tool {tool!r} classified twice "
                    f"({tool_class[tool]} and {classification})"
                )
            tool_class[tool] = classification
    return tool_class


def collect_agents(repo_root: Path) -> list[tuple[Path, dict]]:
    """Every kagent Agent in git, found by KIND — not by directory.

    The identity validator globs only kagent/agents/*.yaml, which silently
    misses build-orchestrator.yaml one level up. Find them by kind instead.
    """
    kagent_dir = repo_root / "base-apps" / "kagent"
    found: list[tuple[Path, dict]] = []
    for path in sorted(kagent_dir.rglob("*.yaml")):
        for doc in _load_docs(path):
            if doc.get("kind") == "Agent":
                found.append((path, doc))
    return found


def _tool_refs(doc: dict) -> list[dict]:
    return (doc.get("spec", {}).get("declarative", {}) or {}).get("tools") or []


def _agent_class(doc: dict) -> str | None:
    return ((doc.get("metadata") or {}).get("labels") or {}).get(CLASS_LABEL)


def _delegates(doc: dict) -> list[str]:
    out = []
    for ref in _tool_refs(doc):
        if ref.get("type") == "Agent":
            name = (ref.get("agent") or {}).get("name")
            if name:
                out.append(name)
    return out


def check_declared_class(agents) -> list[str]:
    """Invariant 1."""
    errors = []
    for path, doc in agents:
        name = (doc.get("metadata") or {}).get("name", "<unnamed>")
        cls = _agent_class(doc)
        if cls is None:
            errors.append(
                f"{name}: missing label {CLASS_LABEL} ({path}). "
                f"Every agent must declare a capability class; there is no default."
            )
        elif cls not in CLASS_RANK:
            errors.append(
                f"{name}: invalid {CLASS_LABEL}={cls!r} ({path}). "
                f"Must be one of: read, write, admin."
            )
    return errors


def check_tools_within_class(agents, taxonomy) -> list[str]:
    """Invariants 2 and 3."""
    errors = []
    for path, doc in agents:
        name = (doc.get("metadata") or {}).get("name", "<unnamed>")
        cls = _agent_class(doc)
        if cls not in CLASS_RANK:
            continue  # already reported by invariant 1
        may_bind = CLASS_MAY_BIND[cls]

        for ref in _tool_refs(doc):
            if ref.get("type") != "McpServer":
                continue
            mcp = ref.get("mcpServer") or {}
            server = mcp.get("name", "<unnamed-server>")
            tools = mcp.get("toolNames") or []
            approval = set(mcp.get("requireApproval") or [])

            for tool in tools:
                classification = taxonomy.get(tool)

                # Invariant 2a — fail closed on unknown tools.
                if classification is None:
                    errors.append(
                        f"{name}: tool {tool!r} (server {server}) is not in the "
                        f"capability taxonomy. Classify it in {TAXONOMY_PATH} "
                        f"before binding it."
                    )
                    continue

                # Invariant 2b — within class.
                if classification not in may_bind:
                    errors.append(
                        f"{name}: class {cls!r} may not bind {classification!r} "
                        f"tool {tool!r} (server {server}). "
                        f"Permitted: {sorted(may_bind)}."
                    )
                    continue

                # Invariant 3 — mutating tools must be gated.
                if classification in MUST_APPROVE and tool not in approval:
                    errors.append(
                        f"{name}: {classification} tool {tool!r} (server {server}) "
                        f"is bound but absent from requireApproval."
                    )
    return errors


def check_no_escalation(agents) -> list[str]:
    """Invariant 4 — delegation is capability-transitive.

    An agent's effective class is the max of its declared class and the
    effective class of everything it delegates to. Declaring below your
    effective class is an escalation path and is rejected.
    """
    errors = []
    by_name = {
        (doc.get("metadata") or {}).get("name"): doc
        for _, doc in agents
        if (doc.get("metadata") or {}).get("name")
    }

    def effective(name: str, seen: tuple[str, ...]) -> int:
        """Max class rank reachable from `name`, following delegations."""
        if name in seen:
            errors.append(
                f"delegation cycle: {' -> '.join(seen + (name,))}"
            )
            return CLASS_RANK["read"]
        doc = by_name.get(name)
        if doc is None:
            return CLASS_RANK["read"]  # dangling ref, reported separately
        own = CLASS_RANK.get(_agent_class(doc) or "", CLASS_RANK["read"])
        ranks = [own]
        for d in _delegates(doc):
            ranks.append(effective(d, seen + (name,)))
        return max(ranks)

    for _, doc in agents:
        name = (doc.get("metadata") or {}).get("name")
        cls = _agent_class(doc)
        if not name or cls not in CLASS_RANK:
            continue

        for delegate in _delegates(doc):
            if delegate not in by_name:
                errors.append(
                    f"{name}: delegates to {delegate!r}, which does not exist "
                    f"in git. Dangling delegation."
                )
                continue
            d_eff = effective(delegate, (name,))
            if d_eff > CLASS_RANK[cls]:
                d_cls = next(
                    k for k, v in CLASS_RANK.items() if v == d_eff
                )
                errors.append(
                    f"{name}: class {cls!r} delegates to {delegate!r} whose "
                    f"effective class is {d_cls!r} — privilege escalation. "
                    f"An agent cannot delegate above its own class."
                )
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the agent-capability contract."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root

    taxonomy = load_taxonomy(repo_root)
    agents = collect_agents(repo_root)

    if not agents:
        print("no Agent manifests found — nothing to validate", file=sys.stderr)
        return 1

    errors: list[str] = []
    errors += check_declared_class(agents)
    errors += check_tools_within_class(agents, taxonomy)
    errors += check_no_escalation(agents)

    print(
        f"agent-capability: {len(agents)} agents, "
        f"{len(taxonomy)} classified tools"
    )
    for _, doc in sorted(agents, key=lambda a: (a[1].get("metadata") or {}).get("name", "")):
        name = (doc.get("metadata") or {}).get("name")
        cls = _agent_class(doc) or "UNDECLARED"
        delegates = _delegates(doc)
        suffix = f" -> {', '.join(delegates)}" if delegates else ""
        print(f"  {cls:<11} {name}{suffix}")

    if errors:
        print(f"\n{len(errors)} error(s):", file=sys.stderr)
        for e in errors:
            print(f"  ERROR {e}", file=sys.stderr)
        return 1

    print("\nagent-capability contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
