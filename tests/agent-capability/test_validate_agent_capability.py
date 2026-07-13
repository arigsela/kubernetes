"""Tests for the agent-capability contract validator.

Every invariant gets a negative test. A validator that only ever passes is
indistinguishable from one that does nothing, so each test builds a synthetic
repo that VIOLATES one invariant and asserts the violation is caught.
"""
from pathlib import Path
import importlib.util
import json
import textwrap

import pytest
import yaml

SPEC = importlib.util.spec_from_file_location(
    "validate_agent_capability",
    Path(__file__).resolve().parents[2] / "scripts" / "validate-agent-capability.py",
)
vac = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vac)


TAXONOMY = {
    "read": ["k8s_get_resources", "get_file_contents"],
    "write": ["k8s_patch_resource"],
    "destructive": ["k8s_delete_resource", "k8s_execute_command"],
}


def make_repo(tmp_path: Path, agents: list[dict]) -> Path:
    """Build a throwaway repo: the taxonomy ConfigMap + the given Agent docs."""
    tax_dir = tmp_path / "base-apps" / "kyverno-policies"
    tax_dir.mkdir(parents=True)
    (tax_dir / "agent-capability-taxonomy.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "agent-capability-taxonomy"},
                "data": {k: json.dumps(v) for k, v in TAXONOMY.items()},
            }
        )
    )
    agent_dir = tmp_path / "base-apps" / "kagent" / "agents"
    agent_dir.mkdir(parents=True)
    for doc in agents:
        name = doc["metadata"]["name"]
        (agent_dir / f"{name}.yaml").write_text(yaml.safe_dump(doc))
    return tmp_path


def agent(name, cls, tools=None, approval=None, delegates=None):
    """Build an Agent doc. cls=None omits the class label entirely."""
    refs = []
    if tools:
        mcp = {"name": "kagent-tool-server", "toolNames": tools}
        if approval:
            mcp["requireApproval"] = approval
        refs.append({"type": "McpServer", "mcpServer": mcp})
    for d in delegates or []:
        refs.append({"type": "Agent", "agent": {"name": d}})

    meta = {"name": name, "namespace": "kagent"}
    if cls is not None:
        meta["labels"] = {vac.CLASS_LABEL: cls}
    return {
        "apiVersion": "kagent.dev/v1alpha2",
        "kind": "Agent",
        "metadata": meta,
        "spec": {"declarative": {"tools": refs}},
    }


def run(tmp_path, agents) -> int:
    return vac.main(["--repo-root", str(make_repo(tmp_path, agents))])


# --------------------------------------------------------------- happy path

def test_valid_repo_passes(tmp_path):
    assert run(tmp_path, [
        agent("reader", "read", tools=["k8s_get_resources"]),
        agent("operator", "admin",
              tools=["k8s_get_resources", "k8s_delete_resource"],
              approval=["k8s_delete_resource"]),
    ]) == 0


# ------------------------------------------- invariant 1: declared class

def test_missing_class_label_is_error(tmp_path):
    assert run(tmp_path, [agent("nolabel", None, tools=["k8s_get_resources"])]) == 1


def test_invalid_class_value_is_error(tmp_path):
    assert run(tmp_path, [agent("bogus", "superuser", tools=["k8s_get_resources"])]) == 1


# ------------------------------------- invariant 2: tools within class

def test_read_agent_binding_destructive_tool_is_error(tmp_path):
    """The core capability check: a read agent may not hold delete."""
    assert run(tmp_path, [
        agent("sneaky", "read",
              tools=["k8s_get_resources", "k8s_delete_resource"],
              approval=["k8s_delete_resource"]),
    ]) == 1


def test_write_agent_binding_destructive_tool_is_error(tmp_path):
    assert run(tmp_path, [
        agent("mid", "write",
              tools=["k8s_execute_command"],
              approval=["k8s_execute_command"]),
    ]) == 1


def test_unclassified_tool_is_error(tmp_path):
    """FAIL-CLOSED: a tool absent from the taxonomy cannot be bound.

    This is what protects against a chart upgrade silently widening capability.
    """
    assert run(tmp_path, [
        agent("future", "admin", tools=["k8s_brand_new_tool_from_chart_bump"]),
    ]) == 1


# ------------------------------------ invariant 3: mutating tools gated

def test_write_tool_without_approval_is_error(tmp_path):
    assert run(tmp_path, [
        agent("ungated", "write", tools=["k8s_patch_resource"], approval=[]),
    ]) == 1


def test_destructive_tool_without_approval_is_error(tmp_path):
    """The exact regression that already happened once, silently, for days."""
    assert run(tmp_path, [
        agent("ungated", "admin", tools=["k8s_delete_resource"], approval=[]),
    ]) == 1


def test_partial_approval_is_error(tmp_path):
    """Gating SOME mutating tools is not enough — this was k8s-agent's real bug."""
    assert run(tmp_path, [
        agent("partial", "admin",
              tools=["k8s_delete_resource", "k8s_patch_resource"],
              approval=["k8s_delete_resource"]),   # patch left ungated
    ]) == 1


# ------------------------------------- invariant 4: no escalation

def test_read_agent_delegating_to_admin_is_error(tmp_path):
    """The homelab-knowledge -> k8s-agent hole."""
    assert run(tmp_path, [
        agent("docs", "read", tools=["get_file_contents"], delegates=["operator"]),
        agent("operator", "admin",
              tools=["k8s_delete_resource"], approval=["k8s_delete_resource"]),
    ]) == 1


def test_read_agent_delegating_to_read_is_ok(tmp_path):
    """The fix: delegate to a read-only agent instead."""
    assert run(tmp_path, [
        agent("docs", "read", tools=["get_file_contents"], delegates=["reader"]),
        agent("reader", "read", tools=["k8s_get_resources"]),
    ]) == 0


def test_escalation_is_transitive(tmp_path):
    """read -> read -> admin must still be caught. Capability is transitive.

    A one-hop check would pass this: `docs` delegates to `middle`, which is
    itself declared `read`. Only following the chain finds the admin at the end.
    """
    assert run(tmp_path, [
        agent("docs", "read", delegates=["middle"]),
        agent("middle", "read", delegates=["operator"]),
        agent("operator", "admin",
              tools=["k8s_delete_resource"], approval=["k8s_delete_resource"]),
    ]) == 1


def test_dangling_delegation_is_error(tmp_path):
    """observability-agent -> promql-agent, which does not exist."""
    assert run(tmp_path, [
        agent("orphan", "read", delegates=["does-not-exist"]),
    ]) == 1


def test_delegation_cycle_is_caught(tmp_path):
    """Must terminate, not recurse forever."""
    assert run(tmp_path, [
        agent("a", "read", delegates=["b"]),
        agent("b", "read", delegates=["a"]),
    ]) == 1


# ------------------------------------------------- discovery regression

def test_agents_found_by_kind_not_directory(tmp_path):
    """build-orchestrator.yaml lives OUTSIDE agents/, and the identity validator
    globs only agents/*.yaml — so it has never been checked. Find by kind."""
    repo = make_repo(tmp_path, [agent("in-dir", "read", tools=["k8s_get_resources"])])
    stray = repo / "base-apps" / "kagent" / "stray-orchestrator.yaml"
    stray.write_text(yaml.safe_dump(
        agent("stray", "read",
              tools=["k8s_delete_resource"], approval=["k8s_delete_resource"])
    ))
    found = {(d.get("metadata") or {}).get("name") for _, d in vac.collect_agents(repo)}
    assert found == {"in-dir", "stray"}
    # and the stray's violation is actually caught
    assert vac.main(["--repo-root", str(repo)]) == 1
