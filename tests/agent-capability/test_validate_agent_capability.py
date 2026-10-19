"""Tests for the agent-capability contract validator (kagent 1.0, api.kagent.dev/v1alpha3).

Every invariant gets a negative test. A validator that only ever passes is
indistinguishable from one that does nothing, so each test builds a synthetic
repo that VIOLATES one invariant and asserts the violation is caught. And it fails
CLOSED (SPEC §V.84): manifests it does not understand, or none at all, are errors.
"""
from pathlib import Path
import importlib.util
import json

import yaml

SPEC = importlib.util.spec_from_file_location(
    "validate_agent_capability",
    Path(__file__).resolve().parents[2] / "scripts" / "validate-agent-capability.py",
)
vac = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vac)

API = "api.kagent.dev/v1alpha3"
REPO = Path(__file__).resolve().parents[2]
GO_ADK = "ghcr.io/kagent-dev/kagent/golang-adk:1.0.0-alpha14@sha256:" + "0" * 64

TAXONOMY = {
    "read": ["k8s_get_resources", "get_file_contents", "list_pull_requests"],
    "propose": ["create_pull_request", "push_files"],
    "write": ["k8s_patch_resource"],
    "destructive": ["k8s_delete_resource", "k8s_execute_command"],
    "runtime-images": ["ghcr.io/kagent-dev/kagent/golang-adk"],
}


def make_repo(tmp_path: Path, templates: list[dict], agents: list[dict] | None = None,
              harnesses: list[dict] | None = None) -> Path:
    """Build a throwaway repo: the taxonomy ConfigMap + the given kagent docs. Every template
    gets an Agent and the repo one Harness unless given explicitly."""
    tax_dir = tmp_path / "base-apps" / "admission-policies"
    tax_dir.mkdir(parents=True)
    (tax_dir / "agent-capability-taxonomy.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "agent-capability-taxonomy"},
                "data": {k: json.dumps(v) for k, v in TAXONOMY.items()},
            }
        )
    )
    kd = tmp_path / "base-apps" / "kagent"
    for sub in ("agent-templates", "agents", "harnesses"):
        (kd / sub).mkdir(parents=True)
    for doc in templates:
        (kd / "agent-templates" / f"{doc['metadata']['name']}.yaml").write_text(yaml.safe_dump(doc))
    if agents is None:
        agents = [agent(d["metadata"]["name"]) for d in templates]
    for doc in agents:
        (kd / "agents" / f"{doc['metadata']['name']}.yaml").write_text(yaml.safe_dump(doc))
    for doc in harnesses if harnesses is not None else [harness("kagent-memory")]:
        (kd / "harnesses" / f"{doc['metadata']['name']}.yaml").write_text(yaml.safe_dump(doc))
    return tmp_path


def template(name, cls, tools=None, approval=None, gated=None, subagents=None):
    """Build an AgentTemplate. tools go in one binding with requireApproval=approval (omitted
    when None); gated adds a second, requireApproval: true binding. cls=None omits the
    class label."""
    bindings = []
    if tools:
        mcp = {"server": {"kind": "RemoteMCPServer", "name": "kagent-tool-server"},
               "tools": tools}
        if approval is not None:
            mcp["requireApproval"] = approval
        bindings.append({"mcp": mcp})
    if gated:
        bindings.append({"mcp": {"server": {"kind": "RemoteMCPServer",
                                            "name": "kagent-tool-server"},
                                 "requireApproval": True, "tools": gated}})
    for s in subagents or []:
        bindings.append({"subAgent": {"name": s, "description": "d", "templateRef": {"name": s}}})

    meta = {"name": name, "namespace": "kagent"}
    if cls is not None:
        meta["labels"] = {vac.CLASS_LABEL: cls}
    return {"apiVersion": API, "kind": "AgentTemplate", "metadata": meta,
            "spec": {"modelConfig": {"name": "m"}, "tools": bindings}}


def agent(name, inline_template=False, inline_harness=False):
    spec: dict = {}
    if inline_template:
        spec["template"] = {"inline": {"modelConfig": {"name": "m"}}}
    else:
        spec["template"] = {"ref": {"name": name}}
    if inline_harness:
        spec["harness"] = harness("x")["spec"]
    else:
        spec["harnessRef"] = {"name": "kagent-memory"}
    return {"apiVersion": API, "kind": "Agent",
            "metadata": {"name": name, "namespace": "kagent"}, "spec": spec}


def harness(name, runtime="kagent", image=GO_ADK):
    spec = {runtime: {}, "workload": {"image": image},
            "substrate": {"workerPoolRef": {"name": "kagent-default"},
                          "snapshotPolicy": {"location": "s3://ate-snapshots/kagent/"}}}
    if runtime == "byo":
        spec["workload"]["command"] = ["/app"]
    return {"apiVersion": API, "kind": "Harness",
            "metadata": {"name": name, "namespace": "kagent"}, "spec": spec}


def run(tmp_path, templates, **kw) -> int:
    return vac.main(["--repo-root", str(make_repo(tmp_path, templates, **kw))])


# --------------------------------------------------------------- happy path

def test_valid_repo_passes(tmp_path):
    assert run(tmp_path, [
        template("reader", "read", tools=["k8s_get_resources"], approval=False),
        template("operator", "admin", tools=["k8s_get_resources"], approval=False,
                 gated=["k8s_delete_resource", "k8s_patch_resource"]),
    ]) == 0


def test_the_real_repo_passes():
    assert vac.main(["--repo-root", str(REPO)]) == 0


# ------------------------------------------- invariant 1: declared class

def test_missing_class_label_is_error(tmp_path):
    assert run(tmp_path, [template("nolabel", None, tools=["k8s_get_resources"])]) == 1


def test_invalid_class_value_is_error(tmp_path):
    assert run(tmp_path, [template("bogus", "superuser", tools=["k8s_get_resources"])]) == 1


# ------------------------------------- invariant 2: tools within class

def test_read_template_binding_destructive_tool_is_error(tmp_path):
    """The core capability check: a read agent may not hold delete, gated or not."""
    assert run(tmp_path, [
        template("sneaky", "read", tools=["k8s_get_resources"], gated=["k8s_delete_resource"]),
    ]) == 1


def test_write_template_binding_destructive_tool_is_error(tmp_path):
    assert run(tmp_path, [template("mid", "write", gated=["k8s_execute_command"])]) == 1


def test_unclassified_tool_is_error(tmp_path):
    """FAIL-CLOSED: a tool absent from the taxonomy cannot be bound."""
    assert run(tmp_path, [
        template("future", "admin", gated=["k8s_brand_new_tool_from_chart_bump"]),
    ]) == 1


# ------------------------------------ invariant 3: mutating tools gated

def test_write_tool_in_ungated_binding_is_error(tmp_path):
    assert run(tmp_path, [
        template("ungated", "write", tools=["k8s_patch_resource"], approval=False),
    ]) == 1


def test_write_tool_with_approval_omitted_is_error(tmp_path):
    """requireApproval omitted defaults to false."""
    assert run(tmp_path, [template("omitted", "write", tools=["k8s_patch_resource"])]) == 1


def test_destructive_tool_without_approval_is_error(tmp_path):
    assert run(tmp_path, [
        template("ungated", "admin", tools=["k8s_delete_resource"], approval=False),
    ]) == 1


def test_mutating_tool_beside_a_gated_binding_is_error(tmp_path):
    """Gating SOME mutating tools is not enough — 0.10's k8s-agent had label/annotate bound
    but ungated. In 1.0 that is a mutating tool left in the ungated binding."""
    assert run(tmp_path, [
        template("partial", "admin", tools=["k8s_get_resources", "k8s_patch_resource"],
                 approval=False, gated=["k8s_delete_resource"]),
    ]) == 1


# ------------------------------------- invariant 4: no escalation

def test_read_template_with_admin_subagent_is_error(tmp_path):
    """The homelab-knowledge -> k8s-agent hole."""
    assert run(tmp_path, [
        template("docs", "read", tools=["get_file_contents"], approval=False,
                 subagents=["operator"]),
        template("operator", "admin", gated=["k8s_delete_resource"]),
    ]) == 1


def test_read_template_with_read_subagent_is_ok(tmp_path):
    assert run(tmp_path, [
        template("docs", "read", tools=["get_file_contents"], approval=False,
                 subagents=["reader"]),
        template("reader", "read", tools=["k8s_get_resources"], approval=False),
    ]) == 0


def test_escalation_is_transitive(tmp_path):
    """read -> read -> admin must still be caught. (kagent 1.0 refuses a subagent with
    subagents at compile time, but CI does not rely on that.)"""
    assert run(tmp_path, [
        template("docs", "read", subagents=["middle"]),
        template("middle", "read", subagents=["operator"]),
        template("operator", "admin", gated=["k8s_delete_resource"]),
    ]) == 1


def test_dangling_subagent_is_error(tmp_path):
    assert run(tmp_path, [template("orphan", "read", subagents=["does-not-exist"])]) == 1


def test_subagent_cycle_is_caught(tmp_path):
    """Must terminate, not recurse forever."""
    assert run(tmp_path, [
        template("a", "read", subagents=["b"]),
        template("b", "read", subagents=["a"]),
    ]) == 1


# ------------------------------------- invariant 5: refs only

def test_inline_template_agent_is_error(tmp_path):
    t = [template("reader", "read", tools=["k8s_get_resources"], approval=False)]
    assert run(tmp_path, t, agents=[agent("reader", inline_template=True)]) == 1


def test_inline_spec_template_agent_fails_with_inline_message(tmp_path, capsys):
    t = [template("reader", "read", tools=["k8s_get_resources"], approval=False)]
    a = agent("reader")
    a["spec"]["template"] = {"inline": {"systemPrompt": "x"}}
    assert run(tmp_path, t, agents=[a]) == 1
    captured = capsys.readouterr()
    assert "inline" in captured.out + captured.err


def test_inline_harness_agent_is_error(tmp_path):
    t = [template("reader", "read", tools=["k8s_get_resources"], approval=False)]
    assert run(tmp_path, t, agents=[agent("reader", inline_harness=True)]) == 1


# ------------------------------------- invariant 6: trusted runtime

def test_byo_harness_is_error(tmp_path):
    t = [template("reader", "read", tools=["k8s_get_resources"], approval=False)]
    assert run(tmp_path, t, harnesses=[harness("kagent-memory", runtime="byo")]) == 1


def test_foreign_image_harness_is_error(tmp_path):
    t = [template("reader", "read", tools=["k8s_get_resources"], approval=False)]
    for image in ("docker.io/someone/agent@sha256:" + "0" * 64,
                  "ghcr.io/kagent-dev/kagent/golang-adk-fork@sha256:" + "0" * 64):
        sub = tmp_path / image.split("/")[-1][:12]
        assert run(sub, t, harnesses=[harness("kagent-memory", image=image)]) == 1, image


def test_unpinned_image_harness_is_error(tmp_path):
    t = [template("reader", "read", tools=["k8s_get_resources"], approval=False)]
    assert run(tmp_path, t, harnesses=[
        harness("kagent-memory", image="ghcr.io/kagent-dev/kagent/golang-adk:1.0.0-alpha14")]) == 1


# ------------------------------------------------- fail closed (§V.84)

def test_no_templates_is_error(tmp_path):
    """An empty directory must not pass."""
    assert run(tmp_path, [], agents=[], harnesses=[]) == 1


def test_v1alpha2_agent_is_error_not_skipped(tmp_path):
    """A planted 0.10 Agent (read class, binding delete UNGATED) next to a valid 1.0 repo. A
    validator that only knew v1alpha3 would skip it; it must fail instead."""
    repo = make_repo(tmp_path, [template("reader", "read", tools=["k8s_get_resources"],
                                         approval=False)])
    (repo / "base-apps" / "kagent" / "agents" / "legacy.yaml").write_text(yaml.safe_dump({
        "apiVersion": "kagent.dev/v1alpha2", "kind": "Agent",
        "metadata": {"name": "legacy", "labels": {vac.CLASS_LABEL: "read"}},
        "spec": {"declarative": {"tools": [{"type": "McpServer", "mcpServer": {
            "name": "kagent-tool-server", "toolNames": ["k8s_delete_resource"]}}]}}}))
    assert vac.main(["--repo-root", str(repo)]) == 1


def test_templates_found_by_kind_not_directory(tmp_path):
    """A template outside agent-templates/ is still found, and its violation still caught."""
    repo = make_repo(tmp_path, [template("in-dir", "read", tools=["k8s_get_resources"],
                                         approval=False)])
    stray = repo / "base-apps" / "kagent" / "stray-orchestrator.yaml"
    stray.write_text(yaml.safe_dump(template("stray", "read", gated=["k8s_delete_resource"])))
    found = {(d.get("metadata") or {}).get("name") for _, d in vac.collect_templates(repo)}
    assert found == {"in-dir", "stray"}
    assert vac.main(["--repo-root", str(repo)]) == 1


def test_real_harnesses_share_one_pinned_runtime_image():
    """The Harnesses differ only in spec.kagent (memory, compaction). One runtime image, pinned
    by digest, so a chart bump moves every agent at once and no Harness is left behind on an
    old runtime."""
    harnesses = vac.collect(REPO)[0].get("Harness", [])
    assert harnesses, "no Harness in the repo"
    images = {(d["spec"].get("workload") or {}).get("image") for _, d in harnesses}
    assert len(images) == 1, images
    assert "@sha256:" in images.pop()


# ------------------------------------------- class propose (branches and PRs only)

def test_read_template_binding_propose_tool_is_error(tmp_path):
    assert run(tmp_path, [
        template("reader", "read", tools=["k8s_get_resources", "create_pull_request"], approval=False),
    ]) == 1


def test_propose_template_binding_write_tool_is_error(tmp_path):
    assert run(tmp_path, [
        template("proposer", "propose", tools=["k8s_get_resources"], approval=False,
                 gated=["k8s_patch_resource"]),
    ]) == 1


def test_propose_template_binds_propose_tools_ungated(tmp_path):
    assert run(tmp_path, [
        template("proposer", "propose",
                 tools=["k8s_get_resources", "create_pull_request", "push_files"], approval=False),
    ]) == 0


def test_write_template_may_bind_propose_tools_ungated(tmp_path):
    assert run(tmp_path, [
        template("operator", "write", tools=["create_pull_request"], approval=False,
                 gated=["k8s_patch_resource"]),
    ]) == 0


def test_propose_template_with_admin_subagent_is_error(tmp_path):
    assert run(tmp_path, [
        template("proposer", "propose", tools=["create_pull_request"], approval=False,
                 subagents=["operator"]),
        template("operator", "admin", tools=["k8s_get_resources"], approval=False,
                 gated=["k8s_delete_resource"]),
    ]) == 1


def test_read_template_with_propose_subagent_is_error(tmp_path):
    assert run(tmp_path, [
        template("reader", "read", tools=["k8s_get_resources"], approval=False,
                 subagents=["proposer"]),
        template("proposer", "propose", tools=["create_pull_request"], approval=False),
    ]) == 1


def test_propose_is_ranked_between_read_and_write():
    assert vac.CLASS_RANK["read"] < vac.CLASS_RANK["propose"] < vac.CLASS_RANK["write"] < vac.CLASS_RANK["admin"]
    assert vac.CLASS_MAY_BIND["propose"] == {"read", "propose"}
    assert "propose" not in vac.MUST_APPROVE


def test_real_taxonomy_propose_list_is_exactly_the_four_pr_tools():
    """update_pull_request is out (review I3, 2026-10-14): it can close or retarget any PR."""
    tools, _ = vac.load_taxonomy(REPO)
    assert {t for t, c in tools.items() if c == "propose"} == {
        "create_branch", "create_or_update_file", "push_files", "create_pull_request"}
