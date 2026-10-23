"""The Model Gateway dashboard (Agents folder, uid model-gateway): today's spend against each
group's daily budget, from the gateway's own metrics, on the gateway's day (00:00 UTC)."""
import importlib.util
import json
import re
import sys

import yaml

import model_budgets as mb
from conftest import REPO

DASHBOARDS = REPO / "base-apps/logging/grafana-dashboards-agents.yaml"
DEPLOY = REPO / "base-apps/logging/grafana-deployment.yaml"


def _gen():
    spec = importlib.util.spec_from_file_location("gen_agent_dashboards", REPO / "scripts/gen-agent-dashboards.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_agent_dashboards"] = mod
    spec.loader.exec_module(mod)
    return mod


def _dashboard():
    for doc in yaml.safe_load_all(DASHBOARDS.read_text()):
        if doc and doc["metadata"]["name"] == "grafana-dashboard-model-gateway":
            return json.loads(doc["data"]["model-gateway.json"])
    raise AssertionError("no grafana-dashboard-model-gateway ConfigMap")


def _targets(d):
    return [(p, t) for p in d["panels"] for t in p.get("targets", [])]


def test_it_is_in_the_agents_folder_and_mounted():
    gen = _gen()
    assert gen.DASHBOARDS["grafana-dashboard-model-gateway"] == ("model-gateway.json", "model-gateway")
    assert "grafana-dashboard-model-gateway" in DEPLOY.read_text()


def test_it_opens_on_the_gateways_day():
    d = _dashboard()
    assert d["uid"] == "model-gateway" and d["title"] == "Model Gateway & Budgets"
    assert d["timezone"] == "utc" and d["time"] == {"from": "now/d", "to": "now"}, "a budget day runs 00:00 to 00:00 UTC"
    assert "agents" in d["tags"]


def test_it_reads_the_gateways_own_metrics_never_with_increase_or_rate():
    d = _dashboard()
    prom = [(p, t) for p, t in _targets(d) if t["datasource"]["type"] == "prometheus"]
    assert prom
    for p, t in prom:
        expr = t["expr"]
        if "agentgateway_" in expr:
            assert 'namespace="model-gateway"' in expr, p["title"]
            assert not re.search(r"(increase|rate)\(agentgateway_", expr), p["title"]
        if t.get("range") and "agentgateway_" in expr and "up{" not in expr:
            assert "offset -$__interval" in expr, (p["title"], "range panels count forward")


def test_todays_share_of_each_budget_uses_the_table():
    d = _dashboard()
    share = next(p for p in d["panels"] if p["title"] == "Today's spend against the daily budget")
    expr = share["targets"][0]["expr"]
    assert mb.budget_vector_expr() in expr
    assert "agentgateway_gen_ai_client_cost_usd_total" in expr and "$__range" in expr
    steps = share["fieldConfig"]["defaults"]["thresholds"]["steps"]
    assert [s["value"] for s in steps] == [None, mb.load()["warn_at"], 1]
    assert share["fieldConfig"]["defaults"]["unit"] == "percentunit"


def test_the_groups_panel_lists_every_group_with_its_budget():
    d = _dashboard()
    note = next(p for p in d["panels"] if p["type"] == "text" and p["title"] == "Groups")["options"]["content"]
    for name, g in mb.load()["groups"].items():
        row = re.search(rf"^\| {re.escape(name)} \| (.*?) \| \$([\d.]+) \| (yes|not yet) \|$", note, re.M)
        assert row, name
        assert float(row[2]) == g["daily_usd"]
        assert (row[3] == "yes") == g["gateway"]


def test_the_budget_generator_keeps_the_dashboard_current():
    """A budget change regenerates the dashboard too (scripts/gen-model-budgets.py --write)."""
    spec = importlib.util.spec_from_file_location("gen_model_budgets", REPO / "scripts/gen-model-budgets.py")
    gmb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gmb)
    assert "base-apps/logging/grafana-dashboards-agents.yaml" in gmb.outputs()
