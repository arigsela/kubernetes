"""Tests for the agent-capability contract validator (kagent 1.0, api.kagent.dev/v1alpha3).

Every invariant gets a negative test. A validator that only ever passes is
indistinguishable from one that does nothing, so each test builds a synthetic
repo that VIOLATES one invariant and asserts the violation is caught. And it fails
CLOSED (SPEC §V.84): manifests it does not understand, or none at all, are errors.
"""
from pathlib import Path
import importlib.util
import json

import pytest
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
# A byo image as the taxonomy lists it: repository@sha256:digest, exact (SPEC §V.101).
DOCS_CHECKER_REPO = "852893458518.dkr.ecr.us-east-2.amazonaws.com/docs-checker"
DOCS_CHECKER = DOCS_CHECKER_REPO + "@sha256:" + "a" * 64
DOCS_CHECKER_TOOLS = ["get_file_contents", "search_code", "list_commits", "get_commit"]

TAXONOMY = {
    "read": ["k8s_get_resources", "get_file_contents", "list_pull_requests", "search_code",
             "list_commits", "get_commit"],
    "propose": ["create_pull_request", "push_files"],
    "write": ["k8s_patch_resource"],
    "destructive": ["k8s_delete_resource", "k8s_execute_command"],
    "runtime-images": ["ghcr.io/kagent-dev/kagent/golang-adk"],
    "byo-images": [DOCS_CHECKER],
    "read-only-servers": ["agent-docs"],
}


def make_repo(tmp_path: Path, templates: list[dict], agents: list[dict] | None = None,
              harnesses: list[dict] | None = None, taxonomy: dict | None = None,
              servers=("agent-docs", "kagent-tool-server")) -> Path:
    """Build a throwaway repo: the taxonomy ConfigMap + the given kagent docs. Every template
    gets an Agent and the repo one Harness unless given explicitly, and the RemoteMCPServers
    `servers` exist."""
    tax_dir = tmp_path / "base-apps" / "admission-policies"
    tax_dir.mkdir(parents=True)
    (tax_dir / "agent-capability-taxonomy.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "agent-capability-taxonomy"},
                "data": {k: json.dumps(v) for k, v in (taxonomy or TAXONOMY).items()},
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
    for name in servers:
        (kd / f"{name}-remote.yaml").write_text(yaml.safe_dump(remote_server(name)))
    return tmp_path


def remote_server(name):
    return {"apiVersion": API, "kind": "RemoteMCPServer",
            "metadata": {"name": name, "namespace": "kagent"},
            "spec": {"description": name, "url": f"http://{name}.kagent:3000/mcp"}}


def template(name, cls, tools=None, approval=None, gated=None, subagents=None,
             server="kagent-tool-server"):
    """Build an AgentTemplate. tools go in one binding with requireApproval=approval (omitted
    when None); gated adds a second, requireApproval: true binding. cls=None omits the
    class label."""
    bindings = []
    if tools:
        mcp = {"server": {"kind": "RemoteMCPServer", "name": server},
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


def agent(name, inline_template=False, inline_harness=False, harness_ref="kagent-memory",
          template_ref=None):
    spec: dict = {}
    if inline_template:
        spec["template"] = {"inline": {"modelConfig": {"name": "m"}}}
    else:
        spec["template"] = {"ref": {"name": template_ref or name}}
    if inline_harness:
        spec["harness"] = harness("x")["spec"]
    else:
        spec["harnessRef"] = {"name": harness_ref}
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

def test_byo_harness_on_an_unlisted_image_is_error(tmp_path):
    """A byo runtime on a kagent ADK image is still not admitted: only byo-images entries."""
    t = [template("reader", "read", tools=["k8s_get_resources"], approval=False)]
    assert run(tmp_path, t, harnesses=[harness("kagent-memory", runtime="byo")]) == 1


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_claude_and_codex_harnesses_are_error(tmp_path, runtime):
    t = [template("reader", "read", tools=["k8s_get_resources"], approval=False)]
    assert run(tmp_path, t, harnesses=[harness("kagent-memory", runtime=runtime)]) == 1


# ------------------------------------- invariant 7: BYO only under exact conditions

def byo_repo(tmp_path, *, cls="read", tools=DOCS_CHECKER_TOOLS, server="agent-docs",
             image=DOCS_CHECKER, subagents=None, taxonomy=None, servers=None,
             extra_templates=()):
    """docs-checker as it ships (K4): a byo Harness on the listed digest, a read template
    binding agent-docs with the four read tools, and an Agent joining them, next to an
    ordinary kagent agent."""
    templates = [template("docs-checker", cls, tools=tools, approval=False, server=server,
                          subagents=subagents),
                 template("reader", "read", tools=["k8s_get_resources"], approval=False),
                 *extra_templates]
    agents = [agent("docs-checker", harness_ref="docs-checker"), agent("reader")]
    harnesses = [harness("kagent-memory"), harness("docs-checker", runtime="byo", image=image)]
    kw = {"taxonomy": taxonomy}
    if servers is not None:
        kw["servers"] = servers
    return make_repo(tmp_path, templates, agents=agents, harnesses=harnesses, **kw)


def run_repo(repo) -> int:
    return vac.main(["--repo-root", str(repo)])


def test_byo_on_a_listed_digest_with_a_read_template_on_read_only_servers_passes(tmp_path):
    assert run_repo(byo_repo(tmp_path)) == 0


def test_byo_harness_no_agent_uses_passes(tmp_path):
    """Admission admits it by digest alone; with no Agent on it, nothing runs."""
    repo = make_repo(tmp_path, [template("reader", "read", tools=["k8s_get_resources"])],
                     harnesses=[harness("kagent-memory"),
                                harness("docs-checker", runtime="byo", image=DOCS_CHECKER)])
    assert run_repo(repo) == 0


def test_byo_foreign_digest_is_error(tmp_path):
    """The repository is listed, this digest is not: every digest pushed there is NOT
    admitted (Codex C#3: a repository allow-list admits whatever is pushed later)."""
    assert run_repo(byo_repo(tmp_path, image=DOCS_CHECKER_REPO + "@sha256:" + "b" * 64)) == 1


@pytest.mark.parametrize("image", [
    DOCS_CHECKER_REPO + "-fork@sha256:" + "a" * 64,          # lookalike repository
    "evil.example/" + DOCS_CHECKER_REPO + "@sha256:" + "a" * 64,
    DOCS_CHECKER_REPO + ":v1@sha256:" + "a" * 64,            # tag + digest is another string
])
def test_byo_image_must_equal_a_listed_entry(tmp_path, image):
    assert run_repo(byo_repo(tmp_path, image=image)) == 1


@pytest.mark.parametrize("cls", ["propose", "write", "admin"])
def test_byo_agent_on_a_non_read_template_is_error(tmp_path, cls):
    """Even binding only read tools: the class is the declared ceiling, and a byo runtime
    need not honour any per-binding gate."""
    assert run_repo(byo_repo(tmp_path, cls=cls)) == 1


def test_byo_agent_binding_a_server_outside_read_only_servers_is_error(tmp_path):
    """kagent-tool-server serves write tools on the same origin: byo egress opens per origin,
    not per tool, so binding even its read tools hands the runtime its write tools."""
    assert run_repo(byo_repo(tmp_path, tools=["k8s_get_resources"],
                             server="kagent-tool-server")) == 1


def test_byo_agent_binding_a_non_read_tool_is_error(tmp_path):
    assert run_repo(byo_repo(tmp_path, tools=["get_file_contents", "push_files"])) == 1


def test_byo_agent_with_a_subagent_is_error(tmp_path):
    """A subAgent is compiled into the byo runtime too; its bindings are not this template's."""
    assert run_repo(byo_repo(tmp_path, subagents=["reader"])) == 1


def test_byo_agent_outside_the_kagent_namespace_is_error(tmp_path):
    """read-only-servers are the RemoteMCPServers in `kagent`, where their server-side
    read-only proof applies. A same-named server in another namespace could point anywhere."""
    repo = byo_repo(tmp_path)
    kd = repo / "base-apps" / "kagent"
    elsewhere = repo / "base-apps" / "team-a"
    elsewhere.mkdir()
    for sub, name in (("agent-templates", "docs-checker"), ("agents", "docs-checker"),
                      ("harnesses", "docs-checker")):
        doc = yaml.safe_load((kd / sub / f"{name}.yaml").read_text())
        doc["metadata"]["namespace"] = "team-a"
        (elsewhere / f"{sub}-{name}.yaml").write_text(yaml.safe_dump(doc))
        (kd / sub / f"{name}.yaml").unlink()
    server = remote_server("agent-docs")
    server["metadata"]["namespace"] = "team-a"
    server["spec"]["url"] = "http://kagent-tools.kagent:8084/mcp"
    (elsewhere / "agent-docs-remote.yaml").write_text(yaml.safe_dump(server))
    assert run_repo(repo) == 1


def _in_team_a(repo, doc):
    """Write doc into namespace team-a, under base-apps/z-team/: it sorts AFTER base-apps/kagent,
    so a name-only index (last one wins) would resolve to it."""
    d = repo / "base-apps" / "z-team"
    d.mkdir(exist_ok=True)
    doc["metadata"]["namespace"] = "team-a"
    (d / f"{doc['kind'].lower()}-{doc['metadata']['name']}.yaml").write_text(yaml.safe_dump(doc))


def _read_twin():
    return template("docs-checker", "read", tools=DOCS_CHECKER_TOOLS, approval=False,
                    server="agent-docs")


def _byo_template_only_in_team_a(tmp_path):
    repo = byo_repo(tmp_path)
    (repo / "base-apps" / "kagent" / "agent-templates" / "docs-checker.yaml").unlink()
    _in_team_a(repo, _read_twin())
    return repo


def _agent_docs_only_in_team_a(tmp_path):
    repo = byo_repo(tmp_path, servers=("kagent-tool-server",))
    server = remote_server("agent-docs")
    server["spec"]["url"] = "http://kagent-tools.kagent:8084/mcp"
    _in_team_a(repo, server)
    return repo


def _admin_template_with_a_read_twin_in_team_a(tmp_path):
    repo = byo_repo(tmp_path, cls="admin")
    _in_team_a(repo, _read_twin())
    return repo


def _byo_harness_with_a_kagent_twin_in_team_a(tmp_path):
    repo = byo_repo(tmp_path, cls="admin")
    _in_team_a(repo, harness("docs-checker"))
    return repo


def _read_template_with_an_admin_twin_in_team_a(tmp_path):
    repo = byo_repo(tmp_path)
    _in_team_a(repo, template("docs-checker", "admin", tools=DOCS_CHECKER_TOOLS,
                              approval=False, server="agent-docs"))
    return repo


@pytest.mark.parametrize("build,expect", [
    # The byo Agent stays in kagent; the name it references exists ONLY in another namespace.
    (_byo_template_only_in_team_a, 1),
    (_agent_docs_only_in_team_a, 1),
    # Its own (kagent) object is bad; a same-named good twin elsewhere must not stand in.
    (_admin_template_with_a_read_twin_in_team_a, 1),
    (_byo_harness_with_a_kagent_twin_in_team_a, 1),
    # Its own object is good; a same-named bad twin elsewhere must not count against it.
    (_read_template_with_an_admin_twin_in_team_a, 0),
], ids=lambda f: f.__name__.strip("_") if callable(f) else str(f))
def test_byo_refs_resolve_in_the_agents_namespace(tmp_path, build, expect):
    """An Agent's spec.template.ref/harnessRef and its template's server refs resolve in the
    Agent's namespace (kagent), never by name alone: a regression to name-only lookup fails
    here."""
    assert run_repo(build(tmp_path)) == expect


def test_agent_with_an_unknown_harness_ref_is_error(tmp_path):
    """Fail closed: a Harness that is not in git has an unknown runtime."""
    t = [template("reader", "read", tools=["k8s_get_resources"], approval=False)]
    assert run(tmp_path, t, agents=[agent("reader", harness_ref="not-in-git")]) == 1


@pytest.mark.parametrize("entry", [
    DOCS_CHECKER_REPO,                                       # a repository: a prefix in disguise
    DOCS_CHECKER_REPO + ":v1",                               # a tag moves
    DOCS_CHECKER_REPO + "@sha256:" + "a" * 63,               # not a sha256 digest
    DOCS_CHECKER_REPO + "@sha256:" + "A" * 64,               # digests are lowercase hex
])
def test_byo_images_entry_must_be_repository_at_sha256_digest(tmp_path, entry):
    """C#3: exact repository@sha256:digest entries, never a prefix or a tag. The image itself
    matches, so only the entry's shape can fail here."""
    tax = dict(TAXONOMY, **{"byo-images": [entry]})
    assert run_repo(byo_repo(tmp_path, image=entry, taxonomy=tax)) == 1


def test_byo_images_placeholder_digest_is_error_and_says_how_to_pin(tmp_path, capsys):
    """Until the docs-checker image is pushed, the entry carries REPLACE_WITH_DIGEST: nothing
    can deploy by accident, and the error says what to do."""
    entry = DOCS_CHECKER_REPO + "@sha256:REPLACE_WITH_DIGEST"
    tax = dict(TAXONOMY, **{"byo-images": [entry]})
    repo = make_repo(tmp_path, [template("reader", "read", tools=["k8s_get_resources"])],
                     taxonomy=tax)
    assert run_repo(repo) == 1
    err = capsys.readouterr().err
    assert "REPLACE_WITH_DIGEST" in err and "runbook" in err


def test_read_only_server_missing_from_git_is_error(tmp_path):
    """A listed server must exist as a RemoteMCPServer in git, where its url is reviewed."""
    tax = dict(TAXONOMY, **{"read-only-servers": ["agent-docs", "ghost"]})
    assert run_repo(byo_repo(tmp_path, taxonomy=tax)) == 1


@pytest.mark.parametrize("key", ["byo-images", "read-only-servers"])
def test_taxonomy_without_a_byo_key_is_error(tmp_path, key):
    """Fail closed (§V.84): a missing list is not an empty one. `[]` admits no byo."""
    tax = {k: v for k, v in TAXONOMY.items() if k != key}
    repo = make_repo(tmp_path, [template("reader", "read", tools=["k8s_get_resources"])],
                     taxonomy=tax)
    assert run_repo(repo) == 1


@pytest.mark.parametrize("key", ["byo-images", "read-only-servers"])
def test_malformed_byo_list_is_a_named_error_not_a_traceback(tmp_path, capsys, key):
    repo = make_repo(tmp_path, [template("reader", "read", tools=["k8s_get_resources"])])
    path = repo / "base-apps" / "admission-policies" / "agent-capability-taxonomy.yaml"
    cm = yaml.safe_load(path.read_text())
    cm["data"][key] = '["unterminated'
    path.write_text(yaml.safe_dump(cm))
    assert run_repo(repo) == 1
    err = capsys.readouterr().err
    assert f"taxonomy '{key}' is not valid JSON" in err, err


def test_empty_byo_lists_admit_no_byo(tmp_path):
    tax = dict(TAXONOMY, **{"byo-images": [], "read-only-servers": []})
    repo = make_repo(tmp_path, [template("reader", "read", tools=["k8s_get_resources"])],
                     taxonomy=tax)
    assert run_repo(repo) == 0
    assert run_repo(byo_repo(tmp_path / "with-byo", taxonomy=tax)) == 1


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
    """The kagent-runtime Harnesses differ only in spec.kagent (memory, compaction). One runtime
    image, pinned by digest, so a chart bump moves every agent at once and no Harness is left
    behind on an old runtime. (A byo Harness runs its own image, from byo-images.)"""
    harnesses = [(p, d) for p, d in vac.collect(REPO)[0].get("Harness", [])
                 if "kagent" in d["spec"]]
    assert harnesses, "no kagent-runtime Harness in the repo"
    images = {(d["spec"].get("workload") or {}).get("image") for _, d in harnesses}
    assert len(images) == 1, images
    assert "@sha256:" in images.pop()


# A byo runtime reaches a bound server's whole ORIGIN (egress is per origin, not per tool), so
# a server earns a place in read-only-servers only by being read-only on the server side. One
# proof per listed server; a server listed without one fails here.
def _agent_docs_is_read_only_server_side():
    """RemoteMCPServer agent-docs -> Service agent-docs-mcp -> agentgateway running
    github-mcp-server with --read-only and only the repos toolset. The RemoteMCPServer is
    resolved the way the validator resolves it (vac.collect, namespace kagent), and must be
    defined exactly once."""
    kd = REPO / "base-apps" / "kagent"
    found = [d for _, d in vac.collect(REPO)[0].get("RemoteMCPServer", [])
             if vac._key(d) == (vac.BYO_NAMESPACE, "agent-docs")]
    assert len(found) == 1, f"RemoteMCPServer kagent/agent-docs is defined {len(found)} times"
    remote = found[0]
    assert remote["spec"]["url"] == "http://agent-docs-mcp.kagent:3000/mcp", remote["spec"]["url"]
    cm = next(d for d in yaml.safe_load_all((kd / "agent-docs-mcp.yaml").read_text())
              if d and d.get("kind") == "ConfigMap")
    gateway = yaml.safe_load(next(iter(cm["data"].values())))
    targets = [t for b in gateway["binds"] for l in b["listeners"] for r in l["routes"]
               for be in r["backends"] for t in be["mcp"]["targets"]]
    assert len(targets) == 1, targets
    args = targets[0]["stdio"]["args"]
    assert "--read-only" in args, args
    assert args[args.index("--toolsets") + 1] == "repos", args


READ_ONLY_PROOFS = {"agent-docs": _agent_docs_is_read_only_server_side}


def test_real_read_only_servers_are_read_only_on_the_server_side():
    servers = vac.load_byo_conditions(REPO)[1]
    assert servers, "read-only-servers is empty"
    for server in servers:
        assert server in READ_ONLY_PROOFS, (
            f"{server} is in read-only-servers with no server-side read-only proof here")
        READ_ONLY_PROOFS[server]()


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
