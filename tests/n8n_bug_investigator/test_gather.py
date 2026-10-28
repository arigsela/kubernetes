# tests/n8n_bug_investigator/test_gather.py
"""Gathering facts over the GitHub API, the same facts kagent's trigger gathers from clones (§T.152, Task 5)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import world as W
from conftest import JsError, REPO as REPO_ROOT

CFG = {"repo": W.REPO, "deployRepo": W.DEPLOY_REPO, "deployPath": W.DEPLOY_PATH, "namespace": W.NAMESPACE, "days": 14}
KAGENT_LABELS = {"investigated", "investigation-failed", "fix-proposed"}
FIXTURES = Path(__file__).parent / "fixtures"


def core_facts(core, w, tmp_path):
    app, deploy, run = w.write_dirs(tmp_path, w.runner(core.util.CommandError))
    return core.gather.gather("full", w.issue, W.REPO, app, deploy, W.DEPLOY_REPO, W.DEPLOY_PATH, W.NAMESPACE, days=14, runner=run)


def js_facts(js, w, now, full=False, deadline_ms=None, clock_ms=None):
    opts = {"deadlineMs": deadline_ms, "clockMs": clock_ms}
    return js("gather", "gatherVia", CFG, w.issue, W.iso(now), opts, ctx=w.http(), full=full)


def n8n_view(facts):
    """What the POC is meant to differ in: it drops every bug-investigator comment (kagent drops only
    `mode=` ones) and kagent's and its own labels; problem texts differ, their sections must not."""
    f = copy.deepcopy(facts)
    t = f["ticket"]
    t["comments"] = [c for c in t["comments"] if "<!-- bug-investigator" not in c["body"]]
    t["labels"] = [x for x in t["labels"] if x not in KAGENT_LABELS and not x.startswith("n8n-")]
    f["problems"] = [p.split(":")[0] for p in f["problems"]]
    return f


@pytest.mark.parametrize("make", [W.rich_world, W.sparse_world], ids=["rich", "sparse"])
def test_gather_facts_match_core(core, js, frozen_now, tmp_path, make):
    w = make(frozen_now)
    py = core_facts(core, w, tmp_path)
    out = js_facts(js, w, frozen_now)
    assert n8n_view(out) == n8n_view(py)
    assert out["generated_at"] == W.iso(frozen_now)


def test_rich_world_exercises_every_rule(core, frozen_now, tmp_path):
    # Guards the fixture: if these stop holding, test_gather_facts_match_core proves less.
    py = core_facts(core, W.rich_world(frozen_now), tmp_path)
    prs = {p["number"]: p for p in py["merged_prs"]}
    assert sorted(prs) == [46, 49, 50]                       # 48 rehearsal and 47 older than 14 days left out
    assert prs[49]["deployed_by"] == W.sha("deploy-3")        # the oldest deploy containing it, not the newest
    assert prs[46]["deployed_by"] == W.sha("deploy-2") and prs[50]["live_since"] is None
    assert [b["number"] for b in py["past_bugs"]] == [39, 30] and py["past_bugs"][0]["fixed_by"][0]["number"] == 38
    assert [d["path"] for d in py["docs"]] == ["docs/rules.md", "docs/z-last.md", "docs/api/routes.md",
                                                f"{W.DEPLOY_PATH}/docs.md", f"{W.DEPLOY_PATH}/docs/index.md"]
    assert [r["id"] for r in py["docs"][0]["rules"]] == ["P4", "C3", "S1"]
    assert py["deploys"][-1]["source_commit"] is None and py["deploys"][0]["source_commit"] == py["deploys"][1]["source_commit"][:7]


def test_bot_authors_match_gh(js, frozen_now):
    f = js_facts(js, W.rich_world(frozen_now), frozen_now)
    assert f["ticket"]["author"] == "app/arigsela-implementer"
    assert [c["author"] for c in f["ticket"]["comments"]] == ["arigsela", "arigsela-implementer"]
    pr50 = next(p for p in f["merged_prs"] if p["number"] == 50)
    assert pr50["author"] == "app/arigsela-implementer"
    assert pr50["comments"][0]["author"] == "arigsela-implementer"
    assert pr50["review_comments"][0]["author"] == "arigsela-implementer[bot]"


def test_kagent_and_own_labels_are_dropped(js, frozen_now):
    f = js_facts(js, W.rich_world(frozen_now), frozen_now)
    assert f["ticket"]["labels"] == ["bug", "from-alert"]
    assert all("<!-- bug-investigator" not in c["body"] for c in f["ticket"]["comments"])


def test_merged_prs_follow_gh_pr_list(js):
    # Recorded 2026-10-28 from arigsela/taskboard: `gh pr list --state merged --limit 50` and the
    # GraphQL query gather.js sends. Order and authors must agree.
    ref = json.loads((FIXTURES / "gh-reference.json").read_text())
    got = js("gather", "prRowsFromGraphql", ref["graphql_merged_prs"])
    assert got == [[p["number"], p["author"]["login"]] for p in ref["gh_pr_list"]]


def test_failing_section_becomes_a_problem(js, frozen_now):
    f = js_facts(js, W.sparse_world(frozen_now), frozen_now)
    assert f["past_bugs"] == [] and len(f["problems"]) == 1 and f["problems"][0].startswith("past_bugs: ")


def test_gather_request_budget(js, frozen_now):
    reply = js_facts(js, W.budget_world(frozen_now), frozen_now, full=True)
    assert len(reply["value"]["merged_prs"]) == 21 and len(reply["value"]["deploys"]) == 6
    assert len(reply["calls"]) <= 60, len(reply["calls"])


def test_gather_deadline_is_a_prepare_failure(js, frozen_now):
    with pytest.raises(JsError) as e:
        js_facts(js, W.rich_world(frozen_now), frozen_now, full=True, deadline_ms=1000, clock_ms=2000)
    assert e.value.name == "GatherTimeout"


# ------------------------------------------------------------------------ markdown and message


@pytest.mark.parametrize("make", [W.rich_world, W.sparse_world], ids=["rich", "sparse"])
def test_to_markdown_matches_core(core, js, frozen_now, tmp_path, make):
    facts = core_facts(core, make(frozen_now), tmp_path)
    assert js("gather", "toMarkdown", facts) == core.gather.to_markdown(facts)


def test_build_message_matches_trigger(core, js, frozen_now, tmp_path):
    from importlib import util as iu
    import sys
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec = iu.spec_from_file_location("bug_investigator_trigger_n8n", REPO_ROOT / "scripts/bug_investigator_trigger.py")
    trigger = iu.module_from_spec(spec)
    sys.modules[spec.name] = trigger
    spec.loader.exec_module(trigger)
    md = core.gather.to_markdown(core_facts(core, W.rich_world(frozen_now), tmp_path))
    assert js("gather", "buildMessage", 41, W.REPO, md) == trigger.MESSAGE.format(issue=41, repo=W.REPO, facts=md)


def test_rule_re_matches_core(core, js):
    lines = ["- **P4** Due dates\n", "| C3 | Archive |\n", "### S1: Sessions\n", "`P10` ticked\n", "P4x no\n",
             "  * AB12— dash\n", "AB123-dash\n", "+ Q9) paren\n", "#P1 no space\n", "- P1000 too long\n",
             "P4", " P5.\n", "- p4 lowercase\n"]
    for line in lines:
        m = core.gather.RULE_RE.match(line)
        assert js("gather", "ruleId", line) == (m.group(1) if m else None), repr(line)


def test_telemetry_pointers_match_core(core, js):
    assert js("gather", "telemetryPointers", "taskboard") == core.gather.telemetry_pointers("taskboard")
