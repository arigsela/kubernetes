# tests/bug_investigator_trigger/test_log_contract.py
"""The trigger's log lines are the hk-demo "Bug investigator" tab's only source (the repo it
investigates is private): every field of the contract in
docs/superpowers/specs/2026-10-27-hk-demo-bug-investigator-tab-design.md, "Log contract", is
present, a line stays under 16 KB, emails are masked and the GitHub token never appears."""
import io
import json
from contextlib import redirect_stdout

import test_trigger as tt

t, a2a = tt.t, tt.a2a
TOKEN = tt.TOKEN
RUN = "bug-investigator-trigger-29885406"

VERIFIED = {
    **tt.VERIFIED_FIXABLE,
    "confidence": "high",
    "next_step": "Revert line 56 and ask alex@example.com to re-test.",
    "hypotheses": [{"id": "H1", "statement": "PR #15 inverted the check", "status": "supported",
                    "evidence_ids": ["E1", "E2"]},
                   {"id": "H2", "statement": "a config change", "status": "ruled_out", "evidence_ids": ["E1"]}],
    "verification": {"verified": 2, "total": 3,
                     "dropped": [{"id": "E3", "kind": "logql", "ref": "permission_denied P3 for sam",
                                  "reason": "out of scope: no stream selector"},
                                 {"id": "similar_past_bugs", "kind": "issue", "ref": "#7", "reason": "closed by nothing"}]},
}

RAW = {"task": {"id": "task-1", "contextId": "ctx-1", "status": {"state": a2a.STATE_COMPLETED}, "artifacts": [
    {"parts": [
        {"data": {"id": "c1", "name": "get_file_contents", "args": {"owner": "arigsela", "repo": "taskboard",
                                                                     "path": "taskboard/permissions.py"}}},
        {"data": {"id": "c1", "name": "get_file_contents", "response": {"content": "SECRET FILE BODY"}}},
        {"data": {"id": "c2", "name": "query_loki_logs", "args": {"datasourceUid": "loki",
                                                                  "logql": '{namespace="taskboard"} |= "P4"'}}},
        {"data": {"id": "c2", "name": "query_loki_logs", "response": {"isError": True, "content": "refused"}}},
        {"data": {"id": "c3", "name": "pull_request_read", "args": {"repo": "taskboard", "pullNumber": 15,
                                                                    "method": "get_diff"}}},
        {"text": tt.json.dumps({**{k: v for k, v in VERIFIED.items() if k != "verification"}})},
    ]}]}}


class Gh(tt.FakeGitHub):
    def list_comments(self, repo, number):
        return [{"body": "<!-- bug-investigator mode=kagent -->\nhi",
                 "html_url": f"https://github.com/{repo}/issues/{number}#issuecomment-1"}]


def run_once(answers=None, verified=None, body="Editors can't move tasks. Ping lee@example.org.", labels=()):
    i = tt.issue(24, "bug", *labels, body=body)
    i["created_at"] = "2026-10-27T17:13:14Z"
    gh = Gh([i], events={24: tt.labelled("arigsela")},
            perms={"arigsela": {"permission": "admin", "role_name": "admin"}},
            pulls={31: "agent/taskboard-issue-24"})
    reply = tt.reply(tt.FINDINGS_JSON)
    reply.raw = RAW
    impl = tt.reply(f"Opened {tt.PR_URL}")
    impl.context_id = "ctx-impl"
    logs = []
    cfg = t.Config(work_dir="/tmp/bit-test-work", run_id=RUN)
    cfg, d, _, _ = tt.make(gh, core=tt.FakeCore(cfg, verified=verified or dict(VERIFIED)),
                           answers=answers or {t.AGENT: reply, t.IMPLEMENTER: impl}, logs=logs, cfg=cfg)
    status = t.run(cfg, TOKEN, d)
    return status, {x["event"]: x for x in logs}, logs


def test_every_line_carries_the_run():
    _, _, logs = run_once()
    assert logs and all(x.get("run") == RUN for x in logs)


def test_investigating_answered_and_gathered_fields():
    _, ev, _ = run_once(labels=("from-alert",))
    inv = ev["investigating"]
    assert inv["alert"] is True and inv["filed_by"] == "arigsela" and inv["opened_at"] == "2026-10-27T17:13:14Z"
    assert isinstance(ev["gathered"]["seconds"], float) and ev["gathered"]["prs"] == 1
    ans = ev["answered"]
    assert ans["session"] == "ctx-1" and ans["verdict"] == "likely_cause"
    assert ans["trail"] == [
        {"tool": "get_file_contents", "target": "taskboard:taskboard/permissions.py", "ok": True},
        {"tool": "query_loki_logs", "target": '{namespace="taskboard"} |= "P4"', "ok": False},
        {"tool": "pull_request_read", "target": "taskboard:#15 get_diff", "ok": None},
    ]
    assert "SECRET FILE BODY" not in json.dumps(ans)          # a tool result is never logged


def test_trail_falls_back_to_reply_tool_calls_without_the_raw_task():
    r = tt.reply("x", tool_calls=[{"name": "search_code", "args": {"query": "due_before"}}])
    assert t.trail_of(r) == [{"tool": "search_code", "target": "due_before", "ok": None}]
    many = tt.reply("x", tool_calls=[{"name": "get_commit", "args": {"sha": "a" * 40}}] * 60)
    assert len(t.trail_of(many)) == t.TRAIL_MAX


def test_posted_carries_the_verified_findings():
    _, ev, _ = run_once()
    p = ev["posted"]
    for key in ("verdict", "verified", "total", "session", "done", "title", "ticket", "confidence", "summary",
                "likely_cause", "docs_rule", "hypotheses", "evidence", "comment_url", "next_step"):
        assert key in p, key
    assert p["likely_cause"] == {"change_ref": "#15", "live_since": "2026-10-27T15:43:00Z",
                                 "code_ref": "taskboard/permissions.py:56"}
    assert p["docs_rule"] == {"path": "docs/permissions.md", "rule_id": "P4", "quote": "Editors may change status"}
    assert p["hypotheses"][0] == {"id": "H1", "statement": "PR #15 inverted the check", "status": "supported",
                                  "evidence": ["E1", "E2"]}
    assert [e["id"] for e in p["evidence"]] == ["E1", "E2", "E3"]
    assert p["evidence"][1] == {"id": "E2", "kind": "file", "ref": "taskboard/permissions.py:56",
                                "verified": True, "reason": None}
    assert p["evidence"][2]["verified"] is False and p["evidence"][2]["reason"].startswith("out of scope")
    assert p["dropped_other"] == [{"id": "similar_past_bugs", "ref": "#7", "reason": "closed by nothing"}]
    assert p["comment_url"].endswith("#issuecomment-1")


def test_fix_handoff_names_the_implementer_session_and_pr():
    _, ev, _ = run_once()
    f = ev["fix_handoff"]
    assert f["session"] == "ctx-impl" and f["outcome"] == tt.PR_URL
    assert f["pr"] == {"url": tt.PR_URL, "number": 31, "head": "agent/taskboard-issue-24"}
    assert f["existing_pr"] is None and isinstance(f["seconds"], int)


def test_failure_names_its_stage():
    _, ev, _ = run_once(answers={t.AGENT: tt.reply("I could not do it")})
    assert ev["investigation_failed"]["stage"] == "answer" and ev["investigation_failed"]["run"] == RUN


def test_emails_are_masked_and_the_token_never_printed():
    body = f"Ping lee@example.org, token {TOKEN}"
    _, ev, _ = run_once(body=body)
    assert "l•••@example.org" in ev["posted"]["ticket"] and "lee@" not in ev["posted"]["ticket"]
    assert TOKEN not in ev["posted"]["ticket"]
    assert "a•••@example.com" in ev["posted"]["next_step"]
    # log_line masks and scrubs the whole printed line too
    scrub = t.Scrubber([TOKEN])
    buf = io.StringIO()
    with redirect_stdout(buf):
        t.log_line({"event": "x", "note": f"mail bob@corp.example {TOKEN}"}, scrub)
    out = buf.getvalue()
    assert TOKEN not in out and "bob@" not in out
    assert json.loads(out)["note"] == "mail b•••@corp.example <redacted>"


def test_a_line_stays_under_16_kb_and_is_never_split():
    huge = {**VERIFIED, "summary": "s" * 5000,
            "evidence": [{"id": f"E{i}", "kind": "logql", "ref": "q" * 150} for i in range(1, 400)],
            "hypotheses": [{"id": f"H{i}", "statement": "h" * 190, "status": "untested", "evidence_ids": []}
                           for i in range(80)]}
    _, ev, _ = run_once(verified=huge)
    buf = io.StringIO()
    with redirect_stdout(buf):
        t.log_line(ev["posted"])
    lines = buf.getvalue().splitlines()
    assert len(lines) == 1 and len(lines[0].encode()) < 16 * 1024
    out = json.loads(lines[0])
    assert out["event"] == "posted" and out["evidence_truncated"] is True and out["evidence"]
    assert out["hypotheses"] and out["likely_cause"] and out["verdict"] == "likely_cause"
