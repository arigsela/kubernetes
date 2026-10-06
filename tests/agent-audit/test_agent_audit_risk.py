"""Risk triage over the REDACTED export.

The input is the same JSONL the S3 export gets, so the only thing this module can
leak is what the export already contains. The tests pin: grouping, the candidate
rule (read-only sessions never reach Jev), the Jev state shape, the verdict rule,
the argument-free summary, and the record contract agent-audit-web will consume.
"""
from pathlib import Path
import importlib.util
import json

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("agent_audit_risk", REPO / "scripts" / "agent-audit-risk.py")
ar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ar)

TAXONOMY = REPO / "base-apps/admission-policies/agent-capability-taxonomy.yaml"


def _call(tool, args=None, at="2026-10-01T10:00:00+00:00", agent="kagent__NS__k8s_agent", session="s1"):
    return {"kind": "call", "tool": tool, "args": {} if args is None else args,
            "at": at, "agent": agent, "session": session}


# ---------------------------------------------------------------- loading

def test_load_records_skips_bad_lines():
    lines = [json.dumps(_call("k8s_get_resources")), "not json", "", json.dumps({"kind": "usage"})]
    records, skipped = ar.load_records(lines)
    assert len(records) == 2 and skipped == 1


def test_group_sessions_orders_calls_and_drops_non_calls():
    recs = [_call("k8s_get_pod_logs", at="2026-10-01T10:00:05+00:00"),
            {"kind": "usage", "tokens": 5, "at": "2026-10-01T10:00:06+00:00",
             "agent": "a", "session": "s1"},
            _call("k8s_get_resources", at="2026-10-01T10:00:01+00:00"),
            _call("k8s_get_events", session="s2")]
    g = ar.group_sessions(recs)
    assert [c["tool"] for c in g["s1"]] == ["k8s_get_resources", "k8s_get_pod_logs"]
    assert [c["tool"] for c in g["s2"]] == ["k8s_get_events"]


def test_group_skips_malformed_records():
    recs = [_call("k8s_get_resources"), {**_call("k8s_patch_resource"), "args": "oops"},
            {"kind": "call", "tool": "x"}]            # no session/at
    g = ar.group_sessions(recs)
    assert [c["tool"] for c in g["s1"]] == ["k8s_get_resources"]
    assert set(g) == {"s1"}


def test_load_gated_tools_reads_three_classes():
    cls = ar.load_gated_tools(TAXONOMY)
    assert cls["k8s_get_resources"] == "read"
    assert cls["k8s_patch_resource"] == "write"
    assert cls["k8s_execute_command"] == "destructive"


# -------------------------------------------------------------- candidates

@pytest.fixture(scope="module")
def classes():
    return ar.load_gated_tools(TAXONOMY)


def test_read_only_session_is_not_a_candidate(classes):
    calls = [_call("k8s_get_resources", {"resource_type": "pods"}), _call("k8s_get_pod_logs")]
    assert ar.is_candidate(calls, classes) == []


def test_gated_call_makes_a_candidate(classes):
    calls = [_call("k8s_get_resources"), _call("k8s_patch_resource", {"name": "x"})]
    assert ar.is_candidate(calls, classes) == ["gated:k8s_patch_resource"]


def test_redacted_argument_makes_a_candidate(classes):
    calls = [_call("k8s_get_resources", {"name": "<REDACTED>"})]
    assert "redacted-arg" in ar.is_candidate(calls, classes)


def test_secret_read_makes_a_candidate(classes):
    calls = [_call("k8s_get_resource_yaml", {"resource_type": "Secret", "name": "db"})]
    assert "secret-read" in ar.is_candidate(calls, classes)
    calls = [_call("k8s_get_pod_logs", {"resource_type": "secret"})]   # not a read tool
    assert ar.is_candidate(calls, classes) == []


# -------------------------------------------------------------------- state

def test_state_shape_and_approval_marker(classes):
    calls = [_call("k8s_get_resources", at="2026-10-01T10:00:00+00:00"),
             _call(ar.CONFIRM_TOOL, at="2026-10-01T10:00:01+00:00"),
             _call("k8s_patch_resource", {"name": "d"}, at="2026-10-01T10:00:02+00:00")]
    s = ar.build_state(calls, classes)
    assert s["agent"] == "kagent__NS__k8s_agent" and s["session"] == "s1"
    assert s["started"] == "2026-10-01T10:00:00+00:00" and s["ended"] == "2026-10-01T10:00:02+00:00"
    assert s["counts"] == {"read": 1, "write": 1, "destructive": 0, "unknown": 0, "approvals": 1}
    assert [c["tool"] for c in s["calls"]] == ["k8s_get_resources", "k8s_patch_resource"]
    assert s["calls"][0]["approval_requested_before"] is False
    assert s["calls"][1]["approval_requested_before"] is True
    assert s["calls"][1]["class"] == "write" and s["calls"][1]["args"] == '{"name": "d"}'
    assert s["truncated"] is False
    assert "response" not in json.dumps(s)


def test_state_truncates_long_sessions(classes):
    calls = [_call("k8s_get_resources", at=f"2026-10-01T10:{i // 60:02d}:{i % 60:02d}+00:00")
             for i in range(70)]
    s = ar.build_state(calls, classes)
    assert len(s["calls"]) == 60 and s["truncated"] is True
    assert s["counts"]["read"] == 70


def test_state_cuts_long_args(classes):
    calls = [_call("k8s_apply_manifest", {"manifest": "x" * 2000})]
    s = ar.build_state(calls, classes)
    assert len(s["calls"][0]["args"]) <= ar.MAX_ARGS_CHARS + 20   # plus the ellipsis marker


def test_state_argument_free_mode(classes):
    calls = [_call("k8s_apply_manifest", {"manifest": "kind: Deployment"})]
    s = ar.build_state(calls, classes, argument_free=True)
    assert "args" not in s["calls"][0] and "Deployment" not in json.dumps(s)


def test_questions_have_attention_choice_and_four_flags():
    assert ar.QUESTIONS["attention"]["type"] == "choice"
    assert set(ar.QUESTIONS["attention"]["criteria"]) == {"low", "medium", "high"}
    assert {k for k, q in ar.QUESTIONS.items() if q["type"] == "noul"} == \
        {"touched_secrets", "beyond_one_namespace", "hard_to_revert", "scope_creep"}


# ------------------------------------------------------------------ verdict

def _body(p=(0.8, 0.15, 0.05), conf=0.9, flags=(0.1, 0.1, 0.1, 0.1), tokens=500):
    names = ("touched_secrets", "beyond_one_namespace", "hard_to_revert", "scope_creep")
    return {"answers": {"attention": {"probabilities": dict(zip(("low", "medium", "high"), p)),
                                      "confidence": conf},
                        **{n: {"noul": f} for n, f in zip(names, flags)}},
            "usage": {"input_tokens": tokens}}


def test_classify_argmax_when_confident_and_calm():
    c = ar.classify(_body())
    assert c["verdict"] == "low" and c["input_tokens"] == 500


def test_classify_high_on_p_high_threshold():
    assert ar.classify(_body(p=(0.4, 0.3, 0.3)))["verdict"] == "high"


def test_classify_high_on_any_flag():
    c = ar.classify(_body(p=(0.9, 0.05, 0.05), flags=(0.1, 0.1, 0.75, 0.1)))
    assert c["verdict"] == "high" and any("hard_to_revert" in r for r in c["reasons"])


def test_classify_review_on_low_confidence():
    assert ar.classify(_body(conf=0.2))["verdict"] == "review"


def test_classify_rejects_malformed_body():
    with pytest.raises(ar.JevError):
        ar.classify({"answers": {"attention": {}}})


# -------------------------------------------------------------------- scoring

def _sessions(classes):
    ro = [_call("k8s_get_resources", session="ro")]
    gated = [_call("k8s_get_resources", session="g"),
             _call(ar.CONFIRM_TOOL, session="g", at="2026-10-01T10:00:01+00:00"),
             _call("k8s_delete_resource", {"name": "x"}, session="g", at="2026-10-01T10:00:02+00:00")]
    return ar.group_sessions(ro + gated)


def test_score_rules_decide_read_only_without_calling_jev(classes):
    calls = []
    ask = lambda state: calls.append(state) or _body()
    recs = ar.score_sessions(_sessions(classes), classes, ask=ask)
    by = {r["session"]: r for r in recs}
    assert by["ro"]["verdict"] == "low" and by["ro"]["decided_by"] == "rules"
    assert by["ro"]["probabilities"] is None
    assert by["g"]["decided_by"] == "jev" and [s["session"] for s in calls] == ["g"]


def test_score_marks_jev_error_as_review(classes):
    def ask(state):
        raise ar.JevError("HTTP 503")
    recs = ar.score_sessions(_sessions(classes), classes, ask=ask)
    g = next(r for r in recs if r["session"] == "g")
    assert g["verdict"] == "review" and g["decided_by"] == "jev-error" and "503" in g["error"]


def test_score_caps_jev_calls(classes):
    sessions = {f"s{i}": [_call("k8s_patch_resource", {"n": i}, session=f"s{i}")] for i in range(3)}
    n = []
    recs = ar.score_sessions(sessions, classes, ask=lambda s: n.append(1) or _body(), max_sessions=2)
    assert len(n) == 2
    assert sorted(r["decided_by"] for r in recs) == ["cap", "jev", "jev"]
    assert next(r for r in recs if r["decided_by"] == "cap")["verdict"] == "review"


def test_risk_record_contract(classes):
    from datetime import datetime, timezone
    fixed = datetime(2026, 10, 2, tzinfo=timezone.utc)
    recs = ar.score_sessions(_sessions(classes), classes, ask=lambda s: _body(), now=fixed)
    g = next(r for r in recs if r["session"] == "g")
    assert set(g) == {"kind", "session", "agent", "at", "verdict", "decided_by", "reasons",
                      "probabilities", "confidence", "flags", "calls", "gated_calls",
                      "candidate_reasons", "model", "input_tokens", "scored_at", "error"}
    assert g["kind"] == "risk" and g["at"] == "2026-10-01T10:00:02+00:00"
    assert g["calls"] == 2 and g["gated_calls"] == 1 and g["model"] == "jev-1.13.0"
    assert g["scored_at"] == "2026-10-02T00:00:00+00:00"
    assert "args" not in json.dumps(g)


# ------------------------------------------------------------------- summary

def test_summary_is_argument_free_and_counts(classes):
    recs = ar.score_sessions(_sessions(classes), classes,
                             ask=lambda s: _body(p=(0.1, 0.2, 0.7), flags=(0.9, 0.1, 0.8, 0.1)))
    s = ar.summarize(recs)
    assert s["check"] == "agent-audit-risk" and s["severity"] == "warning"
    assert s["counts"] == {"low": 1, "medium": 0, "high": 1, "review": 0}
    assert s["high"] == [{"agent": "kagent__NS__k8s_agent", "session": "g",
                          "flags": ["hard_to_revert", "touched_secrets"]}]
    assert s["jev_sessions"] == 1 and s["input_tokens"] == 500
    assert "args" not in json.dumps(s) and "name" not in json.dumps(s)


def test_summary_ok_when_nothing_high(classes):
    recs = ar.score_sessions(_sessions(classes), classes, ask=lambda s: _body())
    assert ar.summarize(recs)["severity"] == "ok"


def test_summary_warns_when_jev_is_down(classes):
    def ask(state):
        raise ar.JevError("HTTP 503")
    s = ar.summarize(ar.score_sessions(_sessions(classes), classes, ask=ask))
    assert s["severity"] == "warning" and s["jev_errors"] == 1


# ---------------------------------------------------------------------- cli

def _export_file(tmp_path, classes):
    recs = []
    for calls in _sessions(classes).values():
        recs += calls
    p = tmp_path / "record.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    return p


def test_main_requires_key_unless_dry_run(tmp_path, monkeypatch, classes):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    p = _export_file(tmp_path, classes)
    with pytest.raises(SystemExit) as e:
        ar.main(["--records", str(p), "--out", str(tmp_path / "r.jsonl"), "--repo-root", str(REPO)])
    assert "TYPESAFE_API_KEY" in str(e.value)


def test_main_dry_run_writes_records_and_summary(tmp_path, monkeypatch, capsys, classes):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    p = _export_file(tmp_path, classes)
    out = tmp_path / "r.jsonl"
    rc = ar.main(["--records", str(p), "--out", str(out), "--dry-run", "--show-state",
                  "--repo-root", str(REPO)])
    assert rc == 0
    recs = [json.loads(l) for l in out.read_text().splitlines()]
    assert {r["session"]: r["decided_by"] for r in recs} == {"ro": "rules", "g": "dry-run"}
    captured = capsys.readouterr()
    summary = json.loads(captured.out.strip().splitlines()[-1])
    assert summary["check"] == "agent-audit-risk" and summary["counts"]["review"] == 1
    assert '"session": "g"' in captured.err          # --show-state printed the state


def test_main_exits_one_on_high(tmp_path, monkeypatch, classes):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setattr(ar, "_http_post", lambda p, h, t: _body(p=(0.1, 0.1, 0.8)))
    p = _export_file(tmp_path, classes)
    rc = ar.main(["--records", str(p), "--out", str(tmp_path / "r.jsonl"), "--repo-root", str(REPO)])
    assert rc == 1
