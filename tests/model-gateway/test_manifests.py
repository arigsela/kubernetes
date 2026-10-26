"""The model gateway's manifests (spec §3.1, §3.3; plan Task 7): names that must agree across
the app, kagent and postgresql, and the settings the local proof showed the design needs."""
import os
import shutil
import subprocess
import time

import pytest
import yaml

import model_budgets as mb
from conftest import REPO

APP = REPO / "base-apps/model-gateway"
IMAGE = ("852893458518.dkr.ecr.us-east-2.amazonaws.com/agentgateway:v1.6.0"
         "@sha256:083adf216093ed57813debcfdbb5f6aa9cb119741b373455233f6c0605faca2d")


def docs(path):
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def one(path, kind, name=None):
    found = [d for d in docs(path) if d["kind"] == kind and (name is None or d["metadata"]["name"] == name)]
    assert len(found) == 1, (path, kind, name)
    return found[0]


def deployment():
    return one(APP / "deployment.yaml", "Deployment")


def test_image_is_the_mcp_adapters_pin():
    (c,) = deployment()["spec"]["template"]["spec"]["containers"]
    assert c["image"] == IMAGE
    adapters = (REPO / "base-apps/kagent/brave-search-mcp.yaml").read_text()
    assert IMAGE in adapters, "one agentgateway image in the repo"


def test_one_active_replica_and_the_config_checksum():
    """Final review I1: Substrate's egress restarts twice a day (base-apps/substrate/cert-restart-cronjob.yaml),
    so ClientIP affinity cannot keep one of two replicas active, and sharing counts between replicas
    was unreliable in the local proof. One replica counts exactly; a rolling update (surge 1,
    unavailable 0) keeps one serving through a config change. No PDB: with one replica it would
    block every node drain."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_model_budgets", REPO / "scripts/gen-model-budgets.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    d = deployment()
    assert d["spec"]["replicas"] == 1
    assert d["spec"]["strategy"] == {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1}}
    assert not (APP / "pdb.yaml").exists()
    ann = d["spec"]["template"]["metadata"]["annotations"]
    assert ann["checksum/config"] == gen.configmap_checksum((APP / "configmap.yaml").read_text())
    assert ann["prometheus.io/port"] == "15020"


def test_a_rollout_drains_streams_instead_of_cutting_them():
    """Final review I2: agentgateway drains for TERMINATION_GRACE_PERIOD_SECONDS - 5 (default 5 s),
    and a model stream at maxTokens 16000 runs longer than that; a stream cut mid-way is not retried."""
    spec = deployment()["spec"]["template"]["spec"]
    grace = spec["terminationGracePeriodSeconds"]
    assert grace >= 120
    (c,) = spec["containers"]
    env = {e["name"]: e["value"] for e in c.get("env", [])}
    assert env["TERMINATION_GRACE_PERIOD_SECONDS"] == str(grace)


def test_the_pod_runs_as_the_proof_ran_it():
    spec = deployment()["spec"]["template"]["spec"]
    assert spec["securityContext"]["runAsUser"] == 65532 and spec["automountServiceAccountToken"] is False
    (c,) = spec["containers"]
    assert c["args"] == ["-f", "/config/config.yaml"]
    assert c["securityContext"]["readOnlyRootFilesystem"] is True
    assert c["envFrom"] == [{"secretRef": {"name": "model-gateway-env"}}]
    mounts = {m["name"]: m["mountPath"] for m in c["volumeMounts"]}
    assert mounts == {"config": "/config", "anthropic": "/var/run/model-gateway/anthropic", "tmp": "/tmp",
                      "ca-bundle": "/etc/ssl/certs"}
    vols = {v["name"]: v for v in spec["volumes"]}
    assert vols["config"]["configMap"]["name"] == "model-gateway-config"
    assert vols["anthropic"]["secret"] == {"secretName": "model-gateway-anthropic",
                                           "items": [{"key": "api-key", "path": "api-key"}]}
    assert c["readinessProbe"]["httpGet"] == {"path": "/healthz/ready", "port": 15021}


def test_the_service():
    svc = one(APP / "service.yaml", "Service")
    assert svc["metadata"]["name"] == "model-gateway" and svc["metadata"]["namespace"] == "model-gateway"
    assert "sessionAffinity" not in svc["spec"], "one replica: nothing to pin"
    assert [p["port"] for p in svc["spec"]["ports"]] == [8080]
    assert mb.GATEWAY_URL == "http://model-gateway.model-gateway.svc.cluster.local:8080"


def test_network_policy_admits_only_the_egress_and_prometheus():
    np = one(APP / "network-policy.yaml", "NetworkPolicy")
    assert np["spec"]["podSelector"] == {"matchLabels": {"app": "model-gateway"}}
    rules = {r["ports"][0]["port"]: r["from"] for r in np["spec"]["ingress"]}
    assert rules == {
        8080: [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "ate-system"}},
                "podSelector": {"matchLabels": {"app": "atenet-egress"}}}],
        15020: [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "logging"}},
                 "podSelector": {"matchLabels": {"app": "prometheus"}}}],
    }


def test_every_group_has_a_kagent_secret_and_a_gateway_env_key():
    groups = list(mb.load()["groups"])
    kagent = docs(REPO / "base-apps/kagent/model-gateway-keys.yaml")
    ess = {d["metadata"]["name"]: d for d in kagent if d["kind"] == "ExternalSecret"}
    assert set(ess) == {f"model-gateway-key-{g}" for g in groups}
    for g in groups:
        es = ess[f"model-gateway-key-{g}"]
        assert es["spec"]["secretStoreRef"]["name"] == "vault-model-gateway-keys"
        assert es["spec"]["data"] == [{"secretKey": "ANTHROPIC_API_KEY",
                                       "remoteRef": {"key": "model-gateway-keys", "property": g}}]
    env = one(APP / "env-external-secret.yaml", "ExternalSecret")
    keys = {d["secretKey"]: d["remoteRef"] for d in env["spec"]["data"]}
    assert keys == {**{mb.env_name(g): {"key": "model-gateway-keys", "property": g} for g in groups},
                    "PGPASSWORD": {"key": "model-gateway-db", "property": "password"}}


def test_the_application_skips_the_budget_table():
    app = one(REPO / "base-apps/model-gateway.yaml", "Application")
    assert app["spec"]["source"]["path"] == "base-apps/model-gateway"
    assert "budgets.yaml" in app["spec"]["source"]["directory"]["exclude"]
    ns = one(APP / "namespace.yaml", "Namespace")
    assert "istio.io/dataplane-mode" not in ns["metadata"].get("labels", {}), "outside the mesh, like its client"


def test_the_database_the_config_names_is_the_one_the_job_creates():
    cfg = yaml.safe_load(one(APP / "configmap.yaml", "ConfigMap")["data"]["config.yaml"])
    url = cfg["config"]["database"]["url"]
    assert url.startswith("postgres://model_gateway@postgresql.postgresql.svc.cluster.local:5432/model_gateway")
    job = (REPO / "base-apps/postgresql/init-model-gateway-db.yaml").read_text()
    assert "CREATE DATABASE model_gateway OWNER model_gateway" in job and "CREATE ROLE model_gateway LOGIN" in job
    es = one(REPO / "base-apps/postgresql/external-secrets-model-gateway-db.yaml", "ExternalSecret")
    assert es["spec"]["secretStoreRef"]["name"] == "vault-model-gateway-db"


def _docker():
    return shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def test_init_db_job_against_postgres():
    """The Job's own script, against Postgres 18 (the cluster's major), run twice and with a new
    password: idempotent, the login works, it owns the database, PUBLIC cannot connect."""
    if not _docker():
        if os.environ.get("CI"):
            pytest.fail("Docker is required in CI")
        pytest.skip("needs Docker")
    job = one(REPO / "base-apps/postgresql/init-model-gateway-db.yaml", "Job")
    (c,) = job["spec"]["template"]["spec"]["containers"]
    script = c["command"][2].replace("postgresql.postgresql.svc.cluster.local", "127.0.0.1")
    name = f"mg-initdb-{os.getpid()}"
    subprocess.run(["docker", "run", "-d", "--name", name, "-e", "POSTGRES_USER=n8n", "-e", "POSTGRES_PASSWORD=admin",
                    "-e", "POSTGRES_DB=n8n", c["image"]], check=True, capture_output=True)
    try:
        for _ in range(60):
            if subprocess.run(["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "n8n", "-d", "n8n"],
                              capture_output=True).returncode == 0:
                break
            time.sleep(1)
        time.sleep(2)
        # The image trusts loopback (initdb's defaults): log in over the container's own address,
        # where its password rule applies, so a wrong password is refused as in the cluster.
        ip = subprocess.run(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name],
                            capture_output=True, text=True, check=True).stdout.strip()

        def run(password):
            env = ["-e", "POSTGRES_USER=n8n", "-e", "POSTGRES_PASSWORD=admin", "-e", "POSTGRES_DB=n8n",
                   "-e", f"GW_PASSWORD={password}"]
            return subprocess.run(["docker", "exec", *env, name, "sh", "-c", script], capture_output=True, text=True)

        def as_gateway(password, sql):
            return subprocess.run(["docker", "exec", "-e", f"PGPASSWORD={password}", name, "psql", "-h", ip,
                                   "-U", "model_gateway", "-d", "model_gateway", "-tAc", sql], capture_output=True, text=True)

        for attempt in ("first", "again"):
            r = run("pw-one")
            assert r.returncode == 0, (attempt, r.stdout + r.stderr)
        out = as_gateway("pw-one", "create table t (x int); drop table t; select current_user").stdout
        assert out.strip().splitlines()[-1] == "model_gateway", "it can create tables in its own database"
        r = run("pw-two")
        assert r.returncode == 0, r.stderr
        assert as_gateway("pw-one", "select 1").returncode != 0, "the old password stops working"
        assert as_gateway("pw-two", "select 1").returncode == 0
        subprocess.run(["docker", "exec", name, "psql", "-h", "127.0.0.1", "-U", "n8n", "-d", "n8n", "-c",
                        "create role someone login password 'x'"], check=True, capture_output=True)
        other = subprocess.run(["docker", "exec", "-e", "PGPASSWORD=x", name, "psql", "-h", ip, "-U", "someone",
                                "-d", "model_gateway", "-c", "select 1"], capture_output=True, text=True)
        assert other.returncode != 0 and "permission denied" in other.stderr
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


ALPINE = "alpine:3.24@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a"


def test_the_gateway_can_verify_anthropics_certificate():
    """Live 2026-10-23: the image is FROM scratch, so agentgateway (rustls-native-certs) had no
    trust store and every call to api.anthropic.com failed 503 "invalid peer certificate:
    UnknownIssuer". An init container copies the CA bundle of the repo's pinned alpine into an
    emptyDir the gateway reads through SSL_CERT_FILE (local proof check 14)."""
    spec = deployment()["spec"]["template"]["spec"]
    (init,) = spec["initContainers"]
    assert init["image"] == ALPINE
    assert init["command"] == ["cp", "/etc/ssl/certs/ca-certificates.crt", "/ca/ca-certificates.crt"]
    assert {m["name"]: m["mountPath"] for m in init["volumeMounts"]} == {"ca-bundle": "/ca"}
    assert init["securityContext"]["readOnlyRootFilesystem"] is True
    (c,) = spec["containers"]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["SSL_CERT_FILE"] == "/etc/ssl/certs/ca-certificates.crt"
    mounts = {m["name"]: m for m in c["volumeMounts"]}
    assert mounts["ca-bundle"]["mountPath"] == "/etc/ssl/certs" and mounts["ca-bundle"]["readOnly"] is True
    vols = {v["name"]: v for v in spec["volumes"]}
    assert "emptyDir" in vols["ca-bundle"]
    assert ALPINE in (REPO / "base-apps/kagent/eval-canary.yaml").read_text() or \
        sum(ALPINE in f.read_text() for f in (REPO / "base-apps").rglob("*.yaml")) > 1, "an image the repo already pins"
