"""Tests for the agent-identity contract validator (kagent 1.0, api.kagent.dev/v1alpha3).

Every agent and every credential is held to all three invariants — there is no
pilot staging any more, so there is no "warning" tier to test. A violation is an
error, whichever agent it is on. And the validator fails CLOSED (SPEC §V.84): a
kagent manifest it does not understand, or no agents at all, is an error.
"""
from pathlib import Path
import importlib.util

import yaml

_spec = importlib.util.spec_from_file_location(
    "validate_agent_identity",
    Path(__file__).resolve().parents[2] / "scripts" / "validate-agent-identity.py",
)
vai = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vai)

API = "api.kagent.dev/v1alpha3"
REPO = Path(__file__).resolve().parents[2]


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _dump(doc: dict) -> str:
    return yaml.safe_dump(doc, sort_keys=False)


def _external_secret(name: str, store: str, key: str) -> str:
    return (
        "apiVersion: external-secrets.io/v1\n"
        "kind: ExternalSecret\n"
        f"metadata:\n  name: {name}\n  namespace: kagent\n"
        "spec:\n"
        f"  secretStoreRef:\n    name: {store}\n    kind: SecretStore\n"
        "  data:\n"
        "    - secretKey: TOKEN\n"
        f"      remoteRef:\n        key: {key}\n        property: p\n"
    )


def _template(name: str, model: str = "anthropic-claude-sonnet-5-5", server: str = "agent-docs",
              tools: list[str] | None = None, subagent: str | None = None,
              server_kind: str = "RemoteMCPServer") -> str:
    bindings = [{"mcp": {"server": {"kind": server_kind, "name": server},
                         **({"tools": tools} if tools is not None else {})}}]
    if subagent:
        bindings.append({"subAgent": {"name": subagent, "description": "d",
                                      "templateRef": {"name": subagent}}})
    return _dump({"apiVersion": API, "kind": "AgentTemplate",
                  "metadata": {"name": name, "namespace": "kagent",
                               "labels": {"capability.homelab/class": "read"}},
                  "spec": {"modelConfig": {"name": model}, "systemPrompt": "x",
                           "tools": bindings}})


def _agent(name: str, template: str | None = None, harness: str = "kagent-memory",
           inline_template: bool = False, inline_harness: bool = False) -> str:
    spec: dict = {}
    if inline_template:
        spec["template"] = {"modelConfig": {"name": "anthropic-claude-sonnet-5-5"},
                            "systemPrompt": "x"}
    else:
        spec["templateRef"] = {"name": template or name}
    if inline_harness:
        spec["harness"] = {"kagent": {}, "workload": {"image": "x@sha256:" + "0" * 64},
                           "substrate": {"workerPoolRef": {"name": "p"},
                                         "snapshotPolicy": {"location": "s3://b/"}}}
    else:
        spec["harnessRef"] = {"name": harness}
    return _dump({"apiVersion": API, "kind": "Agent",
                  "metadata": {"name": name, "namespace": "kagent"}, "spec": spec})


def _harness(name: str, memory_model: str | None = "embedding-model-config",
             summarizer: str | None = None) -> str:
    kagent: dict = {}
    if memory_model:
        kagent["memory"] = {"modelConfigRef": {"name": memory_model}}
    if summarizer:
        kagent["compaction"] = {"compactionInterval": 5,
                                "summarizer": {"modelConfigRef": {"name": summarizer}}}
    return _dump({"apiVersion": API, "kind": "Harness",
                  "metadata": {"name": name, "namespace": "kagent"},
                  "spec": {"kagent": kagent,
                           "workload": {"image": "ghcr.io/kagent-dev/kagent/golang-adk@sha256:"
                                                 + "0" * 64},
                           "substrate": {"workerPoolRef": {"name": "kagent-default"},
                                         "snapshotPolicy": {"location": "s3://ate-snapshots/"}}}})


def _remote_mcp(name: str) -> str:
    return _dump({"apiVersion": API, "kind": "RemoteMCPServer",
                  "metadata": {"name": name, "namespace": "kagent"},
                  "spec": {"description": "d", "url": "http://example"}})


def _model_config(name: str) -> str:
    return _dump({"apiVersion": API, "kind": "ModelConfig",
                  "metadata": {"name": name, "namespace": "kagent"},
                  "spec": {"provider": "Anthropic", "model": "x"}})


def _good_repo(tmp_path: Path) -> Path:
    root = tmp_path
    kd = root / "base-apps" / "kagent"
    _write(kd / "agent-docs-mcp-external-secret.yaml",
           _external_secret("agent-docs-github-mcp-token", "vault-agent-docs-mcp",
                            "kagent-agent-docs-mcp"))
    _write(kd / "agent-docs-mcp-remote.yaml", _remote_mcp("agent-docs"))
    _write(kd / "model-configs" / "anthropic-claude-sonnet-5-5.yaml",
           _model_config("anthropic-claude-sonnet-5-5"))
    _write(kd / "embedding-model-config.yaml", _model_config("embedding-model-config"))
    _write(kd / "harnesses" / "kagent-memory.yaml", _harness("kagent-memory"))
    _write(kd / "agent-templates" / "homelab-knowledge.yaml",
           _template("homelab-knowledge", tools=["get_file_contents"]))
    _write(kd / "agents" / "homelab-knowledge.yaml", _agent("homelab-knowledge"))
    return root


def run(root: Path) -> int:
    return vai.main(["--repo-root", str(root)])


def test_good_repo_passes(tmp_path):
    assert run(_good_repo(tmp_path)) == 0


def test_the_real_repo_passes():
    assert run(REPO) == 0


# ------------------------------------------- invariant 1: scoped credentials

def test_broad_secretstore_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "bad.yaml",
           _external_secret("some-cred", "vault-backend", "kagent-some-cred"))
    assert run(root) == 1


def test_monolithic_vault_key_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "bad.yaml",
           _external_secret("some-cred", "vault-some-cred", "kagent"))
    assert run(root) == 1


def test_any_credential_is_held_to_the_bar_not_just_a_pilot(tmp_path):
    """The staging is gone. An unscoped credential is an ERROR whatever its name."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "bad.yaml",
           _external_secret("not-the-pilot", "vault-backend", "kagent"))
    assert run(root) == 1


def test_monolithic_key_is_caught_in_ANY_namespace(tmp_path):
    """postgresql/kagent-db-credentials is a KAGENT credential living in the postgresql
    namespace. It kept reading the monolithic `kagent` key and broke silently."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "postgresql" / "external-secrets-kagent.yaml",
           _external_secret("kagent-db-credentials", "vault-backend", "kagent"))
    assert run(root) == 1


def test_vault_backend_OUTSIDE_kagent_is_not_flagged(tmp_path):
    """`vault-backend` is a legitimate per-namespace SecretStore name elsewhere."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "atlantis" / "external-secrets.yaml",
           _external_secret("atlantis-env", "vault-backend", "atlantis/aws"))
    assert run(root) == 0


# ---------------------------------------- invariant 2: in-git model identity

def test_out_of_band_template_modelconfig_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agent-templates" / "other.yaml",
           _template("other", model="applied-by-hand", tools=["get_file_contents"]))
    assert run(root) == 1


def test_out_of_band_memory_modelconfig_is_error(tmp_path):
    """Memory's embedding model moved from the agent to the Harness in 1.0. Still identity."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "harnesses" / "kagent-memory.yaml",
           _harness("kagent-memory", memory_model="applied-by-hand"))
    assert run(root) == 1


def test_out_of_band_summarizer_modelconfig_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "harnesses" / "kagent-memory.yaml",
           _harness("kagent-memory", summarizer="applied-by-hand"))
    assert run(root) == 1


def test_chart_generated_modelconfig_is_exempt(tmp_path):
    """default-model-config is rendered by the chart from git-tracked Helm values."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agent-templates" / "other.yaml",
           _template("other", model="default-model-config", tools=["get_file_contents"]))
    assert run(root) == 0


# ------------------------------------------ invariant 3: capability surface

def test_missing_tools_is_error(tmp_path):
    """An omitted `tools` exposes the whole server: an implicit bind-all."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agent-templates" / "other.yaml",
           _template("other", tools=None))
    assert run(root) == 1


def test_empty_tools_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agent-templates" / "other.yaml",
           _template("other", tools=[]))
    assert run(root) == 1


def test_unknown_mcp_ref_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agent-templates" / "other.yaml",
           _template("other", server="nope", tools=["x"]))
    assert run(root) == 1


def test_binding_to_an_mcpserver_kind_is_error(tmp_path):
    """1.0 bindings accept only RemoteMCPServer (the CRD enforces it; CI says so first)."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agent-templates" / "other.yaml",
           _template("other", server="agent-docs", tools=["x"], server_kind="MCPServer"))
    assert run(root) == 1


def test_chart_provided_mcp_server_ref_is_exempt(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agent-templates" / "other.yaml",
           _template("other", server="kagent-tool-server", tools=["k8s_get_resources"]))
    assert run(root) == 0


def test_chart_provided_mcp_server_still_needs_tools(tmp_path):
    """Exempt from 'must exist in git', NOT from 'must name its tools'."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agent-templates" / "other.yaml",
           _template("other", server="kagent-tool-server", tools=[]))
    assert run(root) == 1


def test_dangling_subagent_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agent-templates" / "other.yaml",
           _template("other", tools=["get_file_contents"], subagent="does-not-exist"))
    assert run(root) == 1


def test_valid_subagent_passes(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agent-templates" / "other.yaml",
           _template("other", tools=["get_file_contents"], subagent="homelab-knowledge"))
    assert run(root) == 0


def test_agent_with_dangling_template_or_harness_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "a.yaml", _agent("a", template="nope"))
    assert run(root) == 1
    root2 = _good_repo(tmp_path / "two")
    _write(root2 / "base-apps" / "kagent" / "agents" / "a.yaml",
           _agent("a", template="homelab-knowledge", harness="nope"))
    assert run(root2) == 1


def test_inline_template_or_harness_is_error(tmp_path):
    """An inline template carries tools no template check ever sees."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "a.yaml",
           _agent("a", inline_template=True))
    assert run(root) == 1
    root2 = _good_repo(tmp_path / "two")
    _write(root2 / "base-apps" / "kagent" / "agents" / "a.yaml",
           _agent("a", template="homelab-knowledge", inline_harness=True))
    assert run(root2) == 1


# ----------------------------------------------------- fail closed (§V.84)

V1ALPHA2_AGENT = """\
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: legacy
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: anthropic-claude-sonnet-5-5
    tools:
    - type: McpServer
      mcpServer: {name: kagent-tool-server}
"""


def test_v1alpha2_agent_is_error_not_skipped(tmp_path):
    """The 0.10 shape. A validator that only knew v1alpha3 would skip it and pass, while the
    implicit bind-all above shipped. Unknown kagent kinds/versions fail."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "legacy.yaml", V1ALPHA2_AGENT)
    assert run(root) == 1


def test_unknown_kagent_kind_is_error(tmp_path):
    """A kind the validator was never taught (ModelProviderConfig holds provider
    credentials, so it is identity-relevant) fails until it is."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "mpc.yaml", _dump({
        "apiVersion": API, "kind": "ModelProviderConfig",
        "metadata": {"name": "p", "namespace": "kagent"}, "spec": {}}))
    assert run(root) == 1


def test_kagent_manifest_outside_kagent_dir_is_found(tmp_path):
    """Found by apiVersion, not directory: a stray v1alpha2 Agent elsewhere still fails."""
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "elsewhere" / "legacy.yaml", V1ALPHA2_AGENT)
    assert run(root) == 1


def test_unparsable_kagent_file_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "broken.yaml",
           "apiVersion: api.kagent.dev/v1alpha3\nkind: Agent\nmetadata: {name: [\n")
    assert run(root) == 1


def test_empty_repo_is_error(tmp_path):
    """No agents found is a failure, not a vacuous pass."""
    (tmp_path / "base-apps" / "kagent").mkdir(parents=True)
    assert run(tmp_path) == 1


def test_templates_without_agents_is_error(tmp_path):
    root = _good_repo(tmp_path)
    (root / "base-apps" / "kagent" / "agents" / "homelab-knowledge.yaml").unlink()
    assert run(root) == 1


def test_agents_found_by_kind_not_by_directory(tmp_path):
    root = _good_repo(tmp_path)
    stray = root / "base-apps" / "kagent" / "stray-orchestrator.yaml"
    _write(stray, _template("stray", tools=[]))
    found = {(d.get("metadata") or {}).get("name") for _, d in vai.collect_templates(root)}
    assert found == {"homelab-knowledge", "stray"}
    # ...and the stray's bind-all is actually caught
    assert run(root) == 1
