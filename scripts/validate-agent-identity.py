#!/usr/bin/env python3
"""Validate the agent-identity ("agent principal") contract for kagent 1.0 agents.

An agent-principal = (credentials it can obtain) + (its model) + (its capability
surface). Three invariants (see templates/agent-identity/README.md), and EVERY
agent and credential is held to all three:

  1. Scoped credentials: every ExternalSecret resolves through a dedicated scoped
     SecretStore + per-consumer Vault key — never the broad 'vault-backend' store,
     never the monolithic 'kagent' key.
  2. In-git model identity: every ModelConfig an AgentTemplate or a Harness names
     (spec.modelConfig, spec.kagent.memory.modelConfigRef,
     spec.kagent.compaction.summarizer.modelConfigRef) is a manifest in git
     (chart-rendered configs exempt — see below).
  3. Capability surface: every AgentTemplate MCP binding names a RemoteMCPServer in
     git and lists its tools explicitly (an empty list is a bind-all); every subAgent,
     Agent template.ref and Agent harnessRef resolves to an object in git; no Agent
     carries an inline template or harness, which would bypass the checks above.

FAIL CLOSED (SPEC §V.84). kagent 1.0 moved the agent API from kagent.dev/v1alpha2
(Agent.spec.declarative) to api.kagent.dev/v1alpha3 (AgentTemplate + Harness + Agent).
A validator that only knew the old shape would have found zero agents and passed. So:
any kagent manifest in base-apps/ of a kind/version this script does not understand is
an ERROR, an unparsable file that mentions kagent.dev is an ERROR, and finding no
AgentTemplate or no Agent is an ERROR. Teach the validator a new kind before shipping it.

NO PILOT STAGING. Every violation is a hard error, whichever agent it is on. (The
staged version once let an implicit bind-all on k8s-agent through as a warning.)

Exit 1 on any error.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

BROAD_SECRETSTORE = "vault-backend"
MONOLITHIC_VAULT_KEY = "kagent"

# The kagent manifests this validator understands: (apiVersion, kind). Anything else in a
# kagent API group is an error, not a skip.
KNOWN_KAGENT = {
    ("api.kagent.dev/v1alpha3", "Agent"),
    ("api.kagent.dev/v1alpha3", "AgentTemplate"),
    ("api.kagent.dev/v1alpha3", "Harness"),
    ("api.kagent.dev/v1alpha3", "ModelConfig"),
    ("api.kagent.dev/v1alpha3", "RemoteMCPServer"),
}
KAGENT_GROUPS = ("kagent.dev", "api.kagent.dev")

# ModelConfigs rendered by the kagent Helm chart rather than declared as their own
# manifest. Exempt, and legitimately so: the invariant exists to catch identity that is
# INVISIBLE TO REVIEW (a ModelConfig "applied by hand"). default-model-config is rendered
# from the `providers:` values in base-apps/kagent.yaml, so it is version-controlled and
# reviewed. (No agent references it after the 1.0 cutover; the exemption stays because
# the reasoning does.)
CHART_GENERATED_MODELCONFIGS = {"default-model-config"}
# MCP servers rendered by the kagent Helm chart rather than declared in git. Exempt for the
# same reason: versioned via the chart's targetRevision. Adopting them into git would put
# the chart and the kagent-secrets Argo app in a tug-of-war over one object — the failure
# mode that once silently stripped the agents' HITL approval gates on every sync.
# Invariant 3's real teeth are the explicit tools check, which still applies to these.
CHART_PROVIDED_MCPSERVERS = {"kagent-tool-server"}


def _group(doc: dict) -> str:
    return str(doc.get("apiVersion", "")).split("/")[0]


def _name(doc: dict) -> str | None:
    return (doc.get("metadata") or {}).get("name")


def load_manifests(repo_root: Path) -> tuple[list[tuple[Path, dict]], list[str]]:
    """Every YAML document under base-apps/, plus errors for kagent files that do not parse.

    Repo-wide on purpose: an agent is found by WHAT it is, not where it lives. A kagent
    manifest placed outside base-apps/kagent/ is still validated (and still deployed).
    """
    docs: list[tuple[Path, dict]] = []
    errors: list[str] = []
    base = repo_root / "base-apps"
    if not base.is_dir():
        return docs, errors
    for path in sorted(base.rglob("*.yaml")):
        text = path.read_text()
        try:
            loaded = list(yaml.safe_load_all(text))
        except yaml.YAMLError as exc:
            if "kagent.dev" in text:
                errors.append(f"{path}: does not parse ({exc.__class__.__name__}), and it "
                              f"mentions kagent.dev. An unreadable agent manifest is not a "
                              f"valid one.")
            continue
        docs.extend((path, d) for d in loaded if isinstance(d, dict))
    return docs, errors


def kagent_objects(docs) -> tuple[dict[str, list[tuple[Path, dict]]], list[str]]:
    """{kind: [(path, doc)]} for the understood kagent kinds; errors for everything else."""
    by_kind: dict[str, list[tuple[Path, dict]]] = {}
    errors: list[str] = []
    for path, doc in docs:
        if _group(doc) not in KAGENT_GROUPS:
            continue
        key = (doc.get("apiVersion"), doc.get("kind"))
        if key not in KNOWN_KAGENT:
            errors.append(
                f"{_name(doc)} ({path}): {key[0]} {key[1]} is a kagent manifest this validator "
                f"does not understand. It cannot vouch for it, so it fails closed: teach "
                f"scripts/validate-agent-identity.py (and the admission policies) the new "
                f"kind/version first (SPEC §V.84).")
            continue
        by_kind.setdefault(doc["kind"], []).append((path, doc))
    return by_kind, errors


def collect_by_kind(repo_root: Path) -> dict[str, list[tuple[Path, dict]]]:
    docs, _ = load_manifests(repo_root)
    return kagent_objects(docs)[0]


def collect_agents(repo_root: Path) -> list[tuple[Path, dict]]:
    """Every kagent 1.0 Agent in git, found by KIND — not by directory."""
    return collect_by_kind(repo_root).get("Agent", [])


def collect_templates(repo_root: Path) -> list[tuple[Path, dict]]:
    return collect_by_kind(repo_root).get("AgentTemplate", [])


def check_credential_scoping(repo_root: Path) -> list[str]:
    """Invariant 1.

    Two different scopes, deliberately:

    * The monolithic `kagent` Vault key is checked REPO-WIDE. That key is destroyed;
      nothing legitimate reads it from any namespace. (postgresql/kagent-db-credentials
      was a kagent credential living in another namespace, and broke silently.)
    * The broad `vault-backend` SecretStore is only checked inside base-apps/kagent/.
      `vault-backend` is a legitimate PER-NAMESPACE SecretStore name used by ~30
      healthy ExternalSecrets across the cluster. Only the kagent one was retired.
    """
    errors: list[str] = []
    kagent_dir = repo_root / "base-apps" / "kagent"
    docs, _ = load_manifests(repo_root)

    for path, doc in docs:
        if doc.get("kind") != "ExternalSecret":
            continue
        name = _name(doc)
        spec = doc.get("spec") or {}
        store = (spec.get("secretStoreRef") or {}).get("name")
        keys = {
            (ref.get("remoteRef") or {}).get("key")
            for ref in (spec.get("data") or [])
        }

        if MONOLITHIC_VAULT_KEY in keys:
            errors.append(
                f"{name} ({path}): reads the monolithic Vault key "
                f"{MONOLITHIC_VAULT_KEY!r}, which is DESTROYED. It held every "
                f"consumer's credential at once. Use a per-consumer key "
                f"(k8s-secrets/<consumer>) with its own ESO ServiceAccount, "
                f"SecretStore and Vault role. A kagent credential does not stop "
                f"being kagent's because it is declared in another namespace."
            )

        in_kagent = kagent_dir in path.parents or path.parent == kagent_dir
        if in_kagent and store == BROAD_SECRETSTORE:
            errors.append(
                f"{name} ({path}): uses the broad {BROAD_SECRETSTORE!r} SecretStore, "
                f"which is retired in the kagent namespace. Every credential must "
                f"resolve through a dedicated, path-scoped SecretStore."
            )
    return errors


def _model_refs(kind: str, doc: dict) -> list[tuple[str, str]]:
    """(field, ModelConfig name) pairs an AgentTemplate or Harness references."""
    spec = doc.get("spec") or {}
    refs: list[tuple[str, str]] = []
    if kind == "AgentTemplate":
        name = (spec.get("modelConfig") or {}).get("name")
        if name:
            refs.append(("spec.modelConfig", name))
    elif kind == "Harness":
        kagent = spec.get("kagent") or {}
        name = ((kagent.get("memory") or {}).get("modelConfigRef") or {}).get("name")
        if name:
            refs.append(("spec.kagent.memory.modelConfigRef", name))
        summarizer = (kagent.get("compaction") or {}).get("summarizer") or {}
        name = (summarizer.get("modelConfigRef") or {}).get("name")
        if name:
            refs.append(("spec.kagent.compaction.summarizer.modelConfigRef", name))
    return refs


def check_model_config_in_git(by_kind) -> list[str]:
    """Invariant 2."""
    errors: list[str] = []
    in_git = {_name(doc) for _p, doc in by_kind.get("ModelConfig", [])}
    for kind in ("AgentTemplate", "Harness"):
        for _path, doc in by_kind.get(kind, []):
            for field, ref in _model_refs(kind, doc):
                if ref in in_git or ref in CHART_GENERATED_MODELCONFIGS:
                    continue
                errors.append(
                    f"{kind} {_name(doc)}: {field} {ref!r} is not a ModelConfig manifest in "
                    f"git. An agent's model is part of its identity; it may not be applied "
                    f"out-of-band where review cannot see it."
                )
    return errors


def check_capability_surface(by_kind) -> list[str]:
    """Invariant 3."""
    errors: list[str] = []
    remote_mcp = {_name(doc) for _p, doc in by_kind.get("RemoteMCPServer", [])}
    templates = {_name(doc) for _p, doc in by_kind.get("AgentTemplate", [])}
    harnesses = {_name(doc) for _p, doc in by_kind.get("Harness", [])}

    for _path, doc in by_kind.get("AgentTemplate", []):
        name = _name(doc)
        for tool in (doc.get("spec") or {}).get("tools") or []:
            if "subAgent" in tool:
                ref = ((tool.get("subAgent") or {}).get("templateRef") or {}).get("name")
                if ref not in templates:
                    errors.append(
                        f"AgentTemplate {name}: subAgent templateRef {ref!r} is not an "
                        f"AgentTemplate in git — dangling delegation")
                continue
            if "mcp" not in tool:
                errors.append(f"AgentTemplate {name}: tool entry with neither mcp nor subAgent: "
                              f"{tool!r}")
                continue
            mcp = tool.get("mcp") or {}
            server = mcp.get("server") or {}
            ref = server.get("name")
            if server.get("kind") != "RemoteMCPServer":
                errors.append(
                    f"AgentTemplate {name}: MCP binding to {server.get('kind')!r} {ref!r}; "
                    f"kagent 1.0 binds only RemoteMCPServer (deploy with MCPServer, bind the "
                    f"RemoteMCPServer that points at it)")
            elif ref not in remote_mcp and ref not in CHART_PROVIDED_MCPSERVERS:
                errors.append(f"AgentTemplate {name}: references unknown RemoteMCPServer {ref!r}")
            if not mcp.get("tools"):
                errors.append(
                    f"AgentTemplate {name}: MCP binding {ref!r} lists no tools — an implicit "
                    f"bind-all. Every tool an agent may call must be named.")

    for _path, doc in by_kind.get("Agent", []):
        name = _name(doc)
        spec = doc.get("spec") or {}
        # alpha14 (kagent#3080): spec.template is required and holds exactly one of ref (an
        # AgentTemplate by name) or inline; an inline template or harness bypasses the checks.
        tpl = spec.get("template") or {}
        if "inline" in tpl:
            errors.append(
                f"Agent {name}: inline spec.template is not allowed; reference an AgentTemplate "
                f"with spec.template.ref: the contract is checked on AgentTemplates, so an "
                f"inline template would bypass it.")
        else:
            ref = (tpl.get("ref") or {}).get("name")
            if ref not in templates:
                errors.append(f"Agent {name}: spec.template.ref {ref!r} is not an AgentTemplate in git")
        if "harness" in spec:
            errors.append(
                f"Agent {name}: inline spec.harness. Reference a Harness with spec.harnessRef: "
                f"the contract is checked on Harnesses, so an inline harness would bypass it.")
        ref = (spec.get("harnessRef") or {}).get("name")
        if ref not in harnesses:
            errors.append(f"Agent {name}: spec.harnessRef {ref!r} is not a Harness in git")
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate the agent-identity contract.")
    parser.add_argument("--repo-root", default=".", type=Path)
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()

    docs, errors = load_manifests(repo_root)
    by_kind, unknown = kagent_objects(docs)
    errors += unknown
    errors += check_credential_scoping(repo_root)
    errors += check_model_config_in_git(by_kind)
    errors += check_capability_surface(by_kind)

    templates = by_kind.get("AgentTemplate", [])
    agents = by_kind.get("Agent", [])
    if not templates:
        errors.append("no AgentTemplate found under base-apps/: a guardrail that finds nothing "
                      "to check must not pass (SPEC §V.84)")
    if not agents:
        errors.append("no Agent found under base-apps/: a guardrail that finds nothing to "
                      "check must not pass (SPEC §V.84)")

    print(f"agent-identity: {len(agents)} agents, {len(templates)} templates, "
          f"{len(by_kind.get('Harness', []))} harnesses held to all three invariants")

    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        print(f"\nagent-identity validation FAILED with {len(errors)} error(s).")
        return 1
    print("\nagent-identity contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
