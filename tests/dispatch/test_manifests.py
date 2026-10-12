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
