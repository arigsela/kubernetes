"""The Deployment restarts when the table or the code ConfigMap changes (Argo CD syncs
ConfigMaps, nothing else restarts a pod that read them at start): checksum annotations,
the same pattern as Grafana's checksum/alerting."""
from pathlib import Path
import hashlib
import json

import yaml

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "base-apps" / "dispatcher"


def _configmap_data(name: str) -> dict:
    doc = [d for d in yaml.safe_load_all((APP / name).read_text()) if d and d.get("kind") == "ConfigMap"][0]
    return doc["data"]


def checksum(name: str) -> str:
    return hashlib.sha256(json.dumps(_configmap_data(name), sort_keys=True).encode()).hexdigest()


def test_deployment_checksums_match_the_configmaps():
    deploy = [d for d in yaml.safe_load_all((APP / "deployment.yaml").read_text()) if d and d.get("kind") == "Deployment"][0]
    annotations = deploy["spec"]["template"]["metadata"].get("annotations") or {}
    for key, file in (("checksum/table", "dispatch-table.yaml"), ("checksum/code", "code-configmap.yaml")):
        want = checksum(file)
        assert annotations.get(key) == want, f"set {key}: {want} in base-apps/dispatcher/deployment.yaml"


def _one(file: str, kind: str) -> dict:
    docs = [d for d in yaml.safe_load_all((APP / file).read_text()) if d and d.get("kind") == kind]
    assert len(docs) == 1, (file, kind)
    return docs[0]


def test_deployment_runs_as_the_dispatcher_service_account_with_a_projected_token():
    pod = _one("deployment.yaml", "Deployment")["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "dispatcher"
    vol = next(v for v in pod["volumes"] if v["name"] == "kagent-token")
    src = vol["projected"]["sources"][0]["serviceAccountToken"]
    assert src["audience"] == "kagent" and src["expirationSeconds"] <= 3600 and src["path"] == "token"
    mount = next(m for m in pod["containers"][0]["volumeMounts"] if m["name"] == "kagent-token")
    assert mount["mountPath"] == "/var/run/kagent" and mount["readOnly"] is True
    env = {e["name"]: e.get("value") for e in pod["containers"][0]["env"]}
    assert "DISPATCH_USER" not in env
    sa = _one("serviceaccount.yaml", "ServiceAccount")
    assert sa["metadata"]["name"] == "dispatcher" and sa["metadata"]["namespace"] == "dispatcher"
