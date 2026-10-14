"""The agent action record on kagent 1.0 (--source v1).

fixtures/kagent-v1/events.json holds real rows: kagent v1.0.0-alpha11's own
persistence code wrote them (migrations as kagent_owner into schema `kagent`, then
CreateRuntimeTask/UpdateSessionTask with the runtime's settlement protocol), from
events built with the Go runtime's own helpers (adka2a.ToA2APart,
BuildHITLStatusMessage, AttachHITL). The generator is
fixtures/kagent-v1/audit_fixture_test.go. Each scenario is one session:

  approved-delete   gate fires, human approves, the delete runs    -> gated
  unapproved-write  label tool bound without requireApproval      -> FLAGGED
  read-only         a reader; the session was later deleted by its user
  rejected-delete   gate fires, human rejects                       -> recorded
  pending-patch     gate fires, HITL not negotiated, never answered -> not flagged
  mixed             approved delete + unapproved annotate, same session
  eval              homelab-knowledge Q&A, the answer streamed
  idle-deleted      unapproved patch; the idle sweeper removed the session row
"""
from __future__ import annotations

import base64
import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aa = _load("agent_audit", REPO / "scripts" / "agent-audit.py")
ar = _load("agent_audit_risk", REPO / "scripts" / "agent-audit-risk.py")
wire = _load("a2a_wire", HERE / "a2a_wire.py")

FIXTURE = json.loads((HERE / "fixtures" / "kagent-v1" / "events.json").read_text())
S = FIXTURE["scenarios"]
TAXONOMY = REPO / "base-apps/admission-policies/agent-capability-taxonomy.yaml"
GATED = aa.load_gated_tools(REPO, TAXONOMY)


def test_propose_tools_are_not_gated_for_the_ungated_finding():
    """§V.94: a propose tool (a branch or PR) needs no approval, so an ungated call is not a finding."""
    assert "create_pull_request" not in GATED and "k8s_patch_resource" in GATED
K8S_AGENT = "kagent__NS__k8s_agent"


def _b(v):
    return base64.b64decode(v) if v is not None else None


def sql_rows():
    """The fixture exactly as V1_EVENTS_SELECT returns it from psycopg."""
    return [(datetime.fromisoformat(at), sid, _b(sdata), name, ns, _b(data))
            for at, sid, sdata, name, ns, data in FIXTURE["rows"]]


@pytest.fixture(scope="module")
def rows():
    return list(aa.v1_rows(sql_rows()))


@pytest.fixture(scope="module")
def records(rows):
    return aa.iter_calls_v1(rows)


def _session(records, scenario):
    return [r for r in records if r["session"] == S[scenario]]


def _findings(records):
    return aa.report_ungated(records, GATED)


# ------------------------------------------------------------ the wire reader

def test_every_fixture_row_decodes(rows):
    for _, _, _, data in rows:
        event = aa.decode_event_v1(data)
        assert len(event) == 1 and set(event) <= {"task", "message", "status_update", "artifact_update"}


def test_reader_matches_the_go_shapes(records):
    approved = _session(records, "approved-delete")
    call = next(r for r in approved if r["kind"] == "call" and r["tool"] == "k8s_delete_resource")
    assert call["args"] == {"resource_type": "configmap", "resource_name": "audit-fixture",
                            "namespace": "scratch"}
    request = next(r for r in approved if r["tool"] == aa.CONFIRM_TOOL)
    assert request["for_call"] == call["call_id"] and request["for_tool"] == "k8s_delete_resource"
    assert request["args"]["originalFunctionCall"]["args"] == call["args"]


def test_struct_numbers_come_back_as_ints():
    assert aa._pb_value(wire.value(100)) == 100 and isinstance(aa._pb_value(wire.value(100)), int)
    assert aa._pb_value(wire.value(1.5)) == 1.5
    assert aa._pb_value(wire.value([None, True, "x", {"a": [1]}])) == [None, True, "x", {"a": [1]}]


def test_unknown_fields_are_skipped_not_fatal():
    """A later kagent adding a field must not break the audit."""
    msg = wire.message("m1", aa.ROLE_USER, [wire.part(text="hi")]) + wire.unknown_field(42, b"\x01\x02")
    event = aa.decode_event_v1(wire.stream_message(msg) + wire.unknown_field(9, b"zz"))
    assert event["message"]["parts"] == [{"text": "hi"}]


@pytest.mark.parametrize("raw", [b"\x12", b"\x12\x05ab", b"\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x01",
                                 b"\x0b", b"\x10\x01"])
def test_corrupt_bytes_raise(raw):
    with pytest.raises(aa.ProtoError):
        aa.decode_event_v1(raw)


def test_non_bytes_is_a_decode_error():
    with pytest.raises(aa.ProtoError):
        aa.decode_event_v1("not bytes")


# ------------------------------------------------------------- agent identity

def test_agent_id_matches_0_10_and_the_go_runtime(records):
    agents = {r["session"]: r["agent"] for r in records}
    assert agents[S["approved-delete"]] == K8S_AGENT
    assert agents[S["eval"]] == "kagent__NS__homelab_knowledge"


def test_user_deleted_session_keeps_its_agent_from_session_data(records):
    """Deletion nulls prepared_revision; session.data still names the Agent."""
    raw = next(r for r in FIXTURE["rows"] if r[1] == S["read-only"])
    assert raw[3] is None and raw[4] is None and raw[2] is not None
    assert {r["agent"] for r in _session(records, "read-only")} == {"kagent__NS__k8s_reader"}


def test_idle_deleted_session_is_unattributed(records):
    raw = next(r for r in FIXTURE["rows"] if r[1] == S["idle-deleted"])
    assert raw[2] is None and raw[3] is None
    assert {r["agent"] for r in _session(records, "idle-deleted")} == {aa.UNATTRIBUTED}


def test_agent_falls_back_to_the_revision():
    assert aa.v1_agent_id(None, "istio-agent", "kagent") == "kagent__NS__istio_agent"
    assert aa.v1_agent_id(wire.session("kagent", "k8s-agent"), "other", "x") == K8S_AGENT
    assert aa.v1_agent_id(b"\xff", None, None) == aa.UNATTRIBUTED


# ------------------------------------------------- the ungated finding, per call

def test_approved_delete_is_gated(records):
    assert not [f for f in _findings(records) if f["session"] == S["approved-delete"]]
    ev = aa.gate_evidence(records)[(S["approved-delete"], "toolu_approved_delete")]
    assert aa.approval_outcome(ev) == "approved" and ev["executed"]


def test_unapproved_write_is_flagged(records):
    hits = [f for f in _findings(records) if f["session"] == S["unapproved-write"]]
    assert [(f["agent"], f["tool"]) for f in hits] == [(K8S_AGENT, "k8s_label_resource")]
    assert aa.approval_outcome(aa.gate_evidence(records).get((S["unapproved-write"], hits[0]["call_id"]))) == "none"


def test_read_only_session_is_ignored(records):
    assert any(r["kind"] == "call" for r in _session(records, "read-only"))
    assert not [f for f in _findings(records) if f["session"] == S["read-only"]]


def test_rejected_delete_is_recorded_not_flagged(records):
    session = _session(records, "rejected-delete")
    decision = next(r for r in session if r["kind"] == "approval")
    assert (decision["decision"], decision["tool"], decision["call_id"]) == \
        ("rejected", "k8s_delete_resource", "toolu_rejected_delete")
    assert any(r.get("gate") == "rejected" for r in session)
    assert not [f for f in _findings(records) if f["session"] == S["rejected-delete"]]
    ev = aa.gate_evidence(records)[(S["rejected-delete"], "toolu_rejected_delete")]
    assert aa.approval_outcome(ev) == "rejected" and not ev["executed"]


def test_pending_request_is_not_a_finding(records):
    """HITL not negotiated: the status is text only. The runtime's "requires
    confirmation" result still proves the gate fired, and the tool never ran."""
    session = _session(records, "pending-patch")
    assert not any(r["tool"] == aa.CONFIRM_TOOL for r in session if r["kind"] == "call")
    assert not [f for f in _findings(records) if f["session"] == S["pending-patch"]]
    ev = aa.gate_evidence(records)[(S["pending-patch"], "toolu_pending_patch")]
    assert aa.approval_outcome(ev) == "pending"


def test_one_approval_does_not_excuse_another_call_in_the_session(records):
    """0.10 matched per session; this is what per-call buys."""
    hits = [f for f in _findings(records) if f["session"] == S["mixed"]]
    assert [f["tool"] for f in hits] == ["k8s_annotate_resource"]


def test_idle_deleted_finding_still_alerts(records):
    hits = [f for f in _findings(records) if f["session"] == S["idle-deleted"]]
    assert [(f["agent"], f["tool"]) for f in hits] == [(aa.UNATTRIBUTED, "k8s_patch_resource")]


def test_a_call_that_ran_without_an_approved_decision_is_flagged():
    """Gate fired, nobody approved, and yet the tool returned a real result."""
    recs = [
        {"kind": "call", "tool": "k8s_delete_resource", "args": {}, "call_id": "c1", "session": "s", "agent": "a", "at": "t"},
        {"kind": "response", "tool": "k8s_delete_resource", "response": {}, "call_id": "c1",
         "gate": "confirmation-requested", "session": "s", "agent": "a", "at": "t"},
        {"kind": "response", "tool": "k8s_delete_resource", "response": {}, "call_id": "c1",
         "session": "s", "agent": "a", "at": "t"},
    ]
    assert aa.report_ungated(recs, {"k8s_delete_resource"}) == [recs[0]]


def test_summary_over_the_fixture(rows, records):
    open_, acked = aa.partition_acknowledged(_findings(records), [])
    summary = aa.summarize_findings(open_, len(acked), source="v1",
                                    health=aa.extraction_health(rows, records))
    assert summary["severity"] == "warning" and summary["source"] == "v1"
    assert summary["findings"] == [
        {"agent": K8S_AGENT, "tool": "k8s_annotate_resource", "count": 1},
        {"agent": K8S_AGENT, "tool": "k8s_label_resource", "count": 1},
        {"agent": aa.UNATTRIBUTED, "tool": "k8s_patch_resource", "count": 1},
    ]
    assert summary["events_scanned"] == len(rows) and summary["decode_errors"] == 0
    assert summary["problems"] == []
    blob = json.dumps(summary)
    assert "scratch" not in blob and "audit-fixture" not in blob   # no argument values


# ------------------------------------------------------------- one fact, once

def test_each_fact_is_recorded_once(records):
    """Status messages are archived again as history, snapshots repeat artifacts."""
    keys = [(r["session"], r["kind"], r.get("tool"), r.get("call_id"), r.get("approval_id"), r.get("gate"),
             (r.get("response") or {}).get("sha256_12"))
            for r in records if r["kind"] != "usage"]
    assert len(keys) == len(set(keys))
    approved = _session(records, "approved-delete")
    assert sum(r["tool"] == aa.CONFIRM_TOOL for r in approved if r["kind"] == "call") == 1
    assert sum(r["kind"] == "approval" for r in approved) == 1


def test_a_streamed_answer_is_billed_once(records):
    tokens = aa.report_cost(_session(records, "eval"))
    assert tokens == {"kagent__NS__homelab_knowledge": 912 + 1260}


# ------------------------------------------------------------------ redaction

def test_no_response_body_and_no_secret_leaves(records):
    blob = json.dumps(records)
    assert "c3VwZXJzZWNyZXQ=" not in blob            # the Secret a tool returned
    assert "ghp_" not in blob and "hvs." not in blob  # tokens in args / answer text
    assert "requires confirmation" not in blob       # read for the gate, never emitted
    for r in records:
        if r["kind"] == "response":
            assert set(r["response"]) <= {"present", "bytes", "sha256_12"}


def test_secret_in_arguments_is_redacted(records):
    label = next(r for r in _session(records, "unapproved-write") if r["kind"] == "call")
    assert label["args"]["command"] == aa.REDACTED
    assert label["args"]["labels"] == "team=platform"


# ------------------------------------------------------------ fail closed

def test_undecodable_row_becomes_a_decode_error_record():
    recs = aa.iter_calls_v1([(datetime(2026, 10, 12), "a", "s", b"\x12\x05ab")])
    assert recs == [{"kind": "decode-error", "error": "ProtoError", "at": "2026-10-12T00:00:00",
                     "agent": "a", "session": "s"}]


def test_decode_errors_make_the_summary_a_warning():
    health = {"events": 10, "tool_calls": 4, "decode_errors": 2}
    summary = aa.summarize_findings([], 0, source="v1", health=health)
    assert summary["severity"] == "warning" and summary["problems"] == ["2 event(s) could not be decoded"]


def test_events_without_a_recognised_tool_call_are_a_warning():
    """--summary scans all history: zero calls means the extractor stopped matching."""
    summary = aa.summarize_findings([], 0, source="v1", health={"events": 5, "tool_calls": 0, "decode_errors": 0})
    assert summary["severity"] == "warning" and "no tool call recognised" in summary["problems"][0]
    empty = aa.summarize_findings([], 0, source="v1", health={"events": 0, "tool_calls": 0, "decode_errors": 0})
    assert empty["severity"] == "ok"


def test_v010_summary_shape_is_unchanged():
    assert set(aa.summarize_findings([])) == {"check", "severity", "ungated_invocations",
                                             "acknowledged_invocations", "findings", "detail"}


# ------------------------------------------------- acknowledged, unattributed

def test_unattributed_finding_matches_its_acknowledgement_by_session(tmp_path):
    ack = tmp_path / "acks.yaml"
    ack.write_text(
        "acknowledged:\n  - session: s1\n    agent: kagent__NS__k8s_agent\n"
        "    through: \"2026-10-12T00:00:00+00:00\"\n    reason: triaged and fixed in the binding\n")
    acks = aa.load_acknowledged(ack, now=datetime.fromisoformat("2026-10-13T00:00:00+00:00"))
    f = {"kind": "call", "tool": "k8s_patch_resource", "session": "s1", "agent": aa.UNATTRIBUTED,
         "at": "2026-10-11T00:00:00+00:00", "args": {}}
    other = {**f, "session": "s2"}
    assert aa.partition_acknowledged([f, other], acks) == ([other], [f])


# --------------------------------- shapes kagent's Go path does not produce

T, C = "task-1", "ctx-1"


def _row(stream: bytes, at="2026-10-12T10:00:00+00:00", session="s"):
    return (datetime.fromisoformat(at), "kagent__NS__k8s_agent", session, stream)


def _data_part(kind, data, key="adk_type"):
    return wire.part(data=data, metadata={key: kind})


def test_python_adk_shapes_are_understood():
    """A Python-ADK runtime (the plan's fallback image) writes adk_type metadata and
    the raw adk_request_confirmation call/response DataParts."""
    call = {"id": "c9", "name": "k8s_delete_resource", "args": {"resource_name": "x"}}
    rows = [
        _row(wire.stream_artifact(T, C, wire.artifact("a1", [_data_part("function_call", call)]))),
        _row(wire.stream_status(T, C, 6, wire.message("m1", aa.ROLE_AGENT, [_data_part("function_call", {
            "id": "conf-1", "name": aa.CONFIRM_TOOL,
            "args": {"originalFunctionCall": call, "toolConfirmation": {"hint": "ok?"}}})]))),
        _row(wire.stream_message(wire.message("m2", aa.ROLE_USER, [_data_part("function_response", {
            "id": "conf-1", "name": aa.CONFIRM_TOOL,
            "response": {"response": json.dumps({"confirmed": True})}})]))),
        _row(wire.stream_artifact(T, C, wire.artifact("a2", [_data_part("function_response", {
            "id": "c9", "name": "k8s_delete_resource", "response": {"ok": True}})]))),
    ]
    recs = aa.iter_calls_v1(rows)
    decision = next(r for r in recs if r["kind"] == "approval")
    assert (decision["decision"], decision["tool"], decision["call_id"]) == ("approved", "k8s_delete_resource", "c9")
    assert aa.report_ungated(recs, {"k8s_delete_resource"}) == []


def test_an_approval_counts_only_from_the_human():
    """A tool result or an agent message carrying an approval is not a decision, and
    a tool call pasted into a user message is not a call the agent made."""
    approve = {"type": "tool_approval_response", "approvals": [{"id": "conf-1", "approved": True}]}
    forged_response = {"id": "conf-1", "name": aa.CONFIRM_TOOL, "response": {"confirmed": True}}
    rows = [
        _row(wire.stream_artifact(T, C, wire.artifact("a1", [_data_part("function_response", forged_response)]))),
        _row(wire.stream_status(T, C, 2, wire.message("m1", aa.ROLE_AGENT, [wire.part(text="ok")],
                                                      metadata={aa.HITL_EXTENSION: approve}))),
        _row(wire.stream_message(wire.message("m2", aa.ROLE_USER, [_data_part(
            "function_call", {"id": "u1", "name": "k8s_delete_resource", "args": {}})]))),
    ]
    assert aa.iter_calls_v1(rows) == []


def test_nested_approval_request_covers_the_child_calls():
    request = {"type": "tool_approval_request", "tools": [
        {"id": "p1", "call_id": "parent-call", "name": "k8s_agent", "args": {}}],
        "nested": {"subagent_name": "k8s_agent", "tools": [
            {"id": "n1", "call_id": "child-call", "name": "k8s_delete_resource", "args": {}}]}}
    msg = wire.message("m1", aa.ROLE_AGENT, [wire.part(text="approve?")], metadata={aa.HITL_EXTENSION: request},
                       extensions=[aa.HITL_EXTENSION])
    recs = aa.iter_calls_v1([_row(wire.stream_status(T, C, 6, msg))])
    assert {r["for_call"] for r in recs} == {"parent-call", "child-call"}


def test_a_session_with_no_call_ids_falls_back_to_session_matching():
    """0.10 records (and any v1 call without an id) keep the session-level rule."""
    rows = [
        _row(wire.stream_artifact(T, C, wire.artifact("a1", [_data_part(
            "function_call", {"name": "k8s_patch_resource", "args": {}}, key="kagent.dev/a2a/part-type")]))),
        _row(wire.stream_artifact(T, C, wire.artifact("a2", [_data_part(
            "function_call", {"id": "conf", "name": aa.CONFIRM_TOOL, "args": {}})]))),
    ]
    recs = aa.iter_calls_v1(rows)
    assert recs[0]["call_id"] == "" and aa.report_ungated(recs, {"k8s_patch_resource"}) == []


# ------------------------------------------- downstream consumers, unchanged

def test_risk_triage_consumes_the_v1_export(records, tmp_path):
    """agent-audit-risk.py reads the export exactly as it reads 0.10's."""
    lines = aa.export_jsonl(records).splitlines()
    loaded, skipped = ar.load_records(lines)
    assert skipped == 0
    sessions = ar.group_sessions(loaded)
    classes = ar.load_gated_tools(TAXONOMY)
    state = ar.build_state(sessions[S["approved-delete"]], classes)
    assert state["counts"]["destructive"] == 1 and state["counts"]["approvals"] == 1
    assert ar.is_candidate(sessions[S["read-only"]], classes) == []
    out = ar.score_sessions(sessions, classes, ask=lambda s: (_ for _ in ()).throw(ar.JevError("dry")))
    assert {r["session"] for r in out} == set(S.values())


def test_v1_sql_is_schema_qualified():
    for table in ("session_task_event", "a2a_context", "session", "runtime_instance",
                  "agent_revision", "runtime_revision"):
        assert f"{aa.V1_SCHEMA}.{table} " in aa.V1_EVENTS_SELECT
    assert aa.V1_SCHEMA == "kagent"


# ------------------------------------------------------------ the CronJobs

def _gen():
    return _load("gen_agent_audit_cronjob", REPO / "scripts" / "gen-agent-audit-cronjob.py")


def _pod(cronjob):
    return cronjob["spec"]["jobTemplate"]["spec"]["template"]


def _db_key(container):
    env = {e["name"]: e for e in container["env"]}
    return env["AUDIT_DB"]["valueFrom"]["secretKeyRef"]["key"]


def test_scheduled_jobs_read_kagent_1_0():
    import yaml
    docs = _gen().build("script", "taxonomy", "acks", "risk")
    es = yaml.safe_load((REPO / "base-apps/postgresql/external-secrets-kagent-audit.yaml").read_text())
    keys = {d["secretKey"]: d["remoteRef"]["property"] for d in es["spec"]["data"]}
    for job in docs[1:4]:
        assert not job["spec"].get("suspend")
        container = _pod(job)["spec"]["containers"][0]
        script = container["args"][0]
        assert script.count("agent-audit.py --source v1 ") == script.count("agent-audit.py")
        assert _db_key(container) == "audit-database-v1" and keys["audit-database-v1"] == "db-name-v1"
        assert _pod(job)["metadata"]["labels"]["app"] == "agent-audit"


def test_archive_job_is_on_demand_and_never_feeds_the_alerts():
    archive = _gen().build("script", "taxonomy", "acks", "risk")[4]
    assert archive["metadata"]["name"] == "agent-audit-archive" and archive["spec"]["suspend"] is True
    container = _pod(archive)["spec"]["containers"][0]
    script = container["args"][0]
    assert "--source v010 --summary" in script and "--ungated" not in script and "--export" not in script
    assert _db_key(container) == "audit-database"
    # The Grafana rules select app=agent-audit; the archive's summary must not count.
    assert _pod(archive)["metadata"]["labels"]["app"] == "agent-audit-archive"
    assert archive["metadata"]["labels"]["app"] == "agent-audit-archive"
