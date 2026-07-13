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


def _scope(root: Path):
    _write(root / "scripts" / "agent-identity-scope.txt",
           "pilot-credential: agent-docs-github-mcp-token\n"
           "pilot-agent: homelab-knowledge\n")


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


def _agent(name: str, model: str, mcp_ref: str, tool_names: list[str]) -> str:
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
    _scope(root)
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


def test_load_scope(tmp_path):
    _scope(tmp_path)
    secrets, agents = vai.load_scope(tmp_path)
    assert secrets == {"agent-docs-github-mcp-token"}
    assert agents == {"homelab-knowledge"}


def test_good_repo_passes_all(tmp_path):
    root = _good_repo(tmp_path)
    secrets, agents = vai.load_scope(root)
    assert vai.check_credential_scoping(root, secrets) == ([], [])
    assert vai.check_model_config_in_git(root, agents) == ([], [])
    assert vai.check_capability_surface(root, agents) == ([], [])
    assert vai.main(["--repo-root", str(root)]) == 0


def test_pilot_unscoped_credential_is_error(tmp_path):
    root = _good_repo(tmp_path)
    # Regress the pilot ExternalSecret back to the broad store + monolithic key.
    _write(root / "base-apps" / "kagent" / "agent-docs-mcp-external-secret.yaml",
           _external_secret("agent-docs-github-mcp-token", "vault-backend", "kagent"))
    secrets, _ = vai.load_scope(root)
    errors, warnings = vai.check_credential_scoping(root, secrets)
    assert any("agent-docs-github-mcp-token" in e for e in errors)
    assert vai.main(["--repo-root", str(root)]) == 1


def test_nonpilot_unscoped_credential_is_warning(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "db-external-secret.yaml",
           _external_secret("kagent-db-credentials", "vault-backend", "kagent"))
    secrets, _ = vai.load_scope(root)
    errors, warnings = vai.check_credential_scoping(root, secrets)
    assert errors == []
    assert any("kagent-db-credentials" in w for w in warnings)


def test_pilot_missing_modelconfig_is_error(tmp_path):
    root = _good_repo(tmp_path)
    (root / "base-apps" / "kagent" / "model-configs"
     / "anthropic-claude-sonnet-4-6.yaml").unlink()
    _, agents = vai.load_scope(root)
    errors, _ = vai.check_model_config_in_git(root, agents)
    assert any("anthropic-claude-sonnet-4-6" in e for e in errors)


def test_chart_generated_modelconfig_is_exempt(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "skill-suggester.yaml",
           _agent("skill-suggester", "default-model-config", "agent-docs",
                  ["get_file_contents"]))
    _, agents = vai.load_scope(root)
    errors, warnings = vai.check_model_config_in_git(root, agents)
    # skill-suggester is not a pilot agent and default-model-config is exempt:
    assert errors == []
    assert not any("default-model-config" in w for w in warnings)


def test_pilot_empty_toolnames_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "homelab-knowledge.yaml",
           _agent("homelab-knowledge", "anthropic-claude-sonnet-4-6", "agent-docs", []))
    _, agents = vai.load_scope(root)
    errors, _ = vai.check_capability_surface(root, agents)
    assert any("toolNames" in e for e in errors)


def test_pilot_unknown_mcp_ref_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "homelab-knowledge.yaml",
           _agent("homelab-knowledge", "anthropic-claude-sonnet-4-6",
                  "does-not-exist", ["t"]))
    _, agents = vai.load_scope(root)
    errors, _ = vai.check_capability_surface(root, agents)
    assert any("does-not-exist" in e for e in errors)


def test_chart_provided_mcp_server_ref_is_exempt(tmp_path):
    """kagent-tool-server is rendered by the kagent Helm chart, not declared in
    git. Requiring it as a manifest would mean two Argo apps owning one object —
    the tug-of-war that silently stripped the agents' HITL gates. Exempt, exactly
    as default-model-config is for the model invariant."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "k8s-agent.yaml",
           _agent("k8s-agent", "default-model-config", "kagent-tool-server",
                  ["k8s_get_resources"]))
    _, agents = vai.load_scope(root)
    errors, warnings = vai.check_capability_surface(root, agents)
    assert errors == []
    assert not any("kagent-tool-server" in w for w in warnings)


def test_chart_provided_mcp_server_still_needs_toolnames(tmp_path):
    """The exemption covers only 'does this ref resolve'. Invariant 3's real
    teeth — no implicit bind-all — still apply to chart-provided servers."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "homelab-knowledge.yaml",
           _agent("homelab-knowledge", "anthropic-claude-sonnet-4-6",
                  "kagent-tool-server", []))
    _, agents = vai.load_scope(root)
    errors, _ = vai.check_capability_surface(root, agents)
    assert any("toolNames" in e for e in errors)
