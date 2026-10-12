# tests/dispatch/test_dispatch.py
"""scripts/dispatch.py: table → decision → investigation → finding, with a fake agent."""
from pathlib import Path
import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("dispatch", REPO / "scripts" / "dispatch.py")
dp = importlib.util.module_from_spec(_spec)
sys.modules["dispatch"] = dp   # dataclasses resolve the module by name at class creation
_spec.loader.exec_module(dp)
a2a = dp.a2a

TABLE = {
    "version": 1,
    "limits": {"investigations_per_hour": 2, "repeat_after_hours": 6},
    "rules": [
        {"id": "cpu", "source": "grafana", "match": {"alertname": "Pod CPU starved"},
         "route": "agent:k8s-reader", "prompt": "Pod {labels[namespace]}/{labels[pod]}: {annotations[summary]}",
         "why": "test"},
        {"id": "quiet", "source": "grafana", "match": {"alertname": "Quiet"}, "route": "none", "why": "test"},
    ],
}


def alert(name="Pod CPU starved", status="firing", fingerprint="fp1", **labels):
    labels.setdefault("namespace", "n8n")
    labels.setdefault("pod", "n8n-0")
    return {"status": status, "fingerprint": fingerprint, "startsAt": "2026-10-12T10:00:00Z",
            "labels": {"alertname": name, **labels}, "annotations": {"summary": "waiting 40% for CPU"}}


# ---------------------------------------------------------------- table

def test_load_table_accepts_the_shape():
    assert dp.load_table(json.dumps(TABLE))["limits"]["investigations_per_hour"] == 2


@pytest.mark.parametrize("mutate, message", [
    (lambda t: t.update(version=2), "version"),
    (lambda t: t["limits"].pop("repeat_after_hours"), "repeat_after_hours"),
    (lambda t: t["rules"][0].pop("why"), "why"),
    (lambda t: t["rules"][0].update(route="human:slack"), "route"),
    (lambda t: t["rules"][0].pop("prompt"), "prompt"),
    (lambda t: t["rules"].append(dict(t["rules"][1])), "duplicate"),
])
def test_load_table_rejects_bad_tables(mutate, message):
    table = json.loads(json.dumps(TABLE))
    mutate(table)
    with pytest.raises(dp.TableError, match=message):
        dp.load_table(json.dumps(table))


def test_prompt_list_is_joined_with_spaces():
    table = json.loads(json.dumps(TABLE))
    table["rules"][0]["prompt"] = ["Pod {labels[pod]}:", " look at it. "]
    assert dp.load_table(json.dumps(table))["rules"][0]["prompt"] == "Pod {labels[pod]}: look at it."
    table["rules"][0]["prompt"] = ["ok", 3]
    with pytest.raises(dp.TableError, match="only strings"):
        dp.load_table(json.dumps(table))


def test_match_rule_first_match_wins_and_all_keys_must_equal():
    table = dp.load_table(json.dumps(TABLE))
    assert dp.match_rule(table, "grafana", {"alertname": "Pod CPU starved", "pod": "x"})["id"] == "cpu"
    assert dp.match_rule(table, "grafana", {"alertname": "Other"}) is None
    assert dp.match_rule(table, "github-check", {"alertname": "Pod CPU starved"}) is None


def test_render_prompt_fills_labels_and_marks_missing_keys():
    rule = TABLE["rules"][0]
    assert dp.render_prompt(rule, alert()) == "Pod n8n/n8n-0: waiting 40% for CPU"
    assert dp.render_prompt(rule, {"labels": {"alertname": "x"}, "annotations": {}}) == "Pod ?/?: ?"


# ---------------------------------------------------------------- budget

def test_budget_suppresses_repeat_fingerprint():
    now = [1000.0]
    b = dp.Budget(per_hour=5, repeat_after_hours=6, clock=lambda: now[0])
    assert b.admit("fp1") is None
    assert b.admit("fp1") == "repeat"
    now[0] += 6 * 3600
    assert b.admit("fp1") is None


def test_budget_caps_investigations_per_hour():
    now = [1000.0]
    b = dp.Budget(per_hour=2, repeat_after_hours=6, clock=lambda: now[0])
    assert b.admit("a") is None and b.admit("b") is None
    assert b.admit("c") == "budget"
    now[0] += 3601
    assert b.admit("c") is None
