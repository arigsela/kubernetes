# tests/dispatch/test_dispatch.py
"""scripts/dispatch.py: table → decision → investigation → finding, with a fake agent."""
from pathlib import Path
import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("dispatch", REPO / "scripts" / "dispatch.py")
dp = importlib.util.module_from_spec(_spec)
sys.modules["dispatch"] = dp   # dataclasses resolve the module by name at class creation
_spec.loader.exec_module(dp)
a2a = dp.a2a

TABLE = {
    "version": 1,
    "limits": {"investigations_per_hour": 2, "repeat_after_hours": 6},
    "rules": [
        {"id": "cpu", "source": "grafana", "match": {"alertname": "Pod CPU starved"},
         "route": "agent:k8s-reader", "prompt": "Pod {labels[namespace]}/{labels[pod]}: {annotations[summary]}",
         "why": "test"},
        {"id": "quiet", "source": "grafana", "match": {"alertname": "Quiet"}, "route": "none", "why": "test"},
    ],
}


def alert(name="Pod CPU starved", status="firing", fingerprint="fp1", **labels):
    labels.setdefault("namespace", "n8n")
    labels.setdefault("pod", "n8n-0")
    return {"status": status, "fingerprint": fingerprint, "startsAt": "2026-10-12T10:00:00Z",
            "labels": {"alertname": name, **labels}, "annotations": {"summary": "waiting 40% for CPU"}}


# ---------------------------------------------------------------- table

def test_load_table_accepts_the_shape():
    assert dp.load_table(json.dumps(TABLE))["limits"]["investigations_per_hour"] == 2


@pytest.mark.parametrize("mutate, message", [
    (lambda t: t.update(version=2), "version"),
    (lambda t: t["limits"].pop("repeat_after_hours"), "repeat_after_hours"),
    (lambda t: t["rules"][0].pop("why"), "why"),
    (lambda t: t["rules"][0].update(route="human:slack"), "route"),
    (lambda t: t["rules"][0].pop("prompt"), "prompt"),
    (lambda t: t["rules"].append(dict(t["rules"][1])), "duplicate"),
])
def test_load_table_rejects_bad_tables(mutate, message):
    table = json.loads(json.dumps(TABLE))
    mutate(table)
    with pytest.raises(dp.TableError, match=message):
        dp.load_table(json.dumps(table))


def test_prompt_list_is_joined_with_spaces():
    table = json.loads(json.dumps(TABLE))
    table["rules"][0]["prompt"] = ["Pod {labels[pod]}:", " look at it. "]
    assert dp.load_table(json.dumps(table))["rules"][0]["prompt"] == "Pod {labels[pod]}: look at it."
    table["rules"][0]["prompt"] = ["ok", 3]
    with pytest.raises(dp.TableError, match="only strings"):
        dp.load_table(json.dumps(table))


def test_match_rule_first_match_wins_and_all_keys_must_equal():
    table = dp.load_table(json.dumps(TABLE))
    assert dp.match_rule(table, "grafana", {"alertname": "Pod CPU starved", "pod": "x"})["id"] == "cpu"
    assert dp.match_rule(table, "grafana", {"alertname": "Other"}) is None
    assert dp.match_rule(table, "github-check", {"alertname": "Pod CPU starved"}) is None


def test_render_prompt_fills_labels_and_marks_missing_keys():
    rule = TABLE["rules"][0]
    assert dp.render_prompt(rule, alert()) == "Pod n8n/n8n-0: waiting 40% for CPU"
    assert dp.render_prompt(rule, {"labels": {"alertname": "x"}, "annotations": {}}) == "Pod ?/?: ?"


# ---------------------------------------------------------------- budget

def test_budget_suppresses_repeat_fingerprint():
    now = [1000.0]
    b = dp.Budget(per_hour=5, repeat_after_hours=6, clock=lambda: now[0])
    assert b.admit("fp1") is None
    assert b.admit("fp1") == "repeat"
    now[0] += 6 * 3600
    assert b.admit("fp1") is None


def test_budget_caps_investigations_per_hour():
    now = [1000.0]
    b = dp.Budget(per_hour=2, repeat_after_hours=6, clock=lambda: now[0])
    assert b.admit("a") is None and b.admit("b") is None
    assert b.admit("c") == "budget"
    now[0] += 3601
    assert b.admit("c") is None
# ---------------------------------------------------------------- dispatcher

def completed_task(text="The pod is throttled by its own 100m limit.", session="sess-1"):
    return {"task": {"id": "task-1", "contextId": session,
                     "status": {"state": a2a.STATE_COMPLETED},
                     "artifacts": [{"parts": [{"text": text}]}]}}


def waiting_task(session="sess-2"):
    return {"task": {"id": "task-2", "contextId": session,
                     "status": {"state": "TASK_STATE_INPUT_REQUIRED",
                                "message": {"parts": [{"text": "Which namespace?"}]}}}}


class Fake:
    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error
        self.sent, self.posted, self.logged = [], [], []

    def send(self, url, text, **kw):
        self.sent.append({"url": url, "text": text, **kw})
        if self.error:
            raise self.error
        return self.reply

    def post(self, url, obj, timeout=10.0):
        self.posted.append((url, obj))

    def log(self, obj):
        self.logged.append(obj)


def make(fake, table=TABLE, per_hour=2):
    t = json.loads(json.dumps(table))
    t["limits"]["investigations_per_hour"] = per_hour
    cfg = dp.Config(table_path="/dev/null", a2a_base="http://a2a:8083", n8n_base="http://n8n:5678/webhook",
                    user="dispatcher", timeout=5.0)
    return dp.Dispatcher(dp.load_table(json.dumps(t)), cfg, send=fake.send, post=fake.post,
                         log=fake.log, run_async=False)


def test_firing_matched_alert_is_investigated_as_dispatcher_in_a_new_session():
    fake = Fake(reply=completed_task())
    d = make(fake)
    decisions = d.handle_grafana({"alerts": [alert()]})
    assert [x["decision"] for x in decisions] == ["investigate"]
    sent = fake.sent[0]
    assert sent["url"] == "http://a2a:8083/agents/kagent/k8s-reader"
    assert sent["token_file"] == "/var/run/kagent/token" and sent["user"] is None   # its own identity (V91)
    assert sent.get("context_id") is None and sent.get("task_id") is None and sent.get("metadata") is None
    assert sent["text"] == "Pod n8n/n8n-0: waiting 40% for CPU"
    url, finding = fake.posted[0]
    assert url == "http://n8n:5678/webhook/agent-findings"
    assert finding["session"] == "sess-1" and finding["agent"] == "k8s-reader"
    # No kagent-UI link: a dispatcher-owned session cannot be opened there (dead links in
    # Slack since the dispatcher went live). The session id and the continuation command instead.
    assert finding["ui"] is None
    assert "--agent kagent/k8s-reader --as-serviceaccount dispatcher/dispatcher --context sess-1" in finding["text"]
    assert finding["text"].startswith("The pod is throttled")
    assert finding["error"] is None
    events = [x["event"] for x in fake.logged]
    assert events == ["dispatch", "finding"]
    assert all("text" not in x for x in fake.logged)


def test_none_resolved_and_unmatched_are_logged_not_sent():
    fake = Fake(reply=completed_task())
    d = make(fake)
    decisions = d.handle_grafana({"alerts": [alert("Quiet"), alert(status="resolved"), alert("Nope")]})
    assert [x["decision"] for x in decisions] == ["none", "resolved", "unmatched"]
    assert fake.sent == [] and fake.posted == []


def test_same_fingerprint_twice_investigates_once():
    fake = Fake(reply=completed_task())
    d = make(fake)
    first = d.handle_grafana({"alerts": [alert(), alert()]})
    second = d.handle_grafana({"alerts": [alert()]})
    assert [x["decision"] for x in first + second] == ["investigate", "skip-repeat", "skip-repeat"]
    assert len(fake.sent) == 1


def test_budget_exhaustion_is_logged_as_skip_budget():
    fake = Fake(reply=completed_task())
    d = make(fake, per_hour=1)
    out = d.handle_grafana({"alerts": [alert(fingerprint="a"), alert(fingerprint="b", pod="other")]})
    assert [x["decision"] for x in out] == ["investigate", "skip-budget"]


def test_waiting_reply_posts_paused_finding_without_answering():
    fake = Fake(reply=waiting_task())
    make(fake).handle_grafana({"alerts": [alert()]})
    _, finding = fake.posted[0]
    assert finding["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert "paused" in finding["text"] and "kagent UI" not in finding["text"]
    assert "--as-serviceaccount dispatcher/dispatcher --context sess-2" in finding["text"]
    assert '"<answer>"' in finding["text"] and "--approve" not in finding["text"]   # a question, not a call
    assert finding["ui"] is None
    assert len(fake.sent) == 1, "the dispatcher never answers a paused task"


def test_a2a_error_posts_error_finding():
    fake = Fake(error=a2a.A2AError("A2A error -32000: boom"))
    make(fake).handle_grafana({"alerts": [alert()]})
    _, finding = fake.posted[0]
    assert finding["error"].startswith("A2A error")
    assert finding["session"] is None and finding["text"] is None
    assert fake.logged[-1]["event"] == "finding" and fake.logged[-1]["error"].startswith("A2A error")


def test_handle_grafana_tolerates_missing_alerts_and_labels():
    fake = Fake(reply=completed_task())
    d = make(fake)
    assert d.handle_grafana({}) == []
    out = d.handle_grafana({"alerts": [{"status": "firing"}]})
    assert out[0]["decision"] == "unmatched" and out[0]["alertname"] == "?"


def test_config_from_env_defaults_and_overrides():
    cfg = dp.Config.from_env({})
    assert cfg.a2a_base == a2a.IN_CLUSTER_BASE
    assert cfg.user == "dispatcher" and cfg.timeout == 300.0
    cfg = dp.Config.from_env({"INVESTIGATION_TIMEOUT": "12", "DISPATCH_USER": "x"})
    assert cfg.timeout == 12.0 and cfg.user == "x"
# ---------------------------------------------------------------- http

@pytest.fixture
def server():
    fake = Fake(reply=completed_task())
    d = make(fake)
    srv = dp.serve(d, port=0)
    yield fake, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _post(url, body: bytes):
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def test_healthz_and_readyz(server):
    _, base = server
    for path in ("/healthz", "/readyz"):
        with urllib.request.urlopen(base + path, timeout=5) as resp:
            assert resp.status == 200


def test_signal_grafana_returns_decisions(server):
    fake, base = server
    status, body = _post(base + "/signal/grafana", json.dumps({"alerts": [alert()]}).encode())
    assert status == 200
    assert body == {"decisions": ["investigate"]}


def test_signal_grafana_rejects_bad_json_and_unknown_paths(server):
    _, base = server
    assert _post(base + "/signal/grafana", b"not json")[0] == 400
    assert _post(base + "/signal/other", b"{}")[0] == 404


# ---------------------------------------------------------------- review fixes

def test_timeout_posts_still_running_not_failed():
    fake = Fake(error=TimeoutError("The read operation timed out"))
    make(fake).handle_grafana({"alerts": [alert()]})
    _, finding = fake.posted[0]
    assert finding["error"] is None
    assert "still running" in finding["text"]
    assert "dispatcher" in finding["text"]


def test_urlerror_wrapping_timeout_is_still_running_too():
    fake = Fake(error=urllib.error.URLError(TimeoutError("timed out")))
    make(fake).handle_grafana({"alerts": [alert()]})
    _, finding = fake.posted[0]
    assert finding["error"] is None and "still running" in finding["text"]


def test_unexpected_exception_still_posts_and_logs_a_finding():
    fake = Fake(error=KeyError("artifacts"))
    make(fake).handle_grafana({"alerts": [alert()]})
    _, finding = fake.posted[0]
    assert finding["error"].startswith("unexpected KeyError")
    assert fake.logged[-1]["event"] == "finding"


def test_secret_looking_values_are_scrubbed_before_posting():
    text = ("Found Authorization: Bearer abc.def-123 and AKIAIOSFODNN7EXAMPLE and password=hunter2 "
            "and github_pat_11AAAA and sk-ant-api03-xyz; the pod is fine.")
    fake = Fake(reply=completed_task(text=text))
    make(fake).handle_grafana({"alerts": [alert()]})
    _, finding = fake.posted[0]
    for leaked in ("abc.def-123", "AKIAIOSFODNN7EXAMPLE", "hunter2", "github_pat_11AAAA", "sk-ant-api03-xyz"):
        assert leaked not in finding["text"], leaked
    assert finding["text"].count("[redacted]") == 5
    assert "the pod is fine.\nSession sess-1" in finding["text"]


def test_scrub_leaves_ordinary_text_alone():
    assert dp.scrub("pod n8n-0 restarted 3 times; see events") == "pod n8n-0 restarted 3 times; see events"


# ---------------------------------------------------------------- identity (callers as principals)

def test_investigation_sends_the_projected_token_not_a_user(tmp_path):
    f = tmp_path / "token"
    f.write_text("disp.tok.en")
    seen = {}

    def send(url, text, **kw):
        seen.update(kw)
        return {"task": {"id": "t1", "contextId": "c1", "status": {"state": "TASK_STATE_COMPLETED"},
                         "artifacts": [{"parts": [{"text": "looked"}]}]}}

    cfg = dp.Config(table_path="x", a2a_base="http://a", n8n_base="http://n", user="dispatcher",
                    timeout=5, token_file=str(f))
    d = dp.Dispatcher(dp.load_table(json.dumps(TABLE)), cfg, send=send, post=lambda *a, **k: None,
                      log=lambda o: None, run_async=False)
    d.handle_grafana({"alerts": [alert()]})
    assert seen["token_file"] == str(f) and seen["user"] is None


def test_config_token_file_default_and_disable():
    assert dp.Config.from_env({}).token_file == "/var/run/kagent/token"
    assert dp.Config.from_env({"KAGENT_TOKEN_FILE": ""}).token_file is None


# ---------------------------------------------------------------- open-PR cap (§T.117)

PROPOSE_TABLE = {
    "version": 1,
    "limits": {"investigations_per_hour": 4, "repeat_after_hours": 6, "open_prs_max": 2},
    "rules": [{"id": "mem", "source": "grafana", "match": {"alertname": "Container near memory limit"},
               "route": "agent:implementer", "proposes": True, "prompt": "fix {labels[pod]}", "why": "w"}],
}


def mem_alert(fp="fp-1", pod="n8n-0", namespace="n8n"):
    return {"status": "firing", "fingerprint": fp,
            "labels": {"alertname": "Container near memory limit", "namespace": namespace, "pod": pod}}


def make_propose(fake, count):
    cfg = dp.Config(table_path="/dev/null", a2a_base="http://a2a:8083", n8n_base="http://n8n:5678/webhook",
                    user="dispatcher", timeout=5.0)
    counter = dp.OpenPRs("arigsela/kubernetes", "arigsela-implementer[bot]", fetch=count, clock=lambda: 0.0)
    return dp.Dispatcher(dp.load_table(json.dumps(PROPOSE_TABLE)), cfg, send=fake.send, post=fake.post,
                         log=fake.log, run_async=False, open_prs=counter)


def test_open_prs_max_defaults_to_three_and_proposes_is_validated():
    t = dp.load_table(json.dumps({**PROPOSE_TABLE, "limits": {"investigations_per_hour": 1, "repeat_after_hours": 1}}))
    assert t["limits"]["open_prs_max"] == 3
    with pytest.raises(dp.TableError, match="proposes"):
        bad = json.loads(json.dumps(PROPOSE_TABLE)); bad["rules"][0]["proposes"] = "yes"
        dp.load_table(json.dumps(bad))


def test_below_the_cap_a_proposing_rule_is_investigated():
    fake = Fake(reply=completed_task(text="https://github.com/arigsela/kubernetes/pull/900"))
    d = make_propose(fake, count=lambda: [{"n": 1}])
    assert [x["decision"] for x in d.handle_grafana({"alerts": [mem_alert()]})] == ["investigate"]
    assert len(fake.sent) == 1


def test_at_the_cap_a_proposing_rule_is_skipped_with_a_reason():
    fake = Fake(reply=completed_task())
    d = make_propose(fake, count=lambda: [{"n": 1}, {"n": 2}])
    decisions = d.handle_grafana({"alerts": [mem_alert()]})
    assert [x["decision"] for x in decisions] == ["skip-open-prs"]
    assert decisions[0]["open_prs"] == 2 and not fake.sent


def test_open_prs_count_failure_routes_and_logs():
    def boom():
        raise OSError("rate limited")
    fake = Fake(reply=completed_task())
    d = make_propose(fake, count=boom)
    decisions = d.handle_grafana({"alerts": [mem_alert()]})
    assert [x["decision"] for x in decisions] == ["investigate"]
    assert decisions[0]["open_prs"] is None
    assert any(x.get("event") == "open-prs" and "rate limited" in x.get("error", "") for x in fake.logged)


def test_non_proposing_rule_never_counts_prs():
    calls = []
    fake = Fake(reply=completed_task())
    d = make(fake)
    d.open_prs = dp.OpenPRs("r", "l", fetch=lambda: calls.append(1) or [], clock=lambda: 0.0)
    d.handle_grafana({"alerts": [alert()]})
    assert calls == []


def test_open_prs_counter_caches_for_its_ttl():
    calls = []
    now = [0.0]
    c = dp.OpenPRs("r", "l", fetch=lambda: calls.append(1) or [{"n": 1}], clock=lambda: now[0], ttl=300)
    assert c.count() == 1 and c.count() == 1 and len(calls) == 1
    now[0] = 301.0
    assert c.count() == 1 and len(calls) == 2


def test_open_prs_fetch_filters_by_author_login():
    seen = {}
    def opener(req, timeout):
        seen["url"] = req.full_url
        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps([{"user": {"login": "arigsela-implementer[bot]"}},
                                               {"user": {"login": "arigsela"}}]).encode()
        return R()
    items = dp.fetch_open_prs("arigsela/kubernetes", "arigsela-implementer[bot]", opener=opener)
    assert len(items) == 1 and "pulls?state=open&per_page=100" in seen["url"]


def test_config_reads_implementer_login_and_repo():
    c = dp.Config.from_env({})
    assert c.implementer_login == "arigsela-implementer[bot]" and c.github_repo == "arigsela/kubernetes"


def test_open_prs_count_survives_a_mid_body_disconnect_and_caches_the_failure():
    """Review finding I4: http.client errors are not OSError; one must not kill the webhook
    handler (that fails closed on a read), and a failed count must not refetch on every alert."""
    import http.client
    calls = []
    def broken():
        calls.append(1)
        raise http.client.IncompleteRead(b"partial")
    now = [0.0]
    c = dp.OpenPRs("r", "l", fetch=broken, clock=lambda: now[0], ttl=300)
    assert c.count() is None and "IncompleteRead" in (c.last_error or "")
    assert c.count() is None and len(calls) == 1, "a failed count is cached for the ttl"
    now[0] = 301.0
    assert c.count() is None and len(calls) == 2


# ---------------------------------------------------------- remediation (§T.118, §V.96)

REMEDIATE_TABLE = {
    "version": 1,
    "limits": {"investigations_per_hour": 4, "repeat_after_hours": 6, "open_prs_max": 2, "restarts_per_day": 2},
    "rules": [{"id": "mem", "source": "grafana", "match": {"alertname": "Container near memory limit"},
               "route": "agent:implementer", "proposes": True, "prompt": "fix {labels[pod]}",
               "remediate": {"on": ["leak"], "agent": "restarter",
                             "prompt": ["Restart {labels[namespace]}/{labels[pod]}:",
                                        "verdict {verdict} in session {session} for '{alertname}'"]},
               "why": "w"}],
}

LEAK = "RSS grew 40Mi/h for 9h and the pod was OOMKilled twice.\nno pull request: leak\nverdict: leak"


def approval_task(session="sess-r", args=None):
    """A restarter task paused on its gated delete (hitl tool_approval_request, alpha11 shape)."""
    # The real argument key is `resource_name` (seen live 2026-10-14), not `name`.
    call = {"id": "call-1", "name": "k8s_delete_resource",
            "args": args or {"resource_type": "pod", "resource_name": "n8n-0", "namespace": "n8n"}}
    return {"task": {"id": "task-r", "contextId": session,
                     "status": {"state": "TASK_STATE_INPUT_REQUIRED",
                                "message": {"parts": [{"text": "approve?"}],
                                            "metadata": {a2a.HITL_EXTENSION: {
                                                "type": a2a.HITL_TOOL_APPROVAL_REQUEST, "tools": [call]}}}}}}


class Seq(Fake):
    """A fake agent that answers each send from a list (replies or exceptions), in order."""

    def __init__(self, *replies):
        super().__init__()
        self.replies = list(replies)

    def send(self, url, text, **kw):
        self.sent.append({"url": url, "text": text, **kw})
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def make_remediate(fake, table=REMEDIATE_TABLE, clock=None):
    cfg = dp.Config(table_path="/dev/null", a2a_base="http://a2a:8083", n8n_base="http://n8n:5678/webhook",
                    user="dispatcher", timeout=5.0,
                    approvals_url="https://approvals.test/approvals")
    counter = dp.OpenPRs("arigsela/kubernetes", "arigsela-implementer[bot]", fetch=lambda: [], clock=lambda: 0.0)
    kw = {}
    if clock is not None:   # both budgets on the same fake clock
        kw["restarts"] = dp.RestartBudget(table["limits"].get("restarts_per_day", 2), clock=clock)
        kw["budget"] = dp.Budget(table["limits"]["investigations_per_hour"], table["limits"]["repeat_after_hours"],
                                 clock=clock)
    return dp.Dispatcher(dp.load_table(json.dumps(table)), cfg, send=fake.send, post=fake.post,
                         log=fake.log, run_async=False, open_prs=counter, **kw)


def test_parse_verdict_takes_the_last_line_and_rejects_unknown():
    assert dp.parse_verdict("evidence\nverdict: leak\nmore\nVerdict: Limit-Too-Low \n") == "limit-too-low"
    assert dp.parse_verdict("verdict: leak") == "leak"
    assert dp.parse_verdict("the verdict: leak is in prose") is None
    assert dp.parse_verdict("verdict: maybe") is None
    assert dp.parse_verdict("") is None and dp.parse_verdict(None) is None
    assert dp.VERDICTS == {"limit-too-low", "leak", "contention", "rollout-pending", "inconclusive"}


@pytest.mark.parametrize("mutate, message", [
    (lambda t: t["rules"][0]["remediate"].update(on=["restart"]), "remediate.on"),
    (lambda t: t["rules"][0]["remediate"].update(on=[]), "remediate.on"),
    (lambda t: t["rules"][0]["remediate"].pop("prompt"), "remediate.prompt"),
    (lambda t: t["rules"][0]["remediate"].pop("agent"), "remediate.agent"),
    (lambda t: t["rules"][0].update(route="none", remediate=t["rules"][0]["remediate"]), "agent route"),
    (lambda t: t["limits"].update(restarts_per_day=-1), "restarts_per_day"),
    (lambda t: t["rules"][0].update(remediate="restarter"), "remediate must be an object"),
])
def test_load_table_validates_remediate_and_restarts_per_day(mutate, message):
    t = json.loads(json.dumps(REMEDIATE_TABLE))
    mutate(t)
    with pytest.raises(dp.TableError, match=message):
        dp.load_table(json.dumps(t))


def test_load_table_accepts_remediate_and_defaults_the_restart_cap():
    t = json.loads(json.dumps(REMEDIATE_TABLE))
    del t["limits"]["restarts_per_day"]
    loaded = dp.load_table(json.dumps(t))
    assert loaded["limits"]["restarts_per_day"] == 2
    assert loaded["rules"][0]["remediate"]["prompt"].startswith("Restart {labels[namespace]}")   # list joined


def test_restart_budget_caps_per_day_and_marks_recurrence():
    now = [0.0]
    b = dp.RestartBudget(2, clock=lambda: now[0])
    assert b.admit("a") is None
    assert b.admit("a") == "recurring"
    assert b.admit("b") is None
    assert b.admit("c") == "restarts"
    now[0] += 24 * 3600 + 1
    assert b.admit("a") is None


def test_leak_verdict_opens_a_restarter_session_and_posts_the_pending_call():
    fake = Seq(completed_task(text=LEAK, session="sess-i"), approval_task())
    d = make_remediate(fake)
    assert [x["decision"] for x in d.handle_grafana({"alerts": [mem_alert()]})] == ["investigate"]
    assert len(fake.sent) == 2
    first, second = fake.sent
    assert second["url"] == "http://a2a:8083/agents/kagent/restarter"
    assert second["text"] == "Restart n8n/n8n-0: verdict leak in session sess-i for 'Container near memory limit'"
    for s in (first, second):   # new sessions, never an approval (V88)
        assert s.get("context_id") is None and s.get("task_id") is None and s.get("metadata") is None
    (_, finding), (_, remediation) = fake.posted
    assert finding["verdict"] == "leak" and finding["agent"] == "implementer"
    assert remediation["kind"] == "remediation" and remediation["agent"] == "restarter"
    assert remediation["session"] == "sess-r" and remediation["evidence_session"] == "sess-i"
    assert remediation["pending"] == [{"name": "k8s_delete_resource",
                                       "args": {"resource_type": "pod", "resource_name": "n8n-0", "namespace": "n8n"}}]
    assert remediation["ui"] is None
    t = remediation["text"]
    assert "awaiting approval" in t and "k8s_delete_resource" in t and '"resource_name": "n8n-0"' in t
    assert ("--agent kagent/restarter --as-serviceaccount dispatcher/dispatcher --context sess-r "
            "--task task-r --approve") in t, "the laptop never saw the pause: --task lets ask-hk fetch it"
    assert "--reject" in t and "sess-i" in t and "MISMATCH" not in t
    assert "kagent UI cannot see" in t, "a dispatcher-owned session is not in the UI's identity"
    events = [(x["event"], x.get("decision")) for x in fake.logged]
    assert events == [("dispatch", "investigate"), ("remediate", "remediate"), ("finding", None), ("finding", None)]
    assert fake.logged[1]["verdict"] == "leak" and fake.logged[1]["rule"] == "mem"
    assert fake.logged[3]["kind"] == "remediation" and fake.logged[3]["session"] == "sess-r"
    assert all("text" not in x for x in fake.logged)


def test_limit_too_low_verdict_sends_once_and_logs_no_match():
    fake = Seq(completed_task(text="https://github.com/arigsela/kubernetes/pull/9\nverdict: limit-too-low"))
    make_remediate(fake).handle_grafana({"alerts": [mem_alert()]})
    assert len(fake.sent) == 1 and len(fake.posted) == 1
    assert fake.posted[0][1]["verdict"] == "limit-too-low"
    assert [x.get("decision") for x in fake.logged if x["event"] == "remediate"] == ["no-match"]


def test_missing_verdict_is_null_and_never_remediates():
    fake = Seq(completed_task(text="no pull request: leak"))
    make_remediate(fake).handle_grafana({"alerts": [mem_alert()]})
    assert len(fake.sent) == 1 and fake.posted[0][1]["verdict"] is None


def test_restart_cap_skips_and_says_so():
    fake = Seq(completed_task(text=LEAK, session="s1"), approval_task("r1"),
               completed_task(text=LEAK, session="s2"), approval_task("r2"),
               completed_task(text=LEAK, session="s3"))
    d = make_remediate(fake, clock=lambda: 0.0)
    for fp in ("a", "b", "c"):   # three different workloads: the per-day cap, not recurrence
        d.handle_grafana({"alerts": [mem_alert(fp, pod=f"{fp}-7d4bq8xk2-zz9q1")]})
    assert len(fake.sent) == 5, "two restarts, the third investigation only"
    third = fake.posted[-1][1]
    assert third["verdict"] == "leak" and "Restart cap reached" in third["text"] and third["agent"] == "implementer"
    assert [x["decision"] for x in fake.logged if x["event"] == "remediate"] == ["remediate", "remediate", "skip-restarts"]


def test_recurring_leak_skips_and_says_so():
    now = [0.0]
    fake = Seq(completed_task(text=LEAK, session="s1"), approval_task("r1"),
               completed_task(text=LEAK, session="s2"))
    d = make_remediate(fake, clock=lambda: now[0])
    d.handle_grafana({"alerts": [mem_alert("same")]})
    now[0] += 7 * 3600          # past repeat_after_hours, inside the 24 h recurrence window
    d.handle_grafana({"alerts": [mem_alert("same")]})
    assert len(fake.sent) == 3
    assert "Recurring leak" in fake.posted[-1][1]["text"]
    assert [x["decision"] for x in fake.logged if x["event"] == "remediate"] == ["remediate", "skip-recurring"]


def test_restarter_refusal_posts_its_answer_not_an_approve_command():
    fake = Seq(completed_task(text=LEAK, session="s1"),
               completed_task(text="no restart: the pod has no owner", session="r1"))
    make_remediate(fake).handle_grafana({"alerts": [mem_alert()]})
    rem = fake.posted[1][1]
    assert rem["kind"] == "remediation" and rem["pending"] == []
    assert rem["text"] == "no restart: the pod has no owner" and "--approve" not in rem["text"]
    assert rem["state"] == a2a.STATE_COMPLETED


def test_restarter_question_posts_paused_without_approve_command():
    fake = Seq(completed_task(text=LEAK, session="s1"), waiting_task("r1"))
    make_remediate(fake).handle_grafana({"alerts": [mem_alert()]})
    rem = fake.posted[1][1]
    assert rem["pending"] == [] and "paused" in rem["text"] and "--approve" not in rem["text"]


def test_restarter_error_still_charges_the_budget():
    fake = Seq(completed_task(text=LEAK, session="s1"), a2a.A2AError("boom"),
               completed_task(text=LEAK, session="s2"), approval_task("r2"),
               completed_task(text=LEAK, session="s3"))
    d = make_remediate(fake, clock=lambda: 0.0)
    for fp in ("a", "b", "c"):
        d.handle_grafana({"alerts": [mem_alert(fp, pod=f"{fp}-0")]})
    rem = fake.posted[1][1]
    assert rem["kind"] == "remediation" and rem["error"] == "boom" and rem["session"] is None
    assert len(fake.sent) == 5 and "Restart cap reached" in fake.posted[-1][1]["text"]


def test_restarter_timeout_posts_still_running():
    fake = Seq(completed_task(text=LEAK, session="s1"), TimeoutError("timed out"))
    make_remediate(fake).handle_grafana({"alerts": [mem_alert()]})
    rem = fake.posted[1][1]
    assert rem["error"] is None and "still running" in rem["text"] and "session id is unknown" in rem["text"]
    assert "--approve" not in rem["text"]


def test_remediation_text_is_scrubbed():
    fake = Seq(completed_task(text=LEAK, session="s1"),
               completed_task(text="no restart: saw password=hunter2 in the log", session="r1"))
    make_remediate(fake).handle_grafana({"alerts": [mem_alert()]})
    assert "hunter2" not in fake.posted[1][1]["text"]


# ------------------------------------------------ review fix pass (2026-10-14, findings 2-5, 7, 9)

def test_verdict_is_parsed_from_the_raw_answer_not_the_truncated_scrubbed_text():
    long = "x" * (dp.ANSWER_LIMIT + 50) + "\nno pull request: leak\nverdict: leak"
    fake = Seq(completed_task(text=long, session="s1"), approval_task("r1"))
    make_remediate(fake).handle_grafana({"alerts": [mem_alert()]})
    assert fake.posted[0][1]["verdict"] == "leak" and len(fake.sent) == 2
    scrubbed = "the log ends with token:\nverdict: leak"      # the secret pattern spans the newline
    fake = Seq(completed_task(text=scrubbed, session="s1"), approval_task("r1"))
    make_remediate(fake).handle_grafana({"alerts": [mem_alert()]})
    assert fake.posted[0][1]["verdict"] == "leak" and "verdict: leak" not in fake.posted[0][1]["text"]


@pytest.mark.parametrize("line", ["`verdict: leak`", "**verdict: leak**", "'verdict: leak'",
                                  "- verdict: leak", "verdict: leak.", "  Verdict: LEAK  "])
def test_parse_verdict_tolerates_model_formatting(line):
    assert dp.parse_verdict("evidence\n" + line) == "leak"


def test_parse_verdict_still_rejects_prose_and_unknown_values():
    assert dp.parse_verdict("the verdict: leak is in prose") is None
    assert dp.parse_verdict("verdict: leak or not") is None
    assert dp.parse_verdict("**verdict: probably**") is None


@pytest.mark.parametrize("pod, key", [
    ("homepage-9f48c7989-mj8tf", "n8n/homepage"),     # Deployment (hash + suffix)
    ("dispatcher-699758b64b-5pcwt", "n8n/dispatcher"),
    ("prometheus-0", "n8n/prometheus"),                # StatefulSet ordinal
    ("atelet-gn9l7", "n8n/atelet"),                    # DaemonSet suffix
    ("n8n-0", "n8n/n8n"),
])
def test_restart_key_is_the_workload_not_the_pod(pod, key):
    assert dp.restart_key({"namespace": "n8n", "pod": pod}) == key


def test_recurrence_survives_a_replacement_pod_and_a_second_container():
    fake = Seq(completed_task(text=LEAK, session="s1"), approval_task("r1"),
               completed_task(text=LEAK, session="s2"), completed_task(text=LEAK, session="s3"))
    d = make_remediate(fake, clock=lambda: 0.0)
    d.handle_grafana({"alerts": [mem_alert("fp-a", pod="web-7d4bq8xk2-aaaaa")]})
    d.handle_grafana({"alerts": [mem_alert("fp-b", pod="web-7d4bq8xk2-bbbbb")]})    # replacement pod
    d.handle_grafana({"alerts": [mem_alert("fp-c", pod="web-7d4bq8xk2-bbbbb")]})    # another container
    assert [x["decision"] for x in fake.logged if x["event"] == "remediate"] == \
        ["remediate", "skip-recurring", "skip-recurring"]


@pytest.mark.parametrize("labels, why", [
    ({"namespace": "vault", "pod": "vault-0"}, "never restarted"),
    ({"namespace": "kagent", "pod": "kagent-controller-abc12"}, "never restarted"),
    ({"namespace": "n8n", "pod": "Bad_Pod"}, "no valid namespace/pod"),
    ({"namespace": "n8n"}, "no valid namespace/pod"),
])
def test_deny_listed_or_malformed_targets_never_open_a_restarter_session(labels, why):
    fake = Seq(completed_task(text=LEAK, session="s1"))
    alert = {"status": "firing", "fingerprint": "fp",
             "labels": {"alertname": "Container near memory limit", **labels}}
    d = make_remediate(fake, clock=lambda: 0.0)
    d.handle_grafana({"alerts": [alert]})
    assert len(fake.sent) == 1 and len(fake.posted) == 1
    assert why in fake.posted[0][1]["text"]
    assert [x["decision"] for x in fake.logged if x["event"] == "remediate"] == ["skip-target"]
    assert d.restarts.admit("n8n/anything") is None, "a refused target is not charged"


@pytest.mark.parametrize("args", [
    {"resource_type": "pod", "resource_name": "other-0", "namespace": "n8n"},
    {"resource_type": "pod", "resource_name": "n8n-0", "namespace": "vault"},
    {"resource_type": "deployment", "resource_name": "n8n-0", "namespace": "n8n"},
])
def test_a_pending_call_that_is_not_the_alerts_pod_gets_a_reject_command_only(args):
    fake = Seq(completed_task(text=LEAK, session="s1"), approval_task("r1", args=args))
    make_remediate(fake).handle_grafana({"alerts": [mem_alert()]})
    t = fake.posted[1][1]["text"]
    assert "MISMATCH" in t and "--reject" in t and "--approve" not in t


def test_two_pending_calls_get_a_reject_command_only():
    task = approval_task("r1")
    tools = task["task"]["status"]["message"]["metadata"][a2a.HITL_EXTENSION]["tools"]
    tools.append({"id": "call-2", "name": "k8s_delete_resource",
                  "args": {"resource_type": "pod", "resource_name": "n8n-1", "namespace": "n8n"}})
    fake = Seq(completed_task(text=LEAK, session="s1"), task)
    make_remediate(fake).handle_grafana({"alerts": [mem_alert()]})
    rem = fake.posted[1][1]
    assert len(rem["pending"]) == 2 and "MISMATCH" in rem["text"] and "--approve" not in rem["text"]


def test_restarter_ask_user_request_posts_paused_without_approve_command():
    task = waiting_task("r1")
    task["task"]["status"]["message"]["metadata"] = {a2a.HITL_EXTENSION: {
        "type": a2a.HITL_ASK_USER_REQUEST, "id": "q-1", "question": "which pod?"}}
    fake = Seq(completed_task(text=LEAK, session="s1"), task)
    make_remediate(fake).handle_grafana({"alerts": [mem_alert()]})
    rem = fake.posted[1][1]
    assert rem["pending"] == [] and "paused" in rem["text"] and "--approve" not in rem["text"]


def test_a_broken_remediation_prompt_still_posts_a_finding_and_a_log_line():
    table = json.loads(json.dumps(REMEDIATE_TABLE))
    table["rules"][0]["remediate"]["prompt"] = "restart {} now"      # {} raises in format_map
    fake = Seq(completed_task(text=LEAK, session="s1"))
    make_remediate(fake, table=table).handle_grafana({"alerts": [mem_alert()]})
    rem = fake.posted[1][1]
    assert rem["kind"] == "remediation" and rem["error"].startswith("unexpected ")
    assert [x["event"] for x in fake.logged][-1] == "finding" and fake.logged[-1]["kind"] == "remediation"


# ----------------------------------------------- the fifth verdict: rollout-pending (2026-10-14)

ROLLOUT = ("git already declares 4608Mi; the StatefulSet is OnDelete so the pod still runs 3Gi.\n"
           "no pull request: already in git\nverdict: rollout-pending")


def test_rollout_pending_verdict_opens_a_restarter_session_when_the_row_lists_it():
    table = json.loads(json.dumps(REMEDIATE_TABLE))
    table["rules"][0]["remediate"]["on"] = ["leak", "rollout-pending"]
    fake = Seq(completed_task(text=ROLLOUT, session="s1"), approval_task("r1"))
    make_remediate(fake, table=table).handle_grafana({"alerts": [mem_alert()]})
    assert len(fake.sent) == 2 and fake.posted[0][1]["verdict"] == "rollout-pending"
    assert "verdict rollout-pending" in fake.sent[1]["text"]
    assert "awaiting approval" in fake.posted[1][1]["text"]


def test_rollout_pending_is_a_no_match_when_the_row_lists_only_leak():
    fake = Seq(completed_task(text=ROLLOUT, session="s1"))
    make_remediate(fake).handle_grafana({"alerts": [mem_alert()]})
    assert len(fake.sent) == 1
    assert [x["decision"] for x in fake.logged if x["event"] == "remediate"] == ["no-match"]


# ------------------------------------------------- the approvals page (§T.119, §V.88, §V.97)

import urllib.parse


def pending_after_leak(fake=None, clock=None):
    """A dispatcher that has registered one pending restart (n8n/n8n-0, session sess-r, task task-r)."""
    fake = fake or Seq(completed_task(text=LEAK, session="sess-i"), approval_task())
    d = make_remediate(fake, clock=clock)
    d.handle_grafana({"alerts": [mem_alert()]})
    return d, fake


def test_registry_add_get_remove_and_nonce():
    reg = dp.PendingRestarts(clock=lambda: 100.0)
    nonce = reg.add({"ctx": "c1", "task_id": "t1", "agent": "restarter", "calls": [{"id": "x"}]})
    item = reg.get("c1")
    assert item["nonce"] == nonce and len(nonce) >= 16 and item["created"] == 100.0
    assert [i["ctx"] for i in reg.items()] == ["c1"]
    assert reg.remove("c1") is item and reg.get("c1") is None and reg.items() == []
    assert reg.add({"ctx": "c1", "task_id": "t2"}) != nonce, "a fresh nonce per registration"


def test_remediate_registers_a_matching_pending_call_and_names_the_page():
    d, fake = pending_after_leak()
    item = d.pending.get("sess-r")
    assert item["task_id"] == "task-r" and item["agent"] == "restarter"
    assert item["calls"] == [{"id": "call-1", "name": "k8s_delete_resource",
                              "args": {"resource_type": "pod", "resource_name": "n8n-0", "namespace": "n8n"}}]
    assert item["labels"]["pod"] == "n8n-0" and item["rule"] == "mem" and item["evidence_session"] == "sess-i"
    assert item["evidence_text"].startswith("RSS grew 40Mi/h"), "the implementer's answer travels with the item"
    t = fake.posted[1][1]["text"]
    assert "https://approvals.test/approvals" in t
    assert "--task task-r --approve" in t, "the fallback survives a dispatcher restart"


def test_remediate_does_not_register_a_mismatch():
    fake = Seq(completed_task(text=LEAK, session="s1"),
               approval_task("r1", args={"resource_type": "pod", "resource_name": "other-0", "namespace": "n8n"}))
    d = make_remediate(fake)
    d.handle_grafana({"alerts": [mem_alert()]})
    assert d.pending.items() == []


def test_approve_sends_the_registered_ids_on_the_task_and_posts_the_decision():
    d, fake = pending_after_leak()
    nonce = d.pending.get("sess-r")["nonce"]
    fake.replies.append(completed_task(text="restart requested: n8n/n8n-0", session="sess-r"))
    out = d.decide_pending("sess-r", nonce, approved=True, reason=None, approver="owner@example.com")
    assert out["ok"] and out["status"] == 200 and "restart requested" in out["text"]
    sent = fake.sent[-1]
    assert sent["url"].endswith("/agents/kagent/restarter")
    assert sent["context_id"] == "sess-r" and sent["task_id"] == "task-r"
    assert sent["metadata"] == {a2a.HITL_EXTENSION: {"type": a2a.HITL_TOOL_APPROVAL_RESPONSE,
                                                      "approvals": [{"id": "call-1", "approved": True}]}}
    assert sent["token_file"] == "/var/run/kagent/token" and sent["user"] is None
    assert d.pending.get("sess-r") is None
    _, decision = fake.posted[-1]
    assert decision["kind"] == "decision" and decision["agent"] == "restarter" and decision["session"] == "sess-r"
    assert decision["text"].startswith("Restart approved by owner@example.com: restart requested")
    assert decision["approved_by"] == "owner@example.com" and decision["labels"]["pod"] == "n8n-0"
    log = [x for x in fake.logged if x["event"] == "decision"][-1]
    assert log == {"event": "decision", "approved": True, "approved_by": "owner@example.com", "rule": "mem",
                   "session": "sess-r", "state": a2a.STATE_COMPLETED, "error": None}
    # V88: every other send in the whole run carried no metadata
    assert all(s.get("metadata") is None for s in fake.sent[:-1])


def test_reject_carries_the_reason_and_the_restarter_stops():
    d, fake = pending_after_leak()
    nonce = d.pending.get("sess-r")["nonce"]
    fake.replies.append(completed_task(text="no restart: rejected", session="sess-r"))
    out = d.decide_pending("sess-r", nonce, approved=False, reason="not now", approver="owner@example.com")
    assert out["ok"]
    approvals = fake.sent[-1]["metadata"][a2a.HITL_EXTENSION]["approvals"]
    assert approvals == [{"id": "call-1", "approved": False, "rejection_reason": "not now"}]
    assert fake.posted[-1][1]["text"].startswith("Restart rejected by owner@example.com (not now)")
    assert d.pending.get("sess-r") is None


@pytest.mark.parametrize("ctx, nonce, approver, status", [
    ("nope", "x", "owner@example.com", 404),
    ("sess-r", "wrong", "owner@example.com", 409),
    ("sess-r", None, "", 403),
])
def test_bad_decisions_change_nothing(ctx, nonce, approver, status):
    d, fake = pending_after_leak()
    good = d.pending.get("sess-r")["nonce"]
    out = d.decide_pending(ctx, good if nonce is None else nonce, approved=True, reason=None, approver=approver)
    assert not out["ok"] and out["status"] == status
    assert d.pending.get("sess-r") is not None and len(fake.sent) == 2 and len(fake.posted) == 2


def test_a_failed_decision_send_keeps_the_item_pending_and_says_so():
    d, fake = pending_after_leak()
    nonce = d.pending.get("sess-r")["nonce"]
    fake.replies.append(a2a.A2AError("gateway down"))
    out = d.decide_pending("sess-r", nonce, approved=True, reason=None, approver="owner@example.com")
    assert not out["ok"] and out["status"] == 502 and "gateway down" in out["text"]
    assert d.pending.get("sess-r") is not None
    assert fake.posted[-1][1]["kind"] == "decision" and fake.posted[-1][1]["error"]


def test_decision_answer_is_scrubbed():
    d, fake = pending_after_leak()
    nonce = d.pending.get("sess-r")["nonce"]
    fake.replies.append(completed_task(text="restart requested; saw token: abcdefghijkl", session="sess-r"))
    out = d.decide_pending("sess-r", nonce, approved=True, reason=None, approver="o@e.com")
    assert "abcdefghijkl" not in out["text"] and "abcdefghijkl" not in fake.posted[-1][1]["text"]


def test_render_escapes_everything():
    html = dp.render_approvals_page([{"ctx": "c<1>", "nonce": "n", "agent": "restarter", "created": 0.0,
                                      "task_id": "t", "verdict": "leak", "evidence_session": "e",
                                      "evidence_text": "OOMKilled twice <script>x</script>",
                                      "labels": {"alertname": "A<b>", "namespace": "n", "pod": "p"},
                                      "calls": [{"name": "k8s_delete_resource", "args": {"x": "<y>"}}]}],
                                    clock=lambda: 90.0)
    assert "A<b>" not in html and "A&lt;b&gt;" in html and "&lt;y&gt;" in html and "<y>" not in html
    assert 'action="/approvals/c%3C1%3E/approve"' in html and 'name="nonce" value="n"' in html
    assert "1 min" in html
    # The evidence is the implementer's answer, shown on the page (a dispatcher-owned session is
    # invisible in the kagent UI, so no link there); the session id stays as text for ask-hk.
    assert "<details>" in html and "OOMKilled twice &lt;script&gt;" in html and "<script>" not in html
    assert "kagent.arigsela.com" not in html and ">e<" in html
    assert "nothing pending" in dp.render_approvals_page([]).lower()


@pytest.fixture
def approvals():
    d, fake = pending_after_leak()
    srv = dp.serve_approvals(d, port=0)
    yield d, fake, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _form(url, fields: dict, email: str | None):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if email is not None:
        headers["X-Forwarded-Email"] = email
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


def test_approvals_server_binds_localhost_only(approvals):
    _, _, base = approvals
    assert base.startswith("http://127.0.0.1:")


def test_get_lists_the_pending_item_with_its_nonce(approvals):
    d, _, base = approvals
    with urllib.request.urlopen(base + "/approvals", timeout=5) as resp:
        html = resp.read().decode()
        assert resp.status == 200 and resp.headers["Content-Type"].startswith("text/html")
    assert "n8n/n8n-0" in html and "k8s_delete_resource" in html
    assert d.pending.get("sess-r")["nonce"] in html


def test_post_approve_with_the_sidecar_header_decides(approvals):
    d, fake, base = approvals
    fake.replies.append(completed_task(text="restart requested: n8n/n8n-0", session="sess-r"))
    status, html = _form(f"{base}/approvals/sess-r/approve", {"nonce": d.pending.get("sess-r")["nonce"]},
                         "owner@example.com")
    assert status == 200 and "approved by owner@example.com" in html
    assert d.pending.get("sess-r") is None
    with urllib.request.urlopen(base + "/approvals", timeout=5) as resp:
        assert "nothing pending" in resp.read().decode().lower()


def test_post_without_the_email_header_is_403_and_wrong_nonce_409(approvals):
    d, fake, base = approvals
    nonce = d.pending.get("sess-r")["nonce"]
    assert _form(f"{base}/approvals/sess-r/approve", {"nonce": nonce}, None)[0] == 403
    assert _form(f"{base}/approvals/sess-r/reject", {"nonce": "stale", "reason": "x"}, "o@e.com")[0] == 409
    assert _form(f"{base}/approvals/sess-r/other", {"nonce": nonce}, "o@e.com")[0] == 404
    assert d.pending.get("sess-r") is not None and len(fake.sent) == 2


def test_root_redirects_to_the_approvals_page(approvals):
    """After the Dex login oauth2-proxy sends the browser to the path it first asked for; a
    visit to the bare host must not end on 'not found' (seen 2026-10-14)."""
    _, _, base = approvals
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    with pytest.raises(urllib.error.HTTPError) as err:
        opener.open(base + "/", timeout=5)
    assert err.value.code == 302 and err.value.headers["Location"] == "/approvals"
    with pytest.raises(urllib.error.HTTPError) as err:
        opener.open(base + "/other", timeout=5)
    assert err.value.code == 404


def test_webhook_server_never_serves_approvals(server):
    _, base = server
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(base + "/approvals", timeout=5)
    assert err.value.code == 404


def test_config_reads_the_approvals_settings():
    cfg = dp.Config.from_env({})
    assert cfg.approvals_url == "https://dispatcher.arigsela.com/approvals" and cfg.approvals_port == 8081
    cfg = dp.Config.from_env({"APPROVALS_URL": "https://x/approvals", "APPROVALS_PORT": "0"})
    assert cfg.approvals_url == "https://x/approvals" and cfg.approvals_port == 0
