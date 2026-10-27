"""base-apps/taskboard/: the bug investigator's target app (§T.145). Two Deployments run one code
ConfigMap on the pinned python image, locked down like alert-demo; Prometheus scrapes the app, and
only the traffic generator and Prometheus reach it."""
from pathlib import Path
import re

import yaml

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "base-apps" / "taskboard"
IMAGE = re.compile(r"^python:3\.12-slim@sha256:[0-9a-f]{64}$")


def _docs(name):
    return [d for d in yaml.safe_load_all((APP / name).read_text()) if d]


def _deployments():
    return {d["metadata"]["name"]: d for d in _docs("deployment.yaml")}


def test_application_is_contract_shaped():
    app = yaml.safe_load((REPO / "base-apps" / "taskboard.yaml").read_text())
    assert app["kind"] == "Application" and app["metadata"]["name"] == "taskboard"
    assert app["spec"]["source"]["path"] == "base-apps/taskboard"
    assert app["spec"]["source"]["directory"]["exclude"] == "{catalog-info.yaml,mkdocs.yml}"
    assert app["spec"]["destination"]["namespace"] == "taskboard"
    assert app["spec"]["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}


def test_two_deployments_on_the_pinned_image_with_limits():
    deps = _deployments()
    assert set(deps) == {"taskboard", "taskboard-traffic"}
    for name, dep in deps.items():
        meta = dep["spec"]["template"]["metadata"]
        assert dep["metadata"]["labels"]["app.kubernetes.io/name"] == name
        assert meta["labels"]["app"] == name and meta["labels"]["app.kubernetes.io/name"] == name
        assert dep["spec"]["selector"]["matchLabels"] == {"app": name}
        spec = dep["spec"]["template"]["spec"]
        assert spec["automountServiceAccountToken"] is False
        assert spec["securityContext"]["runAsNonRoot"] is True
        assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
        (c,) = spec["containers"]
        assert IMAGE.match(c["image"]), c["image"]
        assert c["command"][:2] == ["python", "-u"]
        for kind in ("requests", "limits"):
            assert set(c["resources"][kind]) == {"cpu", "memory"}, (name, kind)
        sc = c["securityContext"]
        assert sc["readOnlyRootFilesystem"] is True
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["capabilities"]["drop"] == ["ALL"]
        mounts = {m["name"]: m for m in c["volumeMounts"]}
        assert mounts["code"]["mountPath"] == "/opt/taskboard" and mounts["code"]["readOnly"] is True
        volumes = {v["name"]: v for v in spec["volumes"]}
        assert volumes["code"]["configMap"]["name"] == "taskboard-code"
        assert "checksum/code" in meta["annotations"]


def test_same_image_as_alert_demo():
    """One digest for the stdlib apps, so Renovate bumps them together."""
    alert = yaml.safe_load((REPO / "base-apps" / "alert-demo" / "deployment.yaml").read_text())
    want = alert["spec"]["template"]["spec"]["containers"][0]["image"]
    for dep in _deployments().values():
        assert dep["spec"]["template"]["spec"]["containers"][0]["image"] == want


def test_app_runs_the_server_with_sqlite_on_an_emptydir():
    dep = _deployments()["taskboard"]
    assert dep["spec"]["replicas"] == 1
    assert dep["spec"]["strategy"] == {"type": "Recreate"}
    spec = dep["spec"]["template"]["spec"]
    (c,) = spec["containers"]
    assert c["command"] == ["python", "-u", "/opt/taskboard/server.py"]
    env = {e["name"]: e for e in c["env"]}
    assert env["PORT"]["value"] == "8080"
    assert env["DB_PATH"]["value"] == "/data/taskboard.db"
    assert env["PYTHONDONTWRITEBYTECODE"]["value"] == "1"
    assert env["TASKBOARD_COMMIT"]["valueFrom"]["fieldRef"]["fieldPath"] == \
        "metadata.annotations['taskboard.arigsela.com/source-commit']"
    mounts = {m["mountPath"]: m["name"] for m in c["volumeMounts"]}
    volumes = {v["name"]: v for v in spec["volumes"]}
    for path in ("/data", "/tmp"):
        assert "emptyDir" in volumes[mounts[path]], path
    assert c["readinessProbe"]["httpGet"]["path"] == "/healthz"
    assert "livenessProbe" not in c, "a restart wipes the demo data"


def test_app_is_scraped_and_names_its_commit():
    annotations = _deployments()["taskboard"]["spec"]["template"]["metadata"]["annotations"]
    assert annotations["prometheus.io/scrape"] == "true"
    assert annotations["prometheus.io/port"] == "8080"
    assert annotations["prometheus.io/path"] == "/metrics"
    assert re.match(r"^[0-9a-f]{40}$", annotations["taskboard.arigsela.com/source-commit"])


def test_traffic_runs_simulate_against_the_service():
    dep = _deployments()["taskboard-traffic"]
    (c,) = dep["spec"]["template"]["spec"]["containers"]
    assert c["command"] == ["python", "-u", "/opt/taskboard/simulate.py"]
    env = {e["name"]: e.get("value") for e in c["env"]}
    assert env["TASKBOARD_URL"] == "http://taskboard.taskboard.svc.cluster.local:8080"
    assert env["INTERVAL_SECONDS"] == "3"
    service = _docs("service.yaml")[0]
    assert service["spec"]["selector"] == {"app": "taskboard"}
    assert service["spec"]["ports"][0]["port"] == 8080


def test_only_traffic_and_prometheus_reach_the_app():
    policies = {p["metadata"]["name"]: p for p in _docs("network-policy.yaml")}
    app = policies["taskboard-ingress"]["spec"]
    assert app["podSelector"] == {"matchLabels": {"app": "taskboard"}}
    assert app["policyTypes"] == ["Ingress"]
    (rule,) = app["ingress"]
    assert rule["from"] == [
        {"podSelector": {"matchLabels": {"app": "taskboard-traffic"}}},
        {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "logging"}},
         "podSelector": {"matchLabels": {"app": "prometheus"}}},
    ]
    assert rule["ports"] == [{"port": 8080, "protocol": "TCP"}]
    traffic = policies["taskboard-traffic-ingress"]["spec"]
    assert traffic["podSelector"] == {"matchLabels": {"app": "taskboard-traffic"}}
    assert "ingress" not in traffic, "nothing reaches the traffic generator"


def test_prometheus_pods_carry_the_label_the_policy_admits():
    text = (REPO / "base-apps" / "logging" / "prometheus-statefulset.yaml").read_text()
    docs = [d for d in yaml.safe_load_all(text) if d and d["kind"] == "StatefulSet"]
    assert docs[0]["metadata"]["namespace"] == "logging"
    assert docs[0]["spec"]["template"]["metadata"]["labels"]["app"] == "prometheus"


def test_namespace_is_not_in_the_mesh():
    ns = _docs("namespace.yaml")[0]
    assert "istio.io/dataplane-mode" not in ns["metadata"].get("labels", {})


def test_service_accounts_mount_no_token():
    sas = {s["metadata"]["name"]: s for s in _docs("serviceaccount.yaml")}
    assert set(sas) == {"taskboard", "taskboard-traffic"}
    assert all(s["automountServiceAccountToken"] is False for s in sas.values())
    for name, dep in _deployments().items():
        assert dep["spec"]["template"]["spec"]["serviceAccountName"] == name
