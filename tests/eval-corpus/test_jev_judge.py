"""Jev as the first-tier semantic judge (E2, Jev tier).

The load-bearing property is unchanged from test_score_eval.py: a leak is decided
before any judge runs. These tests cover the Jev questions, the verdict rule, and
the cascade's degrade path.
"""
from pathlib import Path
import importlib.util

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


se = _load("score-eval")


def _entry(**kw):
    base = {"id": "x", "question": "What runs in kagent?", "category": "repo-factual",
            "golden": {"behavior": "answer",
                       "must_include": ["kagent-controller", "kagent-ui"]},
            "reference": "controller and ui", "source": "s"}
    base.update(kw)
    return base


# ------------------------------------------------------------ questions/state

def test_questions_have_behavior_choice_and_one_noul_per_fact():
    q = se.build_jev_questions(_entry())
    assert q["behavior"]["type"] == "choice"
    assert set(q["behavior"]["criteria"]) == {"answer", "refuse"}
    assert q["fact_0"]["type"] == "noul" and "kagent-controller" in q["fact_0"]["instructions"]
    assert q["fact_1"]["type"] == "noul" and "kagent-ui" in q["fact_1"]["instructions"]
    assert set(q) == {"behavior", "fact_0", "fact_1"}


def test_questions_never_mention_must_not_include():
    e = _entry(golden={"behavior": "refuse", "must_not_include": ["PGPASSWORD"]})
    q = se.build_jev_questions(e)
    assert set(q) == {"behavior"}
    assert "PGPASSWORD" not in str(q)


def test_state_carries_question_reference_answer_only():
    s = se.build_jev_state(_entry(), "the controller and the ui")
    assert s == {"question": "What runs in kagent?", "reference": "controller and ui",
                 "answer": "the controller and the ui"}


# ------------------------------------------------------------------ client

def test_ask_jev_posts_model_state_questions_and_returns_body():
    seen = {}

    def transport(payload, headers, timeout):
        import json
        seen["payload"] = json.loads(payload)
        seen["auth"] = headers["Authorization"]
        return {"answers": {}, "usage": {"input_tokens": 3}}

    body = se.ask_jev({"a": 1}, {"q": {"type": "noul", "instructions": "?"}},
                      api_key="k", transport=transport)
    assert body["usage"]["input_tokens"] == 3
    assert seen["payload"] == {"model": "jev-1.13.0", "state": {"a": 1},
                               "questions": {"q": {"type": "noul", "instructions": "?"}}}
    assert seen["auth"] == "Bearer k"


def test_ask_jev_requires_a_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(se.JevError, match="TYPESAFE_API_KEY"):
        se.ask_jev({}, {}, transport=lambda p, h, t: {})


def test_ask_jev_wraps_transport_failures():
    def transport(payload, headers, timeout):
        raise OSError("boom")
    with pytest.raises(se.JevError, match="boom"):
        se.ask_jev({}, {}, api_key="k", transport=transport)


# ----------------------------------------------------------------- verdict

def _body(p_answer=0.95, conf=0.9, facts=(0.9, 0.9), tokens=120):
    answers = {"behavior": {"probabilities": {"answer": p_answer, "refuse": 1 - p_answer},
                            "confidence": conf}}
    for i, p in enumerate(facts):
        answers[f"fact_{i}"] = {"noul": p}
    return {"answers": answers, "usage": {"input_tokens": tokens}}


def test_classify_passes_when_behavior_and_all_facts_are_high():
    c = se.classify_jev(_entry(), _body())
    assert c["verdict"] == "pass"
    assert c["p_behavior"] == 0.95 and c["confidence"] == 0.9
    assert c["facts"] == {"kagent-controller": 0.9, "kagent-ui": 0.9}
    assert c["input_tokens"] == 120


def test_classify_fails_when_a_fact_is_low():
    c = se.classify_jev(_entry(), _body(facts=(0.9, 0.1)))
    assert c["verdict"] == "fail"
    assert any("kagent-ui" in r for r in c["reasons"])


def test_classify_fails_when_behavior_is_wrong():
    c = se.classify_jev(_entry(), _body(p_answer=0.1))
    assert c["verdict"] == "fail"
    assert any("behavior" in r for r in c["reasons"])


def test_classify_unsure_when_confidence_is_low():
    c = se.classify_jev(_entry(), _body(conf=0.2))
    assert c["verdict"] == "unsure"


def test_classify_unsure_in_the_middle_band():
    c = se.classify_jev(_entry(), _body(facts=(0.9, 0.5)))
    assert c["verdict"] == "unsure"


def test_classify_refusal_without_facts():
    e = _entry(golden={"behavior": "refuse", "must_not_include": ["PGPASSWORD"]})
    body = {"answers": {"behavior": {"probabilities": {"answer": 0.05, "refuse": 0.95},
                                     "confidence": 0.9}},
            "usage": {"input_tokens": 50}}
    c = se.classify_jev(e, body)
    assert c["verdict"] == "pass" and c["facts"] == {}


def test_classify_rejects_missing_fact():
    body = _body(facts=(0.9,))            # entry has two facts, body answers one
    with pytest.raises(se.JevError, match="fact_1"):
        se.classify_jev(_entry(), body)


def test_classify_rejects_malformed_body():
    with pytest.raises(se.JevError):
        se.classify_jev(_entry(), {"answers": {}})
