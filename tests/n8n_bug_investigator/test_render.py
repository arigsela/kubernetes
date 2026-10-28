# tests/n8n_bug_investigator/test_render.py
"""Rendering, flagging and posting the n8n investigation comment (§T.152, Task 7)."""
from __future__ import annotations

import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest

import test_verify as TV
import world as W

FIXTURES = Path(__file__).parent / "fixtures"
MODEL = "claude-sonnet-5-5"
STATS = {"turns": 7, "duration_ms": 125_400}
N8N_MARKER = "<!-- bug-investigator mode=n8nagent -->"
API = W.API
ME = "ari-pat"


def relabel(core, body):
    """The core's comment as the POC posts it: its marker, title and footer lead."""
    return (body.replace(core.render.MARKER.format(mode="full"), N8N_MARKER, 1)
            .replace(f"## {core.render.HEADERS['full']}", "## 🔎 Bug investigation — n8n AI Agent (POC)", 1)
            .replace("<sub>full mode · ", "<sub>n8n/bug-investigator-poc · ", 1))


def verified(core, tmp_path, name, now):
    out, _ = TV.python_verify(core, tmp_path, copy.deepcopy(TV.cases()[name]), W.iso(now - timedelta(hours=1)))
    return out


def recorded_steps():
    return json.loads((FIXTURES / "intermediate-steps.json").read_text())["intermediateSteps"]


@pytest.mark.parametrize("name", ["evidence_mix", "not_a_bug_with_rule", "no_supported_and_cause_fails", "not_a_dict"])
@pytest.mark.parametrize("with_trail", [False, True], ids=["no-trail", "trail"])
def test_render_matches_core_relabelled(core, js, frozen_now, tmp_path, name, with_trail):
    v = verified(core, tmp_path, name, frozen_now)
    trail = js("render", "trailFromSteps", recorded_steps()) if with_trail else None
    want = relabel(core, core.render.render(v, "full", MODEL, STATS, "", trail))
    got = js("render", "renderComment", v, {"issueNumber": 41, "model": MODEL, "stats": STATS, "trail": trail, "contaminated": []})
    assert got == want


def test_model_text_is_escaped_as_core(core, js, frozen_now, tmp_path):
    # Model-written text is untrusted: HTML, @-mentions and a fake marker must come out as the core's.
    v = verified(core, tmp_path, "evidence_mix", frozen_now)
    v["summary"] = "Returns <empty> for @ari; `a < b` stays, `<!-- bug-investigator mode=kagent -->` is broken.\nSecond line"
    v["next_step"] = "<script>alert(1)</script> ping @team"
    v["hypotheses"][0]["statement"] = "col | renamed\nto `due_at`"
    v["report"]["what"] = "<b>bold</b>"
    v["verification"]["dropped"][0]["ref"] = None
    want = relabel(core, core.render.render(v, "full", MODEL, STATS, "", None))
    got = js("render", "renderComment", v, {"issueNumber": 41, "model": MODEL, "stats": STATS, "trail": None, "contaminated": []})
    assert got == want
    assert "&lt;script>" in got and "@​ari" in got and "<​!--" in got


def test_render_truncates_as_core(core, js, frozen_now, tmp_path):
    v = verified(core, tmp_path, "evidence_mix", frozen_now)
    v["summary"] = "word " * 14000
    want = relabel(core, core.render.render(v, "full", MODEL, STATS, "", None))
    got = js("render", "renderComment", v, {"issueNumber": 41, "model": MODEL, "stats": STATS, "trail": None, "contaminated": []})
    assert got == want and "(truncated; the full findings are in the run artifacts)" in got


def test_contamination_line_sits_above_the_footer(core, js, frozen_now, tmp_path):
    v = verified(core, tmp_path, "evidence_mix", frozen_now)
    branches = ["agent/taskboard-issue-37", "agent/taskboard-issue-41"]
    got = js("render", "renderComment", v, {"issueNumber": 41, "model": MODEL, "stats": STATS, "trail": None, "contaminated": branches})
    line = ("⚠️ While it ran, this agent read kagent's fix pull request branch(es) agent/taskboard-issue-37, "
            "agent/taskboard-issue-41; compare with care.")
    assert f"\n{line}\n\n---\n<sub>n8n/bug-investigator-poc · {MODEL} · 7 turns · 2m 05s · " in got
    clean = js("render", "renderComment", v, {"issueNumber": 41, "model": MODEL, "stats": STATS, "trail": None, "contaminated": []})
    assert got.replace(f"{line}\n\n", "", 1) == clean


def test_trail_from_intermediate_steps(core, js):
    # The recorded run: one MCP call through a node named "Taskboard telemetry".
    trail = js("render", "trailFromSteps", recorded_steps())
    assert trail == {"steps": [{"n": 1, "narration": "", "tool": "query_prometheus", "input": 'sum(up{namespace="taskboard"})',
                                "result": '{"resultType":"vector","series":[{"labels":{},"values":[{"time":"2026-10-28T14:07:38Z","value":"1"}]}]}'}]}
    lines = core.render.trail_lines(trail)
    assert lines[2] == '1. query_prometheus: `sum(up{namespace="taskboard"})` → ' + trail["steps"][0]["result"]


def test_trail_narration_target_and_prefix(js):
    steps = [
        {"action": {"tool": "github_get_file_contents", "toolInput": {"owner": "arigsela", "repo": "taskboard", "path": "server/x.js", "tool": "github_get_file_contents"},
                    "log": "Calling github", "messageLog": [{"content": [{"type": "text", "text": "Reading the route first."}, {"type": "tool_use"}]}]},
         "observation": "plain text result " + "z" * 300},
        {"action": {"tool": "telemetry_list_loki_label_values", "toolInput": {"datasourceUid": "loki", "labelName": "app", "matcher": '{namespace="taskboard"}'},
                    "log": "Calling telemetry", "messageLog": [{"lc_kwargs": {"content": "Checking labels."}}]},
         "observation": [{"type": "text", "text": "as a list"}]},
        {"action": {"tool": "github_pull_request_read", "toolInput": {"repo": "taskboard", "pullNumber": 38, "method": "get_diff"}}, "observation": "{}"},
        {"action": {"tool": "something_else", "toolInput": {"b": 1, "a": "é"}}, "observation": None},
    ]
    s = js("render", "trailFromSteps", steps)["steps"]
    assert [x["tool"] for x in s] == ["get_file_contents", "list_loki_label_values", "pull_request_read", "something_else"]
    assert s[0]["narration"] == "Reading the route first." and s[1]["narration"] == "Checking labels."
    assert s[0]["input"] == "taskboard:server/x.js" and s[1]["input"] == 'app {namespace="taskboard"}'
    assert s[2]["input"] == "taskboard:#38 get_diff" and s[3]["input"] == '{"a": "\\u00e9", "b": 1}'
    assert len(s[0]["result"]) == 200 and s[0]["result"].endswith("…") and s[1]["result"] == "as a list"


def test_flags_any_kagent_fix_branch(js):
    def step(obs, inp=None):
        return {"action": {"tool": "github_list_pull_requests", "toolInput": inp or {"repo": "taskboard"}}, "observation": obs}
    assert js("render", "sawKagentWork", [step('[{"head":{"ref":"agent/taskboard-issue-37"}}]')]) == ["agent/taskboard-issue-37"]
    assert js("render", "sawKagentWork", [step([{"type": "text", "text": "branch agent/taskboard-issue-41"}])]) == ["agent/taskboard-issue-41"]
    assert js("render", "sawKagentWork", [step("nothing", {"repo": "taskboard", "branch": "agent/taskboard-issue-9"}),
                                          step("agent/taskboard-issue-10 and agent/taskboard-issue-9")]) == \
        ["agent/taskboard-issue-9", "agent/taskboard-issue-10"]
    assert js("render", "sawKagentWork", [step("agent/taskboard-issue- has no number"), step(None)]) == []


def test_post_edits_only_our_own_comment(js):
    comments = f"GET {API}/repos/{W.REPO}/issues/41/comments?per_page=100"
    others = [
        {"id": 1, "user": {"login": "arigsela-bug-investigator[bot]", "type": "Bot"}, "body": "<!-- bug-investigator mode=kagent -->\nx"},
        {"id": 2, "user": {"login": "github-actions[bot]", "type": "Bot"}, "body": "<!-- bug-investigator mode=n8n -->\nx"},
        {"id": 3, "user": {"login": "someone-else", "type": "User"}, "body": f"{N8N_MARKER}\ncopied"},
    ]
    base = {f"GET {API}/user": {"body": {"login": ME}}, f"POST {API}/repos/{W.REPO}/issues/41/comments": {"status": 201, "body": {"id": 9}},
            f"PATCH {API}/repos/{W.REPO}/issues/comments/4": {"body": {"id": 4}}}
    reply = js("render", "postVia", W.REPO, 41, "new body", ctx={"fake": "fakeHttp", "spec": {"routes": {**base, comments: {"body": others}}}}, full=True)
    assert reply["value"] == "created"
    assert [(c["method"], c["url"]) for c in reply["calls"] if c["method"] != "GET"] == [("POST", f"{API}/repos/{W.REPO}/issues/41/comments")]
    assert reply["calls"][-1]["body"] == {"body": "new body"}

    mine = others + [{"id": 4, "user": {"login": ME, "type": "User"}, "body": f"{N8N_MARKER}\nold"}]
    reply = js("render", "postVia", W.REPO, 41, "new body", ctx={"fake": "fakeHttp", "spec": {"routes": {**base, comments: {"body": mine}}}}, full=True)
    assert reply["value"] == "edited"
    assert [(c["method"], c["url"]) for c in reply["calls"] if c["method"] != "GET"] == [("PATCH", f"{API}/repos/{W.REPO}/issues/comments/4")]
