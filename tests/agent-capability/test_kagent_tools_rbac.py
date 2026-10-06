"""The tool server's RBAC is the floor beneath the capability classes.

The kagent tool server (one ServiceAccount, shared by every agent's tools) used to be
bound by the Helm chart to `*` on everything. A read-class agent listed every Secret
in the cluster through it on 2026-07-15. The role now lives in git as
"cluster-admin minus Secrets" and these tests pin that contract: no Secrets, no
wildcards that could re-admit them, bound to exactly the tool server's identity, and
the chart told not to create its own role.
"""
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "base-apps" / "cluster-rbac" / "kagent-tools.yaml"
APPLICATION = REPO / "base-apps" / "kagent.yaml"


def _docs() -> list[dict]:
    return [d for d in yaml.safe_load_all(MANIFEST.read_text()) if d]


def _role() -> dict:
    return next(d for d in _docs() if d["kind"] == "ClusterRole")


def _binding() -> dict:
    return next(d for d in _docs() if d["kind"] == "ClusterRoleBinding")


def test_role_never_grants_secrets():
    for rule in _role()["rules"]:
        assert "secrets" not in rule.get("resources", []), rule


def test_role_has_no_wildcard_that_could_cover_secrets():
    """`*` on core resources or `*` in apiGroups would re-admit Secrets silently."""
    for rule in _role()["rules"]:
        groups = rule.get("apiGroups", [])
        assert "*" not in groups, rule
        if "" in groups:
            assert "*" not in rule.get("resources", []), rule


def test_role_keeps_what_the_tools_need():
    """Pods, logs and exec stay: k8s_execute_command is destructive-class and gated by
    approval; its blast radius is the tool-guard's job, not RBAC's."""
    core = [r for r in _role()["rules"] if "" in r.get("apiGroups", [])]
    resources = {res for r in core for res in r.get("resources", [])}
    for needed in ("pods", "pods/log", "pods/exec", "configmaps", "events", "namespaces"):
        assert needed in resources, needed


def test_binding_is_exactly_the_tool_server():
    b = _binding()
    assert b["roleRef"] == {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole",
                            "name": _role()["metadata"]["name"]}
    assert b["subjects"] == [{"kind": "ServiceAccount", "name": "kagent-tools",
                              "namespace": "kagent"}]


def test_chart_no_longer_creates_its_own_rbac():
    app = next(d for d in yaml.safe_load_all(APPLICATION.read_text())
               if d and d.get("kind") == "Application")
    values = app["spec"]["source"]["helm"]["valuesObject"]
    assert values["kagent-tools"]["rbac"]["create"] is False
