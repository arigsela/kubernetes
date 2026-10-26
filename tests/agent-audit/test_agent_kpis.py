"""scripts/agent-kpis.py (§T.144): the facts behind the Agent KPIs dashboard.

Read from the same real kagent 1.0 rows the audit is tested on (fixtures/kagent-v1/events.json,
see test_agent_audit_v1.py for the scenarios), so a KPI never disagrees with the audit.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kpis = _load("agent_kpis", REPO / "scripts" / "agent-kpis.py")
aa = kpis.aa
gen = _load("gen_agent_audit_cronjob", REPO / "scripts" / "gen-agent-audit-cronjob.py")

FIXTURE = json.loads((HERE / "fixtures" / "kagent-v1" / "events.json").read_text())
S = FIXTURE["scenarios"]
TAXONOMY = REPO / "base-apps/admission-policies/agent-capability-taxonomy.yaml"
GATED = aa.load_gated_tools(REPO, TAXONOMY)
# Every fixture row was written between 18:39 and 18:40 UTC on 2026-10-11.
AFTER = datetime(2026, 10, 11, 19, 5, tzinfo=timezone.utc)


def _b(v):
    return base64.b64decode(v) if v is not None else None


def sql_rows():
    return [(datetime.fromisoformat(at), sid, _b(sdata), name, ns, _b(data))
            for at, sid, sdata, name, ns, data in FIXTURE["rows"]]


@pytest.fixture(scope="module")
def db():
    return kpis.facts_from_db(sql_rows(), GATED)


def _facts(db, kind):
    return [f for f in db["facts"] if f["kind"] == kind]


# --- what the database says -----------------------------------------------------------------------

def test_every_fixture_task_ends_completed_and_the_gated_ones_say_they_paused(db):
    tasks = _facts(db, "task")
    assert tasks and {t["state"] for t in tasks} == {"completed"}
    by_agent = {}
    for t in tasks:
        by_agent.setdefault(t["agent"], []).append(t["paused"])
    # approved-delete, rejected-delete and mixed each waited for a decision on the way.
    assert sorted(by_agent["k8s-agent"]).count(True) == 3
    assert not any(by_agent["homelab-knowledge"]) and not any(by_agent["k8s-reader"])
    assert all(t["duration_s"] >= 0 for t in tasks)


def test_a_task_still_waiting_for_a_decision_is_not_reported():
    """pending-patch never got an answer: it has not ended, so it is neither a success nor a failure."""
    tasks, _, _ = kpis.collect(sql_rows())
    pending = [t for t in tasks.values() if t["session"] == S["pending-patch"]]
    assert pending and all(kpis.task_fact(t) is None for t in pending)


def test_a_task_that_paused_and_then_finished_counts_once():
    t0 = AFTER
    task = {"task_id": "t", "session": "s", "agent": "k8s-agent", "caller": "person", "first": t0,
            "states": [(t0, "submitted"), (t0 + timedelta(seconds=1), "working"),
                       (t0 + timedelta(seconds=2), "input-required"), (t0 + timedelta(minutes=5), "working"),
                       (t0 + timedelta(minutes=6), "completed"), (t0 + timedelta(minutes=7), "completed")]}
    fact = kpis.task_fact(task)
    assert fact["state"] == "completed" and fact["paused"] is True
    assert fact["duration_s"] == 360.0 and fact["_at"] == t0 + timedelta(minutes=6)
    task["states"].append((t0 + timedelta(minutes=8), "failed"))
    assert kpis.task_fact(task)["state"] == "failed"
    task["states"].append((t0 + timedelta(minutes=9), "working"))
    assert kpis.task_fact(task) is None


def test_approvals_carry_the_decision_and_how_long_it_waited(db):
    approvals = _facts(db, "approval")
    assert sorted(a["decision"] for a in approvals) == ["approved", "approved", "rejected"]
    assert {a["tool"] for a in approvals} == {"k8s_delete_resource"}
    assert all(a["agent"] == "k8s-agent" and a["caller"] == "person" for a in approvals)
    assert all(0 <= a["wait_s"] < 5 for a in approvals)


def test_the_window_rollup_agrees_with_the_audit(db):
    records = aa.iter_calls_v1(list(aa.v1_rows(sql_rows())))
    start = datetime(2026, 10, 11, 18, tzinfo=timezone.utc)
    (window,) = [line for line in kpis.window_lines(start, start + timedelta(hours=1), db=db, changes=[],
                                                    sources={"db": "ok", "github": "ok"})
                 if line["kind"] == "window"]
    assert window["ungated_writes"] == len(aa.report_ungated(records, GATED)) == 3
    assert window["tool_calls"] == sum(r["kind"] == "call" and r["tool"] != aa.CONFIRM_TOOL for r in records)
    assert window["scenario_tool_calls"] == window["tool_calls"]      # no eval gate session in the fixture
    assert window["run"] == "hourly" and window["window_start_unix"] == int(start.timestamp())
    assert window["sessions"] == len(S) and window["sessions_unknown"] == 1   # idle-deleted: no session row
    assert window["db"] == "ok" and window["check"] == "agent-kpi"


def test_facts_outside_the_window_are_left_to_their_own_hour(db):
    start = datetime(2026, 10, 11, 17, tzinfo=timezone.utc)
    lines = kpis.window_lines(start, start + timedelta(hours=1), db=db, changes=[], sources={"db": "ok", "github": "ok"})
    assert [line["kind"] for line in lines] == ["window"]
    assert lines[0]["tool_calls"] == 0 and lines[0]["sessions"] == 0


# --- callers, never identities ----------------------------------------------------------------------

@pytest.mark.parametrize("creator,cls", [
    ("ari@example.test", "person"),
    ("admin@kagent.dev", "ui"),
    ("system:serviceaccount:kagent:eval-gate", "eval"),
    ("system:serviceaccount:dispatcher:dispatcher", "dispatcher"),
    ("dispatcher", "dispatcher"),
    ("system:serviceaccount:slack-bridge:slack-bridge", "slack"),
    ("system:serviceaccount:hk-demo:hk-demo", "demo"),
    ("system:serviceaccount:backstage:default", "backstage"),
    ("system:serviceaccount:kagent:docs-checker-trigger", "docs-check"),
    ("system:serviceaccount:other:thing", "automation"),
    ("", "unknown"),
    (None, "unknown"),
    ("someone", "other"),
])
def test_caller_classes(creator, cls):
    assert kpis.caller_class(creator) == cls


def test_the_session_creator_is_read_from_the_session_proto():
    creators = {kpis.session_creator(sdata) for _, _, sdata, _, _, _ in sql_rows() if sdata}
    assert creators == {"ari@example.test"}


def _session_proto(creator: str) -> bytes:
    """A kagent Session with only field 2 (creator) set."""
    raw = creator.encode()
    return bytes([0x12, len(raw)]) + raw


def test_the_eval_gates_sessions_are_classed_eval_and_left_out_of_scenario_tool_calls():
    """The fixture's sessions are all a person's; re-own every session as the eval gate and the
    scenario count must drop to zero while the total stays."""
    eval_sa = _session_proto("system:serviceaccount:kagent:eval-gate")
    rows = [(at, sid, eval_sa, name or "homelab-knowledge", ns or "kagent", data)
            for at, sid, _, name, ns, data in sql_rows()]
    db = kpis.facts_from_db(rows, GATED)
    assert {f["caller"] for f in db["facts"]} == {"eval"}
    start = datetime(2026, 10, 11, 18, tzinfo=timezone.utc)
    (window,) = [line for line in kpis.window_lines(start, start + timedelta(hours=1), db=db, changes=[],
                                                    sources={"db": "ok", "github": "ok"}) if line["kind"] == "window"]
    assert window["tool_calls"] > 0 and window["scenario_tool_calls"] == 0
    assert window["sessions_eval"] == window["sessions"]


def _main(argv, **kw):
    out = io.StringIO()
    rc = kpis.main(argv, out=out, **kw)
    return rc, [json.loads(line) for line in out.getvalue().splitlines()]


class _Cursor:
    def __init__(self, rows):
        self.rows, self.params = rows, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        assert "WHERE e.created_at >= %(since)s::timestamptz" in sql
        self.params = params

    def fetchall(self):
        return self.rows


class _Conn(_Cursor):
    def cursor(self):
        return _Cursor(self.rows)


def _github(pages):
    calls = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def opener(req, timeout):
        calls.append(req.full_url)
        return _Resp(json.dumps(pages[len(calls) - 1] if len(calls) <= len(pages) else []).encode())
    return opener, calls


def test_the_output_is_argument_free(capsys):
    """No tool argument, no email: names, counts, states and seconds only."""
    opener, _ = _github([[]])
    rc, lines = _main(["--taxonomy", str(TAXONOMY)], now=AFTER, connect=lambda: _Conn(sql_rows()), opener=opener)
    assert rc == 0 and {line["kind"] for line in lines} == {"task", "approval", "window"}
    text = json.dumps(lines)
    for leaked in ("audit-fixture", "scratch", "@", "configmap"):
        assert leaked not in text, leaked
    assert all(line["window_start"] == "2026-10-11T18:00:00Z" for line in lines)


def test_a_database_failure_is_reported_and_github_still_reports(capsys):
    def broken():
        raise OSError("connection refused")
    opener, _ = _github([[]])
    rc, lines = _main(["--taxonomy", str(TAXONOMY)], now=AFTER, connect=broken, opener=opener)
    assert rc == 1
    assert lines == [{"kind": "window", "db": "error", "github": "ok", "check": "agent-kpi", "run": "hourly",
                      "window_start": "2026-10-11T18:00:00Z", "window_end": "2026-10-11T19:00:00Z",
                      "window_start_unix": int(datetime(2026, 10, 11, 18, tzinfo=timezone.utc).timestamp())}]
    assert "connection refused" in capsys.readouterr().err


def test_a_missing_dsn_is_a_database_error_not_an_exit(monkeypatch):
    monkeypatch.delenv("AGENT_AUDIT_DSN", raising=False)
    opener, _ = _github([[]])
    rc, lines = _main(["--taxonomy", str(TAXONOMY)], now=AFTER, opener=opener)
    assert rc == 1 and lines[-1]["db"] == "error"


# --- windows ----------------------------------------------------------------------------------------

def test_the_hourly_window_is_the_hour_that_just_ended():
    now = datetime(2026, 10, 26, 14, 5, 17, tzinfo=timezone.utc)
    assert kpis.windows(now) == [(datetime(2026, 10, 26, 13, tzinfo=timezone.utc),
                                  datetime(2026, 10, 26, 14, tzinfo=timezone.utc))]


def _loki(done=None, earliest=None, fail=False):
    """A fake Loki for backfill_end: answers the 'already backfilled' and 'earliest hour' queries."""
    asked = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def opener(req, timeout):
        if fail:
            raise OSError("connection refused")
        asked.append(req.full_url)
        value = done if 'run%3D%22backfill%22' in req.full_url else earliest
        result = [] if value is None else [{"metric": {}, "value": [0, str(value)]}]
        return _Resp(json.dumps({"status": "success", "data": {"resultType": "vector", "result": result}}).encode())
    return opener, asked


def _hourly_runs(first_run: datetime, now: datetime):
    """The windows every hourly run printed from first_run until now (runs at :05)."""
    out, t = [], first_run
    while t <= now:
        out += kpis.windows(t)
        t += timedelta(hours=1)
    return out


@pytest.mark.parametrize("late_hours", [0, 1, 5, 30])
def test_a_late_backfill_never_reprints_an_hour_the_hourly_job_printed(late_hours):
    """The review's case: hourly runs at 11:05, 12:05, 13:05 and the backfill at 13:20. However late
    the backfill runs, it ends at the earliest hour any hourly run printed."""
    first_run = datetime(2026, 10, 26, 11, 5, tzinfo=timezone.utc)
    now = first_run + timedelta(hours=late_hours, minutes=15)
    printed = _hourly_runs(first_run, now)
    opener, _ = _loki(earliest=printed[0][0].timestamp())
    spans = kpis.backfill_windows(kpis.backfill_end(now, "http://loki", opener), 168)
    assert len(spans) == 168 and not set(spans) & set(printed)
    assert spans[-1][1] == printed[0][0]                     # contiguous with the hourly job's first hour
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:]))


def test_a_backfill_before_any_hourly_run_ends_at_this_hour():
    """Nothing printed yet: the next hourly run prints the hour that is running now, so the
    backfill covers everything before it."""
    now = datetime(2026, 10, 26, 14, 20, tzinfo=timezone.utc)
    opener, _ = _loki()
    end = kpis.backfill_end(now, "http://loki", opener)
    assert end == datetime(2026, 10, 26, 14, tzinfo=timezone.utc)
    later = kpis.windows(datetime(2026, 10, 26, 15, 5, tzinfo=timezone.utc))
    assert later[0][0] == end


@pytest.mark.parametrize("case", ["already-backfilled", "loki-down", "minute-5"])
def test_a_backfill_refuses_rather_than_risk_counting_twice(case):
    now = datetime(2026, 10, 26, 14, 5 if case == "minute-5" else 20, tzinfo=timezone.utc)
    opener, _ = _loki(done=168 if case == "already-backfilled" else None, fail=case == "loki-down")
    with pytest.raises(kpis.BackfillRefused):
        kpis.backfill_end(now, "http://loki", opener)


def test_a_refused_backfill_prints_nothing_and_exits_2(capsys):
    opener, _ = _loki(done=168)
    rc, lines = _main(["--no-db", "--backfill-hours", "168"], now=datetime(2026, 10, 26, 14, 20, tzinfo=timezone.utc),
                      opener=opener)
    assert rc == 2 and lines == []
    assert "backfill refused" in capsys.readouterr().err


def test_a_backfill_marks_its_lines():
    loki_opener, _ = _loki(earliest=datetime(2026, 10, 26, 13, tzinfo=timezone.utc).timestamp())
    gh_opener, _ = _github([[]])

    def opener(req, timeout):
        return (loki_opener if "/loki/api/v1/query" in req.full_url else gh_opener)(req, timeout)
    rc, lines = _main(["--no-db", "--backfill-hours", "3"], now=datetime(2026, 10, 26, 14, 20, tzinfo=timezone.utc),
                      opener=opener)
    assert rc == 0 and [line["window_start"] for line in lines] == [
        "2026-10-26T10:00:00Z", "2026-10-26T11:00:00Z", "2026-10-26T12:00:00Z"]
    assert {line["run"] for line in lines} == {"backfill"}


# --- GitHub -----------------------------------------------------------------------------------------

def _pr(n, login, created, closed, merged=True, updated=None):
    return {"number": n, "user": {"login": login}, "created_at": created, "closed_at": closed,
            "merged_at": closed if merged else None, "updated_at": updated or closed}


def test_changes_in_the_window_with_author_and_lead_time():
    start, end = datetime(2026, 10, 26, 13, tzinfo=timezone.utc), datetime(2026, 10, 26, 14, tzinfo=timezone.utc)
    page = [
        _pr(5, "arigsela", "2026-10-26T13:10:00Z", "2026-10-26T13:40:00Z"),
        _pr(4, "arigsela-implementer[bot]", "2026-10-26T09:00:00Z", "2026-10-26T13:30:00Z"),
        _pr(3, "arigsela-renovate[bot]", "2026-10-25T13:00:00Z", "2026-10-26T13:20:00Z", merged=False),
        _pr(2, "arigsela-hk-demo-release[bot]", "2026-10-26T12:00:00Z", "2026-10-26T14:00:00Z"),  # next hour
        _pr(1, "someone", "2026-10-26T11:00:00Z", "2026-10-26T12:59:59Z"),                     # last hour
    ]
    opener, calls = _github([page])
    changes = kpis.fetch_changes("arigsela/kubernetes", start, end, opener=opener)
    assert [(c["number"], c["author"], c["outcome"], c["lead_s"]) for c in changes] == [
        (5, "owner", "merged", 1800.0), (4, "implementer", "merged", 16200.0), (3, "renovate", "closed", 87600.0)]
    assert len(calls) == 1 and "state=closed&sort=updated&direction=desc" in calls[0]


def test_paging_stops_once_a_page_ends_before_the_window():
    start, end = datetime(2026, 10, 26, 13, tzinfo=timezone.utc), datetime(2026, 10, 26, 14, tzinfo=timezone.utc)
    full = [_pr(100 + i, "arigsela", "2026-10-26T13:00:00Z", "2026-10-26T13:30:00Z") for i in range(100)]
    old = [_pr(i, "arigsela", "2026-10-20T13:00:00Z", "2026-10-20T13:30:00Z") for i in range(100)]
    opener, calls = _github([full, old, old])
    changes = kpis.fetch_changes("arigsela/kubernetes", start, end, opener=opener)
    assert len(changes) == 100 and len(calls) == 2


@pytest.mark.parametrize("login,cls", [("arigsela", "owner"), ("arigsela-implementer[bot]", "implementer"),
                                       ("arigsela-renovate[bot]", "renovate"), ("renovate[bot]", "renovate"),
                                       ("dependabot[bot]", "bot"), ("someone", "other"), (None, "other")])
def test_author_classes(login, cls):
    assert kpis.author_class(login) == cls


def test_github_only_mode_skips_the_database():
    page = [_pr(7, "arigsela", "2026-10-26T13:10:00Z", "2026-10-26T13:40:00Z")]
    opener, _ = _github([page])
    rc, lines = _main(["--no-db"], now=datetime(2026, 10, 26, 14, 5, tzinfo=timezone.utc), opener=opener)
    assert rc == 0
    assert [line["kind"] for line in lines] == ["change", "window"]
    assert lines[-1]["db"] == "skipped" and "tool_calls" not in lines[-1]
    assert lines[0]["at"] == "2026-10-26T13:40:00Z"


# --- the CronJobs ------------------------------------------------------------------------------------

def _pod(cronjob):
    return cronjob["spec"]["jobTemplate"]["spec"]["template"]


def _kpi_jobs():
    docs = gen.build("script", "taxonomy", "acks", "risk", "kpis")
    return docs[0], {d["metadata"]["name"]: d for d in docs if d["metadata"]["name"].startswith("agent-kpis")}


def test_the_kpi_jobs_ship_the_script_beside_the_audit():
    code_cm, jobs = _kpi_jobs()
    assert code_cm["data"]["agent-kpis.py"] == "kpis" and code_cm["data"]["agent-audit.py"] == "script"
    assert set(jobs) == {"agent-kpis", "agent-kpis-backfill"}


def test_the_hourly_job_prints_the_hour_that_just_ended_without_retries():
    _, jobs = _kpi_jobs()
    hourly = jobs["agent-kpis"]
    assert hourly["spec"]["schedule"] == "5 * * * *" and not hourly["spec"].get("suspend")
    assert hourly["spec"]["jobTemplate"]["spec"]["backoffLimit"] == 0
    # A pod that starts after the next hour would print that run's hour too.
    assert hourly["spec"]["startingDeadlineSeconds"] < 3300 and hourly["spec"]["timeZone"] == "Etc/UTC"
    script = _pod(hourly)["spec"]["containers"][0]["args"][0]
    assert "python /opt/audit/agent-kpis.py --taxonomy /opt/audit/agent-capability-taxonomy.yaml\n" in script
    assert "--backfill-hours" not in script
    # Alloy tails Running pods only: the pod waits after printing, then exits with the script's code.
    assert script.index("RC=$?") < script.index("sleep ") < script.index("exit $RC")


def test_the_backfill_is_on_demand_and_feeds_the_same_stream():
    _, jobs = _kpi_jobs()
    backfill = jobs["agent-kpis-backfill"]
    assert backfill["spec"]["suspend"] is True
    assert "--backfill-hours 168" in _pod(backfill)["spec"]["containers"][0]["args"][0]
    for job in jobs.values():
        # The dashboard reads {namespace="postgresql", app="agent-kpis"}; the audit's Grafana
        # rules read app=agent-audit and must never see these lines.
        assert _pod(job)["metadata"]["labels"]["app"] == "agent-kpis"
        env = {e["name"]: e for e in _pod(job)["spec"]["containers"][0]["env"]}
        assert env["AUDIT_DB"]["valueFrom"]["secretKeyRef"]["key"] == "audit-database-v1"
