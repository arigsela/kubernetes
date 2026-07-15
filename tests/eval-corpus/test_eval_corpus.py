"""Tests for the eval-corpus validator and the miner.

Two things matter most and get the most tests:
  * the committed corpus is well-formed AND contains no secret
  * the miner redacts free-text question/answer content before it leaves the DB
"""
from pathlib import Path
import importlib.util

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vec = _load("validate-eval-corpus")
mine = _load("mine-eval-corpus")


# ---------------------------------------------------------- the real corpus

def test_committed_corpus_is_valid():
    assert vec.validate(REPO) == []


def test_committed_corpus_has_a_refusal_and_a_factual_entry():
    """The corpus must exercise both the safety property (refuse to leak) and a
    plain factual answer — a corpus of only one shape tests only one thing."""
    doc = yaml.safe_load((REPO / "tests/eval-corpus/homelab-knowledge.yaml").read_text())
    cats = {e["category"] for e in doc["entries"]}
    assert "security-refusal" in cats
    assert "repo-factual" in cats


# -------------------------------------------------- validator: schema errors

def _entry(**kw):
    base = {
        "id": "x", "question": "q?", "category": "repo-factual",
        "golden": {"behavior": "answer", "must_include": ["fact"]},
        "source": "somewhere",
    }
    base.update(kw)
    return base


def test_missing_question_is_error(tmp_path):
    assert vec.check_entry(Path("f.yaml"), 0, _entry(question=None))


def test_unknown_category_is_error(tmp_path):
    assert vec.check_entry(Path("f.yaml"), 0, _entry(category="vibes"))


def test_answer_without_must_include_is_error():
    e = _entry(golden={"behavior": "answer"})
    assert vec.check_entry(Path("f.yaml"), 0, e)


def test_refusal_without_must_not_include_is_error():
    """A refusal that doesn't say what must stay out isn't scorable."""
    e = _entry(category="security-refusal", golden={"behavior": "refuse"})
    assert vec.check_entry(Path("f.yaml"), 0, e)


def test_valid_refusal_passes():
    e = _entry(category="security-refusal",
               golden={"behavior": "refuse", "must_not_include": ["PGPASSWORD"]})
    assert vec.check_entry(Path("f.yaml"), 0, e) == []


# ------------------------------------- validator: NO secret in the corpus

def test_a_secret_committed_to_the_corpus_is_caught():
    """The corpus is in git. A golden that pasted a real token must fail the gate.
    Assembled at runtime so this test file itself carries no secret-shaped literal."""
    token = "hvs." + "Z" * 26
    e = _entry(reference=f"the value is {token}")
    errs = vec.check_entry(Path("f.yaml"), 0, e)
    assert any("secret-shaped" in msg for msg in errs)


def test_describing_a_secret_by_name_is_fine():
    e = _entry(category="security-refusal",
               golden={"behavior": "refuse", "must_not_include": ["password:"]},
               reference="The agent must decline to print the DB password.")
    assert vec.check_entry(Path("f.yaml"), 0, e) == []


# --------------------------------------------------- miner: pairing + redaction

def _evt(author, text, session="s1"):
    return {"author": author, "content": {"parts": [{"text": text}]}}


class _Row(tuple):
    pass


def _rows(*triples):
    # (author, text, session) -> (created_at, author, session, json)
    import json
    out = []
    for i, (author, text, session) in enumerate(triples):
        out.append((f"2026-07-09T00:00:0{i}", author, session, json.dumps(_evt(author, text))))
    return out


def test_miner_pairs_question_with_following_answer():
    rows = _rows(
        ("user", "What runs in kagent?", "s1"),
        ("homelab_knowledge", "These apps run: ...", "s1"),
    )
    pairs = mine.iter_pairs(rows, "homelab_knowledge")
    assert len(pairs) == 1
    assert pairs[0]["question"] == "What runs in kagent?"
    assert "These apps run" in pairs[0]["candidate_answer"]


def test_miner_starts_a_new_pair_on_the_next_question():
    rows = _rows(
        ("user", "Q1", "s1"),
        ("homelab_knowledge", "A1", "s1"),
        ("user", "Q2", "s1"),
        ("homelab_knowledge", "A2", "s1"),
    )
    pairs = mine.iter_pairs(rows, "homelab_knowledge")
    assert [p["question"] for p in pairs] == ["Q1", "Q2"]


def test_miner_does_not_pair_across_sessions():
    rows = _rows(
        ("user", "Q in s1", "s1"),
        ("homelab_knowledge", "A in s2", "s2"),  # different session
    )
    pairs = mine.iter_pairs(rows, "homelab_knowledge")
    # the s1 question has no answer in its own session -> no pair
    assert pairs == []


def test_miner_redacts_a_secret_in_the_answer_text():
    """A user pastes, or an agent echoes, a token in free text. It must not survive
    into the mined candidate."""
    token = "ghp_" + "Q" * 36
    rows = _rows(
        ("user", "print the token", "s1"),
        ("homelab_knowledge", f"sure: {token}", "s1"),
    )
    pairs = mine.iter_pairs(rows, "homelab_knowledge")
    assert token not in pairs[0]["candidate_answer"]
    assert mine.aa.REDACTED in pairs[0]["candidate_answer"]


def test_miner_keeps_ordinary_answer_text():
    rows = _rows(
        ("user", "what namespace?", "s1"),
        ("homelab_knowledge", "the kagent namespace", "s1"),
    )
    pairs = mine.iter_pairs(rows, "homelab_knowledge")
    assert pairs[0]["candidate_answer"] == "the kagent namespace"
