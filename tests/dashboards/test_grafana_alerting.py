"""Guards on Grafana alert provisioning (base-apps/logging/grafana-alerting.yaml)."""
import hashlib
import json
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
ALERTING = REPO / "base-apps" / "logging" / "grafana-alerting.yaml"
DEPLOY = REPO / "base-apps" / "logging" / "grafana-deployment.yaml"
PROM_UID = "PBFA97CFB590B2093"


def _alerting_data():
    return [d for d in yaml.safe_load_all(ALERTING.read_text()) if d and d.get("kind") == "ConfigMap"][0]["data"]


def _deploy_docs():
    return [d for d in yaml.safe_load_all(DEPLOY.read_text()) if d]


def test_alerting_checksum_matches_configmap():
    """Grafana loads alert rules only at startup. The checksum annotation is what restarts
    it when rules change; a stale checksum means a merged rule silently never loads."""
    want = hashlib.sha256(json.dumps(_alerting_data(), sort_keys=True).encode()).hexdigest()
    deploy = [d for d in _deploy_docs() if d["kind"] == "Deployment"][0]
    have = deploy["spec"]["template"]["metadata"]["annotations"].get("checksum/alerting")
    assert have == want, f"update checksum/alerting in grafana-deployment.yaml to {want}"


def test_grafana_uses_recreate():
    """SQLite on an RWO volume: a rolling update would run two writers on one file."""
    deploy = [d for d in _deploy_docs() if d["kind"] == "Deployment"][0]
    assert deploy["spec"]["strategy"]["type"] == "Recreate"


def test_prometheus_alert_rules_use_the_pinned_uid():
    ds = yaml.safe_load([d for d in _deploy_docs() if d["kind"] == "ConfigMap"
                         and d["metadata"]["name"] == "grafana-datasources"][0]["data"]["datasources.yaml"])
    prom = [d for d in ds["datasources"] if d["type"] == "prometheus"][0]
    assert prom["uid"] == PROM_UID, "the Prometheus datasource uid is load-bearing; do not change it"
    rules = yaml.safe_load(_alerting_data()["rules.yaml"])
    for group in rules["groups"]:
        for rule in group["rules"]:
            for q in rule["data"]:
                if q["datasourceUid"] not in ("__expr__", "loki"):
                    assert q["datasourceUid"] == PROM_UID, f"{rule['uid']} references an unknown datasource"


def _falco_expr():
    rules = yaml.safe_load(_alerting_data()["rules.yaml"])
    rule = [r for g in rules["groups"] for r in g["rules"] if r["uid"] == "falco-sensitive-detection"][0]
    return [q for q in rule["data"] if q["refId"] == "query"][0]["model"]["expr"]


def test_falco_alert_ignores_argo_workflow_plumbing():
    """Every Argo step pod's executor sidecar reports its result through the API, and
    so does the workflow controller. The weekly image scan runs ~80 pods, so this alert
    fired on every run (seen 2026-10-05). Each exclusion is pinned to the exact binary."""
    expr = _falco_expr()
    for marker in ('output_fields_proc_exepath!="/usr/bin/argoexec"',
                   'output_fields_proc_exepath!="/usr/bin/workflow-controller"',
                   'output_fields_k8s_pod_name!~"image-scan-[a-z0-9]+-discover-images-[0-9]+"'):
        assert marker in expr, f"missing exclusion: {marker}"


def test_falco_alert_never_exempts_the_whole_argo_workflows_namespace():
    """The Argo server has no login, so anyone who reaches it can run a workflow there as
    a ServiceAccount with cluster-wide pod read. An unexpected API call from that
    namespace is exactly what this alert is for."""
    ns_list = re.search(r'output_fields_k8s_ns_name!~"([^"]+)"', _falco_expr()).group(1)
    assert "argo-workflows" not in ns_list.split("|")


def test_every_rule_has_a_unique_uid_and_a_runbook():
    rules = yaml.safe_load(_alerting_data()["rules.yaml"])
    uids = [r["uid"] for g in rules["groups"] for r in g["rules"]]
    assert len(uids) == len(set(uids))
    for g in rules["groups"]:
        for r in g["rules"]:
            assert r["annotations"].get("runbook"), f"{r['uid']} has no runbook"
