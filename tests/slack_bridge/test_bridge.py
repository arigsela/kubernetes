# tests/slack_bridge/test_bridge.py
"""scripts/slack_bridge.py: events and clicks → A2A sends, Slack posts, dispatcher decisions,
with a fake agent, a fake Slack and a fake dispatcher."""
from pathlib import Path
import importlib.util
import json
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("slack_bridge_t", REPO / "scripts" / "slack_bridge.py")
sb = importlib.util.module_from_spec(_spec)
sys.modules["slack_bridge_t"] = sb
_spec.loader.exec_module(sb)
a2a = sb.a2a

OWNER, OTHER, BOT = "U0WNER", "U0THER", "UB0T"


# ---------------------------------------------------------------- fakes

def completed(text: str, session: str = "sess-1", task: str = "task-1") -> dict:
    return {"task": {"id": task, "contextId": session, "status": {"state": "TASK_STATE_COMPLETED"},
                     "artifacts": [{"parts": [{"text": text}]}]}}


def paused(session: str = "sess-1", task: str = "task-1", call_id: str = "call-1",
           name: str = "k8s_delete_resource", args: dict | None = None) -> dict:
    args = args if args is not None else {"resource_type": "pod", "resource_name": "n8n-0", "namespace": "n8n"}
    return {"task": {"id": task, "contextId": session, "status": {
        "state": "TASK_STATE_INPUT_REQUIRED",
        "message": {"parts": [{"text": "approve?"}],
                    "metadata": {a2a.HITL_EXTENSION: {"type": "tool_approval_request",
                                                      "tools": [{"id": call_id, "name": name, "args": args}]}}}}}}


def asked(question: str, session: str = "sess-1", task: str = "task-1", req: str = "q-1") -> dict:
    return {"task": {"id": task, "contextId": session, "status": {
        "state": "TASK_STATE_INPUT_REQUIRED",
        "message": {"parts": [{"text": question}],
                    "metadata": {a2a.HITL_EXTENSION: {"type": "ask_user_request", "id": req}}}}}}


class FakeAgent:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []

    def send(self, url, text, **kw):
        self.sent.append({"url": url, "text": text, **kw})
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


class FakeSlackAPI:
    """Records every Web API call; answers ok."""

    def __init__(self):
        self.calls = []

    def post(self, url, payload, headers, timeout):
        method = url.rsplit("/", 1)[1]
        params = json.loads(payload)
        self.calls.append((method, params))
        return {"ok": True, "ts": f"ts-{len(self.calls)}", "channel": params.get("channel")}

    def of(self, method):
        return [p for m, p in self.calls if m == method]


class FakeDispatcher:
    def __init__(self, status=202, text="Decision by o@e.com sent for n8n/n8n-0 (approve)"):
        self.status, self.text, self.calls = status, text, []

    def post(self, url, payload, headers, timeout):
        self.calls.append((url, json.loads(payload)))
        if self.status >= 400:
            raise sb.SlackError(f"HTTP {self.status} from {url}: {json.dumps({'ok': False, 'text': self.text})}")
        return {"ok": True, "status": self.status, "text": self.text}


def make(agent: FakeAgent, dispatcher: FakeDispatcher | None = None, **cfg):
    config = sb.Config(bot_token="xoxb", app_token="xapp", owner_user_id=OWNER, owner_email="o@e.com",
                       agents=("homelab-knowledge", "k8s-reader", "restarter"), token_file=None, user="slack-bridge",
                       a2a_base="http://a2a:8083", decide_base="http://decide:8082", timeout=5.0, **cfg)
    api = FakeSlackAPI()
    logs = []
    disp = dispatcher or FakeDispatcher()
    bridge = sb.Bridge(config, sb.Slack("xoxb", post=api.post), bot_user_id=BOT,
                       send=agent.send, post=disp.post, log=logs.append)
    return bridge, api, logs, disp


def dm(text, user=OWNER, ts="1.1", thread_ts=None, channel="D1"):
    ev = {"type": "message", "channel_type": "im", "channel": channel, "user": user, "text": text, "ts": ts}
    if thread_ts:
        ev["thread_ts"] = thread_ts
    return ev


def mention(text, user=OWNER, ts="2.1", thread_ts=None, channel="C1"):
    ev = {"type": "app_mention", "channel": channel, "user": user, "text": f"<@{BOT}> {text}", "ts": ts}
    if thread_ts:
        ev["thread_ts"] = thread_ts
    return ev


def click(action_id, value, user=OWNER, channel="C1", ts="9.9", thread_ts=None, blocks=None, text="pending"):
    msg = {"ts": ts, "text": text}
    if thread_ts:
        msg["thread_ts"] = thread_ts
    if blocks is not None:
        msg["blocks"] = blocks
    return {"type": "block_actions", "user": {"id": user}, "channel": {"id": channel}, "message": msg,
            "actions": [{"action_id": action_id, "value": value, "type": "button"}]}


# ---------------------------------------------------------------- helpers

def test_pick_agent_only_for_allowed_names():
    agents = ("homelab-knowledge", "k8s-reader")
    assert sb.pick_agent("k8s-reader: why?", agents, "homelab-knowledge") == ("k8s-reader", "why?")
    assert sb.pick_agent("note: why?", agents, "homelab-knowledge") == ("homelab-knowledge", "note: why?")
    assert sb.pick_agent("  k8s-reader :  spaced ", agents, "homelab-knowledge") == ("k8s-reader", "spaced")
    assert sb.pick_agent("", agents, "homelab-knowledge") == ("homelab-knowledge", "")


def test_strip_mention_removes_only_the_bot():
    assert sb.strip_mention(f"<@{BOT}> hi <@U1>", BOT) == "hi <@U1>"
    assert sb.strip_mention(f"<@{BOT}|bridge> hi", BOT) == "hi"


def test_parse_value_shapes():
    assert sb.parse_value("d:ctx-1:n0nce_-") == ("d", "ctx-1", "n0nce_-")
    assert sb.parse_value("s:c:n") == ("s", "c", "n")
    assert sb.parse_value("x:c:n") is None and sb.parse_value("d:c") is None and sb.parse_value("") is None


def test_clip_keeps_under_the_limit():
    assert len(sb.clip("x" * 5000)) <= sb.TEXT_LIMIT and sb.clip("x" * 5000).endswith("(truncated)")
    assert sb.clip("short") == "short"


def test_config_from_env_requires_the_four_secrets_and_ids():
    with pytest.raises(ValueError, match="SLACK_APP_TOKEN"):
        sb.Config.from_env({"SLACK_BOT_TOKEN": "b", "OWNER_SLACK_USER_ID": "U", "OWNER_EMAIL": "e"})
    cfg = sb.Config.from_env({"SLACK_BOT_TOKEN": "b", "SLACK_APP_TOKEN": "a", "OWNER_SLACK_USER_ID": "U",
                              "OWNER_EMAIL": "e", "AGENTS": "k8s-reader, restarter", "DEFAULT_AGENT": "homelab-knowledge",
                              "KAGENT_TOKEN_FILE": ""})
    assert cfg.agents == ("homelab-knowledge", "k8s-reader", "restarter") and cfg.token_file is None


# ---------------------------------------------------------------- who may talk

def test_a_stranger_gets_an_ephemeral_refusal_and_nothing_is_sent():
    agent = FakeAgent(completed("never"))
    bridge, api, logs, disp = make(agent)
    bridge.handle_event(dm("hello", user=OTHER))
    bridge.handle_action(click("approve", "d:ctx:n", user=OTHER))
    assert agent.sent == [] and disp.calls == []
    eph = api.of("chat.postEphemeral")
    assert len(eph) == 2 and eph[0]["user"] == OTHER
    assert [x["event"] for x in logs] == ["refused", "refused"]


def test_bot_messages_edits_and_the_message_copy_of_a_mention_are_ignored():
    bridge, api, logs, _ = make(FakeAgent())
    assert not bridge.is_for_me({"type": "message", "channel_type": "im", "bot_id": "B1", "text": "x"})
    assert not bridge.is_for_me({"type": "message", "channel_type": "im", "subtype": "message_changed"})
    assert not bridge.is_for_me({"type": "message", "channel": "C1", "text": f"<@{BOT}> hi", "ts": "1"})
    assert not bridge.is_for_me({"type": "message", "channel": "C1", "text": "unrelated", "thread_ts": "0.1"})
    assert bridge.is_for_me({"type": "message", "channel_type": "im", "text": "hi"})
    assert bridge.is_for_me({"type": "app_mention", "channel": "C1", "text": f"<@{BOT}> hi"})


# ---------------------------------------------------------------- questions

def test_dm_asks_the_default_agent_with_the_hitl_extension_and_posts_the_answer_with_the_session_line():
    agent = FakeAgent(completed("42", session="sess-a"))
    bridge, api, logs, _ = make(agent)
    bridge.handle_event(dm("what is the answer?"))
    s = agent.sent[0]
    assert s["url"] == "http://a2a:8083/agents/kagent/homelab-knowledge"
    assert s["text"] == "what is the answer?" and s["context_id"] is None and s["metadata"] is None
    assert s["user"] == "slack-bridge" and s["token_file"] is None
    msgs = api.of("chat.postMessage")
    assert len(msgs) == 1 and msgs[0]["thread_ts"] == "1.1" and msgs[0]["channel"] == "D1"
    assert msgs[0]["text"].startswith("42\n_session sess-a")
    assert "--as-serviceaccount slack-bridge/slack-bridge --context sess-a" in msgs[0]["text"]
    reactions = [(p["name"], m) for m, p in api.calls if m.startswith("reactions.")]
    assert reactions == [("eyes", "reactions.add"), ("eyes", "reactions.remove"), ("white_check_mark", "reactions.add")]
    assert logs[-1] == {"event": "ask", "user": OWNER, "agent": "homelab-knowledge", "session": "sess-a",
                        "state": "TASK_STATE_COMPLETED", "error": None}


def test_mention_picks_the_agent_and_a_thread_reply_continues_the_session_without_a_mention():
    agent = FakeAgent(completed("first", session="sess-b"), completed("second", session="sess-b"))
    bridge, api, _, _ = make(agent)
    bridge.handle_event(mention("k8s-reader: which pods restarted?", ts="2.1"))
    assert agent.sent[0]["url"].endswith("/agents/kagent/k8s-reader") and agent.sent[0]["text"] == "which pods restarted?"
    # a plain message in the thread (no mention): accepted because the thread is known
    bridge.handle_event({"type": "message", "channel": "C1", "user": OWNER, "text": "and yesterday?", "ts": "2.2", "thread_ts": "2.1"})
    assert agent.sent[1]["context_id"] == "sess-b" and agent.sent[1]["text"] == "and yesterday?"
    msgs = api.of("chat.postMessage")
    assert "_session sess-b" in msgs[0]["text"] and "_session" not in msgs[1]["text"], "the session line once"
    assert msgs[1]["thread_ts"] == "2.1"


def test_a_redelivered_event_is_handled_once():
    agent = FakeAgent(completed("once"), completed("twice"))
    bridge, api, _, _ = make(agent)
    bridge.handle_event(dm("hi", ts="5.5"))
    bridge.handle_event(dm("hi", ts="5.5"))
    assert len(agent.sent) == 1


def test_empty_text_after_the_mention_gets_a_hint_and_no_send():
    agent = FakeAgent()
    bridge, api, _, _ = make(agent)
    bridge.handle_event(mention("", ts="3.1"))
    assert agent.sent == [] and "Say something" in api.of("chat.postMessage")[0]["text"]


def test_a2a_failure_and_timeout_land_in_the_thread():
    agent = FakeAgent(a2a.A2AError("HTTP 403 from x: RBAC: access denied"), TimeoutError())
    bridge, api, logs, _ = make(agent)
    bridge.handle_event(dm("one", ts="6.1"))
    bridge.handle_event(dm("two", ts="7.1"))
    msgs = api.of("chat.postMessage")
    assert "access denied" in msgs[0]["text"] and msgs[0]["text"].startswith(":x:")
    assert "no answer after 5s" in msgs[1]["text"]
    assert [x["error"] is not None for x in logs if x["event"] == "ask"] == [True, True]
    assert ("x", "reactions.add") in [(p["name"], m) for m, p in api.calls if m.startswith("reactions.")]


def test_a_failed_task_is_reported():
    agent = FakeAgent({"task": {"id": "t", "contextId": "s", "status": {"state": "TASK_STATE_FAILED",
                                                                        "message": {"parts": [{"text": "boom"}]}}}})
    bridge, api, _, _ = make(agent)
    bridge.handle_event(dm("go"))
    assert "ended in TASK_STATE_FAILED: boom" in api.of("chat.postMessage")[0]["text"]


# ---------------------------------------------------------------- the agent asks

def test_an_ask_user_request_is_answered_by_the_next_reply_on_the_same_task():
    agent = FakeAgent(asked("which namespace?", session="sess-q", task="task-q", req="req-9"),
                      completed("done", session="sess-q"))
    bridge, api, _, _ = make(agent)
    bridge.handle_event(dm("restart it", ts="8.1"))
    assert "asks: which namespace?" in api.of("chat.postMessage")[0]["text"]
    bridge.handle_event(dm("n8n", ts="8.2", thread_ts="8.1"))
    s = agent.sent[1]
    assert s["context_id"] == "sess-q" and s["task_id"] == "task-q"
    assert s["metadata"] == a2a.answer_metadata("req-9", "n8n")
    assert api.of("chat.postMessage")[1]["text"] == "done"


# ---------------------------------------------------------------- the agent pauses (own session)

def test_a_pause_posts_the_exact_calls_with_buttons_carrying_an_s_value():
    agent = FakeAgent(paused(session="sess-p", task="task-p"))
    bridge, api, _, _ = make(agent)
    bridge.handle_event(mention("restarter: restart n8n/n8n-0", ts="4.1"))
    msg = api.of("chat.postMessage")[0]
    assert "k8s_delete_resource" in msg["text"] and '"resource_name": "n8n-0"' in msg["text"]
    actions = next(b for b in msg["blocks"] if b["type"] == "actions")
    ids = [e["action_id"] for e in actions["elements"]]
    assert ids == ["approve", "reject"]
    value = actions["elements"][0]["value"]
    kind, ctx, nonce = sb.parse_value(value)
    assert kind == "s" and ctx == "sess-p" and bridge.pending["sess-p"].nonce == nonce
    assert bridge.pending["sess-p"].calls == [{"id": "call-1", "name": "k8s_delete_resource",
                                               "args": {"resource_type": "pod", "resource_name": "n8n-0", "namespace": "n8n"}}]


def pending_bridge(*more):
    agent = FakeAgent(paused(session="sess-p", task="task-p"), *more)
    bridge, api, logs, disp = make(agent)
    bridge.handle_event(mention("restarter: restart n8n/n8n-0", ts="4.1"))
    msg = api.of("chat.postMessage")[0]
    value = next(b for b in msg["blocks"] if b["type"] == "actions")["elements"][0]["value"]
    return bridge, api, logs, agent, msg, value


def test_approve_sends_the_registered_ids_on_the_registered_task_and_strips_the_buttons():
    bridge, api, logs, agent, msg, value = pending_bridge(completed("restart requested: n8n/n8n-0", session="sess-p"))
    bridge.handle_action(click("approve", value, ts="ts-1", thread_ts="4.1", blocks=msg["blocks"], text=msg["text"]))
    s = agent.sent[1]
    assert s["context_id"] == "sess-p" and s["task_id"] == "task-p" and s["text"] == "approved"
    assert s["metadata"] == a2a.approval_metadata(["call-1"], approved=True)
    upd = api.of("chat.update")[0]
    assert upd["ts"] == "ts-1" and not any(b["type"] == "actions" for b in upd["blocks"])
    assert "Approved by o@e.com via Slack" in upd["blocks"][-1]["elements"][0]["text"]
    texts = [m["text"] for m in api.of("chat.postMessage")]
    assert texts[1] == "restart requested: n8n/n8n-0" and "Approved; `restarter` answered above" in texts[2]
    assert "sess-p" not in bridge.pending
    assert logs[-1]["event"] == "decision" and logs[-1]["approved"] and logs[-1]["kind"] == "s"


def test_reject_carries_the_fixed_reason():
    bridge, api, logs, agent, msg, value = pending_bridge(completed("no restart: rejected", session="sess-p"))
    bridge.handle_action(click("reject", value, ts="ts-1", thread_ts="4.1", blocks=msg["blocks"]))
    s = agent.sent[1]
    assert s["metadata"] == a2a.approval_metadata(["call-1"], approved=False, reason="rejected in Slack")
    assert s["text"] == "rejected: rejected in Slack"


def test_a_second_click_or_a_wrong_nonce_never_sends_again():
    bridge, api, logs, agent, msg, value = pending_bridge(completed("ok", session="sess-p"))
    bridge.handle_action(click("approve", value, ts="ts-1", thread_ts="4.1", blocks=msg["blocks"]))
    bridge.handle_action(click("approve", value, ts="ts-1", thread_ts="4.1", blocks=msg["blocks"]))     # same click redelivered
    bridge.handle_action(click("approve", "s:sess-p:other", ts="ts-2", thread_ts="4.1", blocks=msg["blocks"]))
    assert len(agent.sent) == 2
    assert "nothing pending" in api.of("chat.postMessage")[-1]["text"]


def test_a_wrong_nonce_on_a_still_pending_item_is_stale_and_keeps_it():
    bridge, api, logs, agent, msg, value = pending_bridge(completed("ok", session="sess-p"))
    bridge.handle_action(click("approve", "s:sess-p:wrong-nonce", ts="ts-2", thread_ts="4.1"))
    assert len(agent.sent) == 1 and "sess-p" in bridge.pending
    assert "already decided" in api.of("chat.postMessage")[-1]["text"]


def test_a_pause_after_the_decision_becomes_a_new_pending_block_never_auto_approved():
    bridge, api, logs, agent, msg, value = pending_bridge(paused(session="sess-p", task="task-p2", call_id="call-2"))
    bridge.handle_action(click("approve", value, ts="ts-1", thread_ts="4.1", blocks=msg["blocks"]))
    assert len(agent.sent) == 2
    assert bridge.pending["sess-p"].task_id == "task-p2" and bridge.pending["sess-p"].calls[0]["id"] == "call-2"
    assert bridge.pending["sess-p"].nonce != sb.parse_value(value)[2]


def test_an_undelivered_decision_restores_the_item():
    bridge, api, logs, agent, msg, value = pending_bridge(a2a.A2AError("HTTP 502 from x: bad gateway"))
    bridge.handle_action(click("approve", value, ts="ts-1", thread_ts="4.1", blocks=msg["blocks"]))
    assert "sess-p" in bridge.pending and bridge.pending["sess-p"].nonce == sb.parse_value(value)[2]
    assert "not delivered" in api.of("chat.postMessage")[-1]["text"]


def test_a_timed_out_decision_counts_as_delivered():
    bridge, api, logs, agent, msg, value = pending_bridge(TimeoutError())
    bridge.handle_action(click("approve", value, ts="ts-1", thread_ts="4.1", blocks=msg["blocks"]))
    assert "sess-p" not in bridge.pending
    assert "outcome unknown" in api.of("chat.postMessage")[-1]["text"]


# ---------------------------------------------------------------- the dispatcher's restarts

def test_a_d_click_is_forwarded_to_the_dispatcher_with_the_owner_as_approver():
    disp = FakeDispatcher()
    bridge, api, logs, _ = make(FakeAgent(), disp)
    n8n_blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "Restart awaiting approval"}},
                  {"type": "actions", "elements": []}]
    bridge.handle_action(click("approve", "d:sess-r:n0nce", ts="n8n-1", blocks=n8n_blocks, text="Restart awaiting approval"))
    url, body = disp.calls[0]
    assert url == "http://decide:8082/decide/sess-r/approve"
    assert body == {"nonce": "n0nce", "reason": None, "approver": "o@e.com"}
    assert api.of("chat.update")[0]["blocks"][0]["text"]["text"] == "Restart awaiting approval"
    reply = api.of("chat.postMessage")[0]
    assert reply["thread_ts"] == "n8n-1" and "Decision by o@e.com sent" in reply["text"]
    assert logs[-1] == {"event": "decision", "kind": "d", "session": "sess-r", "approved": True,
                        "approved_by": "o@e.com", "state": "sent"}


def test_a_d_reject_sends_the_reason():
    disp = FakeDispatcher()
    bridge, api, logs, _ = make(FakeAgent(), disp)
    bridge.handle_action(click("reject", "d:sess-r:n0nce", ts="n8n-1"))
    assert disp.calls[0][0].endswith("/decide/sess-r/reject")
    assert disp.calls[0][1]["reason"] == "rejected in Slack"


@pytest.mark.parametrize("status, needle, state", [
    (404, "no longer holds", "missing"),
    (409, "already decided", "stale"),
    (403, "refused the decision", "refused"),
    (502, "could not reach", "error"),
])
def test_dispatcher_answers_are_mapped_to_thread_text(status, needle, state):
    disp = FakeDispatcher(status=status, text="x")
    bridge, api, logs, _ = make(FakeAgent(), disp)
    bridge.handle_action(click("approve", "d:sess-r:n0nce", ts="n8n-1"))
    assert needle in api.of("chat.postMessage")[0]["text"]
    assert logs[-1]["state"] == state


def test_a_ctx_with_odd_characters_is_url_quoted():
    disp = FakeDispatcher()
    bridge, api, logs, _ = make(FakeAgent(), disp)
    bridge.handle_action(click("approve", "d:a/b c:n", ts="n8n-2"))
    assert disp.calls[0][0] == "http://decide:8082/decide/a%2Fb%20c/approve"


def test_an_unknown_button_value_is_refused_without_a_call():
    disp = FakeDispatcher()
    bridge, api, logs, _ = make(FakeAgent(), disp)
    bridge.handle_action(click("approve", "garbage", ts="n8n-3"))
    assert disp.calls == [] and "no decision" in api.of("chat.postMessage")[0]["text"]


# ---------------------------------------------------------------- envelopes and health

def test_handle_envelope_routes_and_survives_a_bad_payload():
    agent = FakeAgent(completed("ok"))
    bridge, api, logs, _ = make(agent)
    bridge.handle_envelope({"type": "events_api", "payload": {"event": dm("hi")}})
    bridge.handle_envelope({"type": "interactive", "payload": {"type": "block_actions", "user": None}})
    bridge.handle_envelope({"type": "slash_commands", "payload": {}})
    assert len(agent.sent) == 1 and not any(x["event"] == "error" for x in logs)


def test_health_endpoints_follow_the_socket():
    import threading
    import urllib.request
    import urllib.error
    ready = threading.Event()
    srv = sb.serve_health(ready, 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert urllib.request.urlopen(base + "/healthz", timeout=5).status == 200
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/readyz", timeout=5)
        assert err.value.code == 503
        ready.set()
        assert urllib.request.urlopen(base + "/readyz", timeout=5).status == 200
    finally:
        srv.shutdown()
