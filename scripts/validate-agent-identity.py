#!/usr/bin/env python3
"""Validate the agent-identity ("agent principal") contract for kagent agents.

An agent-principal = (credentials it can obtain) + (its model) + (its capability
surface). Three invariants (see templates/agent-identity/README.md), and EVERY
agent and credential is held to all three:

  1. Scoped credentials: every ExternalSecret resolves through a dedicated scoped
     SecretStore + per-consumer Vault key — never the broad 'vault-backend' store,
     never the monolithic 'kagent' key.
  2. In-git model identity: every modelConfig / memory.modelConfig resolves to a
     ModelConfig manifest in git (chart-rendered configs exempt — see below).
  3. Capability surface: every Agent McpServer tool ref resolves to an in-git MCP
     server and lists explicit toolNames (no implicit bind-all).

NO PILOT STAGING. This validator used to hold only a pilot agent + pilot
credential to a hard failure and downgrade everything else to a warning, so that
the gate could go green while the backlog stayed visible (design doc §44). That
backlog is now empty: every credential is scoped and the broad store is retired.

Keeping the staging after that point was actively harmful — it was a loaded gun.
Stripping every toolName from k8s-agent (an implicit bind-all on the agent holding
k8s_delete_resource and k8s_execute_command) produced a WARNING and exit 0. CI
stayed green on a real capability regression. Everything is a hard error now.

Exit 1 on any error.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

BROAD_SECRETSTORE = "vault-backend"
MONOLITHIC_VAULT_KEY = "kagent"

# ModelConfigs rendered by the kagent Helm chart rather than declared as their own
# manifest. Exempt, and legitimately so: the invariant exists to catch identity
# that is INVISIBLE TO REVIEW — the design doc's complaint (§13) was a ModelConfig
# "applied by hand, so it is invisible to GitOps and to review". default-model-config
# is the opposite of that. Its definition lives in git at base-apps/kagent.yaml
# (the `providers:` block: provider, model, apiKeySecretRef), is version-controlled,
# and every change to it goes through review. Adopting it as a standalone manifest
# would put the chart and Argo in a tug-of-war over the same object — the failure
# mode that silently stripped the agents' HITL requireApproval gates on every sync.
CHART_GENERATED_MODELCONFIGS = {"default-model-config"}
# MCP servers rendered by the kagent Helm chart rather than declared in git.
# Exempt for the same reason as CHART_GENERATED_MODELCONFIGS: they ARE
# declarative (versioned via base-apps/kagent.yaml's chart targetRevision), just
# not as standalone manifests. Adopting them into git would put the chart and
# the kagent-secrets Argo app in a tug-of-war over the same object — the failure
# mode that silently stripped the agents' HITL requireApproval gates on every
# sync. Invariant 3's real teeth are the explicit toolNames check below, which
# still applies to these refs.
CHART_PROVIDED_MCPSERVERS = {"kagent-tool-server", "kagent-grafana-mcp"}


def _load_docs(path: Path) -> list[dict]:
    try:
        return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]
    except yaml.YAMLError:
        return []


def collect_by_kind(repo_root: Path) -> dict[str, list[tuple[Path, dict]]]:
    out: dict[str, list[tuple[Path, dict]]] = {}
    kagent_dir = repo_root / "base-apps" / "kagent"
    if not kagent_dir.is_dir():
        return out
    for path in sorted(kagent_dir.rglob("*.yaml")):
        for doc in _load_docs(path):
            kind = doc.get("kind")
            if kind:
                out.setdefault(kind, []).append((path, doc))
    return out


def collect_agents(repo_root: Path) -> list[tuple[Path, dict]]:
    """Every kagent Agent in git, found by KIND — not by directory.

    This used to glob agents/*.yaml and then hardcode build-orchestrator.yaml,
    which lives one level up. That worked only because someone remembered to add
    the special case; the next agent placed outside agents/ would have been
    silently unvalidated. Find them by what they ARE.
    """
    kagent_dir = repo_root / "base-apps" / "kagent"
    agents: list[tuple[Path, dict]] = []
    if not kagent_dir.is_dir():
        return agents
    for path in sorted(kagent_dir.rglob("*.yaml")):
        for doc in _load_docs(path):
            if doc.get("kind") == "Agent":
                agents.append((path, doc))
    return agents


def check_credential_scoping(repo_root: Path) -> list[str]:
    errors: list[str] = []
    for _path, doc in collect_by_kind(repo_root).get("ExternalSecret", []):
        name = (doc.get("metadata") or {}).get("name")
        spec = doc.get("spec") or {}
        store = (spec.get("secretStoreRef") or {}).get("name")
        keys = {
            (ref.get("remoteRef") or {}).get("key")
            for ref in (spec.get("data") or [])
        }
        if store != BROAD_SECRETSTORE and MONOLITHIC_VAULT_KEY not in keys:
            continue
        errors.append(
            f"{name}: credential is not scoped (store={store!r}, "
            f"keys={sorted(k for k in keys if k)}) — every credential must use a "
            f"dedicated SecretStore and a per-consumer Vault key. The broad "
            f"{BROAD_SECRETSTORE!r} store and the monolithic {MONOLITHIC_VAULT_KEY!r} "
            f"key are retired."
        )
    return errors


def _agent_model_refs(doc: dict) -> list[str]:
    decl = (doc.get("spec") or {}).get("declarative") or {}
    refs = []
    if decl.get("modelConfig"):
        refs.append(decl["modelConfig"])
    mem = decl.get("memory") or {}
    if mem.get("modelConfig"):
        refs.append(mem["modelConfig"])
    return refs


def check_model_config_in_git(repo_root: Path) -> list[str]:
    errors: list[str] = []
    in_git = {
        (doc.get("metadata") or {}).get("name")
        for _path, doc in collect_by_kind(repo_root).get("ModelConfig", [])
    }
    for _path, doc in collect_agents(repo_root):
        name = (doc.get("metadata") or {}).get("name")
        for ref in _agent_model_refs(doc):
            if ref in in_git or ref in CHART_GENERATED_MODELCONFIGS:
                continue
            errors.append(
                f"{name}: modelConfig {ref!r} is not a ModelConfig manifest in git. "
                f"An agent's model is part of its identity; it may not be applied "
                f"out-of-band where review cannot see it."
            )
    return errors


def check_capability_surface(repo_root: Path) -> list[str]:
    errors: list[str] = []
    by_kind = collect_by_kind(repo_root)
    mcp_names = {
        (doc.get("metadata") or {}).get("name")
        for kind in ("RemoteMCPServer", "MCPServer")
        for _path, doc in by_kind.get(kind, [])
    }
    agent_names = {
        (doc.get("metadata") or {}).get("name")
        for _path, doc in collect_agents(repo_root)
    }
    for _path, doc in collect_agents(repo_root):
        name = (doc.get("metadata") or {}).get("name")
        decl = (doc.get("spec") or {}).get("declarative") or {}
        for tool in decl.get("tools") or []:
            kind = tool.get("type")
            if kind == "Agent":
                # A delegation to an agent that does not exist in git is an
                # undeclared capability surface just as much as an unknown MCP
                # server is. observability-agent carried a dangling ref to
                # promql-agent — an agent that does not exist and is disabled in
                # the chart — and nothing caught it.
                ref = (tool.get("agent") or {}).get("name")
                if ref and ref not in agent_names:
                    errors.append(
                        f"{name}: delegates to Agent {ref!r}, which does not exist "
                        f"in git — dangling delegation"
                    )
                continue
            if kind != "McpServer":
                continue
            mcp = tool.get("mcpServer") or {}
            ref = mcp.get("name")
            if ref not in mcp_names and ref not in CHART_PROVIDED_MCPSERVERS:
                errors.append(f"{name}: references unknown MCP server {ref!r}")
            if not mcp.get("toolNames"):
                errors.append(
                    f"{name}: MCP ref {ref!r} lists no toolNames — an implicit "
                    f"bind-all. Every tool an agent may call must be named."
                )
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate the agent-identity contract.")
    parser.add_argument("--repo-root", default=".", type=Path)
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()

    errors: list[str] = []
    for check in (
        check_credential_scoping,
        check_model_config_in_git,
        check_capability_surface,
    ):
        errors.extend(check(repo_root))

    agents = collect_agents(repo_root)
    print(f"agent-identity: {len(agents)} agents held to all three invariants")

    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        print(f"\nagent-identity validation FAILED with {len(errors)} error(s).")
        return 1
    print("\nagent-identity contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
