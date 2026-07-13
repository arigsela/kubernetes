#!/usr/bin/env python3
"""Validate the agent-identity ("agent principal") contract for kagent agents.

An agent-principal = (credentials it can obtain) + (its model) + (its capability
surface). Three invariants (see templates/agent-identity/README.md):
  1. Scoped credentials: pilot credential ExternalSecrets resolve through a
     dedicated scoped SecretStore + per-consumer Vault key (not 'vault-backend'
     / the monolithic 'kagent' key). Non-pilot unscoped credentials warn.
  2. In-git model identity: modelConfig/memory.modelConfig referenced by
     in-scope agents resolve to a ModelConfig manifest in git (chart-generated
     configs exempt). Pilot agent = hard fail; others warn.
  3. Capability surface: each Agent McpServer tool ref resolves to an in-git MCP
     server and lists non-empty toolNames. Pilot agent = hard fail; others warn.

Exit 1 on any hard error; warnings print but do not fail.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

BROAD_SECRETSTORE = "vault-backend"
MONOLITHIC_VAULT_KEY = "kagent"
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
    kagent_dir = repo_root / "base-apps" / "kagent"
    paths = sorted((kagent_dir / "agents").glob("*.yaml"))
    extra = kagent_dir / "build-orchestrator.yaml"
    if extra.is_file():
        paths.append(extra)
    agents: list[tuple[Path, dict]] = []
    for path in paths:
        for doc in _load_docs(path):
            if doc.get("kind") == "Agent":
                agents.append((path, doc))
    return agents


def load_scope(repo_root: Path) -> tuple[set[str], set[str]]:
    path = repo_root / "scripts" / "agent-identity-scope.txt"
    pilot_secrets: set[str] = set()
    pilot_agents: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, val = line.split(":", 1)
        key, val = key.strip(), val.strip()
        if key == "pilot-credential":
            pilot_secrets.add(val)
        elif key == "pilot-agent":
            pilot_agents.add(val)
    return pilot_secrets, pilot_agents


def check_credential_scoping(repo_root: Path, pilot_secrets: set[str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for _path, doc in collect_by_kind(repo_root).get("ExternalSecret", []):
        name = (doc.get("metadata") or {}).get("name")
        spec = doc.get("spec") or {}
        store = (spec.get("secretStoreRef") or {}).get("name")
        keys = {
            (ref.get("remoteRef") or {}).get("key")
            for ref in (spec.get("data") or [])
        }
        scoped = store != BROAD_SECRETSTORE and MONOLITHIC_VAULT_KEY not in keys
        if scoped:
            continue
        detail = (f"{name}: credential is not scoped (store={store!r}, "
                  f"keys={sorted(k for k in keys if k)})")
        if name in pilot_secrets:
            errors.append(detail + " — pilot credential must use a dedicated "
                          "SecretStore and a per-consumer Vault key")
        else:
            warnings.append(detail + " — backlog for a later identity increment")
    return errors, warnings


def _agent_model_refs(doc: dict) -> list[str]:
    decl = (doc.get("spec") or {}).get("declarative") or {}
    refs = []
    if decl.get("modelConfig"):
        refs.append(decl["modelConfig"])
    mem = decl.get("memory") or {}
    if mem.get("modelConfig"):
        refs.append(mem["modelConfig"])
    return refs


def check_model_config_in_git(repo_root: Path, pilot_agents: set[str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    in_git = {
        (doc.get("metadata") or {}).get("name")
        for _path, doc in collect_by_kind(repo_root).get("ModelConfig", [])
    }
    for _path, doc in collect_agents(repo_root):
        name = (doc.get("metadata") or {}).get("name")
        for ref in _agent_model_refs(doc):
            if ref in in_git or ref in CHART_GENERATED_MODELCONFIGS:
                continue
            detail = f"{name}: modelConfig {ref!r} is not a ModelConfig manifest in git"
            if name in pilot_agents:
                errors.append(detail)
            else:
                warnings.append(detail + " — backlog")
    return errors, warnings


def check_capability_surface(repo_root: Path, pilot_agents: set[str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    by_kind = collect_by_kind(repo_root)
    mcp_names = {
        (doc.get("metadata") or {}).get("name")
        for kind in ("RemoteMCPServer", "MCPServer")
        for _path, doc in by_kind.get(kind, [])
    }
    for _path, doc in collect_agents(repo_root):
        name = (doc.get("metadata") or {}).get("name")
        decl = (doc.get("spec") or {}).get("declarative") or {}
        for tool in decl.get("tools") or []:
            if tool.get("type") != "McpServer":
                continue
            mcp = tool.get("mcpServer") or {}
            ref = mcp.get("name")
            tool_names = mcp.get("toolNames") or []
            problems = []
            if ref not in mcp_names and ref not in CHART_PROVIDED_MCPSERVERS:
                problems.append(f"references unknown MCP server {ref!r}")
            if not tool_names:
                problems.append(f"MCP ref {ref!r} lists no toolNames (binds nothing)")
            for problem in problems:
                detail = f"{name}: {problem}"
                if name in pilot_agents:
                    errors.append(detail)
                else:
                    warnings.append(detail + " — backlog")
    return errors, warnings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate the agent-identity contract.")
    parser.add_argument("--repo-root", default=".", type=Path)
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()

    pilot_secrets, pilot_agents = load_scope(repo_root)
    errors: list[str] = []
    warnings: list[str] = []
    for check, arg in (
        (check_credential_scoping, pilot_secrets),
        (check_model_config_in_git, pilot_agents),
        (check_capability_surface, pilot_agents),
    ):
        e, w = check(repo_root, arg)
        errors.extend(e)
        warnings.extend(w)

    for w in warnings:
        print(f"WARN: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    if errors:
        print(f"\nagent-identity validation FAILED with {len(errors)} error(s).")
        return 1
    print(f"\nagent-identity validation passed ({len(warnings)} warning(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
