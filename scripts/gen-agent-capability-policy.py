#!/usr/bin/env python3
"""Generate ClusterPolicy/agent-capability from the capability taxonomy.

WHY THIS EXISTS — the policy embeds the taxonomy inline instead of reading it
from a ConfigMap at admission time.

A `context.configMap` lookup would be DRYer, but it is a runtime dependency with
a total blast radius: it sits at rule level, so it is evaluated for EVERY Agent,
and if the lookup fails for any reason (missing ConfigMap, RBAC, schema change,
sync ordering) every rule errors and Kyverno's default failurePolicy=Fail denies
ALL Agent writes — wedging the kagent app. Worse, the kyverno CLI has no cluster
to resolve a configMap against, so that exact path could not be covered by the
offline tests: the policy we shipped would not be the policy we tested.

Inlining removes the lookup, so the artifact under test is byte-for-byte the
artifact that ships. The cost is that the tool lists are repeated across rules,
and duplication drifts — which is what this generator plus the CI drift check
(`--check`) exist to prevent. The taxonomy has exactly one source of truth:
base-apps/kyverno-policies/agent-capability-taxonomy.yaml.

  ./scripts/gen-agent-capability-policy.py            # regenerate
  ./scripts/gen-agent-capability-policy.py --check    # CI: fail if stale
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

import yaml

TAXONOMY = "base-apps/kyverno-policies/agent-capability-taxonomy.yaml"
POLICY = "base-apps/kyverno-policies/agent-capability.yaml"

HEADER = """\
---
# Agent capability guardrails — the admission half of the L03 Security pillar.
#
# !!! GENERATED FILE — DO NOT EDIT BY HAND !!!
# Source of truth: base-apps/kyverno-policies/agent-capability-taxonomy.yaml
# Regenerate:      ./scripts/gen-agent-capability-policy.py
# CI fails if this file drifts from the taxonomy.
#
# ClusterPolicy/agent-identity answers WHO an agent is (its credentials are scoped
# to a dedicated Vault path). This policy answers what an agent may DO. It DENIES,
# at admission:
#
#   1. an Agent with no capability.homelab/class in {read, write, admin}
#   2. an Agent binding a tool that is not in the taxonomy   (FAIL-CLOSED)
#   3. a `read` agent binding any write/destructive tool
#   4. a `write` agent binding any destructive tool
#   5. any mutating tool bound without a matching requireApproval entry
#   6. a `read` agent delegating to a `write` or `admin` agent
#   7. a `write` agent delegating to an `admin` agent
#
# Rules 6-7 close the privilege-escalation path that admission previously left
# open: an agent's effective capability is its own tools UNION everything it can
# reach by delegating, so a read-only agent delegating to an admin agent IS an
# admin agent. This was live (homelab-knowledge -> k8s-agent) and is reachable by
# an agent, not just a human: k8s-agent holds k8s_create_resource, so it could be
# driven to create a `read` Agent that delegates straight back to itself.
#
# Rules 6-7 check ONE HOP, which is inductively sufficient at admission: if every
# agent is forbidden from delegating above its own class, no chain can exceed its
# head's class. The multi-hop closure (A->B->C, where B is later PROMOTED to
# admin and A is never re-admitted) is not catchable one-object-at-a-time; CI
# computes the full transitive closure over the graph in git, and background
# PolicyReports re-scan existing agents.
#
# Rules 6-7 are the ONLY rules that make a runtime API call (to read the
# delegate's class). That call is made inside `foreach tools[?type=='Agent']`, so
# it never fires for an agent that has no delegations — the blast radius of an
# apiCall failure is the delegating agents alone, not every Agent in the cluster.
#
# ---------------------------------------------------------------------------
# KYVERNO NOTES — both cost real debugging time. Read before editing the generator.
#
# (a) Context variables do NOT resolve inside JMESPath filter expressions.
#     `toolNames[?contains(mutating, @)]` evaluates `mutating` to nil and the rule
#     ERRORS ("Invalid type for: <nil>, expected: array|string"). Variables are
#     substituted by {{ }} templating BEFORE JMESPath runs, so a variable must be
#     injected as a JSON literal: toolNames[?contains(`{{ mutating }}`, @)].
#     Prefer the AnyIn/AnyNotIn operators, which compare arrays natively.
#
# (b) Keep every jmesPath on ONE line. A YAML folded scalar (>-) preserves
#     newlines literally when continuation lines are more-indented, embedding \\n
#     into the expression so it fails to parse and the variable resolves to nil.
#
# Verified offline against the SHIPPED policy (no substitution):
#     ./tests/agent-capability/kyverno/run.sh
#
# Contract: docs/superpowers/specs/2026-07-13-agent-capability-classes-design.md
"""

AGENT_KIND = "kagent.dev/v1alpha2/Agent"
CLASS_LABEL = "capability.homelab/class"


def _match(cls: str | None = None) -> dict:
    res: dict = {"kinds": [AGENT_KIND]}
    if cls:
        res["selector"] = {"matchLabels": {CLASS_LABEL: cls}}
    return {"any": [{"resources": res}]}


def _tool_foreach(deny_key: str, op: str, value: list[str]) -> list[dict]:
    return [{
        "list": "request.object.spec.declarative.tools[?type=='McpServer']",
        "deny": {"conditions": {"any": [
            {"key": deny_key, "operator": op, "value": value}
        ]}},
    }]


def _delegation_foreach(forbidden: list[str]) -> list[dict]:
    """Fetch the delegate Agent and read its declared class.

    An absent class defaults to 'admin' — the most restrictive reading. A
    delegate with no class is itself denied by rule 1, but if one somehow exists
    (created before this policy), we must not treat it as harmless.
    """
    return [{
        "list": "request.object.spec.declarative.tools[?type=='Agent']",
        "context": [{
            "name": "delegateClass",
            "apiCall": {
                "urlPath": "/apis/kagent.dev/v1alpha2/namespaces/{{request.object.metadata.namespace}}/agents/{{element.agent.name}}",
                "jmesPath": "metadata.labels.\"capability.homelab/class\" || 'admin'",
            },
        }],
        "deny": {"conditions": {"any": [
            {"key": "{{ delegateClass }}", "operator": "AnyIn", "value": forbidden}
        ]}},
    }]


def build_policy(read: list[str], write: list[str], destructive: list[str]) -> dict:
    classified = read + write + destructive
    mutating = write + destructive

    rules = [
        {
            "name": "agent-must-declare-capability-class",
            "match": _match(),
            "validate": {
                "message": (
                    "Agent must declare a capability class: the label "
                    "capability.homelab/class must be one of read, write, admin. "
                    "There is no default — an agent with no declared class is denied."
                ),
                "deny": {"conditions": {"any": [{
                    "key": "{{ request.object.metadata.labels.\"capability.homelab/class\" || '<none>' }}",
                    "operator": "AnyNotIn",
                    "value": ["read", "write", "admin"],
                }]}},
            },
        },
        {
            "name": "bound-tools-must-be-classified",
            "match": _match(),
            "validate": {
                "message": (
                    "Agent binds tools that are not in the capability taxonomy. "
                    "Classify them in base-apps/kyverno-policies/agent-capability-taxonomy.yaml "
                    "before binding them — unclassified tools are denied by design, so that a "
                    "chart upgrade cannot silently widen any agent's capability."
                ),
                "foreach": _tool_foreach(
                    "{{ element.mcpServer.toolNames }}", "AnyNotIn", classified
                ),
            },
        },
        {
            "name": "read-agent-must-not-bind-mutating-tools",
            "match": _match("read"),
            "validate": {
                "message": (
                    "Agent is declared capability.homelab/class=read but binds mutating tools. "
                    "A read agent may bind only read-classified tools."
                ),
                "foreach": _tool_foreach(
                    "{{ element.mcpServer.toolNames }}", "AnyIn", mutating
                ),
            },
        },
        {
            "name": "write-agent-must-not-bind-destructive-tools",
            "match": _match("write"),
            "validate": {
                "message": (
                    "Agent is declared capability.homelab/class=write but binds destructive "
                    "tools. Only an admin agent may bind destructive tools."
                ),
                "foreach": _tool_foreach(
                    "{{ element.mcpServer.toolNames }}", "AnyIn", destructive
                ),
            },
        },
        {
            "name": "mutating-tools-must-require-approval",
            "match": _match(),
            "validate": {
                "message": (
                    "Agent binds mutating tools that are not gated behind requireApproval. "
                    "Every write/destructive tool bound must appear in that same tool ref's "
                    "requireApproval list. This gate was silently stripped once before; it is "
                    "enforced now."
                ),
                "foreach": [{
                    "list": "request.object.spec.declarative.tools[?type=='McpServer']",
                    "context": [
                        {"name": "approved", "variable": {
                            "jmesPath": "element.mcpServer.requireApproval || `[]`"}},
                        {"name": "mutating", "variable": {"value": mutating}},
                        {"name": "ungated", "variable": {
                            "jmesPath": "element.mcpServer.toolNames[?contains(`{{ mutating }}`, @) && !contains(`{{ approved }}`, @)] || `[]`"}},
                    ],
                    "deny": {"conditions": {"any": [
                        {"key": "{{ length(ungated) }}", "operator": "GreaterThan", "value": 0}
                    ]}},
                }],
            },
        },
        {
            "name": "read-agent-must-not-delegate-to-higher-class",
            "match": _match("read"),
            "validate": {
                "message": (
                    "Agent is declared capability.homelab/class=read but delegates to a write "
                    "or admin agent. Delegation is capability-transitive: an agent's effective "
                    "capability is its own tools UNION everything it can reach by delegating, "
                    "so this is a privilege escalation. Delegate to a read-class agent instead "
                    "(see k8s-reader), or declare the class this agent actually needs."
                ),
                "foreach": _delegation_foreach(["write", "admin"]),
            },
        },
        {
            "name": "write-agent-must-not-delegate-to-admin",
            "match": _match("write"),
            "validate": {
                "message": (
                    "Agent is declared capability.homelab/class=write but delegates to an admin "
                    "agent. Delegation is capability-transitive — this is a privilege escalation."
                ),
                "foreach": _delegation_foreach(["admin"]),
            },
        },
    ]

    return {
        "apiVersion": "kyverno.io/v1",
        "kind": "ClusterPolicy",
        "metadata": {
            "name": "agent-capability",
            "annotations": {
                "policies.kyverno.io/title": "Agent capability classes",
                "policies.kyverno.io/category": "Agent Guardrails",
                "policies.kyverno.io/severity": "high",
                "policies.kyverno.io/subject": "Agent",
                "policies.kyverno.io/description": (
                    "Constrains what a kagent Agent may do. Each Agent declares a capability "
                    "class; every tool it binds must be classified in the capability taxonomy "
                    "and permitted by that class; every mutating tool must be gated behind "
                    "requireApproval; and an agent may not delegate to a higher-class agent."
                ),
            },
        },
        "spec": {
            "validationFailureAction": "Enforce",
            "background": True,
            "rules": rules,
        },
    }


def load_taxonomy(repo: Path) -> tuple[list[str], list[str], list[str]]:
    cm = next(
        d for d in yaml.safe_load_all((repo / TAXONOMY).read_text())
        if d and d.get("kind") == "ConfigMap"
    )
    out = []
    for key in ("read", "write", "destructive"):
        out.append(json.loads(cm["data"][key]))
    return tuple(out)  # type: ignore[return-value]


def render(repo: Path) -> str:
    read, write, destructive = load_taxonomy(repo)
    body = yaml.safe_dump(
        build_policy(read, write, destructive),
        sort_keys=False, width=10_000, default_flow_style=False,
    )
    return HEADER + body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the committed policy is stale")
    args = ap.parse_args(argv)

    want = render(args.repo_root)
    path = args.repo_root / POLICY

    if not args.check:
        path.write_text(want)
        print(f"wrote {POLICY}")
        return 0

    have = path.read_text() if path.exists() else ""
    if have == want:
        print("agent-capability policy is in sync with the taxonomy")
        return 0

    print(f"{POLICY} is STALE — regenerate with "
          f"./scripts/gen-agent-capability-policy.py", file=sys.stderr)
    sys.stderr.writelines(difflib.unified_diff(
        have.splitlines(True), want.splitlines(True),
        fromfile="committed", tofile="generated",
    ))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
