"""The Claude judge in score-eval.py: the request it sends, and what a non-verdict becomes.

The real SDK is replaced by a stub module, so these run with no key and no network.
"""
import json
import sys
import types
from pathlib import Path
import importlib.util

REPO = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


se = _load("score-eval")

ENTRY = {"id": "x", "question": "q?", "category": "repo-factual", "reference": "r",
         "golden": {"behavior": "answer", "must_include": ["kagent-controller"]}, "source": "s"}


def _stub_sdk(monkeypatch, response):
    calls = []

    class Messages:
        def create(self, **kw):
            calls.append(kw)
            return response

    class Client:
        def __init__(self, **kw):
            self.beta = types.SimpleNamespace(messages=Messages())

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Client))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    return calls


def _response(stop_reason="end_turn", text=None, category=None):
    content = [types.SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(types.SimpleNamespace(type="text", text=text))
    details = types.SimpleNamespace(category=category) if category else None
    return types.SimpleNamespace(stop_reason=stop_reason, content=content, stop_details=details)


def test_the_request_targets_sonnet_5_5_with_a_schema_and_no_sampling(monkeypatch):
    calls = _stub_sdk(monkeypatch, _response(text=json.dumps({"pass": True, "rationale": "ok"})))
    assert se.anthropic_judge(ENTRY, "uses kagent-controller") == {"pass": True, "rationale": "ok"}
    kw = calls[0]
    assert kw["model"] == "claude-sonnet-5-5"
    # thinking counts toward max_tokens on 5.x: the old 300 would truncate the verdict
    assert kw["max_tokens"] >= 4000
    assert kw["output_config"]["effort"] == "low"
    assert kw["output_config"]["format"]["schema"] == se.VERDICT_SCHEMA
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    for banned in ("temperature", "top_p", "top_k", "thinking"):
        assert banned not in kw, f"{banned} is a 400 (or unneeded) on claude-sonnet-5-5"


def test_a_refusal_is_not_a_verdict(monkeypatch):
    _stub_sdk(monkeypatch, _response(stop_reason="refusal", category="cyber"))
    j = se.anthropic_judge(ENTRY, "answer")
    assert j["pass"] is None and "refusal" in j["rationale"] and "cyber" in j["rationale"]
    # score_entry hands an undecided judge back to the rubric instead of failing the answer
    r = se.score_entry(ENTRY, "the kagent-controller deployment", judge=lambda e, a: j)
    assert r["decided_by"] == "rubric(judge unsure)" and r["passed"] == r["rubric_pass"]


def test_a_truncated_call_is_not_a_verdict(monkeypatch):
    _stub_sdk(monkeypatch, _response(stop_reason="max_tokens"))
    assert se.anthropic_judge(ENTRY, "answer")["pass"] is None


def test_the_cascade_keeps_a_declined_claude_verdict_undecided():
    def jev_down(*a, **k):
        raise se.JevError("jev unreachable")

    se_jev = se.jev_judge
    try:
        se.jev_judge = jev_down
        out = se.cascade_judge(ENTRY, "answer", claude=lambda e, a: {"pass": None, "rationale": "declined"})
    finally:
        se.jev_judge = se_jev
    assert out["pass"] is None and out["tier"] == "claude"
