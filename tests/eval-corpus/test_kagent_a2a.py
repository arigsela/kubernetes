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
    assert msg["extensions"] == ["https://kagent.dev/extensions/hitl/v1"], \
        "every send requests HITL, or a pause raised in that turn cannot be answered"
    assert "taskId" not in msg and "metadata" not in msg


def test_send_request_continues_a_context():
    msg = a2a.build_send_request("more", context_id="ctx-1")["params"]["message"]
    assert msg["contextId"] == "ctx-1"


def test_headers_carry_version_identity_and_hitl():
    h = a2a.request_headers("someone")
    assert h["A2A-Version"] == "1.0" and h["X-User-Id"] == "someone"
    assert h["A2A-Extensions"] == "https://kagent.dev/extensions/hitl/v1"
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
    assert r.hitl["type"] == "tool_approval_request"
    assert r.pending_calls == [{"id": "adk-147fe663-08da-4fe0-869d-2204e24f2219", "name": "k8s_delete_resource",
                                "args": {"resource_type": "configmap", "resource_name": "kagent-scratch"}}]
    assert not r.pending_question


def test_waiting_task_with_a_question():
    r = a2a.parse_result(recorded("send-ask-user")["result"])
    assert r.waiting and r.pending_question and r.pending_calls == []
    assert r.hitl["id"] == "adk-9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"


def test_pause_without_hitl_metadata_has_no_pending_calls():
    r = a2a.parse_result({"task": {"id": "t", "contextId": "c",
                                   "status": {"state": "TASK_STATE_INPUT_REQUIRED",
                                              "message": {"parts": [{"text": "?"}]}}}})
    assert r.waiting and r.hitl is None and r.pending_calls == [] and not r.pending_question


def test_pending_calls_accept_the_documented_calls_key():
    r = a2a.Reply(kind="task", state="TASK_STATE_INPUT_REQUIRED", context_id="c", task_id="t",
                  text=None, status_text=None,
                  hitl={"type": "tool_approval_request", "id": "adk-1",
                        "calls": [{"id": "x", "name": "n", "args": {"a": 1}}, {"name": "no-id"}]})
    assert r.pending_calls == [{"id": "x", "name": "n", "args": {"a": 1}}]


# ------------------------------------------------------------------ HITL replies

def test_approval_reply_decides_every_call_on_the_paused_task():
    meta = a2a.approval_metadata(["toolu_07", "toolu_08"], approved=True)
    req = a2a.build_send_request("approved", context_id="ctx", task_id="task-1", metadata=meta)
    msg = req["params"]["message"]
    assert msg["taskId"] == "task-1" and msg["contextId"] == "ctx"
    assert msg["extensions"] == [a2a.HITL_EXTENSION], "kagent ignores the payload without it"
    payload = msg["metadata"][a2a.HITL_EXTENSION]
    assert payload == {"type": "tool_approval_response",
                       "approvals": [{"id": "toolu_07", "approved": True},
                                     {"id": "toolu_08", "approved": True}]}


def test_rejection_carries_the_reason_only_when_rejecting():
    rej = a2a.approval_metadata(["toolu_07"], approved=False, reason="not during the demo")
    assert rej[a2a.HITL_EXTENSION]["approvals"] == [
        {"id": "toolu_07", "approved": False, "rejection_reason": "not during the demo"}]
    ok = a2a.approval_metadata(["toolu_07"], approved=True, reason="ignored")
    assert "rejection_reason" not in ok[a2a.HITL_EXTENSION]["approvals"][0]


def test_answer_reply_names_the_request():
    meta = a2a.answer_metadata("adk-9", "kagent")
    assert meta == {a2a.HITL_EXTENSION: {"type": "ask_user_response", "id": "adk-9",
                                         "answers": [{"answer": ["kagent"]}]}}


def test_send_message_passes_task_and_metadata_through():
    t = Recorder(recorded("send-completed"))
    a2a.send_message("http://h/x", "approved", context_id="c", task_id="t1",
                     metadata=a2a.approval_metadata(["a"], approved=True), transport=t)
    msg = t.calls[0]["body"]["params"]["message"]
    assert msg["taskId"] == "t1" and msg["contextId"] == "c"
    assert msg["metadata"][a2a.HITL_EXTENSION]["type"] == "tool_approval_response"
    assert t.calls[0]["headers"]["A2A-Extensions"] == a2a.HITL_EXTENSION


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


CTX = "7b1e4d2a-9c3f-4e8b-a1d6-5f2e3c4b5a69"


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    """ask-hk remembers pauses on disk; keep every test's state private."""
    monkeypatch.setenv("ASK_HK_STATE_DIR", str(tmp_path / "ask-hk"))
    return tmp_path / "ask-hk"


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


def test_ask_hk_waiting_exits_2_with_the_question(state_dir):
    rc, out, err = run(["delete it"], Recorder(recorded("send-input-required")))
    assert rc == 2
    assert "Approve k8s_delete_resource" in out and out.splitlines()[-1] == f"context: {CTX}"
    assert "waiting for approval of:" in err
    assert 'k8s_delete_resource {"resource_name": "kagent-scratch", "resource_type": "configmap"}' in err
    assert f"ask-hk --context {CTX} --approve | --reject REASON" in err
    saved = json.loads((state_dir / f"{CTX}.json").read_text())
    assert saved["kind"] == "approval" and saved["task_id"] == "5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c8d"
    assert [c["id"] for c in saved["calls"]] == ["adk-147fe663-08da-4fe0-869d-2204e24f2219"]
    assert saved["request_id"] is None, "alpha11 approval requests carry no top-level id"


def test_ask_hk_pause_without_request_id_points_at_the_ui(state_dir):
    fx = recorded("send-input-required")
    del fx["result"]["task"]["status"]["message"]["metadata"]
    rc, out, err = run(["delete it"], Recorder(fx))
    assert rc == 2 and "decide it in the kagent UI" in err
    assert not (state_dir / f"{CTX}.json").exists()


def test_ask_hk_approve_answers_the_paused_task(state_dir):
    run(["delete it"], Recorder(recorded("send-input-required")))
    t = Recorder(recorded("send-completed"))
    rc, out, err = run(["--context", CTX, "--approve"], t)
    assert rc == 0 and out.splitlines()[-1] == f"context: {CTX}"
    msg = t.calls[0]["body"]["params"]["message"]
    assert msg["taskId"] == "5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c8d" and msg["contextId"] == CTX
    assert msg["parts"] == [{"text": "approved"}]
    assert msg["metadata"][a2a.HITL_EXTENSION] == {
        "type": "tool_approval_response",
        "approvals": [{"id": "adk-147fe663-08da-4fe0-869d-2204e24f2219", "approved": True}]}
    assert not (state_dir / f"{CTX}.json").exists(), "a decided pause is forgotten"


def test_ask_hk_reject_passes_the_reason(state_dir):
    run(["delete it"], Recorder(recorded("send-input-required")))
    t = Recorder(recorded("send-completed"))
    rc, _, _ = run(["--context", CTX, "--reject", "not during the demo"], t)
    assert rc == 0
    msg = t.calls[0]["body"]["params"]["message"]
    assert msg["metadata"][a2a.HITL_EXTENSION]["approvals"] == [
        {"id": "adk-147fe663-08da-4fe0-869d-2204e24f2219", "approved": False,
         "rejection_reason": "not during the demo"}]
    assert msg["parts"] == [{"text": "rejected: not during the demo"}]


def test_ask_hk_decision_needs_a_pending_approval():
    rc, _, err = run(["--context", CTX, "--approve"], Recorder())
    assert rc == 1 and "nothing is waiting for approval" in err
    rc, _, err = run(["--approve"], Recorder())
    assert rc == 1 and "need --context" in err
    rc, _, err = run(["--context", CTX, "--approve", "and also this"], Recorder())
    assert rc == 1 and "take no message" in err


def test_ask_hk_refuses_a_plain_message_while_an_approval_is_pending():
    run(["delete it"], Recorder(recorded("send-input-required")))
    t = Recorder()
    rc, _, err = run(["--context", CTX, "never mind"], t)
    assert rc == 1 and "decide it first" in err and t.calls == []


def test_ask_hk_plain_message_answers_a_pending_question(state_dir):
    rc, out, err = run(["list the pods"], Recorder(recorded("send-ask-user")))
    assert rc == 2 and "Which namespace" in out and f'ask-hk --context {CTX} "<answer>"' in err
    t = Recorder(recorded("send-completed"))
    rc, _, _ = run(["--context", CTX, "kagent"], t)
    assert rc == 0
    msg = t.calls[0]["body"]["params"]["message"]
    assert msg["taskId"] == "6b7c8d9e-0f1a-4b2c-9d3e-4f5a6b7c8d9e"
    assert msg["metadata"][a2a.HITL_EXTENSION] == {
        "type": "ask_user_response", "id": "adk-9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b",
        "answers": [{"answer": ["kagent"]}]}
    assert not (state_dir / f"{CTX}.json").exists()


def test_ask_hk_a_second_pause_replaces_the_first(state_dir):
    run(["delete it"], Recorder(recorded("send-input-required")))
    rc, _, _ = run(["--context", CTX, "--approve"], Recorder(recorded("send-ask-user")))
    assert rc == 2
    assert json.loads((state_dir / f"{CTX}.json").read_text())["kind"] == "question"


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
    assert run([], Recorder())[0] == 1
    assert run(["--agent", "homelab-knowledge", "q"], Recorder())[0] == 1


# ---------------------------------------------------------------- tokens (callers as principals)

def test_headers_with_a_token_carry_bearer_and_no_user_id():
    h = a2a.request_headers("someone@example.com", token="abc.def.ghi")
    assert h["Authorization"] == "Bearer abc.def.ghi"
    assert "X-User-Id" not in h


def test_headers_without_a_token_keep_the_user_header():
    h = a2a.request_headers("eval-capture")
    assert h["X-User-Id"] == "eval-capture" and "Authorization" not in h


def test_send_reads_the_token_file_on_every_call(tmp_path):
    f = tmp_path / "token"
    f.write_text("first\n")
    seen = []

    def transport(url, payload, headers, timeout):
        seen.append(headers.get("Authorization"))
        return {"result": {"message": {"role": "agent", "parts": [{"text": "ok"}]}}}

    a2a.send_message("http://x/agents/kagent/a", "q", token_file=str(f), transport=transport)
    f.write_text("second\n")
    a2a.send_message("http://x/agents/kagent/a", "q", token_file=str(f), transport=transport)
    assert seen == ["Bearer first", "Bearer second"]


def test_kubelogin_token_parses_the_exec_credential():
    def run(cmd, **kw):
        assert cmd[:3] == ["kubectl", "oidc-login", "get-token"]
        assert "--oidc-issuer-url=https://dex.arigsela.com" in cmd
        assert "--oidc-client-id=kubernetes" in cmd
        assert "--oidc-extra-scope=email" in cmd
        class R: returncode = 0; stdout = '{"kind":"ExecCredential","status":{"token":"id.tok.en"}}'; stderr = ""
        return R()
    assert a2a.kubelogin_token(run=run) == "id.tok.en"


def test_serviceaccount_token_mints_with_the_kagent_audience():
    def run(cmd, **kw):
        assert cmd == ["kubectl", "create", "token", "dispatcher", "-n", "dispatcher",
                       "--audience", "kagent", "--duration", "10m"]
        class R: returncode = 0; stdout = "sa.tok.en\n"; stderr = ""
        return R()
    assert a2a.serviceaccount_token("dispatcher/dispatcher", run=run) == "sa.tok.en"


def test_serviceaccount_token_rejects_a_bad_ref():
    with pytest.raises(ValueError):
        a2a.serviceaccount_token("dispatcher", run=lambda *a, **k: None)


def test_acquire_token_precedence(tmp_path):
    f = tmp_path / "t"
    f.write_text("from-file")
    assert a2a.acquire_token(token="explicit", token_file=str(f), env={}) == "explicit"
    assert a2a.acquire_token(token_file=str(f), env={"KAGENT_TOKEN": "env"}) == "from-file"
    assert a2a.acquire_token(env={"KAGENT_TOKEN": "env"}) == "env"
    assert a2a.acquire_token(env={"KAGENT_TOKEN_FILE": str(f)}) == "from-file"
    called = []
    def run(cmd, **kw):
        called.append(cmd[:3])
        class R: returncode = 0; stdout = '{"status":{"token":"kl"}}'; stderr = ""
        return R()
    assert a2a.acquire_token(env={}, run=run) == "kl"
    assert called == [["kubectl", "oidc-login", "get-token"]]


def test_acquire_token_reports_a_failed_kubectl():
    def run(cmd, **kw):
        class R: returncode = 1; stdout = ""; stderr = "no browser"
        return R()
    with pytest.raises(RuntimeError, match="no browser"):
        a2a.acquire_token(env={}, run=run)
