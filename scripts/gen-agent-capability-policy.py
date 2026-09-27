#!/usr/bin/env python3
"""Generate the agent-capability admission policies from the capability taxonomy.

Output: base-apps/admission-policies/agent-capability.yaml, two native
ValidatingAdmissionPolicies (+ bindings) evaluated inside the API server.

WHY THE TAXONOMY IS INLINED rather than read at admission time (e.g. a ConfigMap
parameter): a runtime lookup is a dependency with a total blast radius. It is
evaluated for EVERY Agent, and with failurePolicy Fail any lookup failure (missing
object, RBAC, sync ordering) would deny ALL Agent writes, wedging the kagent app.
Inlining makes the artifact under test (tests/admission-policies boots a real API
server) byte-for-byte the artifact that ships. The cost is repetition, and repetition
drifts, which is what this generator plus the CI drift check (`--check`) prevent.
The taxonomy has exactly one source of truth:
base-apps/admission-policies/agent-capability-taxonomy.yaml.

(Until plan Phase 3E this script also generated a Kyverno ClusterPolicy from the same
taxonomy; that policy was deleted when the native one moved to Deny.)

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

TAXONOMY = "base-apps/admission-policies/agent-capability-taxonomy.yaml"
NATIVE = "base-apps/admission-policies/agent-capability.yaml"

NATIVE_HEADER = """\
---
# Agent capability guardrails — the admission half of the L03 Security pillar, natively in
# the API server.
#
# !!! GENERATED FILE — DO NOT EDIT BY HAND !!!
# Source of truth: base-apps/admission-policies/agent-capability-taxonomy.yaml
# Regenerate:      ./scripts/gen-agent-capability-policy.py
# CI fails if this file drifts from the taxonomy. Tests: tests/admission-policies/.
#
# agent-identity answers WHO an agent is (its credentials are scoped to a dedicated Vault
# path). These policies answer what an agent may DO. They DENY, at admission:
#
#   agent-capability             1. an Agent with no capability.homelab/class in
#                                   {read, write, admin}
#                                2. an Agent binding a tool not in the taxonomy (FAIL-CLOSED)
#                                3. a `read` agent binding any write/destructive tool
#                                4. a `write` agent binding any destructive tool
#                                5. a mutating tool bound without a requireApproval entry
#   agent-capability-delegation  6. a `read` agent delegating to a `write` or `admin` agent
#                                7. a `write` agent delegating to an `admin` agent
#
# Rules 6-7 close a privilege-escalation path: an agent's effective capability is its own
# tools UNION everything it can reach by delegating, so a read agent delegating to an admin
# agent IS an admin agent. The policy takes the Agent kind as its PARAMETER and binds with
# paramRef.selector {}: the API server evaluates it once per Agent in the request's namespace
# (each one a potential delegate) and ANDs the results. A delegate whose class label is
# missing or not read/write counts as admin.
#
# Rules 6-7 check ONE HOP, which is inductively sufficient at admission: if no agent may
# delegate above its own class, no chain can exceed its head's class. What admission cannot
# see (a delegate created or PROMOTED after its delegator was admitted; no delegate existing
# yet, where parameterNotFoundAction is Allow) is covered by CI, which computes the full
# transitive closure over the agent graph in git (scripts/validate-agent-capability.py).
#
# ROLLOUT STATE: ENFORCING. [Deny] with failurePolicy Fail: an evaluation error denies too,
# rather than silently admitting. Replaced the Kyverno ClusterPolicy agent-capability, which
# failed OPEN (forceFailurePolicyIgnore, one webhook replica) whenever Kyverno was down.
# Emergency: `kubectl delete validatingadmissionpolicybinding <name>` (runbook).
#
# REPORTING: agent-capability carries reports.kyverno.io/enabled=true, so Kyverno's reports
# controller writes its results to PolicyReports (native policies are opt-in there).
# agent-capability-delegation deliberately does not: it is parameterised (paramKind Agent), and
# Kyverno's engine mis-evaluates parameterised policies, so its report rows would be wrong.
"""

AGENT_RULE = {"apiGroups": ["kagent.dev"], "apiVersions": ["v1alpha2"],
              "resources": ["agents"], "operations": ["CREATE", "UPDATE"]}
ENFORCE = {"failurePolicy": "Fail", "validationActions": ["Deny"]}
# Kyverno's reports controller only reports native policies that opt in with this label.
REPORTING = {"reports.kyverno.io/enabled": "true"}


def _cel_list(items: list[str]) -> str:
    return json.dumps(items)


def _binding(name: str, extra: dict | None = None) -> dict:
    spec = {"policyName": name, "validationActions": ENFORCE["validationActions"]}
    if extra:
        spec.update(extra)
    return {"apiVersion": "admissionregistration.k8s.io/v1",
            "kind": "ValidatingAdmissionPolicyBinding",
            "metadata": {"name": name}, "spec": spec}


def build_native(read: list[str], write: list[str], destructive: list[str]) -> list[dict]:
    classified = read + write + destructive
    mutating = write + destructive
    names = "variables.mcp.all(t, t.mcpServer.?toolNames.orValue([]).all(n, {cond}))"

    capability = {
        "apiVersion": "admissionregistration.k8s.io/v1",
        "kind": "ValidatingAdmissionPolicy",
        "metadata": {"name": "agent-capability", "labels": REPORTING},
        "spec": {
            "failurePolicy": ENFORCE["failurePolicy"],
            "matchConstraints": {"resourceRules": [AGENT_RULE]},
            "variables": [
                {"name": "cls",
                 "expression": "object.metadata.?labels[?'capability.homelab/class'].orValue('')"},
                {"name": "mcp",
                 "expression": "object.spec.?declarative.?tools.orValue([]).filter(t, t.type == 'McpServer')"},
                {"name": "classified", "expression": _cel_list(classified)},
                {"name": "mutating", "expression": _cel_list(mutating)},
                {"name": "destructive", "expression": _cel_list(destructive)},
            ],
            "validations": [
                {"expression": "variables.cls in ['read', 'write', 'admin']",
                 "messageExpression": (
                     "\"Agent '\" + object.metadata.name + \"' must declare a capability class: the "
                     "label capability.homelab/class must be one of read, write, admin. There is "
                     "no default.\""),
                 "reason": "Forbidden"},
                {"expression": names.format(cond="n in variables.classified"),
                 "messageExpression": (
                     "\"Agent '\" + object.metadata.name + \"' binds tools that are not in the "
                     "capability taxonomy. Classify them in "
                     "base-apps/admission-policies/agent-capability-taxonomy.yaml first: "
                     "unclassified tools are denied by design (fail-closed).\""),
                 "reason": "Forbidden"},
                {"expression": "variables.cls != 'read' || " + names.format(cond="!(n in variables.mutating)"),
                 "messageExpression": (
                     "\"Agent '\" + object.metadata.name + \"' is class read but binds mutating "
                     "tools. A read agent may bind only read-classified tools.\""),
                 "reason": "Forbidden"},
                {"expression": "variables.cls != 'write' || " + names.format(cond="!(n in variables.destructive)"),
                 "messageExpression": (
                     "\"Agent '\" + object.metadata.name + \"' is class write but binds "
                     "destructive tools. Only an admin agent may bind destructive tools.\""),
                 "reason": "Forbidden"},
                {"expression": (
                    "variables.mcp.all(t, t.mcpServer.?toolNames.orValue([]).all(n, "
                    "!(n in variables.mutating) || n in t.mcpServer.?requireApproval.orValue([])))"),
                 "messageExpression": (
                     "\"Agent '\" + object.metadata.name + \"' binds mutating tools that are not "
                     "in that tool ref's requireApproval list. Every write/destructive tool must "
                     "be gated behind human approval.\""),
                 "reason": "Forbidden"},
            ],
        },
    }

    delegation = {
        "apiVersion": "admissionregistration.k8s.io/v1",
        "kind": "ValidatingAdmissionPolicy",
        "metadata": {"name": "agent-capability-delegation"},
        "spec": {
            "failurePolicy": ENFORCE["failurePolicy"],
            "paramKind": {"apiVersion": "kagent.dev/v1alpha2", "kind": "Agent"},
            "matchConstraints": {
                "resourceRules": [AGENT_RULE],
                # Only read and write agents are constrained; admin may delegate anywhere.
                "objectSelector": {"matchExpressions": [
                    {"key": "capability.homelab/class", "operator": "In", "values": ["read", "write"]}]},
            },
            "variables": [
                {"name": "mine",
                 "expression": "object.metadata.?labels[?'capability.homelab/class'].orValue('')"},
                {"name": "delegates",
                 "expression": "object.spec.?declarative.?tools.orValue([]).filter(t, t.type == 'Agent').map(t, t.agent.name)"},
                # A missing or unrecognised class on the delegate is treated as admin.
                {"name": "theirs",
                 "expression": "params.metadata.?labels[?'capability.homelab/class'].orValue('admin') in ['read', 'write'] ? params.metadata.labels['capability.homelab/class'] : 'admin'"},
                {"name": "delegatesToParam",
                 "expression": "params.metadata.name in variables.delegates"},
            ],
            "validations": [
                {"expression": "!(variables.mine == 'read' && variables.delegatesToParam) || variables.theirs == 'read'",
                 "messageExpression": (
                     "\"Agent '\" + object.metadata.name + \"' is class read but delegates to '\" + "
                     "params.metadata.name + \"' (class \" + variables.theirs + \"). Delegation is "
                     "capability-transitive, so this is a privilege escalation. Delegate to a "
                     "read-class agent instead (see k8s-reader).\""),
                 "reason": "Forbidden"},
                {"expression": "!(variables.mine == 'write' && variables.delegatesToParam) || variables.theirs != 'admin'",
                 "messageExpression": (
                     "\"Agent '\" + object.metadata.name + \"' is class write but delegates to "
                     "admin agent '\" + params.metadata.name + \"'. Delegation is capability-"
                     "transitive, so this is a privilege escalation.\""),
                 "reason": "Forbidden"},
            ],
        },
    }

    return [
        capability,
        _binding("agent-capability"),
        delegation,
        # Every Agent in the request's namespace is a parameter; with none, allow.
        _binding("agent-capability-delegation",
                 {"paramRef": {"selector": {}, "parameterNotFoundAction": "Allow"}}),
    ]


def render_native(repo: Path) -> str:
    read, write, destructive = load_taxonomy(repo)
    return NATIVE_HEADER + yaml.safe_dump_all(
        build_native(read, write, destructive),
        sort_keys=False, width=10_000, default_flow_style=False,
    )


def load_taxonomy(repo: Path) -> tuple[list[str], list[str], list[str]]:
    cm = next(
        d for d in yaml.safe_load_all((repo / TAXONOMY).read_text())
        if d and d.get("kind") == "ConfigMap"
    )
    out = []
    for key in ("read", "write", "destructive"):
        out.append(json.loads(cm["data"][key]))
    return tuple(out)  # type: ignore[return-value]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the committed policy is stale")
    args = ap.parse_args(argv)

    outputs = [(NATIVE, render_native(args.repo_root))]

    if not args.check:
        for rel, want in outputs:
            (args.repo_root / rel).write_text(want)
            print(f"wrote {rel}")
        return 0

    stale = 0
    for rel, want in outputs:
        path = args.repo_root / rel
        have = path.read_text() if path.exists() else ""
        if have == want:
            continue
        stale += 1
        print(f"{rel} is STALE — regenerate with "
              f"./scripts/gen-agent-capability-policy.py", file=sys.stderr)
        sys.stderr.writelines(difflib.unified_diff(
            have.splitlines(True), want.splitlines(True),
            fromfile="committed", tofile="generated",
        ))
    if stale:
        return 1
    print("agent-capability policy is in sync with the taxonomy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
