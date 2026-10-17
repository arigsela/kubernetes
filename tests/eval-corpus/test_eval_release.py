"""scripts/eval_release.py: the one file between the eval gate and the automations (V99)."""
import json
from pathlib import Path
import importlib.util

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("eval_release", REPO / "scripts" / "eval_release.py")
er = importlib.util.module_from_spec(spec)
spec.loader.exec_module(er)


def test_render_is_sorted_versioned_and_round_trips(tmp_path):
    text = er.render({"b": {"held": True, "hash": "h", "reason": "r", "scored_at": "t"},
                      "a": {"held": False, "hash": "h", "reason": None, "scored_at": "t"}},
                     revision="abc", written_at="2026-10-17T20:00:00+00:00")
    doc = json.loads(text)
    assert list(doc["agents"]) == ["a", "b"] and doc["version"] == 1 and doc["revision"] == "abc"
    p = tmp_path / "release.json"
    p.write_text(text)
    assert er.load(p) == doc
    assert er.hold("b", doc) == "r" and er.hold("a", doc) is None and er.hold("zzz", doc) is None


def test_absent_unreadable_or_foreign_files_are_unknown_never_a_hold(tmp_path):
    assert er.load(tmp_path / "missing.json") is None
    (tmp_path / "bad.json").write_text("{not json")
    assert er.load(tmp_path / "bad.json") is None
    (tmp_path / "v2.json").write_text(json.dumps({"version": 2, "agents": {"a": {"held": True}}}))
    assert er.load(tmp_path / "v2.json") is None
    assert er.hold("a", None) is None


def test_a_held_entry_without_a_reason_still_holds():
    assert er.hold("a", {"version": 1, "agents": {"a": {"held": True}}}) == "eval regression"
    assert er.hold("a", {"version": 1, "agents": {"a": "garbage"}}) is None


def test_consumers_and_path_are_the_documented_ones():
    assert er.CONSUMER_NAMESPACES == ("dispatcher", "slack-bridge")
    assert er.DEFAULT_PATH == "/etc/eval-release/release.json" and er.CONFIGMAP == "eval-release"
