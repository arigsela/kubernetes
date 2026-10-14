# tests/implementer/test_agent.py
"""The implementer template: class propose, the contract tools and nothing else, and a prompt
whose load-bearing lines are pinned (the prompt IS the change contract; §T.116)."""
from conftest import IMPLEMENTER_PROPOSE_TOOLS, IMPLEMENTER_READ_TOOLS, NEVER_LOADED, one

TEMPLATE = "base-apps/kagent/agent-templates/implementer.yaml"
K8S_READ = ["k8s_get_resources", "k8s_get_resource_yaml", "k8s_get_pod_logs", "k8s_describe_resource",
            "k8s_get_events", "k8s_get_cluster_configuration", "k8s_get_available_api_resources",
            "k8s_check_service_connectivity"]


def template():
    return one(TEMPLATE, "AgentTemplate", "implementer")


def bindings():
    return {(b["mcp"]["server"]["name"], b["mcp"].get("requireApproval")): b["mcp"]["tools"]
            for b in template()["spec"]["tools"] if "mcp" in b}


def prompt() -> str:
    return template()["spec"]["systemPrompt"]


def test_class_model_and_harness():
    t = template()
    assert t["metadata"]["labels"]["capability.homelab/class"] == "propose"
    assert t["spec"]["modelConfig"]["name"] == "anthropic-claude-sonnet-5-5"
    agent = one("base-apps/kagent/agents/implementer.yaml", "Agent", "implementer")
    assert agent["spec"] == {"templateRef": {"name": "implementer"}, "harnessRef": {"name": "kagent-stateless"}}


def test_bindings_are_the_contract_and_no_subagents():
    b = bindings()
    assert b[("implementer", False)] == IMPLEMENTER_READ_TOOLS + IMPLEMENTER_PROPOSE_TOOLS
    assert b[("kagent-tool-server", False)] == K8S_READ
    assert set(b) == {("implementer", False), ("kagent-tool-server", False)}
    assert not [x for x in template()["spec"]["tools"] if "subAgent" in x]
    assert not any(n in sum(b.values(), []) for n in NEVER_LOADED)


def test_no_tool_name_is_bound_twice():
    """The Go ADK refuses an agent whose bindings repeat a tool name ("duplicate tool:
    get_file_contents", acceptance 2026-10-14), so the read-only agent-docs server is not bound
    beside the implementer server that has the same read tools."""
    names = sum(bindings().values(), [])
    assert len(names) == len(set(names)), sorted(n for n in names if names.count(n) > 1)


def test_prompt_pins_the_deny_list_and_the_file_cap():
    p = prompt()
    for path in (".github/", "terraform/", ".claude/", "CLAUDE.md", "AGENTS.md", "templates/", "SPEC.md",
                 "external-secret", "secret-store"):
        assert path in p, path
    assert "at most three files" in p


def test_prompt_never_merges_and_never_opens_a_second_pr():
    p = prompt()
    assert "Never merge" in p and "never close, review or comment" in p
    assert "already exists" in p and "update that branch" in p


def test_prompt_fixes_the_branch_slug():
    p = prompt()
    assert "agent/<slug>" in p and "[a-z0-9-]" in p


def test_prompt_covers_chart_rendered_apps():
    assert "valuesObject" in prompt()


def test_prompt_handles_a_fix_already_in_git():
    assert "already differs from the live pod" in prompt()


def test_prompt_says_ci_is_the_validation_and_quotes_evidence():
    p = prompt()
    assert "validation: CI" in p and "never quote values that look like" in p.lower()
