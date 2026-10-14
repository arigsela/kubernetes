# tests/dispatch/test_table.py
"""The dispatch table is complete over the Grafana rules and routes only to read-or-propose agents (V87)."""
from pathlib import Path
import json

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
TABLE = REPO / "base-apps/dispatcher/dispatch-table.yaml"
ALERTING = REPO / "base-apps/logging/grafana-alerting.yaml"
TEMPLATES = REPO / "base-apps/kagent/agent-templates"
TAXONOMY = REPO / "base-apps/admission-policies/agent-capability-taxonomy.yaml"


def load_table() -> dict:
    cm = [d for d in yaml.safe_load_all(TABLE.read_text()) if d and d.get("kind") == "ConfigMap"][0]
    assert cm["metadata"]["name"] == "dispatcher-table"
    assert cm["metadata"]["namespace"] == "dispatcher"
    return json.loads(cm["data"]["dispatch.json"])


def alert_titles() -> set[str]:
    cm = [d for d in yaml.safe_load_all(ALERTING.read_text()) if d and d.get("kind") == "ConfigMap"][0]
    rules = yaml.safe_load(cm["data"]["rules.yaml"])
    return {r["title"] for g in rules["groups"] for r in g["rules"]}


def tools_of(cls: str) -> set[str]:
    cm = yaml.safe_load(TAXONOMY.read_text())
    return set(json.loads(cm["data"][cls]))


def template_class(name: str) -> str:
    doc = yaml.safe_load((TEMPLATES / f"{name}.yaml").read_text())
    return doc["metadata"]["labels"]["capability.homelab/class"]


def prompt_of(row: dict) -> str:
    """A prompt is a string or a list of strings (joined with spaces by the dispatcher)."""
    prompt = row.get("prompt") or ""
    return " ".join(p.strip() for p in prompt) if isinstance(prompt, list) else prompt


def template_tools(name: str) -> tuple[set[str], bool]:
    """(every bound tool, any binding with requireApproval true)."""
    doc = yaml.safe_load((TEMPLATES / f"{name}.yaml").read_text())
    tools, gated = set(), False
    for binding in doc["spec"].get("tools") or []:
        mcp = binding.get("mcp") or {}
        tools.update(mcp.get("tools") or [])
        gated = gated or bool(mcp.get("requireApproval"))
    return tools, gated


def test_table_has_version_limits_and_rules():
    table = load_table()
    assert table["version"] == 1
    assert table["limits"]["investigations_per_hour"] >= 1
    assert table["limits"]["repeat_after_hours"] >= 1
    assert table["rules"], "an empty table routes nothing; say so with explicit none rows"


def test_every_grafana_rule_title_has_a_row():
    rows = {r["match"].get("alertname") for r in load_table()["rules"] if r["source"] == "grafana"}
    missing = alert_titles() - rows
    assert not missing, f"add a row (route none is fine, with a why) for: {sorted(missing)}"


def test_every_row_has_an_id_a_reason_and_a_known_route():
    seen = set()
    for row in load_table()["rules"]:
        assert row["id"] not in seen, f"duplicate id {row['id']}"
        seen.add(row["id"])
        assert row["why"].strip(), f"{row['id']}: why is required"
        assert row["route"] == "none" or row["route"].startswith("agent:"), row["route"]
        if row["route"].startswith("agent:"):
            assert prompt_of(row).strip(), f"{row['id']}: an agent route needs a prompt"


def test_agent_routes_are_read_or_propose_templates_with_no_gate():
    """§V.87 (amended 2026-10-14): read or propose tools only, never a requireApproval binding."""
    allowed = tools_of("read") | tools_of("propose")
    for row in load_table()["rules"]:
        if not row["route"].startswith("agent:"):
            continue
        name = row["route"].split(":", 1)[1]
        assert (TEMPLATES / f"{name}.yaml").exists(), f"{row['id']}: no AgentTemplate {name}"
        tools, gated = template_tools(name)
        assert tools, f"{name} binds no tools; nothing to investigate with"
        assert tools <= allowed, f"{name} binds write/destructive tools: {sorted(tools - allowed)}"
        assert not gated, f"{name} has a requireApproval binding; the dispatcher never answers approvals"


def test_proposes_flag_matches_the_template_class():
    """A row that can end in a pull request says so, and only those rows are capped."""
    for row in load_table()["rules"]:
        if not row["route"].startswith("agent:"):
            assert not row.get("proposes"), row["id"]
            continue
        name = row["route"].split(":", 1)[1]
        assert bool(row.get("proposes")) == (template_class(name) == "propose"), row["id"]


def test_table_caps_open_prs():
    assert load_table()["limits"]["open_prs_max"] >= 1


def test_resource_alerts_route_to_the_implementer():
    rows = {r["match"]["alertname"]: r for r in load_table()["rules"] if r["source"] == "grafana"}
    for title in ("Container near memory limit", "Pod CPU starved"):
        row = rows[title]
        assert row["route"] == "agent:implementer" and row["proposes"] is True
        p = prompt_of(row)
        assert "limit too low" in p and "no pull request" in p and "{labels[pod]}" in p


def test_resource_rows_key_the_branch_on_the_workload_not_the_pod():
    """Review finding I5: a pod name carries a replica hash, so a slug built from it gives one PR
    per restart; the branch is keyed on the pod's owner workload and the agent checks its own
    open PRs touching the same file first."""
    rows = {r["match"]["alertname"]: r for r in load_table()["rules"] if r["source"] == "grafana"}
    for title in ("Container near memory limit", "Pod CPU starved"):
        p = prompt_of(rows[title])
        assert "{labels[pod]}." not in p, "the pod name must not end the branch slug"
        assert "owner workload" in p and "open pull requests" in p and "same file" in p


def test_prompts_only_use_known_placeholders():
    import string
    allowed = {"alertname", "status", "labels", "annotations", "starts_at"}
    for row in load_table()["rules"]:
        for _, field, _, _ in string.Formatter().parse(prompt_of(row)):
            if field:
                assert field.split("[")[0] in allowed, f"{row['id']}: unknown placeholder {field}"
