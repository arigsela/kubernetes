#!/usr/bin/env python3
"""Generate the agent-capability admission policies from the capability taxonomy.

Output: base-apps/admission-policies/agent-capability.yaml, four native
ValidatingAdmissionPolicies (+ bindings) evaluated inside the API server, written for
kagent 1.0 (api.kagent.dev/v1alpha3: AgentTemplate, Agent, Harness).

WHY THE TAXONOMY IS INLINED rather than read at admission time (e.g. a ConfigMap
parameter): a runtime lookup is a dependency with a total blast radius. It is
evaluated for EVERY AgentTemplate, and with failurePolicy Fail any lookup failure
(missing object, RBAC, sync ordering) would deny ALL AgentTemplate writes, wedging the
kagent app. Inlining makes the artifact under test (tests/admission-policies boots a
real API server) byte-for-byte the artifact that ships. The cost is repetition, and
repetition drifts, which is what this generator plus the CI drift check (`--check`)
prevent. The taxonomy has exactly one source of truth:
base-apps/admission-policies/agent-capability-taxonomy.yaml.

(Until plan Phase 3E this script also generated a Kyverno ClusterPolicy from the same
taxonomy; that policy was deleted when the native one moved to Deny. Until the kagent 1.0
cutover the policies matched kagent.dev/v1alpha2 Agents.)

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

GROUP, VERSION = "api.kagent.dev", "v1alpha3"
CLASS_LABEL = "capability.homelab/class"

NATIVE_HEADER = """\
---
# Agent capability guardrails — the admission half of the L03 Security pillar, natively in
# the API server, for kagent 1.0 (api.kagent.dev/v1alpha3).
#
# !!! GENERATED FILE — DO NOT EDIT BY HAND !!!
# Source of truth: base-apps/admission-policies/agent-capability-taxonomy.yaml
# Regenerate:      ./scripts/gen-agent-capability-policy.py
# CI fails if this file drifts from the taxonomy. Tests: tests/admission-policies/.
#
# agent-identity answers WHO an agent is (its credentials are scoped to a dedicated Vault
# path). These policies answer what an agent may DO. kagent 1.0 splits an agent into an
# AgentTemplate (what: model, prompt, tools), a Harness (how: runtime) and an Agent that
# references both. Capability lives on the AgentTemplate, so the class label does too. They
# DENY, at admission:
#
#   agent-capability             1. an AgentTemplate with no capability.homelab/class in
#                                   {read, propose, write, admin}
#                                2. an AgentTemplate binding a tool not in the taxonomy
#                                   (FAIL-CLOSED)
#                                3. a `read` template binding any propose/write/destructive
#                                   tool; 3b. a `propose` template binding any
#                                   write/destructive tool (propose = branches and pull
#                                   requests only, ungated: the merge is the gate, §V.94)
#                                4. a `write` template binding any destructive tool
#                                5. a write/destructive tool in an MCP binding without
#                                   requireApproval: true. Approval is per BINDING in 1.0, so
#                                   an agent that needs both binds the server twice: read
#                                   tools ungated, mutating tools gated.
#   agent-capability-delegation  6. a `read` template with a subAgent whose template is
#                                   `propose`, `write` or `admin`; 6b. a `propose` template
#                                   with a subAgent whose template is `write` or `admin`
#                                7. a `write` template with a subAgent whose template is `admin`
#   agent-capability-refs        8. an Agent whose spec.template is inline instead of spec.template.ref:
#                                   capability is checked on AgentTemplates. (alpha14 has no inline
#                                   Harness any more — kagent#3080 — so the runtime check on Harnesses
#                                   cannot be bypassed from an Agent.)
#   agent-capability-runtime     9. a Harness that is not the `kagent` runtime on one of the
#                                   taxonomy's runtime-images. 1-7 are enforced INSIDE that
#                                   runtime (tool selection and the approval pause);
#                                   byo/claude/codex or a foreign image need not honour them.
#                                   ONE exception (SPEC §V.101): `byo` on an image EQUAL to an
#                                   entry of the taxonomy's byo-images (repository@sha256:digest,
#                                   never a repository or a tag). Its other conditions (every
#                                   Agent on it runs a `read` template binding only
#                                   read-only-servers) span objects, so CI decides them
#                                   (scripts/validate-agent-capability.py, invariant 7), and
#                                   agent-definitions-from-git.yaml makes git the only way in.
#
# Rules 6-7 close a privilege-escalation path: an agent's effective capability is its own
# tools UNION everything its subagents hold. (A 1.0 subAgent is compiled INTO the parent's
# runtime and takes the conversation over, so a read template with an admin subagent IS an
# admin agent.) The policy takes the AgentTemplate kind as its PARAMETER and binds with
# paramRef.selector {}: the API server evaluates it once per AgentTemplate in the request's
# namespace (each one a potential subagent) and ANDs the results. A subagent template whose
# class label is missing or not read/write counts as admin.
#
# Rules 6-7 check ONE HOP, which is inductively sufficient at admission: if no template may
# bind a subagent above its own class, no chain can exceed its head's class (kagent 1.0 also
# rejects a subagent that has subagents). What admission cannot see (a subagent template
# created or PROMOTED after its parent was admitted; no template existing yet, where
# parameterNotFoundAction is Allow) is covered by CI, which computes the full transitive
# closure over the template graph in git (scripts/validate-agent-capability.py).
#
# ROLLOUT STATE: ENFORCING. [Deny] with failurePolicy Fail: an evaluation error denies too,
# rather than silently admitting. Emergency: `kubectl delete validatingadmissionpolicybinding
# <name>` (runbook). Ships in the same sync as the api.kagent.dev CRDs' first objects (SPEC
# §V.84); the parameterised policy denies until the API server's param informer syncs
# (§V.78), so a sync right after it is created may need one retry.
#
# REPORTING: every policy here except agent-capability-delegation carries
# reports.kyverno.io/enabled=true, so Kyverno's reports controller writes its results to
# PolicyReports (native policies are opt-in there; it needs read RBAC on the matched kinds,
# kyverno-reports-rbac.yaml). agent-capability-delegation deliberately does not: it is
# parameterised (paramKind AgentTemplate), and Kyverno's engine mis-evaluates parameterised
# policies, so its report rows would be wrong (§V.76).
"""

ENFORCE = {"failurePolicy": "Fail", "validationActions": ["Deny"]}
# Kyverno's reports controller only reports native policies that opt in with this label.
REPORTING = {"reports.kyverno.io/enabled": "true"}


def _rule(resource: str) -> dict:
    return {"apiGroups": [GROUP], "apiVersions": [VERSION],
            "resources": [resource], "operations": ["CREATE", "UPDATE"]}


def _cel_list(items: list[str]) -> str:
    return json.dumps(items)


def _deny(expression: str, message: str) -> dict:
    return {"expression": expression, "messageExpression": message, "reason": "Forbidden"}


def _binding(name: str, extra: dict | None = None) -> dict:
    spec = {"policyName": name, "validationActions": ENFORCE["validationActions"]}
    if extra:
        spec.update(extra)
    return {"apiVersion": "admissionregistration.k8s.io/v1",
            "kind": "ValidatingAdmissionPolicyBinding",
            "metadata": {"name": name}, "spec": spec}


def _policy(name: str, resource: str, variables: list[dict], validations: list[dict],
            reporting: bool = True, param_kind: dict | None = None,
            object_selector: dict | None = None) -> dict:
    spec: dict = {"failurePolicy": ENFORCE["failurePolicy"]}
    if param_kind:
        spec["paramKind"] = param_kind
    spec["matchConstraints"] = {"resourceRules": [_rule(resource)]}
    if object_selector:
        spec["matchConstraints"]["objectSelector"] = object_selector
    if variables:
        spec["variables"] = variables
    spec["validations"] = validations
    metadata = {"name": name}
    if reporting:
        metadata["labels"] = REPORTING
    return {"apiVersion": "admissionregistration.k8s.io/v1",
            "kind": "ValidatingAdmissionPolicy", "metadata": metadata, "spec": spec}


def build_native(read: list[str], propose: list[str], write: list[str], destructive: list[str],
                 runtime_images: list[str], byo_images: list[str]) -> list[dict]:
    classified = read + propose + write + destructive
    mutating = write + destructive
    tpl = "\"AgentTemplate '\" + object.metadata.name + \"' "
    cls_var = {"name": "cls",
               "expression": f"object.metadata.?labels[?'{CLASS_LABEL}'].orValue('')"}
    # Every MCP binding's tool list. `tools` is optional in the CRD; an empty list exposes the
    # whole server, which agent-identity-mcp-toolnames denies.
    names = "variables.mcp.all(b, b.?tools.orValue([]).all(n, {cond}))"

    capability = _policy(
        "agent-capability", "agenttemplates",
        variables=[
            cls_var,
            {"name": "mcp",
             "expression": "object.spec.?tools.orValue([]).filter(t, has(t.mcp)).map(t, t.mcp)"},
            {"name": "classified", "expression": _cel_list(classified)},
            {"name": "propose", "expression": _cel_list(propose)},
            {"name": "mutating", "expression": _cel_list(mutating)},
            {"name": "destructive", "expression": _cel_list(destructive)},
        ],
        validations=[
            _deny("variables.cls in ['read', 'propose', 'write', 'admin']",
                  tpl + "must declare a capability class: the label capability.homelab/class "
                  "must be one of read, propose, write, admin. There is no default.\""),
            _deny(names.format(cond="n in variables.classified"),
                  tpl + "binds tools that are not in the capability taxonomy. Classify them in "
                  "base-apps/admission-policies/agent-capability-taxonomy.yaml first: "
                  "unclassified tools are denied by design (fail-closed).\""),
            _deny("variables.cls != 'read' || "
                  + names.format(cond="!(n in variables.mutating) && !(n in variables.propose)"),
                  tpl + "is class read but binds propose or mutating tools. A read agent may bind "
                  "only read-classified tools.\""),
            _deny("variables.cls != 'propose' || " + names.format(cond="!(n in variables.mutating)"),
                  tpl + "is class propose but binds write or destructive tools. A propose agent "
                  "may bind read and propose tools only.\""),
            _deny("variables.cls != 'write' || " + names.format(cond="!(n in variables.destructive)"),
                  tpl + "is class write but binds destructive tools. Only an admin agent may "
                  "bind destructive tools.\""),
            _deny("variables.mcp.all(b, b.?requireApproval.orValue(false) || "
                  "b.?tools.orValue([]).all(n, !(n in variables.mutating)))",
                  tpl + "binds write/destructive tools in an MCP binding without "
                  "requireApproval: true. Approval is per binding: bind the server twice, read "
                  "tools ungated and every mutating tool in the gated binding.\""),
        ],
    )

    delegation = _policy(
        "agent-capability-delegation", "agenttemplates",
        reporting=False,
        param_kind={"apiVersion": f"{GROUP}/{VERSION}", "kind": "AgentTemplate"},
        # Only read, propose and write templates are constrained; admin may bind any subagent.
        object_selector={"matchExpressions": [
            {"key": CLASS_LABEL, "operator": "In", "values": ["read", "propose", "write"]}]},
        variables=[
            {"name": "mine",
             "expression": f"object.metadata.?labels[?'{CLASS_LABEL}'].orValue('')"},
            {"name": "delegates",
             "expression": "object.spec.?tools.orValue([]).filter(t, has(t.subAgent))"
                           ".map(t, t.subAgent.?templateRef.?name.orValue(''))"},
            # A missing or unrecognised class on the subagent template is treated as admin.
            {"name": "theirs",
             "expression": f"params.metadata.?labels[?'{CLASS_LABEL}'].orValue('admin') in "
                           f"['read', 'propose', 'write'] ? params.metadata.labels['{CLASS_LABEL}'] : 'admin'"},
            {"name": "delegatesToParam",
             "expression": "params.metadata.name in variables.delegates"},
        ],
        validations=[
            _deny("!(variables.mine == 'read' && variables.delegatesToParam) || variables.theirs == 'read'",
                  tpl + "is class read but binds subagent template '\" + params.metadata.name + "
                  "\"' (class \" + variables.theirs + \"). A subagent runs inside the parent and "
                  "takes the conversation over, so this is a privilege escalation. Bind read "
                  "tools directly instead (see homelab-knowledge).\""),
            _deny("!(variables.mine == 'propose' && variables.delegatesToParam) || "
                  "variables.theirs in ['read', 'propose']",
                  tpl + "is class propose but binds subagent template '\" + params.metadata.name + "
                  "\"' (class \" + variables.theirs + \"). A subagent runs inside the parent, so this "
                  "is a privilege escalation.\""),
            _deny("!(variables.mine == 'write' && variables.delegatesToParam) || variables.theirs != 'admin'",
                  tpl + "is class write but binds admin subagent template '\" + "
                  "params.metadata.name + \"'. A subagent runs inside the parent, so this is a "
                  "privilege escalation.\""),
        ],
    )

    agent = "\"Agent '\" + object.metadata.name + \"' "
    refs = _policy(
        "agent-capability-refs", "agents",
        variables=[],
        validations=[
            _deny("has(object.spec.template) && has(object.spec.template.ref)",
                  agent + "has an inline spec.template.inline. Reference an AgentTemplate with "
                  "spec.template.ref: capability (class, tools, approval gates, subagents) is "
                  "checked on AgentTemplates, so an inline template would bypass it.\""),
        ],
    )

    harness = "\"Harness '\" + object.metadata.name + \"' "
    runtime = _policy(
        "agent-capability-runtime", "harnesses",
        variables=[
            {"name": "images", "expression": _cel_list(runtime_images)},
            # Exact images, compared with `in` (string equality), never as prefixes.
            {"name": "byoImages", "expression": _cel_list(byo_images)},
            {"name": "image", "expression": "object.spec.?workload.?image.orValue('')"},
        ],
        validations=[
            _deny("has(object.spec.kagent) || has(object.spec.byo)",
                  harness + "must use the kagent runtime (spec.kagent). Per-binding tool "
                  "selection and requireApproval are enforced inside kagent's ADK runtimes; a "
                  "claude or codex runtime need not honour them. The one exception is byo on an "
                  "image listed in the taxonomy's byo-images.\""),
            _deny("!has(object.spec.kagent) || variables.images.exists(r, "
                  "variables.image.startsWith(r + '@') || variables.image.startsWith(r + ':'))",
                  harness + "runs image '\" + variables.image + \"', which is not a kagent ADK "
                  "runtime. Allowed repositories are the taxonomy's runtime-images "
                  "(base-apps/admission-policies/agent-capability-taxonomy.yaml).\""),
            _deny("!has(object.spec.byo) || variables.image in variables.byoImages",
                  harness + "is a byo runtime on image '\" + variables.image + \"', which is "
                  "not exactly an entry of the taxonomy's byo-images. A byo runtime need not "
                  "honour tool selection or requireApproval, so only a reviewed image pinned by "
                  "digest runs as byo (SPEC V.101).\""),
        ],
    )

    return [
        capability,
        _binding("agent-capability"),
        delegation,
        # Every AgentTemplate in the request's namespace is a parameter; with none, allow.
        _binding("agent-capability-delegation",
                 {"paramRef": {"selector": {}, "parameterNotFoundAction": "Allow"}}),
        refs,
        _binding("agent-capability-refs"),
        runtime,
        _binding("agent-capability-runtime"),
    ]


def render_native(repo: Path) -> str:
    return NATIVE_HEADER + yaml.safe_dump_all(
        build_native(*load_taxonomy(repo)),
        sort_keys=False, width=10_000, default_flow_style=False,
    )


def load_taxonomy(repo: Path) -> tuple[list[str], ...]:
    """(read, propose, write, destructive, runtime-images, byo-images). A missing key fails
    (KeyError): the CI validator names it. Entry shapes are the validator's job too."""
    cm = next(
        d for d in yaml.safe_load_all((repo / TAXONOMY).read_text())
        if d and d.get("kind") == "ConfigMap"
    )
    keys = ("read", "propose", "write", "destructive", "runtime-images", "byo-images")
    return tuple(json.loads(cm["data"][key]) for key in keys)


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
