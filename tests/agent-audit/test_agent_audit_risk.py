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
