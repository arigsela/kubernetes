#!/usr/bin/env python3
"""Validate the agent-capability contract (L03 Security guardrails) for kagent 1.0.

kagent 1.0 (api.kagent.dev/v1alpha3) splits an agent into an AgentTemplate (what: model,
prompt, tools, subagents), a Harness (how: runtime) and an Agent that references both.
Capability lives on the AgentTemplate, and so does its class label. Invariants, checked
against the manifests in git:

  1. Declared class     every AgentTemplate carries capability.homelab/class in
                        {read, propose, write, admin}. No default; missing is an error.
                        `propose` (2026-10-14) binds read tools and the taxonomy's five
                        proposal tools (branches and pull requests), ungated: the merge
                        is the human gate (§V.94).
  2. Tools within class every bound tool is classified in the taxonomy, and its
                        classification is permitted by the template's class. A tool
                        absent from the taxonomy is an error (FAIL-CLOSED).
  3. Mutating gated     every write/destructive tool sits in an MCP binding with
                        requireApproval: true. 1.0 gates per BINDING, not per tool, so a
                        template that needs both binds the server twice.
  4. No escalation      for every subAgent, the subagent template's effective class is
                        <= this template's class. Effective capability is transitive.
  5. Refs only          every Agent references an AgentTemplate and a Harness by name, both in
                        git; an inline spec.template / spec.harness would bypass 1-4, 6 and 7.
  6. Trusted runtime    every Harness uses the `kagent` runtime and an image from the
                        taxonomy's runtime-images, or the `byo` runtime on an image that is
                        EXACTLY an entry of byo-images (repository@sha256:digest). 1-3 are
                        enforced INSIDE kagent's runtimes (tool selection, the approval pause);
                        byo/claude/codex need not honour them. claude and codex: never.
  7. BYO conditions     every Agent whose harnessRef names a byo Harness lives in `kagent` and
                        uses an AgentTemplate of class `read` whose every tool entry is an MCP
                        binding of a RemoteMCPServer in the taxonomy's read-only-servers (the
                        ones in `kagent`), binding only `read` tools (no subAgent). Egress opens per ORIGIN, so a byo runtime
                        can call any tool a bound server has: the servers must be read-only on
                        the server side (tests/agent-capability proves it per server). Every
                        byo-images entry is a real sha256 digest (REPLACE_WITH_DIGEST fails),
                        and every read-only server exists in git.

This is the authoritative gate. The native admission policies
(base-apps/admission-policies/agent-capability.yaml) enforce 1-3, 5, 6 and one hop of 4.
The transitive closure of 4 and all of 7 span several objects, so CI is where they are
decided; agent-definitions-from-git.yaml makes git the only way in, so what CI saw is what
runs (SPEC §V.101).

FAIL CLOSED (SPEC §V.84): a kagent manifest of a kind/version this script does not
understand (e.g. the 0.10 kagent.dev/v1alpha2 Agent), an unparsable kagent file, zero
AgentTemplates, a missing byo-images or read-only-servers key, or an Agent whose Harness is
not in git is an error, never a vacuous pass.

Exit 0 clean, 1 on any error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

CLASS_LABEL = "capability.homelab/class"

# read < propose < write < admin
CLASS_RANK = {"read": 0, "propose": 1, "write": 2, "admin": 3}

# What each class may bind. `propose` (branches and pull requests only, §V.94) is bindable
# without approval; it is the one class between read and write.
CLASS_MAY_BIND = {
    "read": {"read"},
    "propose": {"read", "propose"},
    "write": {"read", "propose", "write"},
    "admin": {"read", "propose", "write", "destructive"},
}

# Classifications that must sit in a requireApproval: true binding wherever bound.
MUST_APPROVE = {"write", "destructive"}

TAXONOMY_PATH = "base-apps/admission-policies/agent-capability-taxonomy.yaml"

# The kagent manifests this validator understands. Anything else in a kagent API group fails.
KNOWN_KAGENT = {
    ("api.kagent.dev/v1alpha3", "Agent"),
    ("api.kagent.dev/v1alpha3", "AgentTemplate"),
    ("api.kagent.dev/v1alpha3", "Harness"),
    ("api.kagent.dev/v1alpha3", "ModelConfig"),
    ("api.kagent.dev/v1alpha3", "RemoteMCPServer"),
}
KAGENT_GROUPS = ("kagent.dev", "api.kagent.dev")
RUNTIMES = ("kagent", "byo", "claude", "codex")

# A byo-images entry: repository@sha256:<64 lowercase hex>. The repository has no tag (no ':'
# after its last '/'); a registry port before it is fine.
BYO_IMAGE = re.compile(r"[^\s@]+/[^\s@:/]+@sha256:[a-f0-9]{64}")
# Where byo Agents live and read-only-servers are defined (and proved read-only).
BYO_NAMESPACE = "kagent"
DIGEST_PLACEHOLDER = "REPLACE_WITH_DIGEST"
PIN_HOWTO = ("push the image, then pin its digest: admission-policies runbook, "
             "'Pin a byo image digest'")


def _name(doc: dict) -> str:
    return (doc.get("metadata") or {}).get("name", "<unnamed>")


def _key(doc: dict) -> tuple[str, str]:
    meta = doc.get("metadata") or {}
    return meta.get("namespace", ""), meta.get("name", "<unnamed>")


def _taxonomy_data(repo_root: Path) -> dict:
    path = repo_root / TAXONOMY_PATH
    if not path.exists():
        raise SystemExit(f"taxonomy not found: {TAXONOMY_PATH}")
    cm = next(
        (d for d in yaml.safe_load_all(path.read_text())
         if isinstance(d, dict) and d.get("kind") == "ConfigMap"),
        None,
    )
    if cm is None:
        raise SystemExit(f"no ConfigMap in {TAXONOMY_PATH}")
    return cm.get("data") or {}


def load_byo_conditions(repo_root: Path) -> tuple[list[str], list[str], list[str]]:
    """(byo-images, read-only-servers, errors) from the taxonomy. Both keys are required (an
    empty list `[]` admits no byo Harness); every byo-images entry must be an exact
    repository@sha256:digest, never a repository, a tag or the pre-push placeholder."""
    data = _taxonomy_data(repo_root)
    out: list[list[str]] = []
    errors: list[str] = []
    for key in ("byo-images", "read-only-servers"):
        if key not in data:
            errors.append(f"taxonomy missing '{key}' key ({TAXONOMY_PATH}); an empty list [] "
                          f"admits no byo Harness, a missing one is not understood (§V.84)")
            out.append([])
            continue
        items = json.loads(data[key] or "[]")
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            errors.append(f"taxonomy '{key}' must be a JSON list of strings")
            items = []
        out.append(items)
    for entry in out[0]:
        if entry.endswith("@sha256:" + DIGEST_PLACEHOLDER):
            errors.append(f"byo-images entry {entry!r} still carries the placeholder "
                          f"{DIGEST_PLACEHOLDER}, so nothing can deploy on it yet: {PIN_HOWTO}")
        elif not BYO_IMAGE.fullmatch(entry):
            errors.append(f"byo-images entry {entry!r} is not repository@sha256:<digest>. Entries "
                          f"are exact images, never a repository (it would admit every digest "
                          f"pushed there later) or a tag (it moves)")
    return out[0], out[1], errors


def load_taxonomy(repo_root: Path) -> tuple[dict[str, str], list[str]]:
    """({tool_name: classification}, runtime image repositories). Same content the
    admission policy embeds."""
    data = _taxonomy_data(repo_root)

    tool_class: dict[str, str] = {}
    for classification in ("read", "propose", "write", "destructive"):
        raw = data.get(classification)
        if not raw:
            raise SystemExit(f"taxonomy missing '{classification}' key")
        for tool in json.loads(raw):
            if tool in tool_class:
                raise SystemExit(
                    f"tool {tool!r} classified twice "
                    f"({tool_class[tool]} and {classification})"
                )
            tool_class[tool] = classification
    images = json.loads(data.get("runtime-images") or "[]")
    if not images:
        raise SystemExit("taxonomy missing 'runtime-images' key")
    return tool_class, images


def collect(repo_root: Path) -> tuple[dict[str, list[tuple[Path, dict]]], list[str]]:
    """{kind: [(path, doc)]} for every understood kagent manifest under base-apps/, found by
    apiVersion (not directory), plus fail-closed errors for anything not understood."""
    by_kind: dict[str, list[tuple[Path, dict]]] = {}
    errors: list[str] = []
    base = repo_root / "base-apps"
    for path in sorted(base.rglob("*.yaml")) if base.is_dir() else []:
        text = path.read_text()
        try:
            docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
        except yaml.YAMLError as exc:
            if "kagent.dev" in text:
                errors.append(f"{path}: does not parse ({exc.__class__.__name__}) and mentions "
                              f"kagent.dev. An unreadable agent manifest is not a valid one.")
            continue
        for doc in docs:
            if str(doc.get("apiVersion", "")).split("/")[0] not in KAGENT_GROUPS:
                continue
            key = (doc.get("apiVersion"), doc.get("kind"))
            if key not in KNOWN_KAGENT:
                errors.append(
                    f"{_name(doc)} ({path}): {key[0]} {key[1]} is a kagent manifest this "
                    f"validator does not understand, so it fails closed. Teach "
                    f"scripts/validate-agent-capability.py (and the admission policies) the "
                    f"new kind/version first (SPEC §V.84).")
                continue
            by_kind.setdefault(doc["kind"], []).append((path, doc))
    return by_kind, errors


def collect_templates(repo_root: Path) -> list[tuple[Path, dict]]:
    return collect(repo_root)[0].get("AgentTemplate", [])


def _bindings(doc: dict) -> list[dict]:
    return [t["mcp"] or {} for t in (doc.get("spec") or {}).get("tools") or [] if "mcp" in t]


def _template_class(doc: dict) -> str | None:
    return ((doc.get("metadata") or {}).get("labels") or {}).get(CLASS_LABEL)


def _subagents(doc: dict) -> list[str]:
    out = []
    for tool in (doc.get("spec") or {}).get("tools") or []:
        if "subAgent" in tool:
            name = ((tool.get("subAgent") or {}).get("templateRef") or {}).get("name")
            if name:
                out.append(name)
    return out


def check_declared_class(templates) -> list[str]:
    """Invariant 1."""
    errors = []
    for path, doc in templates:
        cls = _template_class(doc)
        if cls is None:
            errors.append(
                f"{_name(doc)}: missing label {CLASS_LABEL} ({path}). "
                f"Every AgentTemplate must declare a capability class; there is no default."
            )
        elif cls not in CLASS_RANK:
            errors.append(
                f"{_name(doc)}: invalid {CLASS_LABEL}={cls!r} ({path}). "
                f"Must be one of: read, propose, write, admin."
            )
    return errors


def check_tools_within_class(templates, taxonomy) -> list[str]:
    """Invariants 2 and 3."""
    errors = []
    for _path, doc in templates:
        name = _name(doc)
        cls = _template_class(doc)
        if cls not in CLASS_RANK:
            continue  # already reported by invariant 1
        may_bind = CLASS_MAY_BIND[cls]

        for binding in _bindings(doc):
            server = (binding.get("server") or {}).get("name", "<unnamed-server>")
            gated = binding.get("requireApproval") is True

            for tool in binding.get("tools") or []:
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

                # Invariant 3 — mutating tools only in a gated binding.
                if classification in MUST_APPROVE and not gated:
                    errors.append(
                        f"{name}: {classification} tool {tool!r} (server {server}) is in a "
                        f"binding without requireApproval: true. Move it to the gated binding."
                    )
    return errors


def check_no_escalation(templates) -> list[str]:
    """Invariant 4 — a subagent is capability-transitive.

    A template's effective class is the max of its declared class and the effective class
    of every subagent template it binds. Declaring below your effective class is an
    escalation path and is rejected.
    """
    errors = []
    by_name = {_name(doc): doc for _, doc in templates}

    def effective(name: str, seen: tuple[str, ...]) -> int:
        """Max class rank reachable from `name`, following subagent bindings."""
        if name in seen:
            errors.append(f"subagent cycle: {' -> '.join(seen + (name,))}")
            return CLASS_RANK["read"]
        doc = by_name.get(name)
        if doc is None:
            return CLASS_RANK["read"]  # dangling ref, reported separately
        ranks = [CLASS_RANK.get(_template_class(doc) or "", CLASS_RANK["admin"])]
        for d in _subagents(doc):
            ranks.append(effective(d, seen + (name,)))
        return max(ranks)

    for _, doc in templates:
        name = _name(doc)
        cls = _template_class(doc)
        if cls not in CLASS_RANK:
            continue

        for sub in _subagents(doc):
            if sub not in by_name:
                errors.append(
                    f"{name}: binds subagent template {sub!r}, which does not exist "
                    f"in git. Dangling delegation."
                )
                continue
            d_eff = effective(sub, (name,))
            if d_eff > CLASS_RANK[cls]:
                d_cls = next(k for k, v in CLASS_RANK.items() if v == d_eff)
                errors.append(
                    f"{name}: class {cls!r} binds subagent {sub!r} whose effective class is "
                    f"{d_cls!r} — privilege escalation. A template cannot bind a subagent "
                    f"above its own class."
                )
    return errors


def check_refs_only(agents, templates, harnesses) -> list[str]:
    """Invariant 5."""
    errors = []
    known = {_name(doc) for _, doc in templates}
    known_harnesses = {_key(doc) for _, doc in harnesses}
    for path, doc in agents:
        spec = doc.get("spec") or {}
        tpl = spec.get("template") or {}
        if "inline" in tpl:
            errors.append(
                f"Agent {_name(doc)} ({path}): inline spec.template is not allowed. Reference an "
                f"AgentTemplate by name with spec.template.ref; an inline template bypasses the "
                f"capability checks.")
        else:
            ref = (tpl.get("ref") or {}).get("name")
            if ref not in known:
                errors.append(f"Agent {_name(doc)}: spec.template.ref {ref!r} is not an "
                              f"AgentTemplate in git")
        if "harness" in spec:
            errors.append(f"Agent {_name(doc)} ({path}): inline spec.harness. Reference a Harness "
                          f"by name; an inline harness bypasses the capability checks.")
        href = (spec.get("harnessRef") or {}).get("name")
        if "harness" not in spec and (_key(doc)[0], href) not in known_harnesses:
            errors.append(f"Agent {_name(doc)}: harnessRef {href!r} is not a Harness in git in "
                          f"its namespace, so its runtime (and whether invariant 7 applies) is "
                          f"unknown")
    return errors


def check_runtime(harnesses, images, byo_images) -> list[str]:
    """Invariant 6."""
    errors = []
    for path, doc in harnesses:
        spec = doc.get("spec") or {}
        runtimes = [r for r in RUNTIMES if r in spec]
        image = (spec.get("workload") or {}).get("image") or ""
        if runtimes == ["byo"]:
            # Exact string equality: no prefix, no repository, no tag (Codex C#3).
            if image not in byo_images:
                errors.append(
                    f"Harness {_name(doc)} ({path}): byo runtime on image {image!r}, which is "
                    f"not exactly an entry of the taxonomy's byo-images {byo_images}. A byo "
                    f"runtime need not honour tool selection or requireApproval, so only a "
                    f"reviewed image pinned by digest runs as byo.")
            continue
        if runtimes != ["kagent"]:
            errors.append(
                f"Harness {_name(doc)} ({path}): runtime {runtimes or 'none'}; must be `kagent`, "
                f"or `byo` on a byo-images entry. Tool selection and requireApproval are "
                f"enforced inside kagent's ADK runtimes only.")
        if not any(image.startswith(r + "@") or image.startswith(r + ":") for r in images):
            errors.append(
                f"Harness {_name(doc)} ({path}): image {image!r} is not one of the taxonomy's "
                f"runtime-images {images}.")
        elif "@sha256:" not in image:
            errors.append(f"Harness {_name(doc)} ({path}): image {image!r} is not pinned by "
                          f"digest.")
    return errors


def check_read_only_servers_exist(read_only_servers, servers) -> list[str]:
    """Invariant 7: a listed server is a RemoteMCPServer in git, in BYO_NAMESPACE, where its
    url is reviewed and its server-side read-only proof applies."""
    in_git = {name for ns, name in (_key(doc) for _, doc in servers) if ns == BYO_NAMESPACE}
    return [f"read-only-servers lists {s!r}, which is not a RemoteMCPServer in namespace "
            f"{BYO_NAMESPACE} in git" for s in read_only_servers if s not in in_git]


def check_byo_agents(agents, templates, harnesses, read_only_servers, taxonomy) -> list[str]:
    """Invariant 7: every Agent on a byo Harness lives in BYO_NAMESPACE and runs a `read`
    template that binds only read-only servers, with only read tools and no subAgent. An
    Agent's refs resolve in its own namespace."""
    errors = []
    byo = {_key(doc) for _, doc in harnesses if "byo" in (doc.get("spec") or {})}
    by_key = {_key(doc): doc for _, doc in templates}
    for _path, doc in agents:
        spec = doc.get("spec") or {}
        ns = _key(doc)[0]
        href = (spec.get("harnessRef") or {}).get("name")
        tref = (spec.get("templateRef") or {}).get("name")
        if (ns, href) not in byo:
            continue
        where = f"Agent {ns}/{_name(doc)} runs on byo Harness {href!r}, so its AgentTemplate {tref!r}"
        if ns != BYO_NAMESPACE:
            errors.append(f"Agent {ns}/{_name(doc)} runs on byo Harness {href!r} outside namespace "
                          f"{BYO_NAMESPACE}, where read-only-servers are defined and proved.")
            continue
        tpl = by_key.get((ns, tref))
        if tpl is None:
            errors.append(f"{where} is not in git in namespace {ns}.")
            continue
        cls = _template_class(tpl)
        if cls != "read":
            errors.append(f"{where} must be class 'read', not {cls!r}: a byo runtime need not "
                          f"honour per-binding tool selection or requireApproval.")
        for tool in (tpl.get("spec") or {}).get("tools") or []:
            if "mcp" not in tool:
                errors.append(f"{where} may bind only MCP servers, not {sorted(tool)}: a "
                              f"subAgent is compiled into the byo runtime with its own bindings.")
                continue
            binding = tool.get("mcp") or {}
            server = binding.get("server") or {}
            if server.get("kind") != "RemoteMCPServer" or server.get("name") not in read_only_servers:
                errors.append(
                    f"{where} binds server {server.get('kind')}/{server.get('name')}, which is not "
                    f"in the taxonomy's read-only-servers {read_only_servers}. Egress opens per "
                    f"origin, so the runtime could call every tool that server has.")
            for name in binding.get("tools") or []:
                if taxonomy.get(name) != "read":
                    errors.append(f"{where} binds {taxonomy.get(name) or 'unclassified'} tool "
                                  f"{name!r}; only read tools.")
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

    taxonomy, images = load_taxonomy(repo_root)
    byo_images, read_only_servers, errors = load_byo_conditions(repo_root)
    by_kind, collect_errors = collect(repo_root)
    errors += collect_errors
    templates = by_kind.get("AgentTemplate", [])
    agents = by_kind.get("Agent", [])
    harnesses = by_kind.get("Harness", [])

    if not templates:
        errors.append("no AgentTemplate manifests found under base-apps/ — a guardrail that "
                      "finds nothing to check must not pass (SPEC §V.84)")

    errors += check_declared_class(templates)
    errors += check_tools_within_class(templates, taxonomy)
    errors += check_no_escalation(templates)
    errors += check_refs_only(agents, templates, harnesses)
    errors += check_runtime(harnesses, images, byo_images)
    errors += check_read_only_servers_exist(read_only_servers,
                                            by_kind.get("RemoteMCPServer", []))
    errors += check_byo_agents(agents, templates, harnesses, read_only_servers, taxonomy)

    print(
        f"agent-capability: {len(templates)} templates, {len(agents)} agents, "
        f"{len(taxonomy)} classified tools"
    )
    for _, doc in sorted(templates, key=lambda a: _name(a[1])):
        subs = _subagents(doc)
        suffix = f" -> subagents {', '.join(subs)}" if subs else ""
        print(f"  {_template_class(doc) or 'UNDECLARED':<11} {_name(doc)}{suffix}")

    if errors:
        print(f"\n{len(errors)} error(s):", file=sys.stderr)
        for e in errors:
            print(f"  ERROR {e}", file=sys.stderr)
        return 1

    print("\nagent-capability contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
