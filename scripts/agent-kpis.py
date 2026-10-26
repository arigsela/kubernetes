#!/usr/bin/env python3
"""Agent-scenario KPIs (§T.144): what the Agent KPIs dashboard reads from kagent's sessions
database and from GitHub, one argument-free JSON line per fact.

    AGENT_AUDIT_DSN=... ./scripts/agent-kpis.py                      # the clock hour that just ended
    AGENT_AUDIT_DSN=... ./scripts/agent-kpis.py --backfill-hours 168 # once, after the first deploy
    ./scripts/agent-kpis.py --no-db --backfill-hours 168             # GitHub only (a laptop try-out)

WHY
---
The playbook the dashboard follows (platformengineering.org, "Agent-first IDP: how big tech and
the clouds are preparing platforms for agents", 2026-10-16, practice 9) says to measure an agent
platform by agent-scenario KPIs instead of portal usage: the share of capabilities safely open to
agents, intent success rate, time to a safe change, cost per scenario, approval cycle time, policy
violation rate, reduction in support load, and the share of scenarios completed without manual
escalation. Prometheus and Loki already hold cost, tool calls, adapter refusals, admission denials
and the dispatcher's verdicts (scripts/gen-agent-dashboards.py reads them directly). Three facts
live only here:

  - how each agent task ended: kagent's A2A task states. A trace only knows a span errored; a task
    that paused for an approval and then finished is one success, not an error and a success.
  - how long a gated call waited for its decision: the HITL request and the decision, both
    stored with the session (the same records scripts/agent-audit.py reads).
  - how long a proposed change took to land: pull requests on the public repo (GitHub's API,
    unauthenticated, like the dispatcher's open-PR count).

WHAT IT PRINTS (stdout -> Alloy -> Loki, {namespace="postgresql", app="agent-kpis"})
--------------
One JSON line per fact, each with check=agent-kpi and the window it belongs to:

  kind=task      a task that reached a final state in the window: agent, caller, state
                 (completed | failed | canceled | rejected), paused (it waited for input on the
                 way, an approval for example), duration_s (first event to final state), task_id
  kind=approval  a decision on a gated call made in the window: agent, tool, caller, decision
                 (approved | rejected), wait_s (request to decision; absent when the request is
                 older than the lookback)
  kind=change    a pull request merged or closed in the window: number, author (owner |
                 implementer | renovate | bot | other), outcome (merged | closed), lead_s
                 (opened to merged or closed)
  kind=window    the window's roll-up: tool_calls, gated_calls, ungated_writes (a write or
                 destructive tool that ran without an approved decision, as agent-audit.py
                 --ungated judges it), sessions per caller, and db / github = ok | error

Windows are whole clock hours, [start, end). The CronJob runs at :05 and prints the hour that just
ended, so consecutive runs neither overlap nor leave gaps. A backfill prints the N hours before
that one, so it never overlaps the hourly job either; run it once. A failed run loses its hour:
the CronJob has no retries, because a retry after a partial print would count the hour twice.
Loki stamps each line when it is printed, not when the fact happened, so a backfill lands at one
instant: totals over 7 days are right, a per-hour bar chart shows one spike.

ARGUMENT-FREE, LIKE agent-audit.py --summary
--------------------------------------------
Never a tool argument, a response, a prompt or an answer: names, counts, states and seconds. The
caller is a class (person | ui | dispatcher | slack | eval | demo | backstage | docs-check |
automation | unknown | other), never the email or service account the mesh verified. It reads the
database as the SELECT-only kagent_audit_ro role, like every audit job (AGENT_AUDIT_DSN, see
agent-audit.py connect()).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import struct
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_audit():
    """scripts/agent-audit.py: its decoder, record extraction and ungated rule, so a KPI never
    disagrees with the audit about what happened. The CronJob mounts both files side by side."""
    spec = importlib.util.spec_from_file_location("agent_audit", HERE / "agent-audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aa = _load_audit()

CHECK = "agent-kpi"

# lf.a2a.v1 TaskState (a2a-go a2apb/v1), the numbers kagent stores.
STATES = {1: "submitted", 2: "working", 3: "completed", 4: "failed", 5: "canceled",
          6: "input-required", 7: "rejected", 8: "auth-required"}
FINAL = {"completed", "failed", "canceled", "rejected"}
PAUSED = {"input-required", "auth-required"}

# How far back a window's facts are looked up: a task that ends in the window began earlier, and a
# decision's request can be days older (an approval never expires). Eight days covers a weekend
# away; an older request still counts its decision, without wait_s.
LOOKBACK = timedelta(days=8)

# The principals the kagent waypoint writes into x-user-id (base-apps/kagent/request-authentication.yaml),
# as kagent stores them on the session (Session.creator). Never printed: only the class is.
CALLERS = {
    "system:serviceaccount:kagent:eval-gate": "eval",
    "system:serviceaccount:dispatcher:dispatcher": "dispatcher",
    "system:serviceaccount:slack-bridge:slack-bridge": "slack",
    "system:serviceaccount:hk-demo:hk-demo": "demo",
    "system:serviceaccount:kagent:docs-checker-trigger": "docs-check",
    # The controller's identity for a request without x-user-id: kagent's own UI, through the
    # UI's own principal (go/core/internal/httpserver/auth/authn.go).
    "admin@kagent.dev": "ui",
    # The dispatcher without a token file (dev only, DISPATCH_USER).
    "dispatcher": "dispatcher",
}

GITHUB_REPO = "arigsela/kubernetes"
OWNER_LOGIN = "arigsela"
IMPLEMENTER_LOGIN = "arigsela-implementer[bot]"
RENOVATE_LOGINS = {"arigsela-renovate[bot]", "renovate[bot]"}


# ----------------------------------------------------------------- small pieces

def caller_class(creator: str | None) -> str:
    if not creator:
        return "unknown"
    if creator in CALLERS:
        return CALLERS[creator]
    if creator.startswith("system:serviceaccount:backstage:"):
        return "backstage"
    if creator.startswith("system:serviceaccount:"):
        return "automation"
    if "@" in creator:
        return "person"
    return "other"


def agent_name(agent_id: str | None) -> str:
    """kagent__NS__homelab_knowledge -> homelab-knowledge (agent-audit.py v1_agent_id's form)."""
    if not agent_id or agent_id == aa.UNATTRIBUTED:
        return "unattributed"
    return agent_id.split("__NS__", 1)[-1].replace("_", "-")


def session_creator(session_data) -> str | None:
    """Session.creator (field 2 of kagent's Session proto): the principal that opened it."""
    if not session_data:
        return None
    try:
        for number, wire, value in aa._fields(bytes(session_data)):
            if number == 2 and wire == 2:
                return aa._text(value)
    except (aa.ProtoError, struct.error):
        return None
    return None


def _ts(value) -> datetime:
    """A datetime from the database, or an ISO string from a record; naive means UTC."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hour_floor(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def windows(now: datetime, backfill_hours: int = 0) -> list[tuple[datetime, datetime]]:
    """The hour that just ended; or, for a backfill, the N hours before it (never overlapping it)."""
    end = hour_floor(now)
    if backfill_hours:
        end -= timedelta(hours=1)
        return [(end - timedelta(hours=i + 1), end - timedelta(hours=i)) for i in reversed(range(backfill_hours))]
    return [(end - timedelta(hours=1), end)]


# ------------------------------------------------------------------- tasks

def _task_id(event: dict) -> str | None:
    for key in ("status_update", "artifact_update"):
        tid = (event.get(key) or {}).get("task_id")
        if tid:
            return tid
    task = event.get("task") or {}
    if task.get("id"):
        return task["id"]
    return (event.get("message") or {}).get("task_id") or None


def _state(event: dict) -> str | None:
    for key in ("status_update", "task"):
        status = (event.get(key) or {}).get("status") or {}
        if "state" in status:
            return STATES.get(status["state"], f"unknown-{status['state']}")
    return None


def collect(rows) -> tuple[dict, dict, dict]:
    """From V1_EVENTS_SELECT rows: tasks by id, the caller class of each session, and each
    session's first event time."""
    tasks: dict[str, dict] = {}
    callers: dict[str, str] = {}
    agents: dict[str, str] = {}
    first_seen: dict[str, datetime] = {}
    for created_at, session_id, session_data, agent_n, namespace, data in rows:
        at = _ts(created_at)
        if session_id not in callers:
            callers[session_id] = caller_class(session_creator(session_data))
            agents[session_id] = agent_name(aa.v1_agent_id(session_data, agent_n, namespace))
            first_seen[session_id] = at
        try:
            event = aa.decode_event_v1(data)
        except (aa.ProtoError, struct.error):
            continue
        tid = _task_id(event)
        if not tid:
            continue
        task = tasks.get(tid)
        if task is None:
            task = tasks[tid] = {"task_id": tid, "session": session_id, "first": at, "states": []}
        state = _state(event)
        if state is not None:
            task["states"].append((at, state))
    for task in tasks.values():
        task["agent"], task["caller"] = agents[task["session"]], callers[task["session"]]
    return tasks, callers, first_seen


def task_fact(task: dict) -> dict | None:
    """The task's outcome if it has reached a final state, else None (still running or paused)."""
    if not task["states"]:
        return None
    last = task["states"][-1][1]
    if last not in FINAL:
        return None
    ended = task["states"][-1][0]
    for at, state in reversed(task["states"]):   # when it reached the state it ended in
        if state != last:
            break
        ended = at
    return {"kind": "task", "agent": task["agent"], "caller": task["caller"], "task_id": task["task_id"],
            "state": last, "paused": any(s in PAUSED for _, s in task["states"]),
            "duration_s": round((ended - task["first"]).total_seconds(), 1), "_at": ended}


# --------------------------------------------------------------- approvals

def approval_facts(records: list[dict], callers: dict[str, str]) -> list[dict]:
    """One fact per decision on a gated call, with how long it waited."""
    requests = {(r["session"], r["call_id"]): r for r in records
                if r["kind"] == "call" and r["tool"] == aa.CONFIRM_TOOL}
    out = []
    for r in records:
        if r["kind"] != "approval":
            continue
        req = requests.get((r["session"], r["approval_id"]))
        decided = _ts(r["at"])
        fact = {"kind": "approval", "agent": agent_name(r["agent"]),
                "tool": r["tool"] or (req or {}).get("for_tool") or "unknown",
                "caller": callers.get(r["session"], "unknown"), "decision": r["decision"], "_at": decided}
        if req is not None:
            fact["wait_s"] = round((decided - _ts(req["at"])).total_seconds(), 1)
        out.append(fact)
    return out


# ----------------------------------------------------------------- GitHub

def author_class(login: str | None) -> str:
    if login == OWNER_LOGIN:
        return "owner"
    if login == IMPLEMENTER_LOGIN:
        return "implementer"
    if login in RENOVATE_LOGINS:
        return "renovate"
    if login and login.endswith("[bot]"):
        return "bot"
    return "other"


def fetch_changes(repo: str, start: datetime, end: datetime, opener=urllib.request.urlopen,
                  max_pages: int = 10) -> list[dict]:
    """Pull requests closed in [start, end), newest updates first. A PR closed in the window was
    updated no earlier than its close, so paging stops at the first page that ends before start."""
    out = []
    for page in range(1, max_pages + 1):
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/pulls?state=closed&sort=updated&direction=desc"
            f"&per_page=100&page={page}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "agent-kpis/1"})
        with opener(req, timeout=15) as resp:
            items = json.loads(resp.read() or b"[]")
        for pr in items:
            if not pr.get("closed_at"):
                continue
            closed = _ts(pr["closed_at"])
            if not start <= closed < end:
                continue
            merged = pr.get("merged_at")
            done = _ts(merged) if merged else closed
            out.append({"kind": "change", "number": pr["number"],
                        "author": author_class((pr.get("user") or {}).get("login")),
                        "outcome": "merged" if merged else "closed",
                        "lead_s": round((done - _ts(pr["created_at"])).total_seconds(), 1), "_at": closed})
        if len(items) < 100 or _ts(items[-1]["updated_at"]) < start:
            break
    return out


# ------------------------------------------------------------------- report

def facts_from_db(rows, gated: set[str]) -> dict:
    """Everything the windows need from the database, computed once (a backfill has 168 windows)."""
    tasks, callers, first_seen = collect(rows)
    records = aa.iter_calls_v1(list(aa.v1_rows(rows)))
    calls = [r for r in records if r["kind"] == "call" and r["tool"] != aa.CONFIRM_TOOL]
    return {
        "facts": [f for f in (task_fact(t) for t in tasks.values()) if f] + approval_facts(records, callers),
        "calls": [(_ts(r["at"]), r["tool"] in gated) for r in calls],
        "ungated": [_ts(f["at"]) for f in aa.report_ungated(records, gated)],
        "sessions": [(at, callers[s]) for s, at in first_seen.items()],
    }


def window_lines(start: datetime, end: datetime, *, db: dict | None, changes: list[dict],
                 sources: dict) -> list[dict]:
    def inside(at: datetime) -> bool:
        return start <= at < end

    facts = []
    if db is not None:
        facts += [f for f in db["facts"] if inside(f["_at"])]
    if sources.get("github") == "ok":
        facts += [c for c in changes if inside(c["_at"])]
    facts.sort(key=lambda f: (f["_at"], f["kind"]))

    rollup = {"kind": "window", **sources}
    if db is not None:
        calls = [gated for at, gated in db["calls"] if inside(at)]
        sessions: dict[str, int] = {}
        for at, caller in db["sessions"]:
            if inside(at):
                sessions[caller] = sessions.get(caller, 0) + 1
        rollup.update(tool_calls=len(calls), gated_calls=sum(calls),
                      ungated_writes=sum(inside(at) for at in db["ungated"]),
                      sessions=sum(sessions.values()),
                      **{f"sessions_{caller.replace('-', '_')}": n for caller, n in sorted(sessions.items())})
    out = []
    for f in facts + [rollup]:
        line = {k: v for k, v in f.items() if not k.startswith("_")}
        if "_at" in f:
            line["at"] = _iso(f["_at"])
        line.update(check=CHECK, window_start=_iso(start), window_end=_iso(end))
        out.append(line)
    return out


def main(argv=None, *, now: datetime | None = None, connect=None, opener=urllib.request.urlopen,
         out=sys.stdout) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill-hours", type=int, default=0,
                    help="print the N whole hours before the one the hourly job prints (run once)")
    ap.add_argument("--no-db", action="store_true", help="skip the sessions database (GitHub only)")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPO", GITHUB_REPO))
    ap.add_argument("--taxonomy", type=Path, help="the capability taxonomy ConfigMap (the CronJob mounts it)")
    ap.add_argument("--repo-root", type=Path, default=HERE.parent)
    args = ap.parse_args(argv)
    if args.backfill_hours < 0 or args.backfill_hours > 24 * 30:
        ap.error("--backfill-hours must be between 0 and 720")

    now = now or datetime.now(timezone.utc)
    spans = windows(now, args.backfill_hours)
    first, last = spans[0][0], spans[-1][1]
    sources = {"db": "skipped" if args.no_db else "ok", "github": "ok"}
    db, changes = None, []

    if not args.no_db:
        try:
            gated = aa.load_gated_tools(args.repo_root, args.taxonomy)
            with (connect or aa.connect)() as conn:
                with conn.cursor() as cur:
                    cur.execute(aa.V1_EVENTS_SELECT + """
                        WHERE e.created_at >= %(since)s::timestamptz AND e.created_at < %(until)s::timestamptz
                        ORDER BY e.created_at, e.sequence
                    """, {"since": first - LOOKBACK, "until": last})
                    rows = cur.fetchall()
            db = facts_from_db(rows, gated)
        # SystemExit too: agent-audit.py's connect() exits when AGENT_AUDIT_DSN is unset.
        except (Exception, SystemExit) as err:  # noqa: BLE001 - the GitHub half still reports
            sources["db"] = "error"
            print(f"agent-kpis: sessions database: {type(err).__name__}: {str(err)[:200]}", file=sys.stderr)
    try:
        changes = fetch_changes(args.repo, first, last, opener=opener)
    except Exception as err:  # noqa: BLE001 - the database half still reports
        sources["github"] = "error"
        print(f"agent-kpis: GitHub: {type(err).__name__}: {str(err)[:200]}", file=sys.stderr)

    for start, end in spans:
        for line in window_lines(start, end, db=db, changes=changes, sources=sources):
            print(json.dumps(line, sort_keys=True), file=out)
    out.flush()
    # A source that did not answer must not look like a quiet hour.
    return 1 if "error" in sources.values() else 0


if __name__ == "__main__":
    raise SystemExit(main())
