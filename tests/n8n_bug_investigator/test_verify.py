# tests/n8n_bug_investigator/test_verify.py
"""Parsing and verifying the agent's findings as kagent's trigger does (§T.152, Task 6).

The core's verify.verify runs on files written to disk (the deploy checkout holds only what
kagent's cone-mode sparse checkout holds), with git and gh answered from the case and telemetry
frames built from it. verify.js runs on fakeVerifyCtx answered from the same case, telemetry as
the taskboard-telemetry server would reply. The outputs must be equal, links, windows and drop
reasons included; the one planned difference is output the server truncated.
"""
from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timedelta, timezone

import pytest

import world as W
from conftest import JsError

HEADS = {"app": W.sha("app-head"), "deploy": W.sha("deploy-head")}
REPOS = {"app": W.REPO, "deploy": W.DEPLOY_REPO}
GRAFANA = "https://grafana.arigsela.com"
C1, C2, D1, SIDE = W.sha("c1"), W.sha("c2"), W.sha("d1"), W.sha("side-branch")
TASKS = "\n".join([f"// line {k}" for k in range(1, 12)]
                  + ["  const due = req.query.due_before;", "  return db.tasks.where('due_at', '<', due);"]
                  + [f"// tail {k}" for k in range(14, 31)]) + "\n"
FILES = {
    "app": {
        "server/routes/tasks.js": TASKS,
        "docs/rules.md": "# Rules\n\n- **P4** Due dates are stored in `due_at`.\n  and compared\n  in UTC.\n",
    },
    # Only what kagent's sparse cone checkout of base-apps/taskboard holds: the cone, root files,
    # and files directly in base-apps/. base-apps/other/x.yaml is in the repo but not the checkout.
    "deploy": {
        "base-apps/taskboard/deployment.yaml": "kind: Deployment\nspec:\n  replicas: 1\n",
        "base-apps/taskboard.yaml": "kind: Application\n",
        "README.md": "# kubernetes\n",
    },
}
COMMITS = {"app": [C1, C2], "deploy": [D1]}
PRS = {"7": {"state": "MERGED", "title": "rename due_at", "url": f"https://github.com/{W.REPO}/pull/7"},
       "8": {"state": "OPEN", "title": "wip", "url": f"https://github.com/{W.REPO}/pull/8"}}
ISSUES = {"39": {"state": "CLOSED", "title": "Archive 500s", "url": f"https://github.com/{W.REPO}/issues/39", "labels": ["bug"]},
          "37": {"state": "CLOSED", "title": "rehearsal", "url": f"https://github.com/{W.REPO}/issues/37", "labels": ["bug", "rehearsal"]},
          "41": {"state": "OPEN", "title": "this one", "url": f"https://github.com/{W.REPO}/issues/41", "labels": ["bug"]}}
LOGQL = '{namespace="taskboard"} |= "due_before"'
QUERIES = {
    # expr → what the data holds: log lines, series values, an error, or output too big for the server.
    LOGQL: {"logs": 3},
    '{namespace="taskboard", app="taskboard"} |= "panic"': {"logs": 0},
    'sum(rate(taskboard_http_requests_total{namespace="taskboard",status="500"}[5m]))': {"series": [0, 0]},
    'histogram_quantile(0.99, sum by (le) (rate(taskboard_http_request_duration_seconds_bucket{namespace="taskboard"}[5m])))': {"series": [math.inf, 0.2]},
    '{namespace="kube-system"} |= "x"': {"logs": 1},
    '{namespace="taskboard"} |= "loki-down"': {"error": "Loki: 502 Bad Gateway"},
    '{namespace="taskboard"} |= "huge"': {"logs": 5, "truncated": True},
}


def ev(i, kind, ref, **kw):
    return {"id": i, "kind": kind, "ref": ref, **kw}


def full_findings(when_start="2026-10-28T08:00:00Z", when_end="2026-10-28T09:00:00Z"):
    return {
        "summary": "Due-date filter reads a renamed column.", "verdict": "likely_cause", "confidence": "medium",
        "report": {"who": "u", "what": "empty list", "when_start": when_start, "when_end": when_end, "where": "/tasks",
                   "expected": "tasks", "actual": "[]"},
        "evidence": [
            ev("E1", "commit", C2[:7]), ev("E2", "commit", "zzz"), ev("E3", "commit", W.sha("unknown")),
            ev("E4", "commit", SIDE), ev("E1", "commit", C1),
            ev("E5", "pr", "#7", detail="merged in v2"), ev("E6", "pr", "PR 8", detail="was merged"), ev("E7", "pr", "#999"),
            ev("E8", "issue", "#39"), ev("E9", "issue", "issue 37"),
            ev("E10", "file", "server/routes/tasks.js:13", quote="return db.tasks.where('due_at'"),
            ev("E11", "file", "server/missing.js"), ev("E12", "file", "server/routes/tasks.js", line=99),
            ev("E13", "file", "server/routes/tasks.js", line=10, quote="db.tasks.where"),
            ev("E14", "file", "server/routes/tasks.js", line="9", quote="db.tasks.where"),
            ev("E15", "file", "server/routes/tasks.js#L12"), ev("E16", "doc", "docs/rules.md", quote="Due dates are stored in due_at."),
            ev("E17", "doc", "docs/rules.md:3", quote="not there"), ev("E18", "doc", "docs/rules.md"),
            ev("E19", "file", "base-apps/taskboard.yaml", repo="deploy", quote="kind: Application"),
            ev("E20", "file", "README.md", repo="deploy"), ev("E21", "file", "base-apps/other/x.yaml", repo="deploy"),
            ev("E22", "logql", LOGQL), ev("E23", "logql", '{namespace="taskboard", app="taskboard"} |= "panic"', expect="none"),
            ev("E24", "promql", 'sum(rate(taskboard_http_requests_total{namespace="taskboard",status="500"}[5m]))'),
            ev("E25", "promql", 'histogram_quantile(0.99, sum by (le) (rate(taskboard_http_request_duration_seconds_bucket{namespace="taskboard"}[5m])))'),
            ev("E26", "logql", '{namespace="kube-system"} |= "x"'), ev("E27", "logql", '{namespace="taskboard"} |= "loki-down"'),
            ev("E28", "logql", LOGQL, start="2026-10-27T10:00:00Z", end="2026-10-27T11:00:00Z"),
            ev("E29", "screenshot", "img.png"), ev("E30", "file", "../etc/passwd"), ev("E31", "file", "./server/routes/tasks.js:12"),
            ev("E32", "file", "server/../server/routes/tasks.js", line=13), "not a dict",
        ],
        "hypotheses": [{"id": "H1", "status": "supported", "evidence_ids": ["E10", "E2"]},
                       {"id": "H2", "status": "ruled_out", "evidence_ids": ["E2"]}, "skip me"],
        "likely_cause": {"change_ref": "#7", "code_ref": "server/routes/tasks.js:13", "explanation": "renamed column"},
        "docs_rule": {"path": "docs/rules.md", "quote": "**P4** Due dates are stored in `due_at`. and compared in UTC."},
        "similar_past_bugs": [{"issue": "#39"}, {"issue": "#41"}, {"issue": "#37"}, "x"],
        "could_not_check": [], "next_step": "fix the column name",
    }


def cases():
    out = {"evidence_mix": full_findings()}
    f = full_findings()
    f["hypotheses"] = [{"id": "H1", "status": "untested", "evidence_ids": []}]
    f["likely_cause"] = {"change_ref": SIDE, "code_ref": "server/nope.js"}
    out["no_supported_and_cause_fails"] = f
    f = full_findings()
    f["hypotheses"] = [{"id": "H1", "status": "untested", "evidence_ids": ["E10"]}]
    out["cause_unconfirmed"] = f
    f = full_findings()
    f["verdict"] = "not_a_bug"
    f["docs_rule"] = {"path": "docs/rules.md", "quote": "this sentence is not in the doc"}
    out["not_a_bug_without_rule"] = f
    f = full_findings()
    f["verdict"] = "not_a_bug"
    out["not_a_bug_with_rule"] = f
    f = full_findings(when_start=None, when_end=None)
    f["report"].pop("when_start")
    f["likely_cause"] = {"change_ref": "neither", "code_ref": "server/routes/tasks.js"}
    out["no_report_window"] = f
    f = full_findings(when_end=None)
    out["start_only_window"] = f
    out["not_a_dict"] = None
    return out


# --------------------------------------------------------------------------- the two sides


def python_verify(core, tmp_path, findings, issue_created):
    dirs = {}
    for key in ("app", "deploy"):
        root = tmp_path / key
        for path, text in FILES[key].items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text)
        (root / "server").mkdir(parents=True, exist_ok=True)
        dirs[str(root)] = key
    CommandError = core.util.CommandError
    windows = []

    def run(cmd):
        cmd = list(cmd)
        if cmd[0] == "git":
            key, args = dirs[cmd[2]], cmd[3:]
            if args == ["rev-parse", "HEAD"]:
                return HEADS[key] + "\n"
            ref = args[-1].replace("^{commit}", "").lower()
            hits = [c for c in COMMITS[key] + [HEADS[key]] if c.startswith(ref)]
            if len(hits) != 1:
                raise CommandError("fatal: Not a valid object name")
            return "" if args[0] == "cat-file" else hits[0] + "\n"
        if cmd[1:3] == ["pr", "view"]:
            st = PRS.get(cmd[3])
            if not st:
                raise CommandError("no pull requests found")
            return json.dumps({"number": int(cmd[3]), "mergedAt": None, **st})
        if cmd[1:3] == ["issue", "view"]:
            st = ISSUES.get(cmd[3])
            if not st:
                raise CommandError("no issue found")
            return json.dumps({"number": int(cmd[3]), **{**st, "labels": [{"name": x} for x in st["labels"]]}})
        raise CommandError(f"unexpected {cmd[:3]}")

    def gquery(kind, expr, s, e):
        windows.append((kind, expr, s, e))
        q = QUERIES[expr]
        if "error" in q:
            raise core.grafana.GrafanaError(q["error"])
        if "logs" in q:
            return {"results": {"A": {"frames": [{"schema": {"fields": [{"name": "Line", "type": "string"}]},
                                                  "data": {"values": [["l"] * q["logs"]]}}]}}}
        return {"results": {"A": {"frames": [{"schema": {"fields": [{"name": "Time", "type": "time"}, {"name": "Value", "type": "number"}]},
                                              "data": {"values": [[0] * len(q["series"]), q["series"]]}}]}}}

    app = next(d for d, k in dirs.items() if k == "app")
    deploy = next(d for d, k in dirs.items() if k == "deploy")
    ctx = core.verify.Ctx(mode="full", repo=W.REPO, app_dir=app, deploy_dir=deploy, deploy_repo=W.DEPLOY_REPO, issue=41,
                          issue_created=issue_created, namespace="taskboard", runner=run, gquery=gquery, telemetry_mode="direct")
    return core.verify.verify(findings, ctx), windows


def server_reply(core, expr):
    """What taskboard-telemetry answers for an expression: its scope refusal, an error, or JSON text."""
    kind = "promql" if not expr.startswith("{") else "logql"
    bad = core.scope.check_query(kind, expr, "taskboard")
    if bad:
        return {"isError": True, "text": f"refused: {bad}"}
    q = QUERIES[expr]
    if "error" in q:
        return {"isError": True, "text": q["error"]}
    if "logs" in q:
        text = json.dumps([{"timestamp": "t", "labels": {}, "line": "l"}] * q["logs"])
        if q.get("truncated"):
            text = text[: len(text) // 2] + " ...[truncated]"
        return {"isError": False, "text": text}
    vals = ["+Inf" if v == math.inf else str(v) for v in q["series"]]
    return {"isError": False, "text": json.dumps({"resultType": "matrix", "series": [{"labels": {}, "values": [{"time": "t", "value": v} for v in vals]}]})}


def js_verify(core, js, findings, now, issue_created):
    spec = {"issue": 41, "issueCreated": issue_created, "now": W.iso(now), "repos": REPOS, "heads": HEADS, "secrets": [],
            "grafanaUrl": GRAFANA, "commits": COMMITS, "files": FILES,
            "prs": PRS, "issues": {k: v for k, v in ISSUES.items()},
            "queries": {expr: server_reply(core, expr) for expr in QUERIES}}
    reply = js("verify", "verifyVia", findings, ctx={"fake": "fakeVerifyCtx", "spec": spec}, full=True)
    return reply["value"], [c for c in reply["calls"] if "expr" in c]


def without(verified, ids):
    v = copy.deepcopy(verified)
    v["evidence"] = [e for e in v["evidence"] if e["id"] not in ids]
    v["verification"]["dropped"] = [d for d in v["verification"]["dropped"] if d["id"] not in ids]
    return v


@pytest.mark.parametrize("name", list(cases()))
def test_verify_matches_core(core, js, frozen_now, tmp_path, name):
    findings = cases()[name]
    created = W.iso(frozen_now - timedelta(hours=1))
    py, py_windows = python_verify(core, tmp_path, copy.deepcopy(findings), created)
    out, js_windows = js_verify(core, js, copy.deepcopy(findings), frozen_now, created)
    assert out == py
    # Every query the core ran, JS ran over the same window. The scope-refused one the core
    # never sends; JS sends it and the server refuses it.
    assert [(k, e, ms_iso(s), ms_iso(en)) for k, e, s, en in py_windows] == \
        [(c["kind"], c["expr"], c["start"], c["end"]) for c in js_windows if "kube-system" not in c["expr"]]


def ms_iso(ms: int) -> str:
    return W.iso(datetime.fromtimestamp(ms / 1000, timezone.utc))


def test_cases_exercise_every_check(core, frozen_now, tmp_path):
    # Guards the case: if these stop holding, test_verify_matches_core proves less.
    py, _ = python_verify(core, tmp_path, full_findings(), W.iso(frozen_now))
    kept = {e["id"] for e in py["evidence"]}
    reasons = {d["id"]: d["reason"] for d in py["verification"]["dropped"]}
    assert {"E1", "E5", "E8", "E10", "E13", "E15", "E16", "E19", "E20", "E22", "E23", "E25", "E28", "E31", "E32"} <= kept
    assert {"E2", "E3", "E4", "E6", "E7", "E9", "E11", "E12", "E14", "E17", "E18", "E21", "E24", "E26", "E27", "E29", "E30"} <= set(reasons)
    assert reasons["E26"].startswith("out of scope: ") and reasons["E27"].startswith("Loki/Prometheus query failed: ")
    assert any(d["reason"] == "duplicate id" for d in py["verification"]["dropped"])
    assert py["hypotheses"][1]["status"] == "untested" and py["verdict"] == "likely_cause"
    assert [b["issue"] for b in py["similar_past_bugs"]] == ["#39"]


def test_truncated_telemetry_is_not_counted(core, js, frozen_now):
    f = full_findings()
    f["evidence"] = [ev("E1", "logql", '{namespace="taskboard"} |= "huge"', expect="none")]
    out, _ = js_verify(core, js, f, frozen_now, W.iso(frozen_now))
    assert out["evidence"] == []
    assert out["verification"]["dropped"][0]["reason"] == \
        "Loki/Prometheus query failed: the telemetry server truncated the output; could not count it"


def test_secrets_are_scrubbed(core, js, frozen_now):
    f = full_findings()
    f["summary"] = "token pat-0123456789abc leaked"
    spec_secret = "pat-0123456789abc"
    spec = {"issue": 41, "issueCreated": None, "now": W.iso(frozen_now), "repos": REPOS, "heads": HEADS, "secrets": [spec_secret],
            "grafanaUrl": GRAFANA, "commits": COMMITS, "files": FILES, "prs": PRS, "issues": ISSUES,
            "queries": {expr: server_reply(core, expr) for expr in QUERIES}}
    out = js("verify", "verifyVia", f, ctx={"fake": "fakeVerifyCtx", "spec": spec})
    assert spec_secret not in json.dumps(out) and "[redacted]" in out["summary"]


# ---------------------------------------------------------------------------- parse_findings


ACCEPTED = [
    '{"verdict": "unclear", "summary": "x"}',
    'Here are the findings:\n```json\n{"verdict": "likely_cause", "n": 1}\n```\nThanks.',
    'The answer follows. {"verdict": "not_a_bug", "a": {"b": [1, 2]}}',
    '{"verdict": "unclear"} and some trailing words {not json}',
    '{"verdict": "unclear", "quote": "a } brace and a { brace and \\"quotes\\""}',
    '{"verdict": "unclear", "text": "Fällig — 期限 😀"}',
]
REJECTED = ["", "   ", "Agent stopped due to max iterations.", "[1, 2, 3]", '{"summary": "no verdict"}',
            '{"verdict": "unclear", "summary": "cut off mid-str', "```json\n{\"verdict\": \n```"]


def test_parse_findings_matches_trigger(js, trigger):
    for text in ACCEPTED:
        assert js("verify", "parseFindings", text) == trigger.parse_findings(text), text


def test_parse_findings_rejects(js, trigger):
    for text in REJECTED:
        with pytest.raises(ValueError) as py:
            trigger.parse_findings(text)
        with pytest.raises(JsError) as e:
            js("verify", "parseFindings", text)
        fixed = str(py.value).split(":")[0].split(" (")[0]
        assert e.value.message.startswith(fixed), (text, str(py.value), e.value.message)


# ------------------------------------------------------------------- makeVerifyCtx over HTTP

API = W.API
CFG = {"repo": W.REPO, "deployRepo": W.DEPLOY_REPO, "deployPath": W.DEPLOY_PATH, "namespace": "taskboard"}
FACTS = {"app_head": HEADS["app"], "deploy_head": HEADS["deploy"], "ticket": {"number": 41, "created_at": "2026-10-28T10:00:00Z"}}
MCP = "http://taskboard-telemetry-mcp.kagent.svc.cluster.local:3000/mcp"


def http(routes):
    return {"fake": "fakeHttp", "spec": {"routes": routes}}


def test_make_verify_ctx_reads_at_the_heads(js, frozen_now):
    now = W.iso(frozen_now)
    file = lambda repo, path, ref: (f"GET {API}/repos/{repo}/contents/{W.enc(path)}?ref={ref}",  # noqa: E731
                                    {"body": {"type": "file", "encoding": "base64", "content": W.b64("a\nb\r\nc")}})
    routes = dict([file(W.REPO, "server/x.js", HEADS["app"]), file(W.DEPLOY_REPO, "base-apps/taskboard/a.yaml", HEADS["deploy"]),
                   file(W.DEPLOY_REPO, "README.md", HEADS["deploy"]), file(W.DEPLOY_REPO, "base-apps/taskboard.yaml", HEADS["deploy"])])
    for key, path in [("app", "server/x.js"), ("deploy", "base-apps/taskboard/a.yaml"), ("deploy", "README.md"), ("deploy", "base-apps/taskboard.yaml")]:
        reply = js("verify", "ctxCall", CFG, FACTS, now, "fileLines", key, path, ctx=http(routes), full=True)
        assert reply["value"] == ["a", "b", "c"], path
        assert reply["calls"][0]["url"].endswith(f"?ref={HEADS[key]}")
    for path in ["base-apps/other/x.yaml", "docs/x.md", "base-apps/taskboard2/x.yaml"]:
        reply = js("verify", "ctxCall", CFG, FACTS, now, "fileLines", "deploy", path, ctx=http(routes), full=True)
        assert reply["value"] is None and reply["calls"] == [], path
    reply = js("verify", "ctxCall", CFG, FACTS, now, "fileLines", "app", "nope.js",
               ctx=http({f"GET {API}/repos/{W.REPO}/contents/nope.js?ref={HEADS['app']}": {"status": 404, "body": {"message": "Not Found"}}}), full=True)
    assert reply["value"] is None


def test_unreachable_commit_is_dropped(js, frozen_now):
    now = W.iso(frozen_now)
    full = W.sha("fix-on-pr-branch")
    for status, want in [("diverged", None), ("ahead", None), ("behind", full), ("identical", full)]:
        routes = {f"GET {API}/repos/{W.REPO}/commits/{full[:7]}": {"body": {"sha": full}},
                  f"GET {API}/repos/{W.REPO}/compare/{HEADS['app']}...{full}": {"body": {"status": status}}}
        assert js("verify", "ctxCall", CFG, FACTS, now, "commit", "app", full[:7], ctx=http(routes)) == want, status
    routes = {f"GET {API}/repos/{W.REPO}/commits/{full[:7]}": {"status": 422, "body": {"message": "No commit found"}}}
    assert js("verify", "ctxCall", CFG, FACTS, now, "commit", "app", full[:7], ctx=http(routes)) is None


def _mcp_reply(rid, text, sse=True):
    msg = {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}], "isError": False}}
    return {"headers": {"content-type": "text/event-stream"}, "body": f"event: message\ndata: {json.dumps(msg)}\n\n"}


def test_one_mcp_session_per_verify(js, frozen_now):
    init = {"headers": {"content-type": "text/event-stream", "mcp-session-id": "s-9"},
            "body": 'data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18"}}\n\n'}
    logs = json.dumps([{"line": "a"}, {"line": "b"}])
    routes = {f"POST {MCP}": [init, {"status": 202}, _mcp_reply(2, logs), _mcp_reply(3, logs), {"status": 500, "body": "boom"}, _mcp_reply(5, logs)],
              f"DELETE {MCP}": {"status": 202}}
    f = full_findings()
    f["evidence"] = [ev(f"E{k}", "logql", LOGQL) for k in range(1, 5)]
    f["likely_cause"] = None
    f["docs_rule"] = None
    f["similar_past_bugs"] = []
    reply = js("verify", "verifyReal", CFG, FACTS, W.iso(frozen_now), f, ctx=http(routes), full=True)
    methods = [(c["method"], (c["body"] or {}).get("method")) for c in reply["calls"]]
    assert methods.count(("POST", "initialize")) == 1 and methods.count(("POST", "tools/call")) == 4
    assert methods[-1] == ("DELETE", None)
    v = reply["value"]
    assert [e["id"] for e in v["evidence"]] == ["E1", "E2", "E4"]
    assert v["verification"]["dropped"][0]["reason"].startswith("Loki/Prometheus query failed: ")
    call = next(c for c in reply["calls"] if (c["body"] or {}).get("method") == "tools/call")
    assert call["body"]["params"]["name"] == "query_loki_logs"
    assert call["body"]["params"]["arguments"]["datasourceUid"] == "loki" and call["body"]["params"]["arguments"]["limit"] == 100
