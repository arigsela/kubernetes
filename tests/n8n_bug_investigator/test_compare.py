# tests/n8n_bug_investigator/test_compare.py
"""Comparing kagent's and n8n's comments on the same issues (§T.152, Task 10)."""
from __future__ import annotations

import copy
import importlib.util
from datetime import timedelta

import test_verify as TV
import world as W
from conftest import REPO

MODEL = "claude-sonnet-5-5"
STATS = {"turns": 7, "duration_ms": 125_400}


def compare():
    spec = importlib.util.spec_from_file_location("compare_bug_investigations", REPO / "scripts/compare-bug-investigations.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def verified(core, tmp_path, now):
    out, _ = TV.python_verify(core, tmp_path, copy.deepcopy(TV.cases()["evidence_mix"]), W.iso(now - timedelta(hours=1)))
    return out


def kagent_comment(core, trigger, v):
    """The trigger's PackageCore.render: the core's render relabelled as kagent's."""
    r = core.render
    body = r.render(v, "full", MODEL, STATS, "")
    return (body.replace(r.MARKER.format(mode="full"), r.MARKER.format(mode=trigger.MODE), 1)
            .replace(f"## {r.HEADERS['full']}", "## 🔎 Bug investigation — kagent platform agent", 1)
            .replace("<sub>full mode · ", "<sub>kagent/bug-investigator · ", 1))


def n8n_comment(js, v, contaminated):
    return js("render", "renderComment", v, {"issueNumber": 41, "model": MODEL, "stats": STATS, "trail": None, "contaminated": contaminated})


def test_parses_kagent_and_n8n_comments(core, js, trigger, frozen_now, tmp_path):
    v = verified(core, tmp_path, frozen_now)
    c = compare()
    common = {"verdict": "likely_cause", "confidence": "medium", "verified": v["verification"]["verified"],
              "total": v["verification"]["total"], "change": "PR #7", "code": "server/routes/tasks.js#L13", "turns": 7, "duration_s": 125}
    assert c.parse_comment(kagent_comment(core, trigger, v)) == {"mode": "kagent", **common, "contaminated": None}
    assert c.parse_comment(n8n_comment(js, v, [])) == {"mode": "n8nagent", **common, "contaminated": []}
    flagged = n8n_comment(js, v, ["agent/taskboard-issue-37", "agent/taskboard-issue-41"])
    assert c.parse_comment(flagged)["contaminated"] == ["agent/taskboard-issue-37", "agent/taskboard-issue-41"]


def test_ignores_other_comments():
    c = compare()
    for body in ["<!-- bug-investigator mode=full -->\n## 🔎 Bug investigation — full\n", "<!-- bug-investigator mode=n8n -->\nbaseline",
                 "<!-- bug-investigator fix -->\nOpened #42.", "<!-- bug-investigator alert route=/tasks -->", "Thanks!", ""]:
        assert c.parse_comment(body) is None, body


def test_table_has_a_row_per_system(core, js, trigger, frozen_now, tmp_path):
    v = verified(core, tmp_path, frozen_now)
    c = compare()
    comments = [{"body": "plain comment"}, {"body": n8n_comment(js, v, ["agent/taskboard-issue-37"])},
                {"body": kagent_comment(core, trigger, v)}, {"body": "<!-- bug-investigator fix -->\nx"}]
    rows = c.rows_for(41, comments)
    assert [r["system"] for r in rows] == ["kagent", "n8nagent"] and all(r["issue"] == 41 for r in rows)
    lines = c.table(rows).splitlines()
    assert lines[0].startswith("| issue | system | verdict |") and len(lines) == 4
    assert lines[2].startswith("| 41 | kagent | likely_cause | medium |") and lines[3].endswith("| agent/taskboard-issue-37 |")
    assert c.rows_for(42, [{"body": "nothing here"}]) == [{"issue": 42, "system": "kagent", "missing": True},
                                                         {"issue": 42, "system": "n8nagent", "missing": True}]
