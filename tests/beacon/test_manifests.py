# tests/beacon/test_manifests.py
"""beacon §T.14, §T.15, §T.41: the deployment keeps the invariants beacon's SPEC.md states."""
import json
import re
from pathlib import Path

import pytest
import yaml
from conftest import HOST, REPO, load, one

APP = REPO / "base-apps/beacon"
INIT = "base-apps/postgresql/init-beacon-roles.yaml"
SERVICES = ("api", "web", "scheduler", "checker", "notifier", "mcp")
IMAGE_RX = re.compile(
    r"^852893458518\.dkr\.ecr\.us-east-2\.amazonaws\.com/beacon-(?P<svc>[a-z]+)"
    r":(?P<tag>v\d+\.\d+\.\d+)@(?P<digest>sha256:[0-9a-f]{64})$"
)
FLAGS = ("checkerSlowProbe", "checkerFalseDown", "notifierReceiverFail", "apiDbPoolExhaust",
         "apiSlowQuery", "tenantFloodIngest", "schedulerStall")


def app_docs():
    for f in sorted(APP.glob("*.yaml")):
        if f.name == "catalog-info.yaml":
            continue
        for d in load(f.relative_to(REPO).as_posix()):
            yield f.name, d


def pod_spec(d):
    if d["kind"] in ("Deployment", "Job"):
        return d["spec"]["template"]["spec"]
    if d["kind"] == "CronJob":
        return d["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return None


def pod_meta(d):
    if d["kind"] == "CronJob":
        return d["spec"]["jobTemplate"]["spec"]["template"]["metadata"]
    return d["spec"]["template"]["metadata"]


def workloads():
    docs = [(n, d) for n, d in app_docs() if pod_spec(d)] + [(INIT, d) for d in load(INIT)]
    return docs


def beacon_images():
    for where, d in workloads():
        for c in pod_spec(d)["containers"] + pod_spec(d).get("initContainers", []):
            if "beacon-" in c["image"]:
                yield where, d["metadata"]["name"], c["name"], c["image"]


def by_name(kind, name):
    hits = [d for _, d in app_docs() if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(hits) == 1, f"{kind}/{name}: {len(hits)}"
    return hits[0]


def env(container):
    return {e["name"]: e for e in container.get("env", [])}


# --- images (beacon §V.17, §V.18, §T.41) ---------------------------------------------------


def test_v17_every_beacon_image_is_ecr_digest_pinned_and_from_one_release():
    seen = list(beacon_images())
    assert seen
    tags, digests = set(), {}
    for where, obj, container, image in seen:
        m = IMAGE_RX.match(image)
        assert m, f"{where} {obj}/{container}: {image} is not an ECR digest pin"
        assert m["svc"] in SERVICES
        tags.add(m["tag"])
        digests.setdefault(m["svc"], set()).add(m["digest"])
    # One promotion PR moves every reference together (runbook "Promote a release").
    assert len(tags) == 1, f"beacon images from several releases: {sorted(tags)}"
    assert all(len(v) == 1 for v in digests.values()), digests


def test_v18_smoke_job_runs_every_image_on_a_node_before_each_sync():
    job = by_name("Job", "beacon-smoke")
    assert job["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PreSync"
    spec = job["spec"]
    assert spec["backoffLimit"] == 0
    containers = spec["template"]["spec"]["containers"]
    smoked = {IMAGE_RX.match(c["image"])["svc"] for c in containers if "beacon-" in c["image"]}
    assert smoked == set(SERVICES)
    # The smoke Job runs exactly the digests that roll out, not some other build.
    rolled = {IMAGE_RX.match(i)["svc"]: i for *_, i in beacon_images()}
    for c in containers:
        assert c["image"] == rolled[IMAGE_RX.match(c["image"])["svc"]]
    names = {c["name"]: c for c in containers}
    for svc in ("api", "scheduler", "checker", "notifier", "mcp"):
        assert names[svc].get("args") == ["--self-check"], svc
    assert "opentelemetry" in names["notifier-no-otel"]["args"][0]
    assert "nginx -t" in names["web"]["args"][0]
    assert "avx" in names["cpu"]["args"][0]


# --- ordering ---------------------------------------------------------------------------


def test_migrations_run_after_the_secrets_and_before_any_workload():
    mig = by_name("Job", "beacon-migrate")
    ann = mig["metadata"]["annotations"]
    assert ann["argocd.argoproj.io/hook"] == "Sync" and ann["argocd.argoproj.io/sync-wave"] == "1"
    c = mig["spec"]["template"]["spec"]["containers"][0]
    assert c["args"] == ["migrate"]
    assert env(c)["DATABASE_URL"]["valueFrom"]["secretKeyRef"]["name"] == "beacon-db-owner"
    for _, d in app_docs():
        if d["kind"] in ("Deployment", "CronJob"):
            assert d["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == "2", d["metadata"]["name"]


def test_only_the_migrate_job_holds_the_owner_credential():
    for _, d in app_docs():
        spec = pod_spec(d)
        if not spec or d["metadata"]["name"] == "beacon-migrate":
            continue
        assert "beacon-db-owner" not in json.dumps(spec), d["metadata"]["name"]


def test_role_init_hook_runs_beacons_own_sql_as_the_cnpg_superuser():
    (job,) = load(INIT)
    ann = job["metadata"]["annotations"]
    assert ann["argocd.argoproj.io/hook"] == "Sync"
    c = job["spec"]["template"]["spec"]["containers"][0]
    assert "exec beacon-service bootstrap-roles" in c["args"][0]
    e = env(c)
    assert e["PGHOST"]["value"] == "postgresql-cluster-rw.postgresql.svc.cluster.local"
    assert e["PGSSLMODE"]["value"] == "require"
    for k in ("PGUSER", "PGPASSWORD"):
        ref = e[k]["valueFrom"]["secretKeyRef"]
        assert ref["name"] == "postgresql-cluster-superuser" and not ref.get("optional")
    for k in ("BEACON_OWNER_PASSWORD", "BEACON_APP_PASSWORD"):
        ref = e[k]["valueFrom"]["secretKeyRef"]
        # Optional: without Vault values the hook skips instead of failing every postgresql sync.
        assert ref["name"] == "beacon-db-credentials" and ref["optional"] is True
    cluster = one("base-apps/postgresql/cnpg-cluster.yaml", "Cluster", "postgresql-cluster")
    assert cluster["spec"]["enableSuperuserAccess"] is True
    db = one("base-apps/postgresql/beacon-database.yaml", "Database", "beacon")
    assert db["spec"]["owner"] == "beacon_owner" and db["spec"]["databaseReclaimPolicy"] == "retain"
    assert db["spec"]["cluster"]["name"] == "postgresql-cluster"


def test_v37_role_hook_runs_in_wave_zero_and_skips_without_inputs():
    """beacon §V.37 (§B.7): in the shared postgresql app a Degraded neighbour in an earlier
    wave halts every later wave. agent-audit-risk exits 1 by design whenever it finds a
    high-risk session, so a wave-1 hook simply never ran. Wave 0, and never fail or hang on
    beacon's own missing inputs."""
    (job,) = load(INIT)
    assert job["metadata"]["annotations"].get("argocd.argoproj.io/sync-wave", "0") == "0"
    c = job["spec"]["template"]["spec"]["containers"][0]
    assert 'exit 0' in c["args"][0]  # skip, don't fail, without Vault's passwords
    for k in ("BEACON_OWNER_PASSWORD", "BEACON_APP_PASSWORD"):
        assert env(c)[k]["valueFrom"]["secretKeyRef"]["optional"] is True  # don't hang either
    assert job["spec"]["backoffLimit"] <= 5 and job["spec"]["ttlSecondsAfterFinished"] <= 600


# --- tracing (beacon §V.8, §V.9, §V.32) -----------------------------------------------------


def test_v8_nothing_opts_ingress_into_the_waypoint():
    for name, d in app_docs():
        labels = d["metadata"].get("labels", {}) or {}
        assert "istio.io/ingress-use-waypoint" not in labels, f"{name}: {d['metadata']['name']}"
    ns = by_name("Namespace", "beacon")["metadata"]["labels"]
    assert ns["istio.io/dataplane-mode"] == "ambient" and ns["istio.io/use-waypoint"] == "beacon-waypoint"
    route = by_name("HTTPRoute", "beacon")
    refs = [b for r in route["spec"]["rules"] for b in r["backendRefs"]]
    assert all(b.get("kind", "Service") == "Service" and b["name"] in ("beacon-api", "beacon-web") for b in refs)


def test_v9_notifier_carries_no_tracing_config_and_does_not_opt_out():
    dep = by_name("Deployment", "beacon-notifier")
    c = dep["spec"]["template"]["spec"]["containers"][0]
    assert not [k for k in env(c) if k.startswith("OTEL_")]
    # It is traced only through the namespace's operator opt-in (beacon §T.22), so it must
    # not carry the SDK services' opt-out.
    assert "instrumentation.opentelemetry.io/inject-python" not in (pod_meta(dep).get("annotations") or {})


def test_v32_sdk_services_opt_out_of_operator_injection():
    for kind, name in (("Deployment", "beacon-api"), ("Deployment", "beacon-checker"),
                       ("CronJob", "beacon-scheduler"), ("CronJob", "beacon-scheduler-daily"),
                       ("Job", "beacon-migrate")):
        ann = pod_meta(by_name(kind, name)).get("annotations") or {}
        assert ann.get("instrumentation.opentelemetry.io/inject-python") == "false", name


# --- network (beacon §V.5, §V.31) -----------------------------------------------------------


def test_v31_every_ingress_allow_in_the_ambient_namespace_admits_hbone():
    policies = [d for _, d in app_docs() if d["kind"] == "NetworkPolicy"]
    assert any(p["metadata"]["name"] == "default-deny-ingress" and p["spec"]["podSelector"] == {}
               and p["spec"]["policyTypes"] == ["Ingress"] and not p["spec"].get("ingress") for p in policies)
    for p in policies:
        for rule in p["spec"].get("ingress", []):
            ports = {x["port"] for x in rule.get("ports", [])}
            assert 15008 in ports, p["metadata"]["name"]


def test_v5_probe_egress_refuses_private_and_cluster_ranges():
    p = by_name("NetworkPolicy", "probe-egress")
    assert set(p["spec"]["podSelector"]["matchExpressions"][0]["values"]) == {"beacon-checker", "beacon-notifier"}
    public = [t["ipBlock"] for r in p["spec"]["egress"] for t in r.get("to", []) if "ipBlock" in t]
    assert len(public) == 1 and public[0]["cidr"] == "0.0.0.0/0"
    assert {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8",
            "169.254.0.0/16", "0.0.0.0/8"} <= set(public[0]["except"])


def test_only_the_checker_may_call_the_notifier():
    ap = by_name("AuthorizationPolicy", "beacon-notifier")
    assert ap["spec"]["action"] == "ALLOW"
    assert ap["spec"]["targetRefs"] == [{"kind": "Service", "group": "", "name": "beacon-notifier"}]
    (rule,) = ap["spec"]["rules"]
    assert rule["from"] == [{"source": {"principals": ["cluster.local/ns/beacon/sa/beacon-checker"]}}]
    assert rule["to"] == [{"operation": {"methods": ["POST"], "paths": ["/internal/v1/notify"]}}]
    assert by_name("Deployment", "beacon-checker")["spec"]["template"]["spec"]["serviceAccountName"] == "beacon-checker"


def test_route_serves_the_spa_and_sends_api_and_public_to_the_api():
    route = by_name("HTTPRoute", "beacon")
    assert route["spec"]["hostnames"] == [HOST]
    assert route["spec"]["parentRefs"] == [{"name": "main", "namespace": "istio-ingress", "sectionName": "https-beacon"}]
    api, spa = route["spec"]["rules"]
    assert {m["path"]["value"] for m in api["matches"]} == {"/api/", "/public/"}
    assert api["backendRefs"] == [{"name": "beacon-api", "port": 8080}]
    assert spa["matches"] == [{"path": {"type": "PathPrefix", "value": "/"}}]
    assert spa["backendRefs"] == [{"name": "beacon-web", "port": 8080}]
    cert = by_name("Certificate", "beacon-tls")
    assert cert["spec"]["dnsNames"] == [HOST] and cert["spec"]["secretName"] == "beacon-tls"


# --- database budget (beacon §V.26) --------------------------------------------------------

APP_ROLE_LIMIT = 20  # beacon_app's CONNECTION LIMIT (arigsela/beacon sql/roles.sql)


def test_v26_beacon_app_connections_fit_its_limit_even_mid_rollout():
    used = 0
    for name in ("beacon-api", "beacon-notifier"):
        dep = by_name("Deployment", name)
        pool = int(env(dep["spec"]["template"]["spec"]["containers"][0])["BEACON_DB_POOL_MAX"]["value"])
        used += pool * dep["spec"]["replicas"] * 2  # old and new pod during a rolling update
    used += 2 * 2  # checker and scheduler: one connection each, doubled the same way
    assert used <= APP_ROLE_LIMIT


# --- config and secrets ----------------------------------------------------------------------


def test_flags_are_all_present_and_all_off():
    cm = by_name("ConfigMap", "beacon-flags")
    flags = json.loads(cm["data"]["beacon-flags.json"])["flags"]
    assert set(flags) == set(FLAGS)
    for name, f in flags.items():
        assert f["defaultVariant"] in ("off",), f"{name} is not off: fault flags change by PR only"
    # Directory mount, not subPath, so a flag change reaches running pods.
    for _, d in app_docs():
        for v in (pod_spec(d) or {}).get("volumes", []):
            if v.get("configMap", {}).get("name") == "beacon-flags":
                for c in pod_spec(d)["containers"]:
                    for m in c.get("volumeMounts", []):
                        if m["name"] == v["name"]:
                            assert "subPath" not in m


def test_secret_stores_and_keys_match_the_provision_script():
    script = (REPO / "scripts/provision-beacon-vault.sh").read_text()
    stores = {d["metadata"]["name"]: d for _, d in app_docs() if d["kind"] == "SecretStore"}
    stores["postgresql/vault-beacon-db"] = one("base-apps/postgresql/beacon-db-secret-store.yaml", "SecretStore", "vault-beacon-db")
    for s in stores.values():
        auth = s["spec"]["provider"]["vault"]["auth"]["kubernetes"]
        assert f"auth/kubernetes/role/{auth['role']}" in script
        assert f"bound_service_account_names={auth['serviceAccountRef']['name']}" in script
    ess = [d for _, d in app_docs() if d["kind"] == "ExternalSecret"] + load("base-apps/postgresql/external-secrets-beacon-db.yaml")
    for es in ess:
        for item in es["spec"]["data"]:
            key, prop = item["remoteRef"]["key"], item["remoteRef"]["property"]
            assert key in ("beacon", "beacon-db")
            assert re.search(rf"\b{re.escape(prop)}\b", script), f"{es['metadata']['name']}: {key}/{prop}"
    url = by_name("ExternalSecret", "beacon-db-app")["spec"]["target"]["template"]["data"]["DATABASE_URL"]
    assert "@postgresql-cluster-rw.postgresql.svc.cluster.local:5432/" in url and "sslmode=require" in url


def test_application_excludes_the_catalog_and_creates_the_namespace_first():
    (app,) = load("base-apps/beacon.yaml")
    assert app["spec"]["source"]["path"] == "base-apps/beacon"
    assert app["spec"]["source"]["directory"]["exclude"] == "{catalog-info.yaml,mkdocs.yml}"
    assert "CreateNamespace=true" in app["spec"]["syncPolicy"]["syncOptions"]
    assert app["spec"]["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}


# --- citations (beacon §V.27) ----------------------------------------------------------------

BARE = re.compile(r"(?<!beacon )§[TVRIB]\.\d+")


@pytest.mark.parametrize("path", sorted(
    [p.relative_to(REPO).as_posix() for p in APP.glob("*.yaml")]
    + [p.relative_to(REPO).as_posix() for p in APP.glob("*.md")]
    + ["base-apps/beacon.yaml", INIT, "base-apps/postgresql/beacon-database.yaml",
       "base-apps/postgresql/beacon-db-secret-store.yaml", "base-apps/postgresql/external-secrets-beacon-db.yaml",
       "base-apps/postgresql/eso-beacon-db-serviceaccount.yaml"]))
def test_v27_beacon_files_cite_beacon_sections_only(path):
    text = Path(REPO / path).read_text()
    # A line break between "beacon" and "§" reads the same to a human; fold it first.
    folded = re.sub(r"beacon\s*\n\s*(#\s*)?§", "beacon §", text)
    assert not BARE.findall(folded), BARE.findall(folded)
