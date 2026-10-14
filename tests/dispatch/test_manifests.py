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


def test_network_policy_admits_only_grafana_and_a_labelled_replay_pod():
    """Review finding I1 (2026-10-14): the webhook is a write-capable prompt once a row proposes;
    anything in the cluster could POST a forged alert. Grafana runs outside the mesh (logging is
    not ambient, SA default), so the gate is a NetworkPolicy on the pod, not an identity policy."""
    docs = [d for d in yaml.safe_load_all((APP / "network-policy.yaml").read_text()) if d]
    np = next(d for d in docs if d["kind"] == "NetworkPolicy")
    assert np["metadata"]["namespace"] == "dispatcher"
    assert np["spec"]["podSelector"] == {"matchLabels": {"app": "dispatcher"}}
    assert np["spec"]["policyTypes"] == ["Ingress"]
    rules = np["spec"]["ingress"]
    assert len(rules) == 1
    froms = rules[0]["from"]
    assert {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "logging"}},
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "grafana"}}} in froms
    assert {"podSelector": {"matchLabels": {"dispatcher/replay": "true"}}} in froms
    assert len(froms) == 2
    assert rules[0]["ports"] == [{"port": 8080, "protocol": "TCP"}]
