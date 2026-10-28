# tests/n8n_bug_investigator/test_finish.py
"""The labels an investigation ends with (§T.152, Task 9)."""
from __future__ import annotations

KAGENT_LABELS = {"investigated", "investigation-failed", "fix-proposed", "from-alert", "bug"}


def test_finish_success(js):
    item = {"issue": {"number": 41}, "failed": False, "posted": "created", "verdict": "likely_cause"}
    assert js("finish", "outcome", item) == {"add": ["n8n-investigated"], "remove": ["n8n-investigating"], "outcome": "investigated"}


def test_finish_answer_failure_is_spent(js):
    for stage in ("answer", "post", "ask"):
        item = {"issue": {"number": 41}, "failed": True, "failure": {"stage": stage, "spent": True, "error": "x"}}
        assert js("finish", "outcome", item) == {"add": ["n8n-investigation-failed"], "remove": ["n8n-investigating"], "outcome": "failed"}


def test_finish_prepare_failure_releases_lock(js):
    item = {"issue": {"number": 41}, "failed": True, "failure": {"stage": "prepare", "spent": False, "error": "GitHub 502"}}
    assert js("finish", "outcome", item) == {"add": [], "remove": ["n8n-investigating"], "outcome": "released"}
    # A failure flag without details was not spent on the agent: released for a retry.
    assert js("finish", "outcome", {"issue": {"number": 41}, "failed": True})["outcome"] == "released"


def test_finish_never_touches_kagent_labels(js):
    items = [{"failed": False}, {"failed": True, "failure": {"spent": True}}, {"failed": True, "failure": {"spent": False}}, {}]
    for item in items:
        o = js("finish", "outcome", {"issue": {"number": 1}, **item})
        assert not (set(o["add"]) | set(o["remove"])) & KAGENT_LABELS
