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
                    user="dispatcher", timeout=5.0, ui_url="https://ui/agents/{session}/chat")
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
    assert finding["ui"] == "https://ui/agents/sess-1/chat"
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
    assert "paused" in finding["text"]
    assert finding["ui"] == "https://ui/agents/sess-2/chat"
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
    assert finding["text"].endswith("the pod is fine.")


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
                    timeout=5, ui_url="u/{session}", token_file=str(f))
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


def mem_alert(fp="fp-1"):
    return {"status": "firing", "fingerprint": fp,
            "labels": {"alertname": "Container near memory limit", "namespace": "n8n", "pod": "n8n-0"}}


def make_propose(fake, count):
    cfg = dp.Config(table_path="/dev/null", a2a_base="http://a2a:8083", n8n_base="http://n8n:5678/webhook",
                    user="dispatcher", timeout=5.0, ui_url="https://ui/agents/{session}/chat")
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
    call = {"id": "call-1", "name": "k8s_delete_resource",
            "args": args or {"resource_type": "pod", "name": "n8n-0", "namespace": "n8n"}}
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
                    user="dispatcher", timeout=5.0, ui_url="https://ui/agents/{session}/chat")
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
    assert dp.VERDICTS == {"limit-too-low", "leak", "contention", "inconclusive"}


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
                                       "args": {"resource_type": "pod", "name": "n8n-0", "namespace": "n8n"}}]
    assert remediation["ui"] == "https://ui/agents/sess-r/chat"
    t = remediation["text"]
    assert "awaiting approval" in t and "k8s_delete_resource" in t and '"name": "n8n-0"' in t
    assert "--agent kagent/restarter --as-serviceaccount dispatcher/dispatcher --context sess-r --approve" in t
    assert "--reject" in t and "sess-i" in t
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
    for fp in ("a", "b", "c"):
        d.handle_grafana({"alerts": [mem_alert(fp)]})
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
        d.handle_grafana({"alerts": [mem_alert(fp)]})
    rem = fake.posted[1][1]
    assert rem["kind"] == "remediation" and rem["error"] == "boom" and rem["session"] is None
    assert len(fake.sent) == 5 and "Restart cap reached" in fake.posted[-1][1]["text"]


def test_restarter_timeout_posts_still_running():
    fake = Seq(completed_task(text=LEAK, session="s1"), TimeoutError("timed out"))
    make_remediate(fake).handle_grafana({"alerts": [mem_alert()]})
    rem = fake.posted[1][1]
    assert rem["error"] is None and "still running" in rem["text"] and "kagent/restarter" in rem["text"]


def test_remediation_text_is_scrubbed():
    fake = Seq(completed_task(text=LEAK, session="s1"),
               completed_task(text="no restart: saw password=hunter2 in the log", session="r1"))
    make_remediate(fake).handle_grafana({"alerts": [mem_alert()]})
    assert "hunter2" not in fake.posted[1][1]["text"]
