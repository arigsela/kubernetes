# tests/composition/test_xtenant_render.py
"""beacon §V.50: render every XTenant with the real function pipeline and check what it composes.

`crossplane render` (the pinned CLI) runs base-apps/crossplane-compositions/composition-tenant.yaml
through the pinned function-python for every case: the cases in xtenant/<case>/ (an XR, optional
observed state, and the golden) and every XTenant committed in base-apps/beacon-tenants/. One
Composition renders every tenant, and Crossplane deletes whatever leaves its desired state, so a
change here must be visible in a golden diff and must never drop or rename a tenant's Namespace,
database or bucket.

Cases: fresh (just merged, nothing observed), running (database Ready, the current release's
init Job done: the whole stack), offboarding (and its dump Job verified the dump).

Run (CI does, in the composition-validate job):
    python -m pytest tests/composition/ -q
needs `crossplane` (or CROSSPLANE_BIN) and Docker for function-python. Locally without Docker,
start function-python yourself (`python -m function.main --insecure` from its v0.4.0 source,
listening on :9443) and set COMPOSITION_RUNTIME=Development. COMPOSITION_REQUIRE=1 turns a
missing CLI into a failure instead of a skip. UPDATE_GOLDENS=1 rewrites the goldens: review
the diff, it is the change every tenant gets (a beacon promotion changes the images in them).
"""
import functools
import os
import re
import shutil
import subprocess
import tempfile
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
NAMESPACED_OBSERVED = {"Cluster", "Job", "Deployment", "CronJob"}


def _committed():
    out = []
    for f in sorted(TENANTS.glob("*.yaml")):
        if f.name == "catalog-info.yaml":
            continue
        if any(d and d.get("kind") == "XTenant" for d in yaml.safe_load_all(f.read_text())):
            out.append(f)
    return out


# case -> (XR file, observed spec or None, golden)
CASES = {d.name: (d / "xr.yaml", d / "observed.yaml", d / "expected.yaml")
         for d in sorted(HERE.iterdir()) if (d / "xr.yaml").exists()}
CASES.update({f"committed-{p.stem}": (p, None, HERE / f"committed-{p.stem}.expected.yaml") for p in _committed()})


def _skip_or_fail(msg):
    if os.environ.get("COMPOSITION_REQUIRE") == "1":
        pytest.fail(msg, pytrace=False)
    pytest.skip(msg)


def _api8():
    m = re.search(r'"api": f"\{ECR\}/beacon-api:[^@]+@sha256:([0-9a-f]{8})', COMPOSITION.read_text())
    assert m, "composition-tenant.yaml: IMAGES['api'] not found"
    return m.group(1)


def _observed_file(case, tmp):
    """observed.yaml is a short spec; expand it into the objects `crossplane render -o` reads,
    each tagged with its composition-resource-name."""
    xr_file, spec_file, _ = CASES[case]
    if not spec_file or not spec_file.exists():
        return None
    ns = f"tenant-{yaml.safe_load(xr_file.read_text())['metadata']['name']}"
    subst = {"{init}": f"init-{_api8()}", "{init_job}": f"beacon-init-{_api8()}"}
    docs = []
    for o in yaml.safe_load(spec_file.read_text()):
        name, meta = subst.get(o["name"], o["name"]), subst.get(o["metadataName"], o["metadataName"])
        md = {"name": meta, "annotations": {"crossplane.io/composition-resource-name": name}}
        if o["kind"] in NAMESPACED_OBSERVED:
            md["namespace"] = ns
        docs.append({"apiVersion": o["apiVersion"], "kind": o["kind"], "metadata": md, "status": o["status"]})
    path = Path(tmp) / f"{case}-observed.yaml"
    path.write_text(yaml.safe_dump_all(docs))
    return path


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
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [CROSSPLANE, "render", str(CASES[case][0]), str(COMPOSITION), str(FUNCTIONS)]
        observed = _observed_file(case, tmp)
        if observed:
            cmd.append(f"--observed-resources={observed}")
        if RUNTIME:
            cmd.append(f"--function-annotations=render.crossplane.io/runtime={RUNTIME}")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0 and "docker" in r.stderr.lower() and not RUNTIME:
        _skip_or_fail(f"crossplane render needs Docker for function-python: {r.stderr.strip()[:300]}")
    assert r.returncode == 0, r.stderr
    return _normalize([d for d in yaml.safe_load_all(r.stdout) if d])


def _composed(case):
    return [d for d in _render(case) if d["kind"] != "XTenant"]


def _rendered_xr(case):
    return next(d for d in _render(case) if d["kind"] == "XTenant")


def _xr(case):
    return yaml.safe_load(CASES[case][0].read_text())


def _one(case, kind, name):
    docs = [d for d in _composed(case) if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(docs) == 1, f"{case}: expected one {kind}/{name}, found {len(docs)}"
    return docs[0]


def _kinds(case, kind):
    return [d for d in _composed(case) if d["kind"] == kind]


def _env(container):
    return {e["name"]: e.get("value") for e in container.get("env", [])}


def _pod(obj):
    spec = obj["spec"]
    spec = spec.get("jobTemplate", {}).get("spec", spec)
    return spec["template"]


def _mi(q):
    for u, f in {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024}.items():
        if q.endswith(u):
            return float(q[:-2]) * f
    return float(q) / 2**20


ALL = sorted(CASES)
STACK = [c for c in ("running", "offboarding") if c in CASES]


@pytest.mark.parametrize("case", ALL)
def test_v50_render_matches_golden(case):
    golden = CASES[case][2]
    if os.environ.get("UPDATE_GOLDENS") == "1":
        golden.write_text(f"# rendered from {CASES[case][0].relative_to(REPO)}; UPDATE_GOLDENS=1 rewrites it\n"
                          + yaml.safe_dump_all(_render(case), sort_keys=True))
    assert golden.exists(), f"{case}: no golden {golden.relative_to(REPO)}: render it with UPDATE_GOLDENS=1"
    expected = _normalize([d for d in yaml.safe_load_all(golden.read_text()) if d])
    assert _render(case) == expected, f"{case}: render differs from {golden.relative_to(REPO)}"


@pytest.mark.parametrize("case", ALL)
def test_v50_stateful_objects_never_vanish(case):
    """Computed from the XR alone, never from a golden someone may have regenerated: the
    tenant's namespace, database and bucket keep exactly these names, whatever the state."""
    xr = _xr(case)
    slug, tid = xr["metadata"]["name"], xr["spec"]["tenantId"]
    ns = f"tenant-{slug}"
    _one(case, "Namespace", ns)
    assert _one(case, "Cluster", "beacon-db")["metadata"]["namespace"] == ns
    _one(case, "Bucket", f"beacon-tenant-{slug}-{tid.replace('-', '')[:8]}")


@pytest.mark.parametrize("case", ALL)
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


@pytest.mark.parametrize("case", ALL)
def test_v31_default_deny_admits_hbone(case):
    """Ambient namespace: every ingress allow to a meshed pod also admits HBONE (15008). The
    database pods are out of the mesh (istio.io/dataplane-mode: none), so theirs need not."""
    pols = {d["metadata"]["name"]: d for d in _kinds(case, "NetworkPolicy")}
    deny = pols["default-deny-ingress"]["spec"]
    assert deny["podSelector"] == {} and deny["policyTypes"] == ["Ingress"] and "ingress" not in deny
    db = _one(case, "Cluster", "beacon-db")
    assert db["spec"]["inheritedMetadata"]["labels"]["istio.io/dataplane-mode"] == "none"
    for name, p in pols.items():
        if name == "default-deny-ingress" or "cnpg.io/cluster" in p["spec"]["podSelector"].get("matchLabels", {}):
            continue
        for rule in p["spec"].get("ingress", []):
            assert 15008 in [port["port"] for port in rule.get("ports", [])], f"{case}: {name} lacks HBONE 15008"


@pytest.mark.parametrize("case", ALL)
def test_v12_bucket_is_orphaned_and_backups_are_not_barman(case):
    buckets = _kinds(case, "Bucket")
    assert buckets and all(b["spec"]["deletionPolicy"] == "Orphan" for b in buckets)
    assert "backup" not in _one(case, "Cluster", "beacon-db")["spec"], \
        "in-tree barmanObjectStore is removed in CNPG 1.31 (beacon §R.46)"
    assert "barmanObjectStore" not in yaml.safe_dump(_composed(case))


@pytest.mark.parametrize("case", ALL)
def test_v49_bucket_name_carries_tenant_id(case):
    id8 = _xr(case)["spec"]["tenantId"].replace("-", "")[:8]
    for b in _kinds(case, "Bucket"):
        assert b["metadata"]["name"].endswith(f"-{id8}")


@pytest.mark.parametrize("case", ALL)
def test_v51_quota_and_limitrange(case):
    hard = _one(case, "ResourceQuota", "tenant-quota")["spec"]["hard"]
    assert _mi(hard["requests.memory"]) <= 1024 and _mi(hard["limits.memory"]) <= 2048
    lr = _one(case, "LimitRange", "tenant-defaults")["spec"]["limits"][0]
    assert lr["type"] == "Container" and lr["default"]["memory"] and lr["defaultRequest"]["memory"]


@pytest.mark.parametrize("case", STACK)
def test_v51_the_running_stack_fits_the_quota(case):
    """Every pod declares requests, and the steady state (database, checker, notifier, and the
    largest Job at a time) stays under the namespace's requests.memory."""
    hard = _one(case, "ResourceQuota", "tenant-quota")["spec"]["hard"]
    steady = _mi(_one(case, "Cluster", "beacon-db")["spec"]["resources"]["requests"]["memory"])
    for d in _kinds(case, "Deployment"):
        for c in _pod(d)["spec"]["containers"]:
            steady += _mi(c["resources"]["requests"]["memory"]) * d["spec"]["replicas"]
    jobs = []
    for d in _kinds(case, "Job") + _kinds(case, "CronJob"):
        spec = _pod(d)["spec"]
        cs = spec["containers"] + spec.get("initContainers", [])
        assert all(c["resources"]["requests"].get("memory") for c in cs), d["metadata"]["name"]
        jobs.append(max(_mi(c["resources"]["requests"]["memory"]) for c in cs))
    assert steady + max(jobs) <= _mi(hard["requests.memory"]), f"{steady} + {max(jobs)} Mi"


@pytest.mark.parametrize("case", ALL)
def test_v52_secrets_are_generated_once_and_pushed_to_the_tenants_folder(case):
    ns = f"tenant-{_xr(case)['metadata']['name']}"
    store = _one(case, "SecretStore", "vault-backend")["spec"]["provider"]["vault"]
    assert store["auth"]["kubernetes"]["role"] == "beacon-tenant"
    assert store["auth"]["kubernetes"]["serviceAccountRef"]["name"] == "eso-tenant"
    _one(case, "ServiceAccount", "eso-tenant")
    for es in _kinds(case, "ExternalSecret"):
        if any("generatorRef" in df.get("sourceRef", {}) for df in es["spec"].get("dataFrom", [])):
            assert es["spec"]["refreshPolicy"] == "CreatedOnce", f"{es['metadata']['name']} would regenerate"
    push = _one(case, "PushSecret", "beacon-vault-push")["spec"]
    assert push["deletionPolicy"] == "Delete"
    remotes = [m["match"]["remoteRef"] for m in push["data"]]
    assert all(r["remoteKey"].startswith(f"tenants/{ns}/") for r in remotes)
    # The one key beacon-api may read holds the DSN and the signing key, nothing else.
    assert {r["property"] for r in remotes if r["remoteKey"] == f"tenants/{ns}/api"} == {"dsn", "signing_key"}
    usages = {(u["spec"]["of"]["kind"], u["spec"]["of"]["resourceRef"]["name"]): u["spec"]["by"]
              for u in _kinds(case, "Usage")}
    for of in [("SecretStore", "vault-backend"), ("ServiceAccount", "eso-tenant")]:
        assert usages[of]["kind"] == "PushSecret" and usages[of]["resourceRef"]["name"] == "beacon-vault-push"


@pytest.mark.parametrize("case", ALL)
def test_v9_tenant_namespace_gets_platform_defaults_and_python_instrumentation(case):
    labels = _one(case, "Namespace", f"tenant-{_xr(case)['metadata']['name']}")["metadata"]["labels"]
    assert labels["platform.arigsela.com/defaults"] == "enabled"
    assert labels["platform.arigsela.com/otel"] == "python"
    assert labels["istio.io/dataplane-mode"] == "ambient"
    assert "istio.io/ingress-use-waypoint" not in labels


# --- the tenant's beacon stack (beacon §T.46) -------------------------------------------------

def test_stack_waits_for_its_database_and_its_release():
    """Nothing runs before the database is Ready; the workers only after the current release's
    init Job (roles, migrations, the tenant row) succeeded."""
    if "fresh" not in CASES:
        pytest.skip("no fresh case")
    assert not [d for d in _composed("fresh") if d["kind"] in ("Job", "CronJob", "Deployment")]
    names = {d["metadata"]["name"] for d in _composed("running")}
    assert f"beacon-init-{_api8()}" in names
    assert {"beacon-checker", "beacon-notifier", "beacon-scheduler", "beacon-scheduler-daily",
            "beacon-dump-nightly"} <= names


@pytest.mark.parametrize("case", STACK)
def test_v12_dumps_are_verified_before_and_after_upload(case):
    jobs = {d["metadata"]["name"]: d for d in _kinds(case, "Job") + _kinds(case, "CronJob")}
    names = ["beacon-dump-nightly"] + (["beacon-offboard-dump"] if case == "offboarding" else [])
    for name in names:
        spec = _pod(jobs[name])["spec"]
        dump = " ".join(spec["initContainers"][0]["command"])
        upload = " ".join(spec["containers"][0]["command"])
        assert "pg_dump --format=custom" in dump and "pg_restore --list" in dump and "test -s" in dump
        assert "head-object" in upload and 'test "$size" -gt 0' in upload
        assert _env(spec["containers"][0])["BUCKET"].startswith("beacon-tenant-")
    nightly = _env(_pod(jobs["beacon-dump-nightly"])["spec"]["containers"][0])
    assert nightly.get("KEY_PREFIX") == "nightly/" and "KEY" not in nightly
    if case == "offboarding":
        assert _env(_pod(jobs["beacon-offboard-dump"])["spec"]["containers"][0])["KEY"] == "offboard/beacon.dump"


def test_v12_offboarding_stops_the_stack_and_records_the_backup():
    if "offboarding" not in CASES:
        pytest.skip("no offboarding case")
    xr = _xr("offboarding")
    bucket = f"beacon-tenant-{xr['metadata']['name']}-{xr['spec']['tenantId'].replace('-', '')[:8]}"
    assert _rendered_xr("offboarding")["status"]["backup"] == f"s3://{bucket}/offboard/beacon.dump"
    assert all(d["spec"]["replicas"] == 0 for d in _kinds("offboarding", "Deployment"))
    assert all(d["spec"]["suspend"] for d in _kinds("offboarding", "CronJob"))
    for case in ("fresh", "running"):
        if case in CASES:
            assert "backup" not in _rendered_xr(case).get("status", {}), case


@pytest.mark.parametrize("case", STACK)
def test_v5_tenant_probes_are_never_exempt(case):
    for name in ("beacon-checker", "beacon-notifier"):
        env = _env(_pod(_one(case, "Deployment", name))["spec"]["containers"][0])
        # Explicitly empty: unset defaults to `homelab` (libs/beacon_core ssrf.Policy.from_env).
        assert env["SSRF_EXEMPT_TENANTS"] == "", name
        assert env["SSRF_SELF_HOSTS"]


@pytest.mark.parametrize("case", STACK)
def test_v9_the_tenants_notifier_is_left_to_the_operator(case):
    """No inject-python annotation on the notifier (the namespace's workload defaults add "true"),
    "false" on every SDK service, and the namespace has its own Instrumentation (beacon §V.9, §V.32)."""
    assert "annotations" not in _pod(_one(case, "Deployment", "beacon-notifier"))["metadata"]
    for kind, name in [("Deployment", "beacon-checker"), ("CronJob", "beacon-scheduler"),
                       ("CronJob", "beacon-scheduler-daily")]:
        ann = _pod(_one(case, kind, name))["metadata"]["annotations"]
        assert ann["instrumentation.opentelemetry.io/inject-python"] == "false", name
    inst = _one(case, "Instrumentation", "beacon-python")["spec"]
    assert inst["exporter"]["endpoint"].endswith(":4318")


@pytest.mark.parametrize("case", STACK)
def test_v41_tenant_sdk_services_export_to_the_collector(case):
    for kind, name in [("Deployment", "beacon-checker"), ("CronJob", "beacon-scheduler")]:
        env = _env(_pod(_one(case, kind, name))["spec"]["containers"][0])
        assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://otel-collector.tracing.svc.cluster.local:4318"
        assert env["OTEL_TRACES_SAMPLER"] == "parentbased_traceidratio" and env["OTEL_TRACES_SAMPLER_ARG"]
    egress = _one(case, "NetworkPolicy", "probe-egress")["spec"]["egress"]
    assert any(r["ports"] == [{"port": 4318, "protocol": "TCP"}] for r in egress if "ports" in r)


@pytest.mark.parametrize("case", STACK)
def test_the_stack_reaches_only_its_own_database_and_notifier(case):
    ns = f"tenant-{_xr(case)['metadata']['name']}"
    for name in ("beacon-checker", "beacon-notifier"):
        env = _env(_pod(_one(case, "Deployment", name))["spec"]["containers"][0])
        assert f"@beacon-db-rw.{ns}.svc.cluster.local:5432/beacon" in env["DATABASE_URL"]
    checker = _env(_pod(_one(case, "Deployment", "beacon-checker"))["spec"]["containers"][0])
    assert checker["NOTIFIER_URL"] == f"http://beacon-notifier.{ns}.svc.cluster.local:8080"


def test_every_committed_tenant_is_a_render_case():
    assert {f"committed-{p.stem}" for p in _committed()} <= set(CASES)
    for p in _committed():
        assert re.fullmatch(r"[a-z][a-z0-9-]{2,30}", p.stem), p
