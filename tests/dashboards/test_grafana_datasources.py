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


TRACE = "3ace5cd912380b069823402928b147e5"
# One line of each shape Loki holds, copied from the cluster (2026-10-21).
LINES = {
    # beacon's services (arigsela/beacon beacon_core.logs.FORMAT)
    "beacon": f"2026-10-19 13:00:00,000 beacon.checker trace_id={TRACE} check ok",
    # kagent's controller, JSON
    "kagent": '{"msg":"rpc completed","grpc_method":"/kagent.runtime.v1.TaskStoreService/GetTask",'
              f'"duration_ms":28,"trace_id":"{TRACE}"}}',
    # agentgateway: the MCP adapters and Substrate's router and egress
    "agentgateway": f"2026-10-21T20:29:23.542055Z\tinfo\trequest grpc.status=0 trace.id={TRACE} span.id=71abe88b5a4ea0ca",
}


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
    for name, line in LINES.items():
        assert re.search(field["matcherRegex"], line).group(1) == TRACE, name
    assert not re.search(field["matcherRegex"], "beacon.notifier trace_id=- outside")


def test_trace_to_logs_searches_every_namespace_by_trace_id():
    """One agent run logs from kagent's controller and from Substrate (ate-system), and an agent's
    service name is no pod's `app` label, so the span's service cannot narrow the search."""
    t2l = _datasources()["tempo"]["jsonData"]["tracesToLogsV2"]
    assert t2l["customQuery"] is True
    assert t2l["query"] == '{namespace=~".+"} |= "$${__trace.traceId}"'


def test_service_map_reads_the_collectors_graph_from_prometheus():
    """traces_service_graph_* come from the collector's service_graph connector (§V.105)."""
    ds = _datasources()
    assert ds["tempo"]["jsonData"]["serviceMap"]["datasourceUid"] == "PBFA97CFB590B2093"
    assert ds["PBFA97CFB590B2093"]["type"] == "prometheus"


def test_existing_uids_are_unchanged():
    """Alert rules and dashboards reference these by uid."""
    assert {"loki", "PBFA97CFB590B2093"} <= set(_datasources())
