"""The kagent 1.0 A2A client (scripts/kagent_a2a.py) and scripts/ask-hk.

Recorded responses in fixtures/a2a-v1/ follow the kagent v1.0.0-alpha11 gateway
(go/core/internal/a2agateway) and the a2a-go v2.6.0 wire types it pins.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path
import importlib.util
import io
import json
import os
import sys
import threading

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "a2a-v1"

sys.path.insert(0, str(SCRIPTS))
import kagent_a2a as a2a  # noqa: E402


def _load_ask_hk():
    loader = SourceFileLoader("ask_hk", str(SCRIPTS / "ask-hk"))
    spec = importlib.util.spec_from_loader("ask_hk", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ask_hk = _load_ask_hk()


def recorded(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


class Recorder:
    """A transport that replays responses and remembers each request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append({"url": url, "payload": payload, "body": json.loads(payload),
                           "headers": headers, "timeout": timeout})
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


# ----------------------------------------------------------------- request shape

def test_send_request_uses_a2a_v1_names():
    req = a2a.build_send_request("hi")
    assert req["jsonrpc"] == "2.0" and req["method"] == "SendMessage"
    msg = req["params"]["message"]
    assert msg["role"] == "ROLE_USER"
    assert msg["parts"] == [{"text": "hi"}], "v1 parts have no `kind`"
    assert msg["messageId"] and "contextId" not in msg


def test_send_request_continues_a_context():
    msg = a2a.build_send_request("more", context_id="ctx-1")["params"]["message"]
    assert msg["contextId"] == "ctx-1"


def test_headers_carry_version_and_identity():
    h = a2a.request_headers("someone")
    assert h["A2A-Version"] == "1.0" and h["X-User-Id"] == "someone"
    assert h["Content-Type"] == "application/json"
    assert "X-User-Id" not in a2a.request_headers(None)


@pytest.mark.parametrize("endpoint,expected", [
    ("https://kagent-mcp.arigsela.com", "https://kagent-mcp.arigsela.com/agents/kagent/homelab-knowledge"),
    ("http://127.0.0.1:8083/", "http://127.0.0.1:8083/agents/kagent/homelab-knowledge"),
    ("http://h/agents/kagent/k8s-reader", "http://h/agents/kagent/k8s-reader"),
])
def test_resolve_endpoint(endpoint, expected):
    assert a2a.resolve_endpoint(endpoint, "kagent/homelab-knowledge") == expected


@pytest.mark.parametrize("agent", ["homelab-knowledge", "kagent/", "/x", "a/b/c"])
def test_agent_must_be_namespace_slash_name(agent):
    with pytest.raises(ValueError):
        a2a.agent_path(agent)


def test_endpoint_must_be_absolute():
    with pytest.raises(ValueError):
        a2a.resolve_endpoint("kagent-mcp.arigsela.com", "kagent/homelab-knowledge")


# ------------------------------------------------------------------- send + retry

def test_send_returns_the_jsonrpc_result():
    t = Recorder(recorded("send-completed"))
    result = a2a.send_message("http://h/agents/kagent/a", "q", user="u", timeout=7, transport=t)
    assert set(result) == {"task"}
    assert t.calls[0]["url"] == "http://h/agents/kagent/a"
    assert t.calls[0]["headers"]["X-User-Id"] == "u" and t.calls[0]["timeout"] == 7


def test_send_not_accepted_is_retried_with_the_identical_request():
    t = Recorder(recorded("error-send-not-accepted"), recorded("send-completed"))
    slept = []
    result = a2a.send_message("http://h/x", "q", transport=t, sleep=slept.append)
    assert a2a.parse_result(result).state == "TASK_STATE_COMPLETED"
    assert len(t.calls) == 2
    assert t.calls[0]["payload"] == t.calls[1]["payload"], "a retry must reuse the messageId"
    assert slept == [0.1]


@pytest.mark.parametrize("error", [
    recorded("error-internal")["error"],
    # -32004 without kagent's reason is not a proven rejection.
    {"code": -32004, "message": "this operation is not supported",
     "data": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "UNSUPPORTED_OPERATION",
               "domain": "a2a-protocol.org", "metadata": {"reason": "OTHER_REASON"}}]},
    # kagent's reason on another code is not one either.
    {"code": -32603, "message": "x",
     "data": [{"metadata": {"reason": "KAGENT_SEND_NOT_ACCEPTED"}}]},
])
def test_other_errors_are_not_retried(error):
    t = Recorder({"jsonrpc": "2.0", "id": "1", "error": error})
    with pytest.raises(a2a.A2AError) as exc:
        a2a.send_message("http://h/x", "q", transport=t, sleep=lambda s: pytest.fail("retried"))
    assert exc.value.code == error["code"]
    assert len(t.calls) == 1


def test_retry_gives_up_at_the_deadline():
    now = [0.0]

    def sleep(s):
        now[0] += s

    t = Recorder(*[recorded("error-send-not-accepted")] * 100)
    with pytest.raises(a2a.A2AError, match="not accepted"):
        a2a.send_message("http://h/x", "q", transport=t, sleep=sleep, clock=lambda: now[0],
                         retry_deadline=1.0)
    assert 2 <= len(t.calls) <= 11


# ------------------------------------------------------------------ result parsing

def test_completed_task():
    r = a2a.parse_result(recorded("send-completed")["result"])
    assert r.kind == "task" and r.completed and not r.failed and not r.waiting
    assert r.context_id == "7b1e4d2a-9c3f-4e8b-a1d6-5f2e3c4b5a69"
    assert r.task_id == "3f0c2b9e-6a43-4c55-8a7e-1d2b3c4d5e6f"
    assert r.answer.splitlines() == [
        "I'll start from the app index.",
        "Every app with a public host uses it: each Certificate names `letsencrypt-route53`.",
    ]


def test_failed_task_has_no_answer_but_keeps_the_error():
    r = a2a.parse_result(recorded("send-failed")["result"])
    assert r.failed and r.answer is None
    assert r.status_text == "model call failed: 529 overloaded_error"


def test_waiting_task():
    r = a2a.parse_result(recorded("send-input-required")["result"])
    assert r.waiting and r.answer is None
    assert r.status_text == "Approve k8s_delete_resource on configmap/kagent-scratch?"
    assert r.text == "Deleting it needs your approval."


def test_unfinished_task_has_no_answer():
    r = a2a.parse_result({"task": {"id": "t", "contextId": "c", "status": {"state": "TASK_STATE_WORKING"},
                                   "artifacts": [{"artifactId": "a", "parts": [{"text": "so far"}]}]}})
    assert not r.completed and r.answer is None


def test_direct_message():
    r = a2a.parse_result(recorded("send-message")["result"])
    assert r.kind == "message" and r.answer == "Hello."


def test_unknown_result_shape():
    with pytest.raises(a2a.A2AError, match="unrecognised"):
        a2a.parse_result({"id": "t", "status": {"state": "completed"}})


# ------------------------------------------------------------ real HTTP round trip

class _Handler(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Handler.seen.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(body)})
        if self.path.startswith("/agents/kagent/"):
            data, code = json.dumps(recorded("send-completed")).encode(), 200
        else:
            data, code = b"RBAC: access denied", 403
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Handler.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_http_round_trip(server):
    url = a2a.resolve_endpoint(server, "kagent/homelab-knowledge")
    reply = a2a.parse_result(a2a.send_message(url, "q", user="me", timeout=5))
    assert reply.completed
    seen = _Handler.seen[0]
    assert seen["path"] == "/agents/kagent/homelab-knowledge"
    assert seen["headers"]["X-User-Id"] == "me" and seen["headers"]["A2A-Version"] == "1.0"
    assert seen["body"]["method"] == "SendMessage"


def test_http_error_is_reported_with_status(server):
    with pytest.raises(a2a.A2AError, match="HTTP 403.*RBAC: access denied"):
        a2a.send_message(server + "/agents/other/x", "q", timeout=5)


# ----------------------------------------------------------------------- ask-hk

def run(argv, transport, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    rc = ask_hk.main(argv, stdin=io.StringIO(stdin), stdout=out, stderr=err, transport=transport)
    return rc, out.getvalue(), err.getvalue()


def test_ask_hk_is_executable():
    path = SCRIPTS / "ask-hk"
    assert os.access(path, os.X_OK)
    assert path.read_text().startswith("#!/usr/bin/env python3\n")


def test_ask_hk_prints_answer_then_context(monkeypatch):
    monkeypatch.delenv("KAGENT_USER_ID", raising=False)
    t = Recorder(recorded("send-completed"))
    rc, out, _ = run(["Which apps use the issuer?"], t)
    assert rc == 0
    lines = out.splitlines()
    assert lines[-1] == "context: 7b1e4d2a-9c3f-4e8b-a1d6-5f2e3c4b5a69"
    assert lines[-2].endswith("`letsencrypt-route53`.")
    call = t.calls[0]
    assert call["url"] == "https://kagent-mcp.arigsela.com/agents/kagent/homelab-knowledge"
    assert call["headers"]["X-User-Id"] == "admin@kagent.dev"
    assert "contextId" not in call["body"]["params"]["message"]


def test_ask_hk_follow_up_sends_the_context():
    t = Recorder(recorded("send-completed"))
    rc, out, _ = run(["--context", "7b1e4d2a-9c3f-4e8b-a1d6-5f2e3c4b5a69", "and then?"], t)
    assert rc == 0
    assert t.calls[0]["body"]["params"]["message"]["contextId"] == "7b1e4d2a-9c3f-4e8b-a1d6-5f2e3c4b5a69"


def test_ask_hk_agent_endpoint_user_and_stdin():
    t = Recorder(recorded("send-message"))
    rc, out, _ = run(["--agent", "kagent/k8s-reader", "--endpoint", "http://127.0.0.1:8083",
                      "--user", "ari", "-"], t, stdin="from stdin\n")
    assert rc == 0 and out.splitlines()[0] == "Hello."
    assert t.calls[0]["url"] == "http://127.0.0.1:8083/agents/kagent/k8s-reader"
    assert t.calls[0]["headers"]["X-User-Id"] == "ari"
    assert t.calls[0]["body"]["params"]["message"]["parts"] == [{"text": "from stdin\n"}]


def test_ask_hk_user_from_environment(monkeypatch):
    monkeypatch.setenv("KAGENT_USER_ID", "env-user")
    t = Recorder(recorded("send-message"))
    run(["q"], t)
    assert t.calls[0]["headers"]["X-User-Id"] == "env-user"


def test_ask_hk_waiting_exits_2_with_the_question():
    rc, out, err = run(["delete it"], Recorder(recorded("send-input-required")))
    assert rc == 2
    assert "Approve k8s_delete_resource" in out and out.splitlines()[-1].startswith("context: ")
    assert "TASK_STATE_INPUT_REQUIRED" in err


def test_ask_hk_failed_task_exits_1_and_keeps_the_context():
    rc, out, err = run(["q"], Recorder(recorded("send-failed")))
    assert rc == 1
    assert out == "context: 7b1e4d2a-9c3f-4e8b-a1d6-5f2e3c4b5a69\n"
    assert "TASK_STATE_FAILED" in err and "overloaded_error" in err


def test_ask_hk_transport_error_keeps_a_known_context():
    rc, out, err = run(["--context", "c-1", "q"], Recorder(TimeoutError("timed out")))
    assert rc == 1 and out == "context: c-1\n" and "timed out" in err


def test_ask_hk_rpc_error():
    rc, out, err = run(["q"], Recorder(recorded("error-internal")))
    assert rc == 1 and out == "" and "-32603" in err


def test_ask_hk_rejects_empty_question_and_bad_agent():
    assert run(["  "], Recorder())[0] == 1
    assert run(["--agent", "homelab-knowledge", "q"], Recorder())[0] == 1
