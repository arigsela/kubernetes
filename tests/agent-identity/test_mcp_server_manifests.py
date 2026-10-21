"""The three stdio MCP servers are plain Deployments (§T.126): agentgateway wraps the stdio
server and serves it on :3000, under the names kmcp used, so the RemoteMCPServers' URLs and the
container-name-keyed alerts keep working. kagent 1.0.0-alpha14 removed kmcp (kagent#3223).

The adapter image is built here (§T.133): scripts/build-agentgateway-image.sh pins the version,
and the three Deployments must pin that version's image, all by the same digest. A Renovate PR
that bumps only the script therefore fails here until the image is rebuilt and re-pinned."""
from pathlib import Path
import re
import yaml
import pytest

REPO = Path(__file__).resolve().parents[2]
SERVERS = {
    "agent-docs-mcp": ("agent-docs-mcp-remote.yaml", "agent-docs", "agent-docs-github-mcp-token"),
    "brave-search-mcp": ("brave-search-mcp-remote.yaml", "brave-search", "brave-search-mcp-api-key"),
    "implementer-mcp": ("implementer-mcp-remote.yaml", "implementer", "implementer-github-app"),
}
BUILD_SCRIPT = REPO / "scripts" / "build-agentgateway-image.sh"
AGW_VERSION = re.search(r"^AGW_VERSION=(\S+)$", BUILD_SCRIPT.read_text(), re.M).group(1)
ADAPTER = re.compile(r"852893458518\.dkr\.ecr\.us-east-2\.amazonaws\.com/agentgateway:"
                     + re.escape(AGW_VERSION) + r"@sha256:[0-9a-f]{64}")


def docs(name):
    return [d for d in yaml.safe_load_all((REPO / "base-apps/kagent" / f"{name}.yaml").read_text()) if d]


def one(name, kind):
    found = [d for d in docs(name) if d["kind"] == kind]
    assert len(found) == 1, (name, kind)
    return found[0]


@pytest.mark.parametrize("name", sorted(SERVERS))
def test_no_mcpserver_cr_remains(name):
    assert all(d["kind"] != "MCPServer" for d in docs(name))


@pytest.mark.parametrize("name", sorted(SERVERS))
def test_deployment_runs_agentgateway_around_the_stdio_server(name):
    dep = one(name, "Deployment")
    assert dep["metadata"]["name"] == name
    pod = dep["spec"]["template"]["spec"]
    init = pod["initContainers"][0]
    assert ADAPTER.fullmatch(init["image"]), (init["image"], f"want the {AGW_VERSION} image from the build script")
    assert init["args"] == ["--copy-self", "/adapterbin/agentgateway"]
    main = pod["containers"][0]
    assert main["name"] == "mcp-server"
    assert main["command"] == ["/adapterbin/agentgateway"] and main["args"] == ["-f", "/config/local.yaml"]
    assert main["ports"] == [{"name": "http", "containerPort": 3000}]
    assert "@sha256:" in main["image"]
    assert pod["serviceAccountName"] == name
    assert main["envFrom"] == [{"secretRef": {"name": SERVERS[name][2]}}]


def test_one_adapter_image_for_all_three():
    """One binary, three Deployments: they move together (what the old Renovate group did)."""
    images = {one(n, "Deployment")["spec"]["template"]["spec"]["initContainers"][0]["image"] for n in SERVERS}
    assert len(images) == 1, images


@pytest.mark.parametrize("name", sorted(SERVERS))
def test_config_serves_mcp_on_3000_from_a_stdio_target(name):
    cfg = yaml.safe_load(one(name, "ConfigMap")["data"]["local.yaml"])
    bind = cfg["binds"][0]
    assert bind["port"] == 3000
    route = bind["listeners"][0]["routes"][0]
    assert [m["path"]["pathPrefix"] for m in route["matches"]] == ["/sse", "/mcp"]
    target = route["backends"][0]["mcp"]["targets"][0]
    assert target["name"] == name and "cmd" in target["stdio"]


@pytest.mark.parametrize("name", sorted(SERVERS))
def test_service_keeps_the_url_the_remote_binds(name):
    svc = one(name, "Service")
    assert svc["spec"]["ports"] == [{"name": "http", "port": 3000, "targetPort": 3000,
                                     "protocol": "TCP", "appProtocol": "kgateway.dev/mcp"}]
    remote_file, remote_name, _ = SERVERS[name]
    remote = [d for d in yaml.safe_load_all((REPO / "base-apps/kagent" / remote_file).read_text())
              if d and d["kind"] == "RemoteMCPServer"][0]
    assert remote["metadata"]["name"] == remote_name
    assert remote["spec"]["url"] == f"http://{name}.kagent:3000/mcp"
    assert svc["spec"]["selector"] == one(name, "Deployment")["spec"]["selector"]["matchLabels"]


@pytest.mark.parametrize("name", sorted(SERVERS))
def test_serviceaccount_mounts_no_token(name):
    assert one(name, "ServiceAccount")["automountServiceAccountToken"] is False


@pytest.mark.parametrize("name", sorted(SERVERS))
def test_deployment_selector_is_the_immutable_pair_kmcp_set(name):
    """kmcp's Deployments select on instance+name. A selector is immutable, so the manifest that
    Argo applies onto the live object must carry exactly that pair or the hand-over is rejected."""
    want = {"app.kubernetes.io/instance": name, "app.kubernetes.io/name": name}
    dep = one(name, "Deployment")
    assert dep["spec"]["selector"]["matchLabels"] == want
    labels = dep["spec"]["template"]["metadata"]["labels"]
    assert all(labels.get(k) == v for k, v in want.items())
    assert one(name, "Service")["spec"]["selector"] == want


@pytest.mark.parametrize("name", sorted(SERVERS))
def test_pod_template_checksum_matches_the_config(name):
    """agentgateway reads local.yaml once at start. kmcp rolled the pod on config changes through
    a template checksum; here the checksum is written by hand and this test keeps it honest, so a
    stdio-args or tool-list edit in git always restarts the server."""
    import hashlib
    text = one(name, "ConfigMap")["data"]["local.yaml"]
    want = hashlib.sha256(text.encode()).hexdigest()
    got = one(name, "Deployment")["spec"]["template"]["metadata"]["annotations"]["checksum/config"]
    assert got == want, f"{name}: set checksum/config to {want}"
