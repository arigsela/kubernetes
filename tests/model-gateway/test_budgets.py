"""base-apps/model-gateway/budgets.yaml: the one table the budgets live in (spec §2, §V.108).

Every kagent agent is in exactly one group, a subagent shares its parent's group (kagent refuses
two Secrets for one host in one actor), and every model a group uses has a price, because the
gateway never blocks an unpriced model."""
import textwrap

import pytest
import yaml

import model_budgets as mb
from conftest import REPO

TEMPLATES = REPO / "base-apps/kagent/agent-templates"


def _templates():
    out = {}
    for f in sorted(TEMPLATES.glob("*.yaml")):
        for doc in yaml.safe_load_all(f.read_text()):
            if doc and doc.get("kind") == "AgentTemplate":
                out[doc["metadata"]["name"]] = doc
    return out


def test_every_agent_template_is_in_exactly_one_group():
    table = mb.load()
    listed = [a for g in table["groups"].values() for a in g["agents"]]
    assert len(listed) == len(set(listed)), "an agent is in two groups"
    assert set(listed) == set(_templates())


def test_subagents_share_their_parents_group():
    of = mb.group_of()
    for name, doc in _templates().items():
        for tool in doc["spec"].get("tools") or []:
            child = ((tool.get("subAgent") or {}).get("templateRef") or {}).get("name")
            if child:
                assert of[child] == of[name], f"{child} runs inside {name}: same key, same group"


def test_every_model_is_priced_by_the_collector():
    prices = mb.collector_prices()
    for name, g in mb.load()["groups"].items():
        assert set(g["models"]) <= set(prices), name


def test_collector_prices_parse():
    prices = mb.collector_prices()
    assert prices["claude-haiku-5-5"] == [
        {"over": 0, "input": 0.1, "cache_write": 0.125, "cache_read": 0.01, "output": 0.5},
        {"over": 100000, "input": 0.5, "cache_write": 0.625, "cache_read": 0.05, "output": 2.5},
    ]
    assert prices["claude-sonnet-5-5"] == [
        {"over": 0, "input": 2.0, "cache_write": 2.5, "cache_read": 0.2, "output": 10.0},
    ]


def test_budgets_match_the_spec():
    table = mb.load()
    assert {g: v["daily_usd"] for g, v in table["groups"].items()} == {
        "hk": 6.0, "build": 10.0, "implementer": 4.0, "restarter": 4.0, "docs-checker": 2.5,
        "k8s-reader": 0.5, "skill-suggester": 0.5, "dcc": 0.5, "ui-default": 0.5,
    }
    assert table["window"] == "24h" and table["warn_at"] == 0.8
    assert table["groups"]["build"]["agents"] == ["build-orchestrator", "k8s-agent", "istio-agent"]
    assert table["groups"]["ui-default"] == {
        "agents": [], "chart_default": True, "models": ["claude-haiku-5-5"], "daily_usd": 0.5,
        "gateway": False}


def test_env_name():
    assert mb.env_name("docs-checker") == "MODEL_GATEWAY_KEY_DOCS_CHECKER"
    assert mb.env_name("hk") == "MODEL_GATEWAY_KEY_HK"


def _write(tmp_path, groups, **top):
    body = {"version": 1, "window": "24h", "warn_at": 0.8, "groups": groups, **top}
    p = tmp_path / "budgets.yaml"
    p.write_text(yaml.safe_dump(body))
    return p


GOOD = {"agents": ["a"], "models": ["claude-haiku-5-5"], "daily_usd": 1.0, "gateway": False}


@pytest.mark.parametrize("groups,top", [
    ({"Bad_Name": GOOD}, {}),
    ({"ok": {**GOOD, "daily_usd": 0}}, {}),
    ({"ok": {**GOOD, "daily_usd": -1}}, {}),
    ({"ok": GOOD}, {"warn_at": 1.0}),
    ({"ok": GOOD}, {"warn_at": 0}),
    ({"ok": {**GOOD, "gateway": "yes"}}, {}),
    ({"ok": {k: v for k, v in GOOD.items() if k != "models"}}, {}),
])
def test_load_rejects(tmp_path, groups, top):
    with pytest.raises(ValueError):
        mb.load(_write(tmp_path, groups, **top))


def test_load_accepts_a_minimal_table(tmp_path):
    assert mb.load(_write(tmp_path, {"ok": GOOD}))["groups"]["ok"]["daily_usd"] == 1.0


def test_the_console_limit_guidance_matches_the_table():
    """Final review I3: the owner sizes the Anthropic console limit (the monthly backstop) from the
    spec's numbers, so they must follow the table: its daily total, its month and a week of the
    largest budget."""
    groups = mb.load()["groups"].values()
    total = sum(g["daily_usd"] for g in groups)
    largest = max(g["daily_usd"] for g in groups)
    spec = (REPO / "docs/superpowers/specs/2026-10-23-model-gateway-budgets-design.md").read_text()
    assert f"The budgets add up to ${total:.2f} a day, about ${int(total * 31)} a month" in spec
    assert f"about a week of the largest budget (about ${int(7 * largest)})" in spec
