"""Capturing fresh agent answers for the corpus (A2A v1 client, no network).

Responses are recorded kagent 1.0 shapes (fixtures/a2a-v1/, built from the
v1.0.0-alpha11 gateway and the a2a-go v2.6.0 wire types it pins).
"""
from pathlib import Path
import importlib.util
import json

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "a2a-v1"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cap = _load("capture-eval-answers")


@pytest.fixture(autouse=True)
def fake_token(monkeypatch):
    """main() sends a Dex token by default; fake it so no test runs kubelogin or opens a browser."""
    monkeypatch.setenv("KAGENT_TOKEN", "test.id.token")
    monkeypatch.delenv("KAGENT_TOKEN_FILE", raising=False)


def recorded(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def test_default_endpoint_is_the_controller_a2a_route():
    assert cap.DEFAULT_ENDPOINT == "https://kagent-mcp.arigsela.com/agents/kagent/homelab-knowledge"


def test_extract_text_joins_artifact_text_and_skips_tool_parts():
    text = cap.extract_text(recorded("send-completed")["result"])
    assert text == ("I'll start from the app index.\n"
                    "Every app with a public host uses it: each Certificate names `letsencrypt-route53`.")
    assert "read_file" not in text


def test_extract_text_falls_back_to_status_message():
    result = {"task": {"id": "t", "contextId": "c",
                       "status": {"state": "TASK_STATE_COMPLETED",
                                  "message": {"role": "ROLE_AGENT", "parts": [{"text": "hi"}]}}}}
    assert cap.extract_text(result) == "hi"


def test_extract_text_reads_a_direct_message_result():
    assert cap.extract_text(recorded("send-message")["result"]) == "Hello."


def test_extract_text_handles_no_text_parts():
    only_data = {"task": {"id": "t", "contextId": "c", "status": {"state": "TASK_STATE_COMPLETED"},
                          "artifacts": [{"artifactId": "a", "parts": [{"data": {"x": 1}}]}]}}
    assert cap.extract_text(only_data) is None


def test_extract_text_ignores_failed_task_message():
    """A failed/canceled/rejected task's status message is an error, not an answer."""
    assert cap.extract_text(recorded("send-failed")["result"]) is None
    for state in ("TASK_STATE_CANCELED", "TASK_STATE_REJECTED"):
        result = {"task": {"id": "t", "contextId": "c",
                           "status": {"state": state,
                                      "message": {"parts": [{"text": "internal error"}]}}}}
        assert cap.extract_text(result) is None, state


def test_extract_text_is_none_while_the_task_waits_for_input():
    assert cap.extract_text(recorded("send-input-required")["result"]) is None


def test_extract_text_rejects_the_0_3_unwrapped_task():
    """kagent 0.10 returned the Task itself as the result; 1.0 wraps it in {"task": ...}."""
    with pytest.raises(RuntimeError, match="unrecognised"):
        cap.extract_text({"artifacts": [{"parts": [{"kind": "text", "text": "a"}]}]})


def test_send_message_is_a2a_v1_send_message():
    seen = {}

    def transport(url, payload, headers, timeout):
        seen.update(url=url, payload=json.loads(payload), headers=headers)
        return recorded("send-completed")

    r = cap.send_message("http://x/agents/kagent/homelab-knowledge", "q?", transport=transport)
    assert seen["url"] == "http://x/agents/kagent/homelab-knowledge"
    assert seen["payload"]["method"] == "SendMessage"
    msg = seen["payload"]["params"]["message"]
    assert msg["parts"] == [{"text": "q?"}]
    assert msg["role"] == "ROLE_USER"
    assert "contextId" not in msg, "one new session per question"
    assert seen["headers"]["X-User-Id"] == "eval-capture"
    assert seen["headers"]["A2A-Version"] == "1.0"
    assert cap.extract_text(r).endswith("`letsencrypt-route53`.")


def test_send_message_raises_on_rpc_error():
    transport = lambda url, payload, headers, timeout: recorded("error-internal")
    with pytest.raises(RuntimeError, match="internal error"):
        cap.send_message("http://x/", "q?", transport=transport)


def test_main_writes_jsonl_and_fails_on_missing_answer(tmp_path, monkeypatch):
    corpus = {"entries": [{"id": "one", "question": "q1", "category": "repo-factual"},
                          {"id": "two", "question": "q2", "category": "repo-factual"}]}
    (tmp_path / "tests" / "eval-corpus").mkdir(parents=True)
    (tmp_path / "tests" / "eval-corpus" / "c.yaml").write_text(json.dumps(corpus))
    seen = []

    def fake_send(endpoint, question, **kw):
        seen.append((endpoint, kw.get("user")))
        if question == "q1":
            return recorded("send-completed")["result"]
        return recorded("send-failed")["result"]

    monkeypatch.setattr(cap, "send_message", fake_send)
    out = tmp_path / "answers.jsonl"
    rc = cap.main(["--out", str(out), "--repo-root", str(tmp_path), "--endpoint", "http://x/"])
    lines = [json.loads(l) for l in out.read_text().splitlines()]
    assert lines[0]["id"] == "one" and lines[0]["answer"].endswith("`letsencrypt-route53`.")
    assert lines[1] == {"id": "two", "answer": None}
    assert rc == 1
    # A bare origin gets the agent path; the eval identity is used.
    assert seen[0] == ("http://x/agents/kagent/homelab-knowledge", "eval-capture")


def test_capture_sends_the_token_file(tmp_path):
    f = tmp_path / "token"
    f.write_text("cap.tok.en")
    seen = {}

    def transport(url, payload, headers, timeout):
        seen.update(headers)
        return {"result": {"message": {"role": "agent", "parts": [{"text": "answer"}]}}}

    res = cap.send_message("http://x/agents/kagent/a", "q", transport=transport, token_file=str(f))
    assert cap.extract_text(res) == "answer"
    assert seen["Authorization"] == "Bearer cap.tok.en" and "X-User-Id" not in seen


def test_capture_returns_answers_and_none_for_failures():
    entries = [{"id": "a", "question": "qa"}, {"id": "b", "question": "qb"}]

    def send(endpoint, question, **kw):
        if question == "qb":
            raise RuntimeError("boom")
        return {"message": {"role": "agent", "parts": [{"text": f"answer to {question}"}]}}

    out = cap.capture(entries, "http://x/agents/kagent/a", send=send, timeout=1)
    assert out == {"a": "answer to qa", "b": None}


# ------------------------------------------------------------------ V99: capture_full and reject_pending
a2a = _load("kagent_a2a")

def _paused(pending, calls=()):
    parts = [{"data": {"id": f"c{i}", "name": c, "args": {}}} for i, c in enumerate(calls)]
    return {"task": {"id": "task-1", "contextId": "ctx-1", "status": {
        "state": "TASK_STATE_INPUT_REQUIRED",
        "message": {"parts": [{"text": "approve?"}], "metadata": {a2a.HITL_EXTENSION: {
            "type": "tool_approval_request", "tools": pending}}}},
        "artifacts": [{"parts": parts}]}}


def test_capture_full_scores_a_pause_on_its_pending_calls_and_rejects_it():
    sent = []

    def send(endpoint, question, **kw):
        sent.append(kw)
        if kw.get("task_id"):
            return {"task": {"id": "task-1", "contextId": "ctx-1", "status": {"state": "TASK_STATE_COMPLETED"},
                             "artifacts": [{"parts": [{"text": "no restart: rejected"}]}]}}
        return _paused([{"id": "call-9", "name": "k8s_delete_resource", "args": {"resource_name": "canary-logger-0"}}],
                       calls=["k8s_describe_resource"])

    out = cap.capture_full([{"id": "e", "question": "restart it"}], "http://a2a/agents/kagent/restarter", send=send,
                           reject=lambda reply: cap.reject_pending(reply, "http://a2a/agents/kagent/restarter", send=send))
    row = out["e"]
    assert [c["name"] for c in row["tool_calls"]] == ["k8s_describe_resource", "k8s_delete_resource"]
    assert row["tool_calls"][1]["pending"] is True and row["pending"][0]["id"] == "call-9"
    assert row["answer"] == "no restart: rejected" and row["state"] == "TASK_STATE_COMPLETED"
    assert sent[1]["task_id"] == "task-1" and sent[1]["context_id"] == "ctx-1"
    approvals = sent[1]["metadata"][a2a.HITL_EXTENSION]["approvals"]
    assert approvals[0]["id"] == "call-9" and approvals[0]["approved"] is False


def test_capture_full_without_a_reject_hook_leaves_the_pause_and_records_it():
    send = lambda endpoint, question, **kw: _paused([{"id": "c", "name": "k8s_delete_resource", "args": {}}])
    row = cap.capture_full([{"id": "e", "question": "q"}], "http://a2a/x", send=send)["e"]
    assert row["answer"] is None and row["pending"] and row["state"] == "TASK_STATE_INPUT_REQUIRED"


def test_capture_keeps_its_old_shape():
    send = lambda endpoint, question, **kw: {"message": {"parts": [{"text": "hello"}]}}
    assert cap.capture([{"id": "e", "question": "q"}], "http://a2a/x", send=send) == {"e": "hello"}


def test_reject_pending_does_nothing_without_pending_calls():
    reply = a2a.parse_result({"message": {"parts": [{"text": "done"}]}})
    assert cap.reject_pending(reply, "http://a2a/x", send=lambda *a, **k: pytest.fail("no send")) is None
