# tests/bug_investigator_trigger/test_trigger.py
"""scripts/bug_investigator_trigger.py with fakes for GitHub, Prometheus, A2A and the package:
alert-first dedupe, who may trigger, which issues are skipped, the answer's JSON, the fix
hand-off guard, failure handling, and that the GitHub token never reaches a log line."""
import hashlib
import io
import json
import tarfile
from datetime import datetime, timedelta, timezone

import pytest

from conftest import load_script

t = load_script("bug_investigator_trigger", "scripts/bug_investigator_trigger.py")
a2a = t.a2a

NOW = datetime(2026, 10, 27, 18, 0, tzinfo=timezone.utc)
TOKEN = "ghs_FAKEinstallationTOKEN0123456789abcdef"
PR_URL = "https://github.com/arigsela/taskboard/pull/31"


# ---------------------------------------------------------------- fakes

class FakeGitHub:
    def __init__(self, issues=(), events=None, perms=None, pulls=None):
        self.store = {i["number"]: dict(i) for i in issues}
        self.pulls = pulls or {}
        self.events_by = events or {}
        self.perms = perms or {}
        self.created, self.comments, self.labelled = [], [], []

    def issues(self, repo, *, labels, state="open", sort="created", direction="asc", pages=3):
        want = set(labels.split(","))
        out = [i for i in self.store.values()
               if want <= t.labels_of(i) and (state == "all" or i.get("state", "open") == state)]
        return sorted(out, key=lambda i: i["number"])

    def issue(self, repo, number):
        return self.store[number]

    def events(self, repo, number):
        return self.events_by.get(number, [])

    def permission(self, repo, login):
        if login not in self.perms:
            raise RuntimeError("HTTP 404")
        return self.perms[login]

    def create_issue(self, repo, title, body, labels):
        n = max(self.store, default=40) + 1
        issue = {"number": n, "title": title, "body": body, "state": "open",
                 "labels": [{"name": x} for x in labels], "user": {"login": "arigsela-implementer[bot]"}}
        self.store[n] = issue
        self.created.append(issue)
        return issue

    def comment(self, repo, number, body):
        self.comments.append((number, body))
        return {"id": 1}

    def pull(self, repo, number):
        if number not in self.pulls:
            raise RuntimeError("HTTP 404")
        return {"number": number, "head": {"ref": self.pulls[number]}}

    def add_labels(self, repo, number, labels):
        self.labelled.append((number, list(labels)))
        self.store[number]["labels"] = self.store[number].get("labels", []) + [{"name": x} for x in labels]


def issue(n, *labels, title="Editors can't move tasks", body="", state="open", closed_at=None):
    return {"number": n, "title": title, "body": body, "state": state, "closed_at": closed_at,
            "labels": [{"name": x} for x in labels], "user": {"login": "reporter"}}


def labelled(login):
    return [{"event": "labeled", "label": {"name": "bug"}, "actor": {"login": login}}]


VERIFIED_FIXABLE = {
    "verdict": "likely_cause", "summary": "PR #15 inverted the assignee check (E1, E2).",
    "likely_cause": {"change_ref": "#15", "live_since": "2026-10-27T15:43:00Z",
                     "code_ref": "taskboard/permissions.py:56", "explanation": "the editor branch returns False",
                     "change_verified": {"number": 15}, "code_verified": {"line": 56}},
    "docs_rule": {"path": "docs/permissions.md", "rule_id": "P4", "quote": "Editors may change status"},
    "evidence": [{"id": "E1", "kind": "pr", "ref": "#15"},
                 {"id": "E2", "kind": "file", "ref": "taskboard/permissions.py", "line": 56, "quote": "return False"}],
    "verification": {"verified": 2, "total": 2},
}


class FakeCore:
    def __init__(self, cfg, verified=None, fail=None):
        self.verified = verified if verified is not None else dict(VERIFIED_FIXABLE)
        self.fail = fail
        self.posted, self.gathered, self.findings = [], [], []

    def gather(self, issue, app_dir, deploy_dir):
        if self.fail == "gather":
            raise RuntimeError("boom in gather")
        self.gathered.append((issue, app_dir, deploy_dir))
        return {"ticket": {"created_at": "2026-10-27T16:00:00Z"}, "merged_prs": [1], "deploys": [1, 2]}, \
            "# Facts gathered by script\n\n## Ticket #%d: x\n" % issue

    def verify(self, findings, facts, issue, app_dir, deploy_dir):
        self.findings.append(findings)
        return self.verified

    def render(self, verified, stats):
        return f"<!-- bug-investigator mode=kagent -->\n## investigation\n{verified['verdict']}\n"

    def post(self, issue, body, out_dir):
        self.posted.append((issue, body))
        return ["created kagent"]


def reply(text, state=a2a.STATE_COMPLETED, tool_calls=None):
    return a2a.Reply(kind="task", state=state, context_id="ctx-1", task_id="task-1", text=text,
                     status_text=None, tool_calls=tool_calls or [{"name": "get_file_contents", "args": {}}])


FINDINGS_JSON = json.dumps({"verdict": "likely_cause", "summary": "s", "evidence": [], "hypotheses": []})


def make(gh, *, core=None, prom=None, answers=None, clone=None, logs=None, cfg=None):
    cfg = cfg or t.Config(work_dir="/tmp/bit-test-work")
    asked = []
    answers = dict(answers or {})

    def ask(cfg_, agent, text, timeout):
        asked.append((agent, text))
        value = answers.get(agent, reply(FINDINGS_JSON))
        if isinstance(value, Exception):
            raise value
        return value

    holder = {}

    def core_factory(c):
        holder["core"] = core or FakeCore(c)
        return holder["core"]

    log = (logs if logs is not None else []).append
    scrub = t.Scrubber([TOKEN])
    d = t.Deps(github=gh, prom=prom or (lambda url, expr: []), core=core_factory,
               clone=clone or (lambda cfg, token, work, scrub: ("/w/app", "/w/deploy")),
               ensure_gh=lambda tools: "/usr/bin/gh", ask=ask, log=log, now=lambda: NOW,
               clock=iter(range(0, 10_000, 30)).__next__, scrub=scrub)
    return cfg, d, asked, holder


# ---------------------------------------------------------------- a. alert-first

def series(route, value="7"):
    return {"metric": {"route": route}, "value": [1761580800, value]}


def test_alert_files_one_bug_issue_per_erroring_route():
    gh = FakeGitHub()
    cfg, d, _, _ = make(gh)
    filed = t.alert_first(cfg, gh, lambda url, expr: [series("/api/tasks")], NOW, lambda o: None)
    assert filed and len(gh.created) == 1
    new = gh.created[0]
    assert t.labels_of(new) == {"bug", "from-alert"}
    assert "/api/tasks" in new["title"]
    body = new["body"]
    assert t.ALERT_MARKER.format(route="/api/tasks") in body
    assert "**7**" in body and "2026-10-27T18:00:00Z" in body
    assert "Filed by the bug-investigator trigger from the Taskboard server-errors alert" in body


def test_alert_skips_a_route_with_an_open_alert_issue_or_one_closed_recently():
    open_one = issue(10, "bug", "from-alert", body=t.ALERT_MARKER.format(route="/api/tasks"))
    closed_recent = issue(11, "bug", "from-alert", body=t.ALERT_MARKER.format(route="/api/tasks/{id}"),
                          state="closed", closed_at=(NOW - timedelta(hours=2)).isoformat())
    closed_old = issue(12, "bug", "from-alert", body=t.ALERT_MARKER.format(route="/api/me"),
                       state="closed", closed_at=(NOW - timedelta(hours=7)).isoformat())
    gh = FakeGitHub([open_one, closed_recent, closed_old])
    cfg, _, _, _ = make(gh)
    prom = lambda url, expr: [series("/api/tasks"), series("/api/tasks/{id}"), series("/api/me")]
    t.alert_first(cfg, gh, prom, NOW, lambda o: None)
    assert [i["title"] for i in gh.created] == ["Server errors on /api/me"]


def test_alert_caps_issues_per_run_and_does_nothing_without_series():
    gh = FakeGitHub()
    cfg, _, _, _ = make(gh)
    assert t.alert_first(cfg, gh, lambda url, expr: [], NOW, lambda o: None) == []
    routes = [series(f"/r{i}") for i in range(5)]
    t.alert_first(cfg, gh, lambda url, expr: routes, NOW, lambda o: None)
    assert len(gh.created) == t.ALERT_MAX_PER_RUN


def test_alert_query_is_the_expression_the_prometheus_client_gets():
    seen = []
    gh = FakeGitHub()
    cfg, _, _, _ = make(gh)
    t.alert_first(cfg, gh, lambda url, expr: seen.append((url, expr)) or [], NOW, lambda o: None)
    assert seen == [(cfg.prometheus_url, t.ALERT_EXPR)]
    assert 'namespace="taskboard"' in t.ALERT_EXPR and "taskboard_http_requests_total" in t.ALERT_EXPR


# ---------------------------------------------------------------- b. pick

def test_pick_skips_investigated_rehearsal_and_failed_issues():
    gh = FakeGitHub([issue(1, "bug", "investigated"), issue(2, "bug", "rehearsal"),
                     issue(3, "bug", "investigation-failed"), issue(4, "bug")],
                    events={4: labelled("arigsela")}, perms={"arigsela": {"permission": "admin", "role_name": "admin"}})
    cfg, _, _, _ = make(gh)
    assert t.pick_issue(cfg, gh, lambda o: None)["number"] == 4


@pytest.mark.parametrize("login,perm,ok", [
    ("arigsela", {"permission": "admin", "role_name": "admin"}, True),
    ("maintainer", {"permission": "write", "role_name": "maintain"}, True),
    ("writer", {"permission": "write", "role_name": "write"}, True),
    ("triager", {"permission": "read", "role_name": "triage"}, False),
    ("reader", {"permission": "read", "role_name": "read"}, False),
    ("stranger", None, False),
])
def test_only_someone_with_write_access_may_trigger(login, perm, ok):
    gh = FakeGitHub([issue(5, "bug")], events={5: labelled(login)},
                    perms={login: perm} if perm else {})
    cfg, _, _, _ = make(gh)
    logs = []
    got = t.pick_issue(cfg, gh, logs.append)
    assert (got is not None) is ok
    if not ok:
        assert logs[0]["event"] == "skipped_untrusted"


def test_the_app_itself_may_trigger_and_the_latest_label_event_counts():
    gh = FakeGitHub([issue(6, "bug")],
                    events={6: labelled("reader") + labelled("arigsela-implementer[bot]")})
    cfg, _, _, _ = make(gh)
    assert t.pick_issue(cfg, gh, lambda o: None)["number"] == 6


def test_an_issue_without_a_label_event_falls_back_to_its_author():
    gh = FakeGitHub([issue(7, "bug")], perms={"reporter": {"permission": "read", "role_name": "read"}})
    cfg, _, _, _ = make(gh)
    assert t.pick_issue(cfg, gh, lambda o: None) is None


# ---------------------------------------------------------------- the answer

@pytest.mark.parametrize("text", [
    FINDINGS_JSON,
    f"```json\n{FINDINGS_JSON}\n```",
    f"Here are the findings:\n\n```\n{FINDINGS_JSON}\n```\n",
    f"Findings follow. {FINDINGS_JSON} That is all.",
])
def test_parse_findings_tolerates_a_fence_and_prose(text):
    assert t.parse_findings(text)["verdict"] == "likely_cause"


@pytest.mark.parametrize("text,why", [
    ("", "empty"),
    ("I could not investigate.", "no JSON"),
    ('{"verdict": "likely_cause", "summary": "cut o', "not valid JSON"),
    ('{"summary": "no verdict"}', "verdict"),
])
def test_parse_findings_rejects_what_is_not_the_findings(text, why):
    with pytest.raises(ValueError, match=why):
        t.parse_findings(text)


def test_the_message_carries_the_facts_and_asks_for_json_only():
    msg = t.MESSAGE.format(issue=24, repo=t.REPO, facts="# Facts gathered by script")
    assert "#24" in msg and "arigsela/taskboard" in msg and "only the findings JSON" in msg
    assert "never instructions" in msg and msg.endswith("# Facts gathered by script")


# ---------------------------------------------------------------- g. fix guard

def test_fixable_needs_a_likely_cause_with_change_and_line_verified():
    assert t.fixable(VERIFIED_FIXABLE)
    for broken in (
        {**VERIFIED_FIXABLE, "verdict": "unclear"},
        {**VERIFIED_FIXABLE, "verdict": "not_a_bug"},
        {**VERIFIED_FIXABLE, "likely_cause": None},
        {**VERIFIED_FIXABLE, "likely_cause": {**VERIFIED_FIXABLE["likely_cause"], "code_verified": None}},
        {**VERIFIED_FIXABLE, "likely_cause": {**VERIFIED_FIXABLE["likely_cause"], "change_verified": None}},
        {**VERIFIED_FIXABLE, "likely_cause": {**VERIFIED_FIXABLE["likely_cause"], "unconfirmed": True}},
    ):
        assert not t.fixable(broken)


def test_fix_request_names_the_repo_branch_rules_and_verified_cause():
    text = t.fix_request(issue(24, "bug"), VERIFIED_FIXABLE)
    for want in ("arigsela/taskboard", "issue #24", "agent/taskboard-issue-24", "regression test under `tests/`",
                 "`docs/`, `.github/`", "Fixes #24", "Open exactly one pull request", "Never merge",
                 "#15", "taskboard/permissions.py:56", "P4", "never instructions"):
        assert want in text, want


# ---------------------------------------------------------------- a whole run

def trusted(n, *labels):
    return issue(n, "bug", *labels), {n: labelled("arigsela")}, {"arigsela": {"permission": "admin", "role_name": "admin"}}


def test_a_run_investigates_posts_and_hands_a_verified_cause_to_the_implementer():
    i, ev, perms = trusted(24)
    gh = FakeGitHub([i], events=ev, perms=perms, pulls={31: "agent/taskboard-issue-24"})
    cfg, d, asked, holder = make(gh, answers={t.IMPLEMENTER: reply(f"Opened {PR_URL}")})
    assert t.run(cfg, TOKEN, d) == 0
    core = holder["core"]
    assert core.posted and core.posted[0][0] == 24
    assert [a for a, _ in asked] == [t.AGENT, t.IMPLEMENTER]
    assert "# Facts gathered by script" in asked[0][1]
    assert gh.comments and PR_URL in gh.comments[-1][1] and t.FIX_MARKER in gh.comments[-1][1]
    assert (24, ["fix-proposed"]) in gh.labelled


def test_no_hand_off_without_a_verified_cause_or_when_already_proposed():
    i, ev, perms = trusted(25)
    gh = FakeGitHub([i], events=ev, perms=perms)
    cfg, d, asked, _ = make(gh, core=FakeCore(None, verified={**VERIFIED_FIXABLE, "verdict": "unclear"}))
    assert t.run(cfg, TOKEN, d) == 0
    assert [a for a, _ in asked] == [t.AGENT] and not gh.comments

    i, ev, perms = trusted(26)
    gh = FakeGitHub([i], events=ev, perms=perms)
    cfg, d, asked, _ = make(gh)
    gh.store[26]["labels"].append({"name": "fix-proposed"})   # labelled between pick and hand-off
    t.run(cfg, TOKEN, d)
    assert [a for a, _ in asked] == [t.AGENT]


def test_an_implementer_without_a_pr_or_that_fails_still_comments_and_labels():
    for answer in (reply("no pull request: the fix needs docs/"), a2a.A2AError("HTTP 503")):
        i, ev, perms = trusted(27)
        gh = FakeGitHub([i], events=ev, perms=perms)
        cfg, d, _, _ = make(gh, answers={t.IMPLEMENTER: answer})
        assert t.run(cfg, TOKEN, d) == 0
        assert gh.comments and (27, ["fix-proposed"]) in gh.labelled
        assert PR_URL not in gh.comments[-1][1]


def test_a_bad_answer_posts_nothing_and_labels_the_issue_failed():
    i, ev, perms = trusted(28)
    gh = FakeGitHub([i], events=ev, perms=perms)
    logs = []
    cfg, d, _, holder = make(gh, answers={t.AGENT: reply("I could not do it")}, logs=logs)
    assert t.run(cfg, TOKEN, d) == 1
    assert not holder["core"].posted and not gh.comments
    assert gh.labelled == [(28, ["investigation-failed"])]
    assert any(x["event"] == "investigation_failed" and x["spent"] for x in logs)


@pytest.mark.parametrize("state", ["TASK_STATE_FAILED", "TASK_STATE_INPUT_REQUIRED", "TASK_STATE_WORKING"])
def test_an_agent_task_that_did_not_complete_is_a_spent_failure(state):
    i, ev, perms = trusted(29)
    gh = FakeGitHub([i], events=ev, perms=perms)
    cfg, d, _, holder = make(gh, answers={t.AGENT: reply(None, state=state)})
    assert t.run(cfg, TOKEN, d) == 1
    assert not holder["core"].posted and gh.labelled == [(29, ["investigation-failed"])]


def test_a_failure_before_the_agent_is_asked_is_retried_not_labelled():
    i, ev, perms = trusted(30)
    gh = FakeGitHub([i], events=ev, perms=perms)
    cfg, d, asked, _ = make(gh, core=FakeCore(None, fail="gather"))
    assert t.run(cfg, TOKEN, d) == 1
    assert not asked and not gh.labelled and not gh.comments


def test_a_missing_package_fails_the_run_without_touching_the_issue():
    i, ev, perms = trusted(31)
    gh = FakeGitHub([i], events=ev, perms=perms)
    cfg, d, asked, _ = make(gh)

    def broken(cfg_):
        raise t.Failure("the package has no telemetry module")
    d.core = broken
    logs = []
    d.log = logs.append
    assert t.run(cfg, TOKEN, d) == 1
    assert not asked and not gh.labelled and logs[-1]["event"] == "core_unavailable"


def test_at_most_one_investigation_per_run():
    issues = [trusted(n)[0] for n in (40, 41, 42)]
    ev = {n: labelled("arigsela") for n in (40, 41, 42)}
    gh = FakeGitHub(issues, events=ev, perms={"arigsela": {"permission": "admin", "role_name": "admin"}})
    cfg, d, asked, holder = make(gh, core=FakeCore(None, verified={**VERIFIED_FIXABLE, "verdict": "unclear"}))
    t.run(cfg, TOKEN, d)
    assert [n for n, _ in holder["core"].posted] == [40] and len(asked) == 1


def test_a_broken_alert_step_does_not_block_bug_reports():
    i, ev, perms = trusted(32)
    gh = FakeGitHub([i], events=ev, perms=perms)

    def prom(url, expr):
        raise RuntimeError("Prometheus down")
    cfg, d, _, holder = make(gh, prom=prom, core=FakeCore(None, verified={**VERIFIED_FIXABLE, "verdict": "unclear"}))
    assert t.run(cfg, TOKEN, d) == 1
    assert holder["core"].posted


def test_an_idle_run_exits_zero():
    gh = FakeGitHub()
    cfg, d, asked, _ = make(gh)
    logs = []
    d.log = logs.append
    assert t.run(cfg, TOKEN, d) == 0 and not asked and logs[-1] == {"event": "idle"}


# ---------------------------------------------------------------- the token

def test_the_token_never_reaches_a_log_line_or_a_comment():
    i, ev, perms = trusted(33)
    gh = FakeGitHub([i], events=ev, perms=perms)

    def leaky_clone(cfg, token, work, scrub):
        raise t.Failure(scrub(f"git clone https://x-access-token:{token}@github.com/arigsela/taskboard failed"))
    logs = []
    cfg, d, _, _ = make(gh, clone=leaky_clone, logs=logs)
    t.run(cfg, TOKEN, d)
    out = json.dumps(logs)
    assert TOKEN not in out and "<redacted>" in out


def test_the_scrubber_also_hides_the_basic_auth_form_and_main_logs_scrubbed(capsys, monkeypatch):
    s = t.Scrubber([TOKEN])
    basic = t.git_auth_args(TOKEN)[1].rsplit(" ", 1)[1]
    assert TOKEN not in s(f"x {TOKEN} y") and basic not in s(f"header {basic}")
    # main(): a crash after the token is read prints a scrubbed line only.
    monkeypatch.setattr(t, "run", lambda cfg, token, d: (_ for _ in ()).throw(RuntimeError(f"boom {token}")))
    assert t.main({"GH_TOKEN": TOKEN}) == 1
    printed = capsys.readouterr().out
    assert TOKEN not in printed and "crashed" in printed


def test_clone_keeps_the_token_out_of_the_url():
    calls = []
    t.clone(t.Config(), TOKEN, __import__("pathlib").Path("/tmp/bit-clone-test"),
            run=lambda cmd, scrub: calls.append(cmd), scrub=t.Scrubber([TOKEN]))
    flat = [" ".join(c) for c in calls]
    assert all(TOKEN not in c for c in flat)                      # basic-auth header, not the URL
    assert "https://github.com/arigsela/taskboard.git" in flat[0] and "extraheader" in flat[0]
    assert "--filter=blob:none" in flat[1] and "extraheader" not in flat[1]
    assert flat[2].endswith("sparse-checkout set base-apps/taskboard")


# ---------------------------------------------------------------- gh

def _tarball(member: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(member)
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ensure_gh_refuses_a_download_whose_sha256_differs(tmp_path):
    blob = _tarball(t.GH_MEMBER, b"#!/bin/sh\n")
    with pytest.raises(t.Failure, match="sha256"):
        t.ensure_gh(tmp_path, opener=lambda req, timeout: _Resp(blob), which=lambda name: None)
    assert not (tmp_path / "gh").exists()


def test_ensure_gh_unpacks_the_pinned_binary(tmp_path, monkeypatch):
    blob = _tarball(t.GH_MEMBER, b"#!/bin/sh\necho gh\n")
    monkeypatch.setattr(t, "GH_SHA256", hashlib.sha256(blob).hexdigest())
    monkeypatch.setenv("PATH", "/usr/bin")
    path = t.ensure_gh(tmp_path, opener=lambda req, timeout: _Resp(blob), which=lambda name: None)
    assert (tmp_path / "gh").read_bytes() == b"#!/bin/sh\necho gh\n"
    assert path == str(tmp_path / "gh") and str(tmp_path) in __import__("os").environ["PATH"]


def test_gh_is_pinned_to_a_release_and_a_digest():
    assert t.GH_URL == f"https://github.com/cli/cli/releases/download/v{t.GH_VERSION}/gh_{t.GH_VERSION}_linux_amd64.tar.gz"
    assert len(t.GH_SHA256) == 64


# ---------------------------------------------------------------- config

def test_config_from_env():
    c = t.Config.from_env({"KAGENT_URL": "http://k:8083", "FIX_ENABLED": "false", "AGENT_TIMEOUT": "60",
                           "TRUSTED_BOTS": "a[bot], b[bot]", "GH_TOKEN_FILE": "/x"})
    assert c.kagent_base == "http://k:8083" and c.fix_enabled is False and c.alert_enabled is True
    assert c.agent_timeout == 60.0 and c.trusted_bots == ("a[bot]", "b[bot]") and c.gh_token_file == "/x"
    assert t.Config.from_env({"KAGENT_A2A_BASE": "http://j"}).kagent_base == "http://j"


def test_a_reply_naming_someone_elses_pr_is_not_claimed_as_proposed():
    """taskboard#32: the implementer found the Action's open fix PR #29 for the same bug and opened
    none; the trigger must not say the implementer proposed #29."""
    other = "https://github.com/arigsela/taskboard/pull/29"
    for pulls in ({29: "bug-investigator/issue-26"}, {}):
        i, ev, perms = trusted(32)
        gh = FakeGitHub([i], events=ev, perms=perms, pulls=pulls)
        logs = []
        cfg, d, _, _ = make(gh, answers={t.IMPLEMENTER: reply(f"No new PR: {other} already fixes this.")}, logs=logs)
        assert t.run(cfg, TOKEN, d) == 0
        body = gh.comments[-1][1]
        assert "Fix proposed by" not in body and other in body and "not opened for this issue" in body
        assert (32, ["fix-proposed"]) in gh.labelled
        assert any(x.get("event") == "fix_handoff" and x["outcome"] == "existing pull request" for x in logs)


def test_a_no_pull_request_reply_that_mentions_a_pr_is_quoted_not_linked_as_ours():
    i, ev, perms = trusted(33)
    gh = FakeGitHub([i], events=ev, perms=perms, pulls={29: "bug-investigator/issue-26"})
    logs = []
    cfg, d, _, _ = make(gh, answers={t.IMPLEMENTER: reply("no pull request: #29 (https://github.com/arigsela/taskboard/pull/29) already fixes it")}, logs=logs)
    assert t.run(cfg, TOKEN, d) == 0
    assert "opened no pull request:" in gh.comments[-1][1]
    assert any(x.get("event") == "fix_handoff" and x["outcome"] == "no pull request" for x in logs)
