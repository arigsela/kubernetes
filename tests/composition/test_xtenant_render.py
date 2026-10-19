# tests/composition/test_xtenant_render.py
"""beacon §V.50: render every XTenant with the real function pipeline and check what it composes.

`crossplane render` (the pinned CLI) runs base-apps/crossplane-compositions/composition-tenant.yaml
through the pinned function-python for every case: the fixtures in xtenant/ and every XTenant
committed in base-apps/beacon-tenants/. One Composition renders every tenant, and Crossplane
deletes whatever leaves its desired state, so a change here must be visible in a golden diff
and must never drop or rename a tenant's Namespace, database or bucket.

Run (CI does, in the composition-validate job):
    python -m pytest tests/composition/ -q
needs `crossplane` (or CROSSPLANE_BIN) and Docker for function-python. Locally without Docker,
start function-python yourself (`python -m function.main --insecure` from its v0.4.0 source,
listening on :9443) and set COMPOSITION_RUNTIME=Development. COMPOSITION_REQUIRE=1 turns a
missing CLI into a failure instead of a skip. UPDATE_GOLDENS=1 rewrites the goldens: review
the diff, it is the change every tenant gets.
"""
import functools
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent / "xtenant"
COMPOSITION = REPO / "base-apps/crossplane-compositions/composition-tenant.yaml"
FUNCTIONS = Path(__file__).parent / "functions.yaml"
TENANTS = REPO / "base-apps/beacon-tenants"

CROSSPLANE = os.environ.get("CROSSPLANE_BIN") or shutil.which("crossplane")
RUNTIME = os.environ.get("COMPOSITION_RUNTIME")
# Kinds the composition may emit without a namespace; anything else must be in tenant-<slug>.
CLUSTER_KINDS = {"Namespace", "Bucket", "BucketLifecycleConfiguration", "User", "Policy",
                 "UserPolicyAttachment", "AccessKey"}


def _committed():
    out = []
    for f in sorted(TENANTS.glob("*.yaml")):
        if f.name == "catalog-info.yaml":
            continue
        for d in yaml.safe_load_all(f.read_text()):
            if d and d.get("kind") == "XTenant":
                out.append(f)
    return out


CASES = {p.stem.removeprefix("xr-"): p for p in sorted(HERE.glob("xr-*.yaml"))}
CASES.update({f"committed-{p.stem}": p for p in _committed()})


def _skip_or_fail(msg):
    if os.environ.get("COMPOSITION_REQUIRE") == "1":
        pytest.fail(msg, pytrace=False)
    pytest.skip(msg)


def _normalize(docs):
    """What the Composition decides, without render-time plumbing: owner refs and the composite
    label (Crossplane's wiring), the XR's status timestamps."""
    out = []
    for d in docs:
        d.get("metadata", {}).pop("ownerReferences", None)
        d.get("metadata", {}).get("labels", {}).pop("crossplane.io/composite", None)
        for c in d.get("status", {}).get("conditions", []):
            c.pop("lastTransitionTime", None)
        out.append(d)
    return sorted(out, key=lambda d: (d["kind"], d["metadata"].get("namespace", ""), d["metadata"]["name"]))


@functools.lru_cache(maxsize=None)
def _render(case):
    if not CROSSPLANE:
        _skip_or_fail("needs the crossplane CLI (or CROSSPLANE_BIN) to render compositions")
    cmd = [CROSSPLANE, "render", str(CASES[case]), str(COMPOSITION), str(FUNCTIONS)]
    if RUNTIME:
        cmd.append(f"--function-annotations=render.crossplane.io/runtime={RUNTIME}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0 and "docker" in r.stderr.lower() and not RUNTIME:
        _skip_or_fail(f"crossplane render needs Docker for function-python: {r.stderr.strip()[:300]}")
    assert r.returncode == 0, r.stderr
    return _normalize([d for d in yaml.safe_load_all(r.stdout) if d])


def _composed(case):
    return [d for d in _render(case) if d["kind"] != "XTenant"]


def _xr(case):
    return yaml.safe_load(CASES[case].read_text())


def _one(case, kind, name):
    docs = [d for d in _composed(case) if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(docs) == 1, f"{case}: expected one {kind}/{name}, found {len(docs)}"
    return docs[0]


def _golden(case):
    return HERE / f"expected-{case}.yaml"


def _mi(q):
    units = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024}
    for u, f in units.items():
        if q.endswith(u):
            return float(q[:-2]) * f
    return float(q) / 2**20


@pytest.mark.parametrize("case", sorted(CASES))
def test_v50_render_matches_golden(case):
    rendered = yaml.safe_dump_all(_render(case), sort_keys=True)
    golden = _golden(case)
    if os.environ.get("UPDATE_GOLDENS") == "1":
        golden.write_text(f"# rendered from {CASES[case].relative_to(REPO)}; UPDATE_GOLDENS=1 rewrites it\n"
                          + rendered)
    assert golden.exists(), f"{case}: no golden {golden.relative_to(REPO)}: render it with UPDATE_GOLDENS=1"
    expected = _normalize([d for d in yaml.safe_load_all(golden.read_text()) if d])
    assert _render(case) == expected, f"{case}: render differs from {golden.relative_to(REPO)}"


@pytest.mark.parametrize("case", sorted(CASES))
def test_v50_stateful_objects_never_vanish(case):
    """Computed from the XR alone, never from a golden someone may have regenerated: the
    tenant's namespace, database and bucket keep exactly these names."""
    xr = _xr(case)
    slug, tid = xr["metadata"]["name"], xr["spec"]["tenantId"]
    ns = f"tenant-{slug}"
    _one(case, "Namespace", ns)
    db = _one(case, "Cluster", "beacon-db")
    assert db["metadata"]["namespace"] == ns
    _one(case, "Bucket", f"beacon-tenant-{slug}-{tid.replace('-', '')[:8]}")


@pytest.mark.parametrize("case", sorted(CASES))
def test_v50_namespaced_objects_stay_in_tenant_ns(case):
    ns = f"tenant-{_xr(case)['metadata']['name']}"
    for d in _composed(case):
        where = d["metadata"].get("namespace")
        if d["kind"] in CLUSTER_KINDS:
            assert where is None, f"{case}: cluster-scoped {d['kind']}/{d['metadata']['name']} has a namespace"
        else:
            assert where == ns, f"{case}: {d['kind']}/{d['metadata']['name']} lands in {where!r}, not {ns}"
    key = _one(case, "AccessKey", f"beacon-tenant-{_xr(case)['metadata']['name']}")
    assert key["spec"]["writeConnectionSecretToRef"]["namespace"] == ns


@pytest.mark.parametrize("case", sorted(CASES))
def test_v31_default_deny_admits_hbone(case):
    """Ambient namespace: every ingress allow to a meshed pod also admits HBONE (15008). The
    database pods are out of the mesh (istio.io/dataplane-mode: none), so theirs need not."""
    pols = {d["metadata"]["name"]: d for d in _composed(case) if d["kind"] == "NetworkPolicy"}
    deny = pols["default-deny-ingress"]["spec"]
    assert deny["podSelector"] == {} and deny["policyTypes"] == ["Ingress"] and "ingress" not in deny
    db = _one(case, "Cluster", "beacon-db")
    assert db["spec"]["inheritedMetadata"]["labels"]["istio.io/dataplane-mode"] == "none"
    for name, p in pols.items():
        if name == "default-deny-ingress" or "cnpg.io/cluster" in p["spec"]["podSelector"].get("matchLabels", {}):
            continue
        for rule in p["spec"].get("ingress", []):
            assert 15008 in [port["port"] for port in rule.get("ports", [])], f"{case}: {name} lacks HBONE 15008"


@pytest.mark.parametrize("case", sorted(CASES))
def test_v12_bucket_is_orphaned_and_backups_are_not_barman(case):
    buckets = [d for d in _composed(case) if d["kind"] == "Bucket"]
    assert buckets and all(b["spec"]["deletionPolicy"] == "Orphan" for b in buckets)
    db = _one(case, "Cluster", "beacon-db")
    assert "backup" not in db["spec"], "in-tree barmanObjectStore is removed in CNPG 1.31 (beacon §R.46)"
    assert "barmanObjectStore" not in yaml.safe_dump(_composed(case))


@pytest.mark.parametrize("case", sorted(CASES))
def test_v49_bucket_name_carries_tenant_id(case):
    xr = _xr(case)
    id8 = xr["spec"]["tenantId"].replace("-", "")[:8]
    for b in (d for d in _composed(case) if d["kind"] == "Bucket"):
        assert b["metadata"]["name"].endswith(f"-{id8}")


@pytest.mark.parametrize("case", sorted(CASES))
def test_v51_quota_and_limitrange(case):
    hard = _one(case, "ResourceQuota", "tenant-quota")["spec"]["hard"]
    assert _mi(hard["requests.memory"]) <= 1024 and _mi(hard["limits.memory"]) <= 2048
    lr = _one(case, "LimitRange", "tenant-defaults")["spec"]["limits"][0]
    assert lr["type"] == "Container" and lr["default"]["memory"] and lr["defaultRequest"]["memory"]


@pytest.mark.parametrize("case", sorted(CASES))
def test_v52_secrets_are_generated_once_and_pushed_to_the_tenants_folder(case):
    ns = f"tenant-{_xr(case)['metadata']['name']}"
    store = _one(case, "SecretStore", "vault-backend")["spec"]["provider"]["vault"]
    assert store["auth"]["kubernetes"]["role"] == "beacon-tenant"
    assert store["auth"]["kubernetes"]["serviceAccountRef"]["name"] == "eso-tenant"
    _one(case, "ServiceAccount", "eso-tenant")
    for es in (d for d in _composed(case) if d["kind"] == "ExternalSecret"):
        if any("generatorRef" in df.get("sourceRef", {}) for df in es["spec"].get("dataFrom", [])):
            assert es["spec"]["refreshPolicy"] == "CreatedOnce", f"{es['metadata']['name']} would regenerate"
    push = _one(case, "PushSecret", "beacon-vault-push")["spec"]
    assert push["deletionPolicy"] == "Delete"
    remotes = [m["match"]["remoteRef"] for m in push["data"]]
    assert all(r["remoteKey"].startswith(f"tenants/{ns}/") for r in remotes)
    # The one key beacon-api may read holds the DSN and the signing key, nothing else.
    assert {r["property"] for r in remotes if r["remoteKey"] == f"tenants/{ns}/api"} == {"dsn", "signing_key"}
    usages = {(u["spec"]["of"]["kind"], u["spec"]["of"]["resourceRef"]["name"]): u["spec"]["by"]
              for u in _composed(case) if u["kind"] == "Usage"}
    for of in [("SecretStore", "vault-backend"), ("ServiceAccount", "eso-tenant")]:
        assert usages[of]["kind"] == "PushSecret" and usages[of]["resourceRef"]["name"] == "beacon-vault-push"


@pytest.mark.parametrize("case", sorted(CASES))
def test_v9_tenant_namespace_gets_platform_defaults_and_python_instrumentation(case):
    ns = _one(case, "Namespace", f"tenant-{_xr(case)['metadata']['name']}")
    labels = ns["metadata"]["labels"]
    assert labels["platform.arigsela.com/defaults"] == "enabled"
    assert labels["platform.arigsela.com/otel"] == "python"
    assert labels["istio.io/dataplane-mode"] == "ambient"
    assert "istio.io/ingress-use-waypoint" not in labels


def test_every_committed_tenant_is_a_render_case():
    assert {f"committed-{p.stem}" for p in _committed()} <= set(CASES)
    for p in _committed():
        assert re.fullmatch(r"[a-z][a-z0-9-]{2,30}", p.stem), p
