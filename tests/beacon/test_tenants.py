# tests/beacon/test_tenants.py
"""beacon enterprise tenants (beacon §T.25+): the static contract around the XTenant composition.

What the composition renders is tested in tests/composition/test_xtenant_render.py (it needs the
crossplane CLI); these need nothing but the files: who may delete a tenant, what Crossplane may
create, and that the Vault role the composition names is the one the script provisions.
"""
import re

import yaml
from conftest import REPO, load, one

XRD = "base-apps/crossplane-compositions/xrd-tenant.yaml"
COMPOSITION = "base-apps/crossplane-compositions/composition-tenant.yaml"
RBAC = "base-apps/crossplane-compositions/composition-rbac.yaml"
APP = "base-apps/beacon-tenants.yaml"
TENANTS = REPO / "base-apps/beacon-tenants"
VAULT_SCRIPT = REPO / "scripts/provision-beacon-tenants-vault.sh"
FIXTURE_CRD = "tests/admission-policies/fixtures/crds/xtenants.yaml"
MAX_TENANTS = 2  # beacon §V.51

# What the chart's own `crossplane` ClusterRole already grants (live 2026-10-19): Secrets,
# ServiceAccounts, Services, ConfigMaps, Deployments, Crossplane's APIs, providers' resources.
BASE_GRANTS = {("", "secrets"), ("", "serviceaccounts"), ("", "services"), ("", "configmaps"),
               ("apps", "deployments")}
# kind -> (group, plural) for what the composition emits.
RESOURCES = {
    "Namespace": ("", "namespaces"), "ResourceQuota": ("", "resourcequotas"),
    "LimitRange": ("", "limitranges"), "ServiceAccount": ("", "serviceaccounts"),
    "ConfigMap": ("", "configmaps"), "NetworkPolicy": ("networking.k8s.io", "networkpolicies"),
    "Deployment": ("apps", "deployments"), "Job": ("batch", "jobs"), "CronJob": ("batch", "cronjobs"),
    "Cluster": ("postgresql.cnpg.io", "clusters"),
    "SecretStore": ("external-secrets.io", "secretstores"),
    "ExternalSecret": ("external-secrets.io", "externalsecrets"),
    "PushSecret": ("external-secrets.io", "pushsecrets"),
    "Password": ("generators.external-secrets.io", "passwords"),
    "Instrumentation": ("opentelemetry.io", "instrumentations"),
}


def _script():
    comp = one(COMPOSITION, "Composition", "tenant")
    return comp["spec"]["pipeline"][0]["input"]["script"]


def _composed_kinds():
    """(apiVersion, kind) of every object the script builds, read from its literals."""
    return set(re.findall(r'"apiVersion": "([^"]+)", "kind": "([^"]+)"', _script()))


def _xtenant_files():
    return [f for f in sorted(TENANTS.glob("*.yaml")) if f.name != "catalog-info.yaml"]


def test_v50_rbac_covers_only_composed_kinds():
    """Crossplane's extra rights are exactly what the composition emits beyond the chart's role:
    cluster-wide namespaces and NetworkPolicies are the blast radius, so nothing more."""
    needed = set()
    for api, kind in _composed_kinds():
        group = api.split("/")[0] if "/" in api else ""
        if group.endswith(("crossplane.io", "upbound.io")):
            continue  # Crossplane's own APIs and the providers' managed resources
        assert kind in RESOURCES, f"add {kind} to RESOURCES"
        needed.add(RESOURCES[kind])
    needed -= BASE_GRANTS
    role = one(RBAC, "ClusterRole", "crossplane:compositions:tenant")
    assert role["metadata"]["labels"]["rbac.crossplane.io/aggregate-to-crossplane"] == "true"
    granted = {(g, r) for rule in role["rules"] for g in rule["apiGroups"] for r in rule["resources"]
               if not r.endswith("/status")}
    assert granted == needed, f"missing {needed - granted}, extra {granted - needed}"


def test_v12_xr_files_carry_prune_and_delete_false():
    """Removing a tenant's file, or deleting the Application, must leave the tenant. Only the
    offboarding PR (spec.offboard: true) drops the annotation (beacon §V.12, §R.18)."""
    for f in _xtenant_files():
        for xr in (d for d in yaml.safe_load_all(f.read_text()) if d):
            opts = xr["metadata"].get("annotations", {}).get("argocd.argoproj.io/sync-options", "")
            if xr["spec"].get("offboard"):
                continue
            assert {"Prune=false", "Delete=false"} <= set(opts.split(",")), f"{f.name}: {opts!r}"


def test_v12_the_tenants_application_never_cascades():
    app = one(APP, "Application", "beacon-tenants")
    assert "finalizers" not in app["metadata"], "a resources-finalizer would cascade-delete every tenant"
    assert app["spec"]["source"]["path"] == "base-apps/beacon-tenants"


def test_v12_the_xrd_is_never_pruned_or_cascade_deleted():
    """Deleting the XRD deletes its CRD and every XTenant with it, with no admission check."""
    xrd = one(XRD, "CompositeResourceDefinition", "xtenants.platform.arigsela.com")
    opts = set(xrd["metadata"]["annotations"]["argocd.argoproj.io/sync-options"].split(","))
    assert {"Prune=false", "Delete=false"} <= opts


def test_v12_a_delete_is_guarded_by_an_enforcing_policy():
    docs = {d["kind"]: d for d in load("base-apps/admission-policies/xtenant-guard.yaml")}
    vap, binding = docs["ValidatingAdmissionPolicy"], docs["ValidatingAdmissionPolicyBinding"]
    assert vap["spec"]["failurePolicy"] == "Fail" and binding["spec"]["validationActions"] == ["Deny"]
    rule = vap["spec"]["matchConstraints"]["resourceRules"][0]
    assert rule["resources"] == ["xtenants"] and set(rule["operations"]) == {"CREATE", "DELETE"}
    exprs = " ".join(v["expression"] for v in vap["spec"]["validations"])
    assert "oldObject.spec.offboard" in exprs and "oldObject.status.backup.startsWith('s3://')" in exprs


def test_v51_at_most_two_enterprise_tenants():
    assert len(_xtenant_files()) <= MAX_TENANTS, "beacon §V.51: at most 2 enterprise tenants"


def test_v50_tenant_files_are_one_xtenant_named_like_the_file():
    for f in _xtenant_files():
        docs = [d for d in yaml.safe_load_all(f.read_text()) if d]
        assert len(docs) == 1 and docs[0]["kind"] == "XTenant", f"{f.name}: one XTenant per file"
        assert docs[0]["metadata"]["name"] == f.stem


def test_v30_fixture_crd_matches_xrd():
    """The admission harness has no Crossplane, so it installs a plain CRD for XTenant. It must
    carry the XRD's own spec and status, or the guard is tested against a different object."""
    xrd = one(XRD, "CompositeResourceDefinition", "xtenants.platform.arigsela.com")
    crd = one(FIXTURE_CRD, "CustomResourceDefinition", "xtenants.platform.arigsela.com")
    xs = xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]
    cs = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]
    assert cs["spec"] == xs["spec"] and cs["status"] == xs["status"]
    assert crd["spec"]["scope"] == xrd["spec"]["scope"] == "Cluster"
    assert crd["spec"]["versions"][0]["subresources"] == {"status": {}}


def test_v52_composition_authenticates_with_the_provisioned_tenant_role():
    """The SecretStore every tenant gets uses the one role scripts/provision-beacon-tenants-vault.sh
    writes, with the one service account it binds, and writes only under tenants/."""
    s = _script()
    assert '"role": "beacon-tenant"' in s and '"serviceAccountRef": {"name": "eso-tenant"}' in s
    assert 'self.vault = f"tenants/{self.ns}"' in s
    script = VAULT_SCRIPT.read_text()
    assert re.search(r"auth/kubernetes/role/beacon-tenant \\\n\s+bound_service_account_names=eso-tenant", script)
    assert "'bound_service_account_namespaces=tenant-*'" in script
