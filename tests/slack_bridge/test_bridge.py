# tests/slack_bridge/test_bridge.py
"""scripts/slack_bridge.py: events and clicks → A2A sends, Slack posts, dispatcher decisions,
with a fake agent, a fake Slack and a fake dispatcher."""
from pathlib import Path
import importlib.util
import json
import sys
import urllib.parse

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


def submitted(task: dict) -> dict:
    """The first stream event: the task with its ids and no answer yet."""
    return {"result": {"task": {"id": task["id"], "contextId": task["contextId"], "status": {"state": "TASK_STATE_SUBMITTED"}}}}


def working(task: dict) -> dict:
    return {"result": {"statusUpdate": {"taskId": task["id"], "contextId": task["contextId"], "status": {"state": "TASK_STATE_WORKING"}}}}


class FakeAgent:
    """One reply per send. A reply is the final SendMessage result ({"task": …} or {"message": …}),
    an exception (raised before any event), or a list of stream events / exceptions to replay
    verbatim (an exception mid-list breaks the stream). `tasks` answers GetTask by id."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []
        self.fetched = []
        self.tasks: dict[str, list] = {}   # task id -> successive GetTask results

    def stream(self, url, payload, headers, timeout):
        req = json.loads(payload)
        msg = req["params"]["message"]
        self.sent.append({"url": url, "text": msg["parts"][0]["text"], "context_id": msg.get("contextId"),
                          "task_id": msg.get("taskId"), "metadata": msg.get("metadata"), "user": headers.get("X-User-Id"),
                          "auth": headers.get("Authorization"), "method": req["method"], "accept": headers.get("Accept"),
                          "timeout": timeout, "payload": payload})
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        if isinstance(r, list) or hasattr(r, "__next__"):
            for ev in r:
                if isinstance(ev, BaseException):
                    raise ev
                yield ev
            return
        task = r.get("task")
        if task is None:
            yield {"result": r}
            return
        self.tasks.setdefault(task["id"], []).append(r)
        yield submitted(task)
        yield working(task)
        yield {"result": r}

    def get_task(self, url, task_id, **kw):
        self.fetched.append(task_id)
        results = self.tasks.get(task_id)
        if not results:
            raise a2a.A2AError("A2A error -32001: task not found")
        return results.pop(0) if len(results) > 1 else results[0]


class FakeSlackAPI:
    """Records every Web API call; answers ok. `parents` answers conversations.replies."""

    def __init__(self):
        self.calls = []
        self.parents: dict[tuple[str, str], dict | Exception] = {}   # (channel, ts) -> parent message

    def post(self, url, payload, headers, timeout):
        method = url.rsplit("/", 1)[1]
        if headers["Content-Type"].startswith("application/json"):
            params = json.loads(payload)
        else:
            params = {k: v[0] for k, v in urllib.parse.parse_qs(payload.decode()).items()}
        self.calls.append((method, params))
        if method == "conversations.replies":
            parent = self.parents.get((params["channel"], params["ts"]))
            if isinstance(parent, Exception):
                raise parent
            if parent is None:
                return {"ok": False, "error": "thread_not_found"}
            return {"ok": True, "messages": [parent]}
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
                       stream=agent.stream, get_task=agent.get_task, post=disp.post, log=logs.append,
                       sleep=lambda s: None, timer=FakeTimer)
    return bridge, api, logs, disp


class FakeTimer:
    """threading.Timer that fires when the test says so."""
    armed: list = []

    def __init__(self, delay, fn):
        self.delay, self.fn, self.cancelled = delay, fn, False
        FakeTimer.armed.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn()


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
    assert s["user"] == "slack-bridge" and s["auth"] is None
    assert s["method"] == "SendStreamingMessage" and s["accept"] == "text/event-stream"
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


# ---------------------------------------------------------------- thread context

ALERT = "Grafana: [RESOLVED] Falco detected a sensitive-file read (Agent Guardrails security warning)"
PARENT_TS = "1792030920.000100"     # 2026-10-15 02:22 UTC, 2026-10-14 22:22 EDT; a thread's ts IS its parent's


def test_a_mention_in_a_thread_the_bridge_does_not_know_carries_the_parent_message_as_context():
    """Mention the bot under an n8n alert: the agent gets the alert text, once, with the question first."""
    agent = FakeAgent(completed("it was Falco", session="sess-t"), completed("more", session="sess-t"))
    bridge, api, logs, _ = make(agent)
    api.parents[("C1", PARENT_TS)] = {"ts": PARENT_TS, "text": ALERT, "bot_id": "B1",
                                      "bot_profile": {"name": "OnCall Agent v2"}}
    bridge.handle_event(mention("review this alert", ts="7.1", thread_ts=PARENT_TS))
    sent = agent.sent[0]["text"]
    assert sent.startswith("review this alert\n")
    assert ALERT in sent and "OnCall Agent v2" in sent
    assert "2026-10-15 02:22 UTC" in sent, "the parent's time, in UTC when no zone is configured"
    fetches = api.of("conversations.replies")
    assert fetches == [{"channel": "C1", "ts": PARENT_TS, "limit": "1"}], "form-encoded, the parent only"
    # the follow-up in the same thread is the bare text: the session already has the context
    bridge.handle_event({"type": "message", "channel": "C1", "user": OWNER, "text": "and why?", "ts": "7.2", "thread_ts": PARENT_TS})
    assert agent.sent[1]["text"] == "and why?" and len(api.of("conversations.replies")) == 1
    assert api.of("chat.postMessage")[0]["text"].startswith("it was Falco\n_session sess-t")


def test_a_mention_that_starts_its_own_thread_fetches_nothing():
    agent = FakeAgent(completed("ok"))
    bridge, api, _, _ = make(agent)
    bridge.handle_event(mention("k8s-reader: pods?", ts="8.1"))
    assert api.of("conversations.replies") == [] and agent.sent[0]["text"] == "pods?"


def test_a_parent_the_bridge_cannot_read_is_logged_and_the_question_still_goes():
    agent = FakeAgent(completed("ok"))
    bridge, api, logs, _ = make(agent)
    bridge.handle_event(mention("review this", ts="9.1", thread_ts="9.0"))   # no parent registered → thread_not_found
    assert agent.sent[0]["text"] == "review this"
    assert any(x.get("event") == "slack-error" and x.get("method") == "conversations.replies" for x in logs)
    assert api.of("chat.postMessage")[0]["text"].startswith("ok\n")


def test_a_parent_without_text_uses_its_section_blocks_and_an_empty_one_adds_nothing():
    agent = FakeAgent(completed("a"), completed("b"))
    bridge, api, _, _ = make(agent)
    api.parents[("C1", "10.0")] = {"ts": "10.0", "user": "U0WNER",
                                   "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "from a block"}},
                                              {"type": "actions"}]}
    api.parents[("C1", "11.0")] = {"ts": "11.0", "user": "U0WNER", "text": "   "}
    bridge.handle_event(mention("q1", ts="10.1", thread_ts="10.0"))
    bridge.handle_event(mention("q2", ts="11.1", thread_ts="11.0"))
    assert "from a block" in agent.sent[0]["text"] and "<@U0WNER>" in agent.sent[0]["text"]
    assert agent.sent[1]["text"] == "q2"


def test_the_owner_timezone_is_told_to_the_agent_on_the_first_message_of_a_thread_only():
    agent = FakeAgent(completed("a", session="s"), completed("b", session="s"))
    bridge, api, _, _ = make(agent, owner_timezone="America/New_York")
    api.parents[("C1", PARENT_TS)] = {"ts": PARENT_TS, "text": ALERT, "user": "UX"}
    bridge.handle_event(mention("when was this?", ts="12.1", thread_ts=PARENT_TS))
    first = agent.sent[0]["text"]
    assert "2026-10-14 22:22 EDT" in first, "the parent's time in the owner's zone"
    assert "America/New_York" in first and "owner's local time" in first
    bridge.handle_event(dm("hello", ts="13.1"))
    assert agent.sent[1]["text"].startswith("hello\n") and "America/New_York" in agent.sent[1]["text"]
    bridge.handle_event({"type": "message", "channel": "C1", "user": OWNER, "text": "more", "ts": "12.2", "thread_ts": PARENT_TS})
    assert agent.sent[2]["text"] == "more"


def test_an_unknown_timezone_falls_back_to_utc_and_says_so():
    agent = FakeAgent(completed("a"))
    bridge, api, logs, _ = make(agent, owner_timezone="Mars/Olympus_Mons")
    bridge.handle_event(dm("hi", ts="14.1"))
    assert "UTC" in agent.sent[0]["text"] and "Mars/Olympus_Mons" in agent.sent[0]["text"]
    assert any(x.get("event") == "timezone" for x in logs)


def test_config_reads_the_owner_timezone():
    env = {"SLACK_BOT_TOKEN": "xoxb", "SLACK_APP_TOKEN": "xapp", "OWNER_SLACK_USER_ID": "U1", "OWNER_EMAIL": "o@e",
           "OWNER_TIMEZONE": "Europe/Madrid"}
    assert sb.Config.from_env(env).owner_timezone == "Europe/Madrid"
    del env["OWNER_TIMEZONE"]
    assert sb.Config.from_env(env).owner_timezone is None


# ---------------------------------------------------------------- streaming

def test_the_ids_are_known_from_the_first_event_and_a_dropped_stream_falls_back_to_get_task():
    final = completed("late answer", session="sess-s", task="task-s")
    agent = FakeAgent([submitted(final["task"]), working(final["task"]), ConnectionError("stream reset")])
    agent.tasks["task-s"] = [final]
    bridge, api, logs, _ = make(agent)
    bridge.handle_event(dm("slow one", ts="20.1"))
    assert agent.fetched == ["task-s"], "the authoritative task, fetched after the stream broke"
    assert bridge.threads[("D1", "20.1")].context_id == "sess-s"
    assert api.of("chat.postMessage")[0]["text"].startswith("late answer\n_session sess-s")
    assert any(x["event"] == "stream" and "stream reset" in x["error"] for x in logs)
    assert logs[-1]["session"] == "sess-s" and logs[-1]["error"] is None


def test_a_timeout_after_the_ids_keeps_the_session_in_the_thread():
    """The agent outlives A2A_TIMEOUT: the thread still gets the session id, and a later reply
    continues it (the run goes on server-side)."""
    task = {"id": "task-l", "contextId": "sess-l"}
    still = {"task": {"id": "task-l", "contextId": "sess-l", "status": {"state": "TASK_STATE_WORKING"}}}
    agent = FakeAgent([submitted(task), TimeoutError()], completed("second", session="sess-l", task="task-l2"))
    agent.tasks["task-l"] = [still, still, still]
    bridge, api, logs, _ = make(agent)
    ticks = iter([0.0, 0.0, 1.0, 2.0, 6.0, 7.0, 8.0, 9.0, 100.0, 101.0])
    bridge.clock = lambda: next(ticks)
    bridge.handle_event(mention("deep dive", ts="21.1"))
    msgs = api.of("chat.postMessage")
    assert msgs[0]["text"].startswith(":x:") and "no answer after 5s" in msgs[0]["text"]
    assert "the run continues" in msgs[0]["text"] and "--context sess-l" in msgs[0]["text"]
    assert len(agent.fetched) >= 2, "polled GetTask until the deadline"
    assert [p["name"] for m, p in api.calls if m == "reactions.add"][-1] == "x"
    assert logs[-1]["session"] == "sess-l" and "no answer" in logs[-1]["error"]
    bridge.handle_event({"type": "message", "channel": "C1", "user": OWNER, "text": "and?", "ts": "21.2", "thread_ts": "21.1"})
    assert agent.sent[1]["context_id"] == "sess-l", "the thread kept the session"
    assert "_session" not in api.of("chat.postMessage")[1]["text"], "the error already carried the line"


def test_a_long_run_posts_one_still_working_note_with_the_session_line_and_the_answer_then_has_no_footer():
    final = completed("done", session="sess-p", task="task-p")
    FakeTimer.armed.clear()

    def events():
        yield submitted(final["task"])
        FakeTimer.armed[-1].fire()          # two minutes pass while the agent works
        yield {"result": final}
    agent = FakeAgent(events())
    agent.tasks["task-p"] = [final]
    bridge, api, _, _ = make(agent)
    bridge.handle_event(dm("long", ts="22.1"))
    msgs = api.of("chat.postMessage")
    assert len(msgs) == 2
    assert msgs[0]["text"].startswith(":hourglass_flowing_sand:") and "still working" in msgs[0]["text"]
    assert "_session sess-p" in msgs[0]["text"] and msgs[0]["thread_ts"] == "22.1"
    assert msgs[1]["text"] == "done", "the note already carried the session line"
    assert FakeTimer.armed[-1].cancelled
    FakeTimer.armed[-1].fire()
    assert len(api.of("chat.postMessage")) == 2, "a cancelled timer posts nothing"


def test_a_quick_answer_never_posts_the_note():
    FakeTimer.armed.clear()
    agent = FakeAgent(completed("fast"))
    bridge, api, _, _ = make(agent)
    bridge.handle_event(dm("quick", ts="23.1"))
    assert FakeTimer.armed[-1].cancelled and len(api.of("chat.postMessage")) == 1


def test_a_direct_message_result_is_an_answer_without_a_task():
    agent = FakeAgent({"message": {"messageId": "m1", "contextId": "sess-m", "role": "ROLE_AGENT", "parts": [{"text": "plain"}]}})
    bridge, api, _, _ = make(agent)
    bridge.handle_event(dm("hey", ts="24.1"))
    assert agent.fetched == [] and api.of("chat.postMessage")[0]["text"].startswith("plain\n_session sess-m")


def test_a_stream_error_event_is_the_agents_error():
    agent = FakeAgent([{"error": {"code": -31403, "message": "permission denied"}}])
    bridge, api, _, _ = make(agent)
    bridge.handle_event(dm("hey", ts="25.1"))
    assert "permission denied" in api.of("chat.postMessage")[0]["text"]


def test_a_stream_that_ends_without_ids_is_an_error():
    agent = FakeAgent([])
    bridge, api, _, _ = make(agent)
    bridge.handle_event(dm("hey", ts="26.1"))
    assert "without a task" in api.of("chat.postMessage")[0]["text"]


# kagent's proven rejection, as recorded on the wire: the session could not take the input and
# nothing was dispatched. 2026-10-26: a new homelab-knowledge session's sandbox needed a cold image
# pull (5.4 s), Substrate's router gives a restore 5 s, and Slack got this error with session "?".
NOT_ACCEPTED = json.loads((REPO / "tests" / "eval-corpus" / "fixtures" / "a2a-v1" / "error-send-not-accepted.json").read_text())


def test_a_not_accepted_rejection_is_resent_identically_and_the_answer_lands():
    agent = FakeAgent([NOT_ACCEPTED], completed("answer", session="sess-r"))
    bridge, api, logs, _ = make(agent)
    slept = []
    bridge.sleep = slept.append
    bridge.handle_event(dm("hello", ts="27.1"))
    assert len(agent.sent) == 2
    assert agent.sent[0]["payload"] == agent.sent[1]["payload"], "the resend is byte-identical (same messageId)"
    assert slept == [0.1], "waits the rejection's retryAfterMs"
    assert api.of("chat.postMessage")[0]["text"].startswith("answer\n_session sess-r")
    assert any(x["event"] == "retry" and x["agent"] == "homelab-knowledge" for x in logs)
    assert logs[-1]["event"] == "ask" and logs[-1]["error"] is None


def test_a_rejection_past_the_retry_deadline_is_reported_without_a_session_line():
    agent = FakeAgent(*[[NOT_ACCEPTED]] * 10)
    bridge, api, logs, _ = make(agent)
    now = [0.0]
    bridge.clock = lambda: now[0]
    bridge.sleep = lambda s: now.__setitem__(0, now[0] + 10.0)   # each wait stands for a slow attempt
    bridge.handle_event(dm("hello", ts="28.1"))
    assert len(agent.sent) == 4, "sends at t=0, 10, 20, 30; the next wait would pass the 30 s deadline"
    text = api.of("chat.postMessage")[0]["text"]
    assert text.startswith(":x:") and "input was not accepted" in text
    assert "_session" not in text and "--context ?" not in text, "no session was started: nothing to continue"
    assert logs[-1]["session"] is None and "-32004" in logs[-1]["error"]


def test_minus_32004_without_kagents_reason_is_not_resent():
    other = {"jsonrpc": "2.0", "id": "1", "error": {"code": -32004, "message": "this operation is not supported"}}
    agent = FakeAgent([other])
    bridge, api, _, _ = make(agent)
    bridge.sleep = lambda s: pytest.fail("resent an error that is not a proven rejection")
    bridge.handle_event(dm("hello", ts="29.1"))
    assert len(agent.sent) == 1 and "not supported" in api.of("chat.postMessage")[0]["text"]


def test_a_rejection_after_the_ids_is_not_resent():
    """Once kagent named the task, the input may be running: never send it twice."""
    final = completed("from the store", session="sess-t", task="task-t")
    agent = FakeAgent([submitted(final["task"]), NOT_ACCEPTED])
    agent.tasks["task-t"] = [final]
    bridge, api, _, _ = make(agent)
    bridge.handle_event(dm("hello", ts="30.1"))
    assert len(agent.sent) == 1 and agent.fetched == ["task-t"]
    assert api.of("chat.postMessage")[0]["text"].startswith("from the store\n_session sess-t")


def test_after_an_error_without_a_session_the_next_answer_carries_the_session_line():
    agent = FakeAgent(a2a.A2AError("HTTP 503 from x: upstream connect error"), completed("second", session="sess-n"))
    bridge, api, _, _ = make(agent)
    bridge.handle_event(mention("first", ts="31.1"))
    assert "_session" not in api.of("chat.postMessage")[0]["text"]
    bridge.handle_event({"type": "message", "channel": "C1", "user": OWNER, "text": "again", "ts": "31.2", "thread_ts": "31.1"})
    assert agent.sent[1]["context_id"] is None, "still no session to continue"
    assert api.of("chat.postMessage")[1]["text"].startswith("second\n_session sess-n")


def test_a_still_working_note_before_the_ids_has_no_session_line_and_the_answer_carries_it():
    final = completed("done", session="sess-q", task="task-q")
    FakeTimer.armed.clear()

    def events():
        FakeTimer.armed[-1].fire()          # two minutes pass before kagent names the session
        yield submitted(final["task"])
        yield {"result": final}
    agent = FakeAgent(events())
    agent.tasks["task-q"] = [final]
    bridge, api, _, _ = make(agent)
    bridge.handle_event(dm("slow start", ts="32.1"))
    msgs = api.of("chat.postMessage")
    assert "still working" in msgs[0]["text"] and "_session" not in msgs[0]["text"]
    assert msgs[1]["text"].startswith("done\n_session sess-q")


def test_sse_events_parses_data_lines_and_skips_comments_and_blanks():
    raw = [b": keepalive\n", b"\n", b"data: {\"a\": 1}\n", b"\n", b"event: x\n", b"data: {\"b\":\n", b"data: 2}\n", b"\n", b"data: not json\n", b"\n"]
    assert list(sb.sse_events(iter(raw))) == [{"a": 1}, {"b": 2}]


# ---------------------------------------------------------------- long answers, failed runs

def test_split_text_keeps_paragraphs_then_lines_then_cuts_hard():
    assert sb.split_text("short", 100) == ["short"]
    paras = "\n\n".join(["a" * 60, "b" * 60, "c" * 60])
    assert sb.split_text(paras, 130) == ["a" * 60 + "\n\n" + "b" * 60, "c" * 60]
    lines = "\n".join(["x" * 50, "y" * 50, "z" * 50])
    assert sb.split_text(lines, 105) == ["x" * 50 + "\n" + "y" * 50, "z" * 50]
    chunks = sb.split_text("q" * 250, 100)
    assert [len(c) for c in chunks] == [100, 100, 50] and "".join(chunks) == "q" * 250
    assert all(len(c) <= 100 for c in sb.split_text("w" * 99 + "\n\n" + "v" * 150, 100))


def test_a_long_answer_is_posted_in_order_in_parts_with_the_session_line_last():
    answer = "\n\n".join(f"paragraph {i} " + "words " * 150 for i in range(8))   # ~6.5k chars
    agent = FakeAgent(completed(answer, session="sess-long"))
    bridge, api, _, _ = make(agent)
    bridge.handle_event(dm("tell me everything", ts="30.1"))
    msgs = api.of("chat.postMessage")
    assert len(msgs) >= 2 and all(len(m["text"]) <= sb.TEXT_LIMIT for m in msgs)
    assert all(m["thread_ts"] == "30.1" for m in msgs)
    assert "(truncated)" not in "".join(m["text"] for m in msgs)
    assert msgs[0]["text"].startswith("_part 1/") and msgs[0]["text"].split("\n", 1)[1].startswith("paragraph 0")
    assert "_session sess-long" in msgs[-1]["text"] and "_session" not in msgs[0]["text"]
    body = "".join(m["text"].split("\n", 1)[1] for m in msgs)
    assert body.replace("\n", "") .startswith(answer.replace("\n", "")[:200])


def test_a_failed_task_gets_the_x_reaction():
    failed = {"task": {"id": "t", "contextId": "s", "status": {"state": "TASK_STATE_FAILED",
                                                                "message": {"parts": [{"text": "agent run failed: MCP init 503"}]}}}}
    agent = FakeAgent(failed)
    bridge, api, _, _ = make(agent)
    bridge.handle_event(dm("go", ts="31.1"))
    assert [p["name"] for m, p in api.calls if m == "reactions.add"][-1] == "x"
    assert api.of("chat.postMessage")[0]["text"].startswith(":x:")


# ------------------------------------------------------------------ V99: the eval gate's hold

def test_a_held_agent_gets_a_refusal_and_no_session(tmp_path):
    release = tmp_path / "release.json"
    release.write_text(json.dumps({"version": 1, "written_at": "t", "revision": "abc",
                                   "agents": {"k8s-reader": {"held": True, "hash": "h", "reason": "eval regression on h: hard canary-pod-logs"}}}))
    agent = FakeAgent()
    bridge, api, logs, _ = make(agent, release_path=str(release))
    bridge.handle_event(mention("k8s-reader: which pods restarted?"))
    posted = [c for c in api.calls if c[0] == "chat.postMessage"]
    assert posted and "is held by the eval gate" in posted[-1][1]["text"] and "canary-pod-logs" in posted[-1][1]["text"]
    assert "ask-hk --agent kagent/k8s-reader" in posted[-1][1]["text"]
    assert not agent.sent, "no session is opened with a held agent"
    assert any(l.get("event") == "held" and l.get("agent") == "k8s-reader" for l in logs)


def test_an_unheld_agent_and_an_absent_file_are_unaffected(tmp_path):
    for path in (str(tmp_path / "absent.json"),):
        agent = FakeAgent(completed("fine"))
        bridge, api, logs, _ = make(agent, release_path=path)
        bridge.handle_event(mention("k8s-reader: which pods restarted?"))
        assert agent.sent, "unknown = send"


def test_config_reads_the_release_path_from_env():
    env = {"SLACK_BOT_TOKEN": "b", "SLACK_APP_TOKEN": "a", "OWNER_SLACK_USER_ID": "U", "OWNER_EMAIL": "e"}
    assert sb.Config.from_env({**env, "EVAL_RELEASE_PATH": "/x/r.json"}).release_path == "/x/r.json"
    assert sb.Config.from_env(env).release_path == "/etc/eval-release/release.json"
