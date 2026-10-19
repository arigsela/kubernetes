# tests/beacon/test_tenants.py
"""beacon enterprise tenants (beacon §T.25+): the static contract around the XTenant composition.

What the composition renders is tested in tests/composition/test_xtenant_render.py (it needs the
crossplane CLI); these need nothing but the files: who may delete a tenant, what Crossplane may
create, and that the Vault role the composition names is the one the script provisions.
"""
import json
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
    "ConfigMap": ("", "configmaps"), "Service": ("", "services"),
    "NetworkPolicy": ("networking.k8s.io", "networkpolicies"),
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
    offboarding PR (spec.offboard: true) drops the annotation (beacon §V.12, beacon §R.18)."""
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


# --- the tenant's beacon stack (beacon §T.46, beacon §V.53) -----------------------------------------
# Read from the committed render of a running tenant (tests/composition/xtenant/running/), which
# the composition-validate job proves equal to what the composition renders.

RUNNING = REPO / "tests/composition/xtenant/running/expected.yaml"
STACK = {"beacon-checker": "deployments.yaml", "beacon-notifier": "deployments.yaml",
         "beacon-scheduler": "cronjobs.yaml", "beacon-scheduler-daily": "cronjobs.yaml"}


def _template(d):
    spec = d["spec"]
    return spec.get("jobTemplate", {}).get("spec", spec)["template"]


def _rendered(name):
    docs = [d for d in yaml.safe_load_all(RUNNING.read_text()) if d and d["metadata"]["name"] == name
            and d["kind"] in ("Deployment", "CronJob", "Job")]
    assert len(docs) == 1, f"{name}: expected one workload in {RUNNING.name}"
    return docs[0]


def _beacons(name):
    return next(d for d in load(f"base-apps/beacon/{STACK[name]}") if d["metadata"]["name"] == name)


def _images_in_composition():
    return dict(re.findall(r'"(api|scheduler|checker|notifier)": f"\{ECR\}/beacon-[a-z]+:([^"]+)"', _script()))


def test_v53_stack_images_match_beacons_digests():
    """A promotion pins base-apps/beacon and the composition's IMAGES together, so a tenant never
    runs another release than beacon (base-apps/beacon/runbook.md, "Promote a release")."""
    pinned = {}
    for f in ("deployments.yaml", "cronjobs.yaml"):
        for d in load(f"base-apps/beacon/{f}"):
            for c in _template(d)["spec"]["containers"]:
                m = re.fullmatch(r".*/beacon-([a-z]+):(.+)", c["image"])
                if m:
                    pinned[m.group(1)] = m.group(2)
    images = _images_in_composition()
    assert set(images) == {"api", "scheduler", "checker", "notifier"}
    for svc, ref in images.items():
        assert ref == pinned[svc], f"composition IMAGES[{svc!r}] = {ref}, base-apps/beacon pins {pinned[svc]}"
    for name in STACK:
        for c in _template(_rendered(name))["spec"]["containers"]:
            assert c["image"].split(":", 1)[1] in images.values() or "beacon-" not in c["image"], name


def test_v53_tenant_workloads_run_like_beacons():
    """Same pod and container security, nodes, service-account token handling and resources as
    beacon's own workload of the same name: the tenant's stack is beacon's, not a second variant."""
    for name in STACK:
        ours, theirs = _template(_rendered(name))["spec"], _template(_beacons(name))["spec"]
        for key in ("securityContext", "nodeSelector", "automountServiceAccountToken", "serviceAccountName"):
            assert ours.get(key) == theirs.get(key), f"{name}: {key} differs from base-apps/beacon"
        for a, b in zip(ours["containers"], theirs["containers"], strict=True):
            for key in ("securityContext", "resources", "args", "ports", "livenessProbe", "readinessProbe"):
                assert a.get(key) == b.get(key), f"{name}/{a['name']}: {key} differs from base-apps/beacon"
            # Every setting beacon passes, the tenant's passes too (values differ: its own database,
            # notifier and no SSRF exemption).
            assert {e["name"] for e in b["env"]} <= {e["name"] for e in a["env"]}, name


def test_v39_tenant_secret_volumes_are_readable_by_the_pod_user():
    checked = 0
    for d in yaml.safe_load_all(RUNNING.read_text()):
        if not d or d["kind"] not in ("Deployment", "CronJob", "Job"):
            continue
        spec = _template(d)["spec"]
        sc = spec.get("securityContext", {})
        for v in spec.get("volumes", []):
            src = v.get("secret") or v.get("configMap")
            if src is None or src.get("defaultMode", 0o644) & 0o004:
                continue
            checked += 1
            assert src["defaultMode"] in (0o440, 0o444) and sc.get("fsGroup") == sc.get("runAsGroup"), \
                f"{d['metadata']['name']}/{v['name']}"
    assert checked >= 1  # the notifier's signing key


def test_v53_tenant_probe_egress_refuses_what_beacons_refuses():
    ours = next(d for d in yaml.safe_load_all(RUNNING.read_text())
                if d and d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "probe-egress")
    theirs = one("base-apps/beacon/network-policies.yaml", "NetworkPolicy", "probe-egress")

    def blocks(p):
        return [t["ipBlock"] for r in p["spec"]["egress"] for t in r.get("to", []) if "ipBlock" in t]

    assert blocks(ours) == blocks(theirs), "tenant probes must refuse every private range beacon's do (beacon §V.5)"
    assert ours["spec"]["podSelector"]["matchExpressions"][0]["values"] == ["beacon-checker", "beacon-notifier"]


def test_v18_the_stacks_third_party_images_already_run_on_a_node():
    """The dump uploader is smoke-run by beacon-smoke; the database image is the pooled cluster's."""
    aws = re.search(r'AWS_CLI = \("([^"]+)"\s+"([^"]+)"\)', _script())
    image = aws.group(1) + aws.group(2)
    smoke = one("base-apps/beacon/jobs.yaml", "Job", "beacon-smoke")["spec"]["template"]["spec"]
    assert image in [c["image"] for c in smoke["containers"]], "add the dump image to beacon-smoke"
    pooled = one("base-apps/postgresql/cnpg-cluster.yaml", "Cluster", "postgresql-cluster")["spec"]["imageName"]
    assert f'PG_IMAGE = "{pooled}"' in _script()


def test_v53_tenant_flags_are_beacons_flags_all_off():
    beacon = json.loads(one("base-apps/beacon/configmaps.yaml", "ConfigMap", "beacon-flags")["data"]["beacon-flags.json"])
    cm = next(d for d in yaml.safe_load_all(RUNNING.read_text())
              if d and d["kind"] == "ConfigMap" and d["metadata"]["name"] == "beacon-flags")
    ours = json.loads(cm["data"]["beacon-flags.json"])["flags"]
    assert {k: v["variants"] for k, v in ours.items()} == {k: v["variants"] for k, v in beacon["flags"].items()}
    assert all(v["defaultVariant"] == "off" and "targeting" not in v for v in ours.values())


# --- beacon-api's route to the tenants' databases (beacon §T.45) -----------------------------


def test_v52_beacon_api_reads_tenants_only_through_its_own_store():
    """The DSNs come through role beacon-tenants (list tenants/, read tenants/+/api), with its
    own service account, never through beacon's main store."""
    store = one("base-apps/beacon/secret-stores.yaml", "SecretStore", "vault-beacon-tenants")
    k8s = store["spec"]["provider"]["vault"]["auth"]["kubernetes"]
    assert k8s["role"] == "beacon-tenants" and k8s["serviceAccountRef"]["name"] == "eso-beacon-tenants"
    one("base-apps/beacon/serviceaccounts.yaml", "ServiceAccount", "eso-beacon-tenants")
    script = VAULT_SCRIPT.read_text()
    assert re.search(r"auth/kubernetes/role/beacon-tenants \\\n\s+bound_service_account_names=eso-beacon-tenants"
                     r" \\\n\s+bound_service_account_namespaces=beacon", script)
    es = one("base-apps/beacon/external-secrets.yaml", "ExternalSecret", "beacon-tenant-dsns")
    assert es["spec"]["secretStoreRef"]["name"] == "vault-beacon-tenants"
    [src] = es["spec"]["dataFrom"]
    assert src["find"]["path"] == "tenants"
    name = re.compile(src["find"]["name"]["regexp"])
    assert name.fullmatch("tenants/tenant-demo/api")
    for other in ("tenants/tenant-demo/db", "tenants/tenant-demo/signing", "beacon/api", "tenants/x/api"):
        assert not name.fullmatch(other), other
    rw = src["rewrite"][0]["regexp"]
    assert re.sub(rw["source"], rw["target"].replace("$1", r"\1"), "tenants/tenant-demo/api") == "demo"
    # Both depend on the owner-run Vault script: last wave, never ahead of beacon's rollout (beacon §V.38).
    for d in (store, es):
        assert d["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == "3"


def test_v55_beacon_api_mounts_the_tenant_dsns():
    api = one("base-apps/beacon/deployments.yaml", "Deployment", "beacon-api")["spec"]["template"]["spec"]
    c = api["containers"][0]
    env = {e["name"]: e.get("value") for e in c["env"]}
    mounts = {m["name"]: m for m in c["volumeMounts"]}
    vols = {v["name"]: v for v in api["volumes"]}
    assert env["TENANT_DSN_DIR"] == mounts["tenant-dsns"]["mountPath"] and mounts["tenant-dsns"]["readOnly"]
    secret = vols["tenant-dsns"]["secret"]
    # Optional: no tenant yet means no Secret, and the api must still start (beacon §V.55: 503s).
    assert secret["secretName"] == "beacon-tenant-dsns" and secret["optional"] is True
    assert secret["defaultMode"] == 0o440 and api["securityContext"]["fsGroup"] == api["securityContext"]["runAsGroup"]
    # Only beacon-api reads across tenants.
    for d in load("base-apps/beacon/deployments.yaml"):
        if d["metadata"]["name"] != "beacon-api":
            assert "tenant-dsns" not in yaml.safe_dump(d), d["metadata"]["name"]


def test_v54_beacon_holds_no_github_credential():
    """Enterprise signup never opens a PR itself (beacon §V.54): no beacon manifest names a
    GitHub token or app key."""
    for f in sorted((REPO / "base-apps/beacon").glob("*.yaml")):
        text = f.read_text()
        assert not re.search(r"(?i)github[_-]?(token|app|key|pat)|GH_TOKEN|api\.github\.com", text), f.name
