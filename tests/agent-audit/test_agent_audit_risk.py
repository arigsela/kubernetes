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
