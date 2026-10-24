# tests/alert_demo/test_manifests.py
"""base-apps/alert-demo/: never restarts mid-run, sized for the demo, reachable only from hk-demo (§T.139)."""
from pathlib import Path
import hashlib
import json

import yaml

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "base-apps" / "alert-demo"


def _docs(name):
    return [d for d in yaml.safe_load_all((APP / name).read_text()) if d]


def _container():
    dep = _docs("deployment.yaml")[0]
    return dep, dep["spec"]["template"]["spec"]["containers"][0]


def test_no_liveness_probe_and_limits():
    dep, c = _container()
    assert "readinessProbe" in c
    assert "livenessProbe" not in c, "a restart mid-run reads as a leak to the implementer"
    assert c["resources"]["limits"]["memory"] == "256Mi"
    assert c["resources"]["requests"]["memory"] == "64Mi"
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["FILL_MIB"] == "236" and env["HOLD_SECONDS"] == "1800"
    assert dep["spec"]["replicas"] == 1
    assert dep["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/name"] == "alert-demo"


def test_checksum_matches_code():
    dep, _ = _container()
    # The dispatcher's formula (scripts/gen-dispatcher-configmap.py configmap_checksum): sha256 of
    # the ConfigMap's data as sorted JSON.
    data = {"alert_demo.py": (REPO / "scripts" / "alert_demo.py").read_text()}
    want = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    assert dep["spec"]["template"]["metadata"]["annotations"]["checksum/code"] == want


def test_only_hk_demo_reaches_it():
    netpol = _docs("network-policy.yaml")[0]
    rules = netpol["spec"]["ingress"]
    assert len(rules) == 1
    peer = rules[0]["from"]
    assert peer == [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "hk-demo"}},
                     "podSelector": {"matchLabels": {"app": "hk-demo"}}}]
    assert {p["port"] for p in rules[0]["ports"]} == {8080, 15008}
    authz = _docs("authorization-policy.yaml")[0]
    assert authz["spec"]["action"] == "ALLOW"
    assert authz["spec"]["rules"] == [{"from": [{"source": {"principals": ["cluster.local/ns/hk-demo/sa/hk-demo"]}}],
                                       "to": [{"operation": {"ports": ["8080"]}}]}]


def test_namespace_is_in_the_mesh():
    ns = _docs("namespace.yaml")[0]
    assert ns["metadata"]["labels"]["istio.io/dataplane-mode"] == "ambient"
