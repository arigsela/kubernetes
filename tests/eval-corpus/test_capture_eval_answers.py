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
