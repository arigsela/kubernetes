"""Capturing fresh agent answers for the corpus (A2A client, no network)."""
from pathlib import Path
import importlib.util
import json

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cap = _load("capture-eval-answers")


def test_extract_text_joins_artifact_parts():
    result = {"artifacts": [{"parts": [{"kind": "text", "text": "a"}, {"kind": "text", "text": "b"}]}]}
    assert cap.extract_text(result) == "a\nb"


def test_extract_text_falls_back_to_status_message():
    result = {"status": {"message": {"parts": [{"kind": "text", "text": "hi"}]}}}
    assert cap.extract_text(result) == "hi"


def test_extract_text_handles_no_text_parts():
    assert cap.extract_text({"artifacts": [{"parts": [{"kind": "file"}]}]}) is None
    assert cap.extract_text({}) is None


def test_send_message_is_jsonrpc_message_send():
    seen = {}

    def transport(url, payload, timeout):
        seen["url"] = url
        seen["payload"] = json.loads(payload)
        return {"jsonrpc": "2.0", "id": seen["payload"]["id"],
                "result": {"artifacts": [{"parts": [{"kind": "text", "text": "ok"}]}]}}

    r = cap.send_message("http://x/", "q?", transport=transport)
    assert seen["url"] == "http://x/"
    assert seen["payload"]["method"] == "message/send"
    assert seen["payload"]["params"]["message"]["parts"] == [{"kind": "text", "text": "q?"}]
    assert seen["payload"]["params"]["message"]["role"] == "user"
    assert cap.extract_text(r) == "ok"


def test_send_message_raises_on_rpc_error():
    transport = lambda url, payload, timeout: {"jsonrpc": "2.0", "id": 1,
                                               "error": {"code": -32000, "message": "nope"}}
    with pytest.raises(RuntimeError, match="nope"):
        cap.send_message("http://x/", "q?", transport=transport)


def test_main_writes_jsonl_and_fails_on_missing_answer(tmp_path, monkeypatch):
    corpus = {"entries": [{"id": "one", "question": "q1", "category": "repo-factual"},
                          {"id": "two", "question": "q2", "category": "repo-factual"}]}
    (tmp_path / "tests" / "eval-corpus").mkdir(parents=True)
    (tmp_path / "tests" / "eval-corpus" / "c.yaml").write_text(json.dumps(corpus))

    def fake_send(endpoint, question, **kw):
        if question == "q1":
            return {"artifacts": [{"parts": [{"kind": "text", "text": "A1"}]}]}
        return {"artifacts": [{"parts": [{"kind": "file"}]}]}

    monkeypatch.setattr(cap, "send_message", fake_send)
    out = tmp_path / "answers.jsonl"
    rc = cap.main(["--out", str(out), "--repo-root", str(tmp_path), "--endpoint", "http://x/"])
    lines = [json.loads(l) for l in out.read_text().splitlines()]
    assert lines == [{"id": "one", "answer": "A1"}, {"id": "two", "answer": None}]
    assert rc == 1


def test_extract_text_ignores_failed_task_message():
    """A failed/canceled/rejected task's status message is an error, not an answer."""
    for state in ("failed", "canceled", "rejected"):
        result = {"status": {"state": state,
                             "message": {"parts": [{"kind": "text", "text": "internal error"}]}}}
        assert cap.extract_text(result) is None, state
    ok = {"status": {"state": "completed",
                     "message": {"parts": [{"kind": "text", "text": "hi"}]}}}
    assert cap.extract_text(ok) == "hi"
