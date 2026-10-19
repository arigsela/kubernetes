# tests/implementer/test_mcp_server.py
"""The implementer MCP server loads exactly the tools the taxonomy's propose list plus the
named read tools, under GitHub App auth, and nothing that merges, reviews or deletes."""
from conftest import (IMPLEMENTER_PROPOSE_TOOLS, IMPLEMENTER_READ_TOOLS, KNOWN_SERVER_TOOLS,
                      NEVER_LOADED, REPO, one, taxonomy)
import yaml

MCP = "base-apps/kagent/implementer-mcp.yaml"


def stdio_target() -> dict:
    cfg = yaml.safe_load(one(MCP, "ConfigMap", "implementer-mcp")["data"]["local.yaml"])
    return cfg["binds"][0]["listeners"][0]["routes"][0]["backends"][0]["mcp"]["targets"][0]["stdio"]


def main_container() -> dict:
    return one(MCP, "Deployment", "implementer-mcp")["spec"]["template"]["spec"]["containers"][0]


def loaded_tools() -> list[str]:
    args = stdio_target()["args"]
    return args[args.index("--tools") + 1].split(",")


def test_server_is_the_pinned_github_mcp_server_in_stdio_mode():
    assert main_container()["image"].startswith("ghcr.io/github/github-mcp-server:v1.14.0@sha256:")
    target = stdio_target()
    assert target["cmd"] == "./github-mcp-server" and target["args"][0] == "stdio"
    assert "--read-only" not in target["args"] and "--toolsets" not in target["args"]


def test_server_authenticates_as_the_github_app_from_the_vault_backed_secret():
    assert main_container()["envFrom"] == [{"secretRef": {"name": "implementer-github-app"}}]
    es = one("base-apps/kagent/implementer-mcp-external-secret.yaml", "ExternalSecret", "implementer-github-app")
    assert es["spec"]["target"]["name"] == "implementer-github-app"
    assert es["spec"]["dataFrom"] == [{"extract": {"key": "implementer-github-app"}}]
    assert es["spec"]["secretStoreRef"] == {"name": "vault-implementer-mcp", "kind": "SecretStore"}
    store = one("base-apps/kagent/implementer-mcp-secret-store.yaml", "SecretStore", "vault-implementer-mcp")
    auth = store["spec"]["provider"]["vault"]["auth"]["kubernetes"]
    assert auth["role"] == "implementer-mcp" and auth["serviceAccountRef"]["name"] == "eso-implementer-mcp"
    sa = one("base-apps/kagent/eso-implementer-mcp-serviceaccount.yaml", "ServiceAccount", "eso-implementer-mcp")
    assert sa["metadata"]["namespace"] == "kagent"


def test_loaded_tools_are_exactly_the_contract():
    assert loaded_tools() == IMPLEMENTER_READ_TOOLS + IMPLEMENTER_PROPOSE_TOOLS


def test_propose_tools_loaded_equal_the_taxonomy_propose_list():
    assert set(IMPLEMENTER_PROPOSE_TOOLS) == set(taxonomy()["propose"])
    assert set(IMPLEMENTER_READ_TOOLS) <= set(taxonomy()["read"])


def test_nothing_that_merges_reviews_or_deletes_is_loaded():
    """Checked on the parsed config: the header comment names these tools to say why they are absent."""
    text = yaml.safe_dump(stdio_target())
    for name in NEVER_LOADED:
        assert name not in text, name


def test_every_loaded_tool_name_is_a_known_server_tool():
    unknown = set(loaded_tools()) - KNOWN_SERVER_TOOLS
    assert not unknown, f"a misspelt tool name fails the server at startup: {sorted(unknown)}"


def test_remote_registers_the_server():
    remote = one("base-apps/kagent/implementer-mcp-remote.yaml", "RemoteMCPServer", "implementer")
    assert remote["spec"]["url"] == "http://implementer-mcp.kagent:3000/mcp"
    assert remote["spec"]["protocol"] == "STREAMABLE_HTTP"
