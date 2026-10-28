# tests/n8n_bug_investigator/test_github_read_mcp.py
"""The read-only GitHub MCP server n8n's agent uses (§T.152, Task 8, Decision 2)."""
from __future__ import annotations

import hashlib
import re

import yaml

from conftest import REPO

FILE = REPO / "base-apps/n8n/github-read-mcp.yaml"
NAME = "n8n-github-read-mcp"
TEMPLATE = REPO / "base-apps/kagent/agent-templates/bug-investigator.yaml"
AGENT_DOCS = REPO / "base-apps/kagent/agent-docs-mcp.yaml"


def docs(path=FILE):
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def one(kind, path=FILE):
    found = [d for d in docs(path) if d["kind"] == kind]
    assert len(found) == 1, kind
    return found[0]


def local_yaml_text(path=FILE):
    return one("ConfigMap", path)["data"]["local.yaml"]


def stdio_args():
    cfg = yaml.safe_load(local_yaml_text())
    route = cfg["binds"][0]["listeners"][0]["routes"][0]
    return route, route["backends"][0]["mcp"]["targets"][0]["stdio"]["args"]


def template_github_tools():
    t = yaml.safe_load(TEMPLATE.read_text())
    binds = [x["mcp"] for x in t["spec"]["tools"] if "mcp" in x and x["mcp"]["server"]["name"] == "implementer"]
    assert len(binds) == 1
    return binds[0]["tools"]


def test_tools_are_exactly_the_bug_investigator_github_reads():
    route, args = stdio_args()
    allow = route["policies"]["mcpAuthorization"]["rules"][0]["allow"]
    allowed = re.findall(r'"([a-z_]+)"', allow)
    tools = args[args.index("--tools") + 1].split(",")
    want = ["get_file_contents", "search_code", "list_commits", "get_commit", "list_pull_requests", "pull_request_read"]
    assert sorted(allowed) == sorted(tools) == sorted(want) == sorted(template_github_tools())
    assert allow.strip().startswith("mcp.tool.name in [")


def test_server_runs_read_only():
    _, args = stdio_args()
    assert args[0] == "stdio" and "--read-only" in args and "--toolsets" not in args


def test_images_match_agent_docs_mcp():
    ours, theirs = one("Deployment"), one("Deployment", AGENT_DOCS)
    spec, ref = ours["spec"]["template"]["spec"], theirs["spec"]["template"]["spec"]
    assert spec["initContainers"][0]["image"] == ref["initContainers"][0]["image"]
    assert spec["containers"][0]["image"] == ref["containers"][0]["image"]
    assert "@sha256:" in spec["containers"][0]["image"] and "@sha256:" in spec["initContainers"][0]["image"]


def test_checksum_config_matches_local_yaml():
    want = hashlib.sha256(local_yaml_text().encode()).hexdigest()
    got = one("Deployment")["spec"]["template"]["metadata"]["annotations"]["checksum/config"]
    assert got == want, f"recompute checksum/config: {want}"


def test_serviceaccount_mounts_no_token():
    sa = one("ServiceAccount")
    assert sa["metadata"]["name"] == NAME and sa["automountServiceAccountToken"] is False
    spec = one("Deployment")["spec"]["template"]["spec"]
    assert spec["serviceAccountName"] == NAME and spec["automountServiceAccountToken"] is False


def test_runs_where_n8n_runs_with_its_token():
    d = one("Deployment")
    spec = d["spec"]["template"]["spec"]
    assert d["metadata"]["namespace"] == "n8n"
    assert spec["nodeSelector"] == {"node.kubernetes.io/workload": "application"} and "tolerations" not in spec
    assert spec["containers"][0]["envFrom"] == [{"secretRef": {"name": "n8n-github-read-mcp-token"}}]
    svc = one("Service")
    assert svc["metadata"]["name"] == NAME and svc["spec"]["ports"][0]["port"] == 3000
    assert svc["spec"]["selector"].items() <= d["spec"]["template"]["metadata"]["labels"].items()


def test_network_policy_admits_only_n8n_and_prometheus():
    np = one("NetworkPolicy")
    assert np["spec"]["policyTypes"] == ["Ingress"]
    assert np["spec"]["podSelector"]["matchLabels"].items() <= one("Deployment")["spec"]["template"]["metadata"]["labels"].items()
    rules = np["spec"]["ingress"]
    assert rules == [
        {"from": [{"podSelector": {"matchLabels": {"app": "n8n"}}}], "ports": [{"port": 3000, "protocol": "TCP"}]},
        {"from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "logging"}}}],
         "ports": [{"port": 15020, "protocol": "TCP"}]},
    ]


def test_header_says_why_and_who_may_call():
    head = FILE.read_text().split("apiVersion:")[0]
    assert "Decision 2" in head and "NetworkPolicy" in head and "no auth" in head
