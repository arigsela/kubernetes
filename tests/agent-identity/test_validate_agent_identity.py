"""Tests for the agent-identity contract validator.

Every agent and every credential is held to all three invariants — there is no
pilot staging any more, so there is no "warning" tier to test. A violation is an
error, whichever agent it is on.
"""
from pathlib import Path
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "validate_agent_identity",
    Path(__file__).resolve().parents[2] / "scripts" / "validate-agent-identity.py",
)
vai = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vai)


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _external_secret(name: str, store: str, key: str) -> str:
    return (
        "apiVersion: external-secrets.io/v1beta1\n"
        "kind: ExternalSecret\n"
        f"metadata:\n  name: {name}\n  namespace: kagent\n"
        "spec:\n"
        f"  secretStoreRef:\n    name: {store}\n    kind: SecretStore\n"
        "  data:\n"
        "    - secretKey: TOKEN\n"
        f"      remoteRef:\n        key: {key}\n        property: p\n"
    )


def _agent(name: str, model: str, mcp_ref: str, tool_names: list[str],
           delegates: str | None = None) -> str:
    tn = "".join(f"        - {t}\n" for t in tool_names)
    tools = (
        "    tools:\n"
        "    - type: McpServer\n"
        "      mcpServer:\n"
        "        apiGroup: kagent.dev\n"
        "        kind: RemoteMCPServer\n"
        f"        name: {mcp_ref}\n"
        + ("        toolNames:\n" + tn if tool_names else "")
    )
    if delegates:
        tools += f"    - type: Agent\n      agent:\n        name: {delegates}\n"
    return (
        "apiVersion: kagent.dev/v1alpha2\n"
        "kind: Agent\n"
        f"metadata:\n  name: {name}\n  namespace: kagent\n"
        "spec:\n  type: Declarative\n  declarative:\n"
        f"    modelConfig: {model}\n"
        "    memory:\n      modelConfig: embedding-model-config\n"
        + tools
    )


def _remote_mcp(name: str) -> str:
    return (
        "apiVersion: kagent.dev/v1alpha2\n"
        "kind: RemoteMCPServer\n"
        f"metadata:\n  name: {name}\n  namespace: kagent\n"
        "spec:\n  url: http://example\n"
    )


def _model_config(name: str) -> str:
    return (
        "apiVersion: kagent.dev/v1alpha2\n"
        "kind: ModelConfig\n"
        f"metadata:\n  name: {name}\n  namespace: kagent\n"
        "spec:\n  provider: Anthropic\n  model: x\n"
    )


def _good_repo(tmp_path: Path) -> Path:
    root = tmp_path
    kd = root / "base-apps" / "kagent"
    _write(kd / "agent-docs-mcp-external-secret.yaml",
           _external_secret("agent-docs-github-mcp-token", "vault-agent-docs-mcp",
                            "kagent-agent-docs-mcp"))
    _write(kd / "agent-docs-mcp-remote.yaml", _remote_mcp("agent-docs"))
    _write(kd / "model-configs" / "anthropic-claude-sonnet-4-6.yaml",
           _model_config("anthropic-claude-sonnet-4-6"))
    _write(kd / "embedding-model-config.yaml", _model_config("embedding-model-config"))
    _write(kd / "agents" / "homelab-knowledge.yaml",
           _agent("homelab-knowledge", "anthropic-claude-sonnet-4-6",
                  "agent-docs", ["get_file_contents"]))
    return root


def test_good_repo_passes(tmp_path):
    assert vai.main(["--repo-root", str(_good_repo(tmp_path))]) == 0


# ------------------------------------------- invariant 1: scoped credentials

def test_broad_secretstore_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "bad.yaml",
           _external_secret("some-cred", "vault-backend", "kagent-some-cred"))
    assert vai.main(["--repo-root", str(root)]) == 1


def test_monolithic_vault_key_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "bad.yaml",
           _external_secret("some-cred", "vault-some-cred", "kagent"))
    assert vai.main(["--repo-root", str(root)]) == 1


def test_any_credential_is_held_to_the_bar_not_just_a_pilot(tmp_path):
    """The staging is gone. An unscoped credential is an ERROR whatever its name.

    This used to be a WARNING for everything except the one pilot credential, so
    CI stayed green on a real regression.
    """
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "bad.yaml",
           _external_secret("not-the-pilot", "vault-backend", "kagent"))
    assert vai.main(["--repo-root", str(root)]) == 1


def test_monolithic_key_is_caught_in_ANY_namespace(tmp_path):
    """The regression that shipped: postgresql/kagent-db-credentials is a KAGENT
    credential living in the postgresql namespace. It kept reading the monolithic
    `kagent` key, broke silently when that key was destroyed, and was invisible
    because the validator only globbed base-apps/kagent/."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "postgresql" / "external-secrets-kagent.yaml",
           _external_secret("kagent-db-credentials", "vault-backend", "kagent"))
    assert vai.main(["--repo-root", str(root)]) == 1


def test_vault_backend_OUTSIDE_kagent_is_not_flagged(tmp_path):
    """`vault-backend` is a legitimate PER-NAMESPACE SecretStore name used by ~30
    healthy ExternalSecrets (atlantis, backstage, cert-manager, mysql, n8n, ...).
    Only the kagent-namespace one was retired. Flagging it globally would be a
    false positive across most of the repo — so the broad-store check stays scoped
    while the monolithic-key check goes repo-wide."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "atlantis" / "external-secrets.yaml",
           _external_secret("atlantis-env", "vault-backend", "atlantis/aws"))
    assert vai.main(["--repo-root", str(root)]) == 0


# ---------------------------------------- invariant 2: in-git model identity

def test_out_of_band_modelconfig_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "other.yaml",
           _agent("other", "applied-by-hand", "agent-docs", ["get_file_contents"]))
    assert vai.main(["--repo-root", str(root)]) == 1


def test_chart_generated_modelconfig_is_exempt(tmp_path):
    """default-model-config is rendered by the chart from git-tracked Helm values
    (base-apps/kagent.yaml `providers:`), so it IS visible to review. The invariant
    exists to catch identity applied by hand, which this is not."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "other.yaml",
           _agent("other", "default-model-config", "agent-docs", ["get_file_contents"]))
    assert vai.main(["--repo-root", str(root)]) == 0


# ------------------------------------------ invariant 3: capability surface

def test_empty_toolnames_is_error(tmp_path):
    """An implicit bind-all. This is the exact regression the old pilot staging
    let through as a warning on k8s-agent — the agent holding k8s_delete_resource."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "other.yaml",
           _agent("other", "anthropic-claude-sonnet-4-6", "agent-docs", []))
    assert vai.main(["--repo-root", str(root)]) == 1


def test_unknown_mcp_ref_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "other.yaml",
           _agent("other", "anthropic-claude-sonnet-4-6", "nope", ["x"]))
    assert vai.main(["--repo-root", str(root)]) == 1


def test_chart_provided_mcp_server_ref_is_exempt(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "other.yaml",
           _agent("other", "anthropic-claude-sonnet-4-6",
                  "kagent-tool-server", ["k8s_get_resources"]))
    assert vai.main(["--repo-root", str(root)]) == 0


def test_chart_provided_mcp_server_still_needs_toolnames(tmp_path):
    """Exempt from 'must exist in git', NOT from 'must name its tools'."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "other.yaml",
           _agent("other", "anthropic-claude-sonnet-4-6", "kagent-tool-server", []))
    assert vai.main(["--repo-root", str(root)]) == 1


def test_dangling_delegation_is_error(tmp_path):
    """observability-agent delegated to promql-agent, which does not exist and is
    disabled in the chart. Nothing caught it. Now something does."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "other.yaml",
           _agent("other", "anthropic-claude-sonnet-4-6", "agent-docs",
                  ["get_file_contents"], delegates="does-not-exist"))
    assert vai.main(["--repo-root", str(root)]) == 1


def test_valid_delegation_passes(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "other.yaml",
           _agent("other", "anthropic-claude-sonnet-4-6", "agent-docs",
                  ["get_file_contents"], delegates="homelab-knowledge"))
    assert vai.main(["--repo-root", str(root)]) == 0


# ----------------------------------------------------- agent discovery

def test_agents_found_by_kind_not_by_directory(tmp_path):
    """collect_agents used to glob agents/*.yaml and then HARDCODE
    build-orchestrator.yaml, which lives one level up. The next agent placed
    outside agents/ would have been silently unvalidated. Find them by kind."""
    root = _good_repo(tmp_path)
    stray = root / "base-apps" / "kagent" / "stray-orchestrator.yaml"
    _write(stray, _agent("stray", "anthropic-claude-sonnet-4-6", "agent-docs", []))
    found = {(d.get("metadata") or {}).get("name") for _, d in vai.collect_agents(root)}
    assert found == {"homelab-knowledge", "stray"}
    # ...and the stray's bind-all is actually caught
    assert vai.main(["--repo-root", str(root)]) == 1
