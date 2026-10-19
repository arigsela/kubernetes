"""Guards on Grafana datasource provisioning (base-apps/logging/grafana-deployment.yaml)."""
import hashlib
import json
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "base-apps" / "logging" / "grafana-deployment.yaml"


def _docs():
    return [d for d in yaml.safe_load_all(DEPLOY.read_text()) if d]


def _datasources_cm():
    return [d for d in _docs() if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "grafana-datasources"][0]


def _datasources():
    return {d["uid"]: d for d in yaml.safe_load(_datasources_cm()["data"]["datasources.yaml"])["datasources"]}


def test_datasources_checksum_matches_configmap():
    """Grafana reads datasource provisioning only at startup, like alert rules: a stale
    checksum means a merged datasource change never loads."""
    want = hashlib.sha256(json.dumps(_datasources_cm()["data"], sort_keys=True).encode()).hexdigest()
    deploy = [d for d in _docs() if d["kind"] == "Deployment"][0]
    have = deploy["spec"]["template"]["metadata"]["annotations"].get("checksum/datasources")
    assert have == want, f"update checksum/datasources in grafana-deployment.yaml to {want}"


def test_tempo_and_loki_link_both_ways():
    """beacon §T.18: trace to logs through Loki, logs to trace through Loki's derived field."""
    ds = _datasources()
    tempo = ds["tempo"]
    assert tempo["type"] == "tempo"
    assert tempo["url"] == "http://tempo.tracing.svc.cluster.local:3200"
    assert tempo["jsonData"]["tracesToLogsV2"]["datasourceUid"] == "loki"
    [field] = ds["loki"]["jsonData"]["derivedFields"]
    assert field["datasourceUid"] == "tempo"
    # `$$` is the provisioning escape for a literal `$` (Grafana expands $VAR in these files).
    assert field["url"] == "$${__value.raw}"
    # The exact line beacon's services write (arigsela/beacon beacon_core.logs.FORMAT).
    line = "2026-10-19 13:00:00,000 beacon.checker trace_id=c28fd7d9c821ab7f5695e965b3c871c7 check ok"
    assert re.search(field["matcherRegex"], line).group(1) == "c28fd7d9c821ab7f5695e965b3c871c7"
    assert not re.search(field["matcherRegex"], "beacon.notifier trace_id=- outside")


def test_existing_uids_are_unchanged():
    """Alert rules and dashboards reference these by uid."""
    assert {"loki", "PBFA97CFB590B2093"} <= set(_datasources())
