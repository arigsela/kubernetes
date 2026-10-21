"""The kagent chart's rendered controller.yaml carries our settings under alpha14's `config`
schema (kagent#3153, §R.46): a value left under the alpha11 key is silently ignored, which is
how a 30-day idle TTL turns into the 7-day default or telemetry stops."""
from pathlib import Path
import json, os, shutil, subprocess, tempfile
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _application():
    return yaml.safe_load((ROOT / "base-apps/kagent.yaml").read_text())


@pytest.fixture(scope="module")
def controller_config() -> dict:
    if shutil.which("helm") is None:
        pytest.skip("helm not installed") if not os.environ.get("CI") else pytest.fail("helm required")
    src = _application()["spec"]["source"]
    values = Path(tempfile.mkdtemp()) / "kagent-values.json"; values.write_text(json.dumps(src["helm"]["valuesObject"]))
    r = subprocess.run(["helm", "template", "kagent", f"oci://{src['repoURL']}/{src['chart']}", "--version", src["targetRevision"],
                        "--namespace", "kagent", "--values", str(values)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    docs = [d for d in yaml.safe_load_all(r.stdout) if isinstance(d, dict)]
    cm = [d for d in docs if d["kind"] == "ConfigMap" and "controller.yaml" in (d.get("data") or {})]
    assert len(cm) == 1
    deploy = [d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "kagent-controller"][0]
    return {"config": yaml.safe_load(cm[0]["data"]["controller.yaml"]), "deployment": deploy, "docs": docs}


def test_sessions_idle_ttl_is_thirty_days_in_seconds(controller_config):
    assert controller_config["config"]["sessions"]["idleTtl"] == "2592000s"


def test_gateway_public_url_and_push_issuer(controller_config):
    c = controller_config["config"]
    assert c["gateway"]["publicUrl"] == "https://kagent-mcp.arigsela.com"
    assert c["push"]["issuer"] == "https://kagent-mcp.arigsela.com"


def test_two_database_logins_and_roles(controller_config):
    db = controller_config["config"]["database"]
    assert db["owner"]["role"] == "kagent_owner" and db["readWrite"]["role"] == "kagent_readwrite"
    assert db["schema"] == "kagent" and db["vectorSchema"] == "extensions" and db["vectorEnabled"] is True
    assert db["skipMigrations"] is False
    # alpha14 projects both connection strings into one volume (`controller-secrets`,
    # volumes[].projected.sources[].secret), not one plain Secret volume each.
    spec = controller_config["deployment"]["spec"]["template"]["spec"]
    projected = {(s["secret"]["name"], s["secret"]["items"][0]["path"])
                 for v in spec["volumes"] if "projected" in v
                 for s in v["projected"]["sources"] if "secret" in s}
    assert ("kagent-postgres-owner", "database-owner-url") in projected
    assert ("kagent-postgres-readwrite", "database-readwrite-url") in projected


def test_substrate_client_certificates_are_still_projected(controller_config):
    """alpha11's controller.substrate.enabled projected these; alpha14 gates them behind
    controller.substrateClientCertificate.enabled (default false). Without them the controller
    cannot talk to ate-api-server and no agent runs."""
    spec = controller_config["deployment"]["spec"]["template"]["spec"]
    names = {v["name"] for v in spec["volumes"]}
    assert {"substrate-servicedns", "substrate-podidentity"} <= names, sorted(names)
    mounts = {m["mountPath"] for m in spec["containers"][0]["volumeMounts"]}
    assert {"/run/substrate-servicedns", "/run/substrate-podidentity"} <= mounts


def test_auth_and_substrate(controller_config):
    c = controller_config["config"]
    assert c["server"]["auth"]["mode"] == "insecure"
    # The chart adds caFile/clientCertFile once substrateClientCertificate is enabled (the mounts
    # checked above), so compare the two endpoints and the two cert paths, not the whole block.
    sub = c["substrate"]
    assert sub["apiEndpoint"] == "dns:///api.ate-system.svc:443"
    assert sub["routerUrl"] == "http://atenet-router.ate-system.svc:80"
    assert sub["caFile"] == "/run/substrate-servicedns/trust-bundle.pem"
    assert sub["clientCertFile"] == "/run/substrate-podidentity/credential-bundle.pem"


def test_v104_controller_and_sessions_send_traces_to_the_collector(controller_config):
    """§V.104: kagent's exporters gzip; Coroot's receiver refuses gzip (§B.37), the collector takes it
    and forwards to Tempo and Coroot. No OTLP logs: stdout already reaches Loki (Alloy, ateom)."""
    c = controller_config["config"]
    collector = {"endpoint": "http://otel-collector.tracing.svc.cluster.local:4317", "protocol": "grpc"}
    for scope, telemetry in (("controller", c["telemetry"]), ("runtimeDefaults", c["runtimeDefaults"]["telemetry"])):
        assert telemetry == {"traces": collector}, scope


def test_no_alpha11_keys_linger(controller_config):
    values = _application()["spec"]["source"]["helm"]["valuesObject"]
    assert "otel" not in values and "kmcp" not in values
    for gone in ("auth", "agentImage", "a2aGatewayUrl", "sessionIdleTTL", "substrate"):
        assert gone not in values["controller"], gone


def test_every_image_is_pinned_by_digest_and_no_kmcp_is_rendered(controller_config):
    images = [c["image"] for d in controller_config["docs"] if d["kind"] == "Deployment"
              for c in d["spec"]["template"]["spec"]["containers"]]
    assert images and all("@sha256:" in i for i in images), images
    assert not [d for d in controller_config["docs"] if "kmcp" in d["metadata"]["name"]]
