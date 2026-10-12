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
    assert sent["user"] == "dispatcher"
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
