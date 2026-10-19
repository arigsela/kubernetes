# tests/beacon/test_alerts.py
"""beacon §V.9: the alert that notices a notifier the platform failed to instrument."""
import json

import yaml

from conftest import one

ALERTING = "base-apps/logging/grafana-alerting.yaml"
COLLECTOR = "base-apps/tracing/collector.yaml"
TITLE = "Beacon notifier not traced"


def rules():
    cm = one(ALERTING, "ConfigMap", "grafana-alerting")
    groups = yaml.safe_load(cm["data"]["rules.yaml"])["groups"]
    return {r["uid"]: r for g in groups for r in g["rules"]}


def test_v9_alert_reads_the_collectors_cumulative_span_count():
    rule = rules()["beacon-notifier-not-traced"]
    assert rule["title"] == TITLE
    query = next(d for d in rule["data"] if d["refId"] == "query")["model"]["expr"]
    # The metric the collector actually exports: its count connector's name, Prometheus-style.
    cfg = yaml.safe_load(one(COLLECTOR, "ConfigMap", "otel-collector-config")["data"]["collector.yaml"])
    [name] = cfg["connectors"]["count"]["spans"]
    metric = name.replace(".", "_") + "_total"
    assert f'{metric}{{service_name="beacon-notifier"}}' in query
    assert "increase(" in query and "[30m]" in query  # a rate over the cumulative counter (beacon §B.14)
    assert query.rstrip().endswith("or vector(0)")  # a missing series alerts too
    assert rule["noDataState"] == "Alerting"
    threshold = next(d for d in rule["data"] if d["refId"] == "threshold")["model"]["conditions"][0]["evaluator"]
    assert threshold == {"type": "lt", "params": [1]}


def test_v22_alert_has_a_dispatch_row():
    cm = one("base-apps/dispatcher/dispatch-table.yaml", "ConfigMap", "dispatcher-table")
    table = json.loads(cm["data"]["dispatch.json"])
    [row] = [r for r in table["rules"] if r["match"] == {"alertname": TITLE}]
    assert row["source"] == "grafana" and row["route"] == "none"
