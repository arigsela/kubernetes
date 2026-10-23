"""The agent budget alerts (spec §5): generated from budgets.yaml into grafana-alerting.yaml.

The ratio query is the alert: each group's estimated spend over the last 24 hours, divided by its
daily budget. It is run through promtool (Prometheus 3.0.1, the deployed version) on series that
are new inside the window, a group of three agents and an agent in no group (Review Focus 5)."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys

import pytest
import yaml

from conftest import REPO

GEN = REPO / "scripts" / "gen-model-budgets.py"
ALERTING = REPO / "base-apps/logging/grafana-alerting.yaml"
TABLE = REPO / "base-apps/dispatcher/dispatch-table.yaml"
PROM_UID = "PBFA97CFB590B2093"
TITLES = {"Agent near its daily budget": ("warning", 0.8), "Agent over its daily budget": ("critical", 0.999)}
GATEWAY_TITLES = {"Agent blocked by its daily budget", "Model gateway down"}


def _gen():
    spec = importlib.util.spec_from_file_location("gen_model_budgets", GEN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_model_budgets"] = mod
    spec.loader.exec_module(mod)
    return mod


def _group(name="agent-budgets"):
    cm = [d for d in yaml.safe_load_all(ALERTING.read_text()) if d and d.get("kind") == "ConfigMap"][0]
    groups = yaml.safe_load(cm["data"]["rules.yaml"])["groups"]
    return next(g for g in groups if g["name"] == name)


def test_generated_files_are_current():
    r = subprocess.run([sys.executable, str(GEN), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_rules_follow_the_house_rules():
    gen = _gen()
    g = _group()
    assert g["folder"] == "Agent Budgets" and g["interval"] == "5m" and g["orgId"] == 1
    rules = {r["title"]: r for r in g["rules"]}
    assert set(rules) == set(TITLES) | GATEWAY_TITLES
    for title, (severity, threshold) in TITLES.items():
        r = rules[title]
        assert r["labels"] == {"severity": severity, "pillar": "cost"}
        assert r["noDataState"] == "OK" and r["execErrState"] == "KeepLast" and r["for"] == "0m"
        assert r["condition"] == "threshold"
        data = {d["refId"]: d for d in r["data"]}
        assert set(data) == {"ratio", "spend", "threshold"}
        for ref in ("ratio", "spend"):
            assert data[ref]["datasourceUid"] == PROM_UID
            assert data[ref]["model"]["datasource"] == {"type": "prometheus", "uid": PROM_UID}
            assert data[ref]["model"]["instant"] is True
        assert data["ratio"]["model"]["expr"] == gen.ratio_expr()
        assert data["spend"]["model"]["expr"] == gen.spend_expr()
        cond = data["threshold"]["model"]
        assert cond["expression"] == "ratio"
        assert cond["conditions"][0]["evaluator"] == {"type": "gt", "params": [threshold]}
        assert "$labels.group" in r["annotations"]["summary"]
        assert "$values.spend" in r["annotations"]["summary"]
        assert "budgets.yaml" in r["annotations"]["runbook"]


def test_threshold_comes_from_the_table():
    import model_budgets
    near = next(r for r in _group()["rules"] if r["title"] == "Agent near its daily budget")
    cond = next(d for d in near["data"] if d["refId"] == "threshold")["model"]["conditions"][0]
    assert cond["evaluator"]["params"] == [model_budgets.load()["warn_at"]]


def _values(points, n=26 * 60):
    out = ["_"] * n
    for (a, b), v in points.items():
        for m in range(a, b + 1):
            out[m] = str(v)
    return " ".join(out)


def _promtool(tmp_path, cases):
    path = tmp_path / "cases.yaml"
    path.write_text(yaml.safe_dump(cases, sort_keys=False))
    if shutil.which("promtool"):
        cmd = ["promtool", "test", "rules", str(path)]
    elif shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
        ss = next(d for d in yaml.safe_load_all((REPO / "base-apps/logging/prometheus-statefulset.yaml").read_text())
                  if d and d["kind"] == "StatefulSet")
        image = ss["spec"]["template"]["spec"]["containers"][0]["image"]
        name = f"promtool-budgets-{os.getpid()}"
        subprocess.run(["docker", "create", "--name", name, "--entrypoint", "promtool", image,
                        "test", "rules", "/cases.yaml"], check=True, capture_output=True)
        subprocess.run(["docker", "cp", str(path), f"{name}:/cases.yaml"], check=True, capture_output=True)
        cmd = ["docker", "start", "-a", name]
    else:
        if os.environ.get("CI"):
            pytest.fail("promtool or Docker is required in CI")
        pytest.skip("needs promtool or Docker")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    finally:
        if cmd[0] == "docker":
            subprocess.run(["docker", "rm", "-f", cmd[-1]], capture_output=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_ratio_through_promtool(tmp_path):
    gen = _gen()
    m = "agent_cost_estimate_usd_total"
    end = 26 * 60 - 1          # minute 1559; the 24h window is (119m, 1559m]
    series = [
        # new inside the window: its whole value counts (window_total, not increase())
        {"series": f'{m}{{service_name="homelab-knowledge", gen_ai_request_model="claude-sonnet-5-5"}}',
         "values": _values({(600, end): 2.5})},
        # the build group: three agents summed; k8s-agent was at 1.0 before the window
        {"series": f'{m}{{service_name="k8s-agent", gen_ai_request_model="claude-sonnet-5-5"}}',
         "values": _values({(0, 899): 1.0, (900, end): 3.0})},
        {"series": f'{m}{{service_name="istio-agent", gen_ai_request_model="claude-sonnet-5-5"}}',
         "values": _values({(1000, end): 2.5})},
        # an agent in no group is dropped, whatever it spends
        {"series": f'{m}{{service_name="mystery", gen_ai_request_model="claude-sonnet-5-5"}}',
         "values": _values({(700, end): 9.0})},
    ]
    budgets = {g["group"]: g for g in gen.budget_labels()}
    hk = {k: v for k, v in budgets["hk"].items()}
    build = {k: v for k, v in budgets["build"].items()}

    def labels(d):
        return "{" + ", ".join(f'{k}="{v}"' for k, v in sorted(d.items())) + "}"

    tests = [
        {"expr": gen.ratio_expr(), "eval_time": f"{end}m",
         "exp_samples": [{"labels": labels(build), "value": 0.9},
                         {"labels": labels(hk), "value": 2.5 / 3.0}]},
        {"expr": gen.spend_expr(), "eval_time": f"{end}m",
         "exp_samples": [{"labels": labels(build), "value": 4.5},
                         {"labels": labels(hk), "value": 2.5}]},
    ]
    _promtool(tmp_path, {"rule_files": [], "evaluation_interval": "1m",
                         "tests": [{"interval": "1m", "input_series": series, "promql_expr_test": tests}]})


def test_budget_labels_carry_the_table():
    import model_budgets
    gen = _gen()
    table = model_budgets.load()["groups"]
    got = {b["group"]: b for b in gen.budget_labels()}
    assert set(got) == set(table)
    assert got["build"]["agents"] == "build-orchestrator, k8s-agent, istio-agent"
    assert got["build"]["budget_usd"] == "5.00" and got["build"]["enforced"] == "false"


def test_every_budget_rule_has_a_dispatch_row_with_route_none():
    cm = yaml.safe_load(TABLE.read_text())
    rows = {r["match"].get("alertname"): r for r in json.loads(cm["data"]["dispatch.json"])["rules"]}
    for title in [*TITLES, "Agent blocked by its daily budget"]:
        assert rows[title]["route"] == "none", title
        assert "spend" in rows[title]["why"], title
    assert rows["Model gateway down"]["route"] == "none"
    assert "model call" in rows["Model gateway down"]["why"]


def test_the_gateway_rules():
    gen = _gen()
    rules = {r["title"]: r for r in _group()["rules"]}
    blocked = rules["Agent blocked by its daily budget"]
    data = {d["refId"]: d for d in blocked["data"]}
    assert set(data) == {"refused", "threshold"} and blocked["condition"] == "threshold"
    expr = data["refused"]["model"]["expr"]
    assert expr == gen.refused_expr()
    for part in ('namespace="model-gateway"', 'listener="llm"', 'status="429"', 'reason="RateLimit"'):
        assert part in expr, part
    assert "increase(" not in expr, "a first refusal is a new series: window_total, never increase() (§V.106)"
    cond = data["threshold"]["model"]
    assert cond["expression"] == "refused" and cond["conditions"][0]["evaluator"] == {"type": "gt", "params": [0]}
    assert blocked["labels"] == {"severity": "critical", "pillar": "cost"} and blocked["for"] == "0m"
    assert "$labels.group" in blocked["annotations"]["summary"] and "$labels.agents" in blocked["annotations"]["summary"]

    down = rules["Model gateway down"]
    data = {d["refId"]: d for d in down["data"]}
    assert data["up"]["model"]["expr"] == gen.gateway_up_expr() == 'sum(up{namespace="model-gateway"}) or vector(0)'
    assert data["threshold"]["model"]["conditions"][0]["evaluator"] == {"type": "lt", "params": [1]}
    assert down["for"] == "5m" and down["labels"] == {"severity": "critical", "pillar": "cost"}
    assert down["noDataState"] == "OK", "or vector(0) makes no-data a 0, which fires"


def test_refused_through_promtool(tmp_path):
    """The first refusal of the day is a new series with value 1: it must count (window_total)."""
    gen = _gen()
    m = "agentgateway_requests_total"
    base = 'namespace="model-gateway", pod="model-gateway-abc", listener="llm"'
    series = [
        # dcc's first refusal at minute 50: a series that did not exist before
        {"series": f'{m}{{{base}, group="dcc", status="429", reason="RateLimit"}}', "values": _values({(50, 59): 1}, 60)},
        # hk's answered calls and an upstream (Anthropic) 429: never a budget refusal
        {"series": f'{m}{{{base}, group="hk", status="200", reason="Upstream"}}', "values": _values({(0, 59): 7}, 60)},
        {"series": f'{m}{{{base}, group="hk", status="429", reason="Upstream"}}', "values": _values({(55, 59): 1}, 60)},
    ]
    budgets = {g["group"]: g for g in gen.budget_labels()}

    def labels(d):
        return "{" + ", ".join(f'{k}="{v}"' for k, v in sorted(d.items())) + "}"

    tests = [{"expr": gen.refused_expr(), "eval_time": "59m",
              "exp_samples": [{"labels": labels(budgets["dcc"]), "value": 1}]}]
    _promtool(tmp_path, {"rule_files": [], "evaluation_interval": "1m",
                         "tests": [{"interval": "1m", "input_series": series, "promql_expr_test": tests}]})
