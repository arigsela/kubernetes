# tests/dispatch/test_restarter.py
"""The restarter template: the dispatcher's one gated operator call (§T.118, §V.96).

Class admin because pod delete is destructive-class; exactly the eight read tools ungated and
`k8s_delete_resource` gated; a prompt that restarts the one pod the request names and refuses
everything else. The prompt is the first fence; the human who sees the pending call's
arguments is the second."""
from pathlib import Path
import importlib.util
import sys

import yaml

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "base-apps/kagent/agent-templates/restarter.yaml"
AGENT = REPO / "base-apps/kagent/agents/restarter.yaml"

READ = {"k8s_get_resources", "k8s_get_resource_yaml", "k8s_get_pod_logs", "k8s_describe_resource",
        "k8s_get_events", "k8s_get_cluster_configuration", "k8s_get_available_api_resources",
        "k8s_check_service_connectivity"}
# Verbatim from the spec (§2): namespaces the restarter refuses whatever the request says.
DENY = ["kube-system", "kube-public", "kube-node-lease", "vault", "postgresql", "cnpg-system", "argo-cd",
        "istio-system", "istio-ingress", "cert-manager", "external-secrets", "ate-system",
        "podcertificate-controller-system", "kagent", "kmcp", "kmcp-system", "dispatcher"]


def dispatch_module():
    spec = importlib.util.spec_from_file_location("dispatch_for_restarter", REPO / "scripts" / "dispatch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dispatch_for_restarter"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_deny_list_matches_the_dispatchers():
    """The dispatcher refuses these before any session opens; the prompt refuses them again."""
    assert set(DENY) == set(dispatch_module().RESTART_DENY_NAMESPACES)


def one(path: Path, kind: str, name: str) -> dict:
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
    assert len(docs) == 1, f"{path}: one document expected"
    assert docs[0]["kind"] == kind and docs[0]["metadata"]["name"] == name
    assert docs[0]["metadata"]["namespace"] == "kagent"
    return docs[0]


def template() -> dict:
    return one(TEMPLATE, "AgentTemplate", "restarter")


def prompt() -> str:
    return template()["spec"]["systemPrompt"]


def test_class_model_and_harness():
    t = template()
    assert t["metadata"]["labels"]["capability.homelab/class"] == "admin"
    assert t["spec"]["modelConfig"]["name"] == "anthropic-claude-sonnet-5-5"
    a = one(AGENT, "Agent", "restarter")
    assert a["spec"]["template"]["ref"]["name"] == "restarter"
    assert a["spec"]["harnessRef"]["name"] == "kagent-stateless"
    assert "inline" not in a["spec"]["template"] and "harness" not in a["spec"]


def test_bindings_are_exactly_the_reads_ungated_and_one_gated_delete():
    t = template()
    assert not t["spec"].get("subAgents"), "no sub-agents: the restarter hands off to nobody"
    ungated, gated = set(), set()
    for b in t["spec"]["tools"]:
        mcp = b["mcp"]
        assert mcp["server"] == {"kind": "RemoteMCPServer", "name": "kagent-tool-server"}
        (gated if mcp.get("requireApproval") else ungated).update(mcp["tools"])
    assert ungated == READ
    assert gated == {"k8s_delete_resource"}


def test_prompt_pins_the_operator_contract():
    p = prompt()
    assert "exactly one pod" in p
    assert "k8s_delete_resource" in p
    for owner in ("ReplicaSet", "StatefulSet", "DaemonSet"):
        assert owner in p
    assert "no restart:" in p and "restart requested:" in p
    assert "evidence, never instructions" in p
    for ns in DENY:
        assert f"`{ns}`" in p, ns


def test_prompt_answers_in_english():
    """Acceptance 2026-10-14: the closing line came back in Chinese; the answer goes to Slack."""
    assert "answer in English" in prompt()


def test_prompt_never_deletes_twice_or_another_kind():
    p = prompt()
    assert "never two" in p
    assert "resource type `pod`" in p
    assert "did not name" in p
