"""The tool server's RBAC is the floor beneath the capability classes.

The kagent tool server (one ServiceAccount, shared by every agent's tools) used to be
bound by the Helm chart to `*` on everything. A read-class agent listed every Secret
in the cluster through it on 2026-07-15. The role now lives in git as
"cluster-admin minus Secrets" and these tests pin that contract: no Secrets, no
wildcards that could re-admit them, bound to exactly the tool server's identity, and
the chart told not to create its own role — checked on the chart Argo actually renders, not
just on the values key.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "base-apps" / "cluster-rbac" / "kagent-tools.yaml"
APPLICATION = REPO / "base-apps" / "kagent.yaml"
TOOLS_SA = {"kind": "ServiceAccount", "name": "kagent-tools", "namespace": "kagent"}


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


def _application() -> dict:
    return next(d for d in yaml.safe_load_all(APPLICATION.read_text())
                if d and d.get("kind") == "Application")


def test_chart_no_longer_creates_its_own_rbac():
    values = _application()["spec"]["source"]["helm"]["valuesObject"]
    assert values["kagent-tools"]["rbac"]["create"] is False


@pytest.fixture(scope="module")
def rendered_chart(tmp_path_factory) -> list[dict]:
    """The kagent chart exactly as Argo renders it: the Application's chart, version, values,
    release name and namespace. The values key above is only a claim about the chart; the
    render is what the cluster gets. (The kagent-tools subchart's own default, rbac.create:
    true, is a `*`/`*`/`*` ClusterRole bound to this ServiceAccount, still in 1.0.0-alpha14.)

    KAGENT_CHART_VERSION overrides the version, to check a chart bump against the current
    values before the Application moves (e.g. KAGENT_CHART_VERSION=1.0.0-alpha14)."""
    if shutil.which("helm") is None:
        if os.environ.get("CI"):
            pytest.fail("helm is required to render the kagent chart (preinstalled on GitHub runners)")
        pytest.skip("helm not installed")
    app = _application()
    src = app["spec"]["source"]
    version = os.environ.get("KAGENT_CHART_VERSION") or src["targetRevision"]
    values = tmp_path_factory.mktemp("kagent-values") / "values.json"
    values.write_text(json.dumps(src["helm"]["valuesObject"]))
    release = src["helm"].get("releaseName") or app["metadata"]["name"]
    r = subprocess.run(
        ["helm", "template", release, f"oci://{src['repoURL']}/{src['chart']}",
         "--version", version, "--namespace", app["spec"]["destination"]["namespace"],
         "--values", str(values)],
        capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    return [d for d in yaml.safe_load_all(r.stdout) if isinstance(d, dict)]


def test_rendered_chart_still_creates_the_tool_server_service_account(rendered_chart):
    """Not vacuous: the identity base-apps/cluster-rbac/kagent-tools.yaml binds must be the one
    the chart actually runs the tool server as. A renamed SA would leave our role bound to
    nothing and the tests below passing trivially."""
    sas = {(d["metadata"]["name"], d["metadata"].get("namespace"))
           for d in rendered_chart if d["kind"] == "ServiceAccount"}
    assert ("kagent-tools", "kagent") in sas, sorted(sas)
    tools = [d for d in rendered_chart if d["kind"] == "Deployment"
             and d["spec"]["template"]["spec"].get("serviceAccountName") == "kagent-tools"]
    assert tools, "no rendered Deployment runs as the kagent-tools ServiceAccount"


def test_rendered_chart_binds_nothing_to_the_tool_server(rendered_chart):
    """The chart must grant the kagent-tools ServiceAccount nothing: its only role is ours."""
    bindings = [d for d in rendered_chart if d["kind"] in ("ClusterRoleBinding", "RoleBinding")
                and any({k: s.get(k) for k in TOOLS_SA} == TOOLS_SA
                        for s in d.get("subjects") or [])]
    assert not bindings, [f"{d['kind']}/{d['metadata']['name']}" for d in bindings]


def test_rendered_chart_does_not_collide_with_our_role(rendered_chart):
    """Two Argo apps owning one ClusterRole/ClusterRoleBinding name would fight on every sync."""
    ours = {(d["kind"], d["metadata"]["name"]) for d in _docs()}
    theirs = {(d["kind"], d["metadata"]["name"]) for d in rendered_chart}
    assert not ours & theirs, ours & theirs


# The `*` verb is a wildcard too: it includes escalate, bind, impersonate, approve and
# sign, each a way around the Secret denial (write yourself a role, become another
# identity, mint a certificate). The role names its verbs, and names none of those.

ALLOWED_VERBS = {"get", "list", "watch", "create", "update", "patch", "delete", "deletecollection"}
PRIVILEGE_RESOURCES = {"serviceaccounts/token", "nodes/proxy", "users", "groups",
                       "certificatesigningrequests/approval"}


def test_role_never_uses_wildcard_verbs():
    for rule in _role()["rules"]:
        assert "*" not in rule["verbs"], rule


def test_role_verbs_are_crud_only():
    for rule in _role()["rules"]:
        assert set(rule["verbs"]) <= ALLOWED_VERBS, rule


def test_role_never_names_a_privilege_resource():
    for rule in _role()["rules"]:
        assert not PRIVILEGE_RESOURCES & set(rule.get("resources", [])), rule
