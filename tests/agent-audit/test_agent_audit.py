"""Tests for the agent action record.

The redaction tests are the point. This tool reads a table that contains live
credentials — `k8s_get_resource_yaml` against a Secret lands its base64 payload in
`event.data` verbatim. A redactor nobody tested is a redactor that does not work,
so every known secret shape gets fed through and asserted absent from the output.
"""
from pathlib import Path
import importlib.util
import json

import pytest

_spec = importlib.util.spec_from_file_location(
    "agent_audit",
    Path(__file__).resolve().parents[2] / "scripts" / "agent-audit.py",
)
aa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aa)


# ------------------------------------------------- responses: NEVER emitted

def test_response_body_is_never_emitted():
    """Categorical, not best-effort. This is where secrets actually live."""
    secret_yaml = (
        "apiVersion: v1\nkind: Secret\ndata:\n"
        "  password: c3VwZXJzZWNyZXR2YWx1ZTEyMwo=\n"
    )
    out = aa.summarize_response({"result": secret_yaml})
    blob = json.dumps(out)
    assert "c3VwZXJzZWNyZXR2YWx1ZTEyMwo=" not in blob
    assert "password" not in blob
    assert out["present"] is True
    assert out["bytes"] > 0
    assert len(out["sha256_12"]) == 12


def test_absent_response_is_marked_absent():
    assert aa.summarize_response(None) == {"present": False}


def test_response_summary_has_no_content_field_at_all():
    out = aa.summarize_response("anything at all")
    assert set(out) == {"present", "bytes", "sha256_12"}


# --------------------------------------------- arguments: secret KEY names

@pytest.mark.parametrize("key", [
    "password", "passwd", "api_key", "token", "secret", "credential",
    "authorization", "bearer", "session_id", "cookie", "private_key",
    "SIGNATURE", "Salt",
])
def test_secret_named_keys_are_redacted(key):
    assert aa.redact_value(key, "whatever-the-value-is") == aa.REDACTED


# ------------------------------------------- arguments: secret VALUE shapes
#
# These fixtures are ASSEMBLED AT RUNTIME rather than written as string literals.
#
# Not stylistic paranoia — it is load-bearing. GitHub push protection scans the
# source and blocks any literal that MATCHES A REAL SECRET SHAPE, regardless of
# whether the value is real. Writing `"xoxb-…"` inline gets the push rejected as a
# "Slack API Token", and every fixture here is by definition secret-shaped: that is
# the whole point of them.
#
# (It also caught something worse. The first draft of this file used a REAL Vault
# root token as the "Vault-shaped" fixture, copy-pasted from a debugging session.
# Push protection rejected it. Building the fixtures from parts makes that class of
# mistake impossible: there is no literal to paste a real value into.)

SECRET_SHAPES = {
    "vault":     "hvs." + "A" * 24,
    "github":    "ghp_" + "B" * 36,
    "anthropic": "sk-ant-api03-" + "C" * 32,
    "aws":       "AKIA" + "D" * 16,
    "slack":     "xox" + "b-" + "1" * 12 + "-" + "e" * 16,
    "pem":       "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...",
    "jwt":       "eyJ" + "F" * 12 + "." + "eyJ" + "G" * 12 + "." + "H" * 20,
}


@pytest.mark.parametrize("kind", sorted(SECRET_SHAPES))
def test_secret_shaped_values_are_redacted_even_under_an_innocent_key(kind):
    """The key name says nothing — a token can be passed as `command`."""
    assert aa.redact_value("command", SECRET_SHAPES[kind]) == aa.REDACTED


def test_high_entropy_base64_blob_is_redacted():
    blob = "aGVsbG8gd29ybGQgdGhpcyBpcyBhIHZlcnkgbG9uZyBzZWNyZXQgdmFsdWU5OTk5OTk5"
    assert aa.redact_value("data", blob) == aa.REDACTED


def test_a_secret_embedded_in_a_command_is_caught():
    """The exact nightmare: an agent runs kubectl with a literal secret."""
    cmd = ("kubectl create secret generic x --from-literal=t="
           + SECRET_SHAPES["github"])
    assert aa.redact_value("command", cmd) == aa.REDACTED


# ------------------------------- arguments: benign values SURVIVE (the point)

def test_benign_command_survives_redaction():
    """Auditing what k8s_execute_command actually ran IS the point. If we redact
    everything, the record is worthless."""
    out = aa.redact_value("command", "kubectl get pods -n kagent")
    assert out == "kubectl get pods -n kagent"


@pytest.mark.parametrize("key,value", [
    ("namespace", "kagent"),
    ("resource_type", "pod"),
    ("pod_name", "homelab-knowledge-abc123"),
    ("path", "base-apps/kagent/agents/k8s-agent.yaml"),
    ("repo", "kubernetes"),
    ("tail_lines", 100),
])
def test_ordinary_arguments_are_preserved(key, value):
    assert aa.redact_value(key, value) == value


def test_long_values_are_truncated_not_dropped():
    long = "a" * 500
    out = aa.redact_value("query", long)
    assert out.startswith("aaa")
    assert "+200 chars" in out


def test_nested_args_are_redacted_recursively():
    args = {"outer": {"password": "hunter2", "namespace": "kagent"}}
    out = aa.redact_args(args)
    assert out["outer"]["password"] == aa.REDACTED
    assert out["outer"]["namespace"] == "kagent"


def test_unknown_types_fail_closed():
    class Weird:
        pass
    assert aa.redact_value("x", Weird()) == aa.REDACTED


# ----------------------------------------------------- the --ungated finding

def _rec(kind, tool, session, agent="kagent__NS__k8s_agent"):
    return {"kind": kind, "tool": tool, "session": session, "agent": agent,
            "args": {}, "at": "2026-07-09T00:00:00"}


def test_ungated_finds_a_gated_tool_with_no_approval():
    """The real k8s_agent incident: k8s_execute_command ran, nobody was asked."""
    records = [_rec("call", "k8s_execute_command", "s1")]
    out = aa.report_ungated(records, gated={"k8s_execute_command"})
    assert len(out) == 1
    assert out[0]["tool"] == "k8s_execute_command"


def test_ungated_ignores_a_gated_tool_that_WAS_approved():
    records = [
        _rec("call", aa.CONFIRM_TOOL, "s1"),
        _rec("call", "k8s_execute_command", "s1"),
    ]
    assert aa.report_ungated(records, gated={"k8s_execute_command"}) == []


def test_ungated_ignores_read_only_tools():
    records = [_rec("call", "k8s_get_resources", "s1")]
    assert aa.report_ungated(records, gated={"k8s_execute_command"}) == []


def test_approval_in_one_session_does_not_excuse_another():
    """Approval is per-session. A confirmation in s1 must not cover s2."""
    records = [
        _rec("call", aa.CONFIRM_TOOL, "s1"),
        _rec("call", "k8s_delete_resource", "s2"),
    ]
    out = aa.report_ungated(records, gated={"k8s_delete_resource"})
    assert len(out) == 1
    assert out[0]["session"] == "s2"


# ------------------------------------------------- taxonomy is the one source

def test_gated_tools_come_from_the_capability_taxonomy():
    """Same file Kyverno enforces at admission — the two cannot drift."""
    repo = Path(__file__).resolve().parents[2]
    gated = aa.load_gated_tools(repo)
    assert "k8s_execute_command" in gated      # destructive
    assert "k8s_delete_resource" in gated      # destructive
    assert "k8s_label_resource" in gated       # write
    assert "update_dashboard" in gated         # write (grafana)
    assert "k8s_get_resources" not in gated    # read
    assert "get_file_contents" not in gated    # read


# --------------------------------------------------------- cost attribution

def test_cost_rolls_up_per_agent():
    records = [
        {"kind": "usage", "tokens": 100, "agent": "a", "session": "s", "at": ""},
        {"kind": "usage", "tokens": 50, "agent": "a", "session": "s", "at": ""},
        {"kind": "usage", "tokens": 7, "agent": "b", "session": "s", "at": ""},
    ]
    assert aa.report_cost(records) == {"a": 150, "b": 7}


# ------------------------- the ALERT payload (--summary) must carry NO arguments
#
# This is the O2 boundary. The alert lands in Loki (30d) and is read through
# Grafana — a wider audience and a longer life than a human running the tool by
# hand against the read-only DB. Argument redaction is only BEST-EFFORT, so the
# alert must not depend on it: it carries counts and names, never arguments.

def _finding(agent, tool, args, session="s1"):
    return {"kind": "call", "agent": agent, "tool": tool, "args": args,
            "session": session, "at": "2026-07-09T23:41:15"}


def test_summary_carries_no_argument_values_at_all():
    """The real finding: k8s_execute_command inside vault-0, reading /vault/data.

    Assert on whole ARGUMENT VALUES, not on short fragments — 'cat' would match
    inside 'ungated_invo(cat)ions' and give a false alarm. The property under test
    is "no argument value survives", so test exactly that.
    """
    args = {
        "pod_name": "vault-0",
        "namespace": "vault",
        "command": "/bin/sh -c 'cat /vault/data/core/master'",
    }
    blob = json.dumps(aa.summarize_findings([
        _finding("k8s_agent", "k8s_execute_command", args)
    ]))
    for value in args.values():
        assert value not in blob, f"argument value {value!r} leaked into the alert"
    # and the distinctive path fragment specifically — this is the sensitive bit
    assert "/vault/data" not in blob


def test_summary_keeps_what_an_alert_actually_needs():
    findings = [
        _finding("k8s_agent", "k8s_execute_command", {"command": "x"}),
        _finding("k8s_agent", "k8s_execute_command", {"command": "y"}),
        _finding("k8s_agent", "k8s_delete_resource", {"resource_name": "z"}),
    ]
    out = aa.summarize_findings(findings)
    assert out["ungated_invocations"] == 3
    assert out["severity"] == "warning"
    assert {"agent": "k8s_agent", "tool": "k8s_execute_command", "count": 2} in out["findings"]
    assert {"agent": "k8s_agent", "tool": "k8s_delete_resource", "count": 1} in out["findings"]


def test_summary_per_finding_fields_are_exactly_agent_tool_count():
    """No `args` key may ever appear here — assert the shape, not just the values."""
    out = aa.summarize_findings([_finding("a", "t", {"secret": "hunter2"})])
    assert set(out["findings"][0]) == {"agent", "tool", "count"}


def test_summary_is_clean_when_there_is_nothing_to_report():
    out = aa.summarize_findings([])
    assert out["ungated_invocations"] == 0
    assert out["severity"] == "ok"
    assert out["findings"] == []


# ------------------- the durable S3 export (O4) must stay redacted end to end
#
# This payload goes to LONG-TERM S3 storage. If a response body ever survived into
# it, a secret would be durably archived, not just briefly logged. So the export
# path is held to the same categorical rule as everything else.

def test_export_is_jsonl_one_valid_object_per_line():
    records = [
        {"kind": "call", "tool": "k8s_get_resources", "args": {"namespace": "kagent"},
         "agent": "a", "session": "s", "at": "2026-07-09T00:00:00"},
        {"kind": "usage", "tokens": 42, "agent": "a", "session": "s", "at": "2026-07-09T00:00:01"},
    ]
    out = aa.export_jsonl(records)
    lines = out.splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # each line parses independently — that is the JSONL contract


def test_export_never_carries_a_response_body():
    """iter_calls already redacts; this proves the export function does not undo it."""
    rec = {"kind": "response", "tool": "k8s_get_resource_yaml",
           "response": aa.summarize_response({"result": "kind: Secret\ndata:\n  p: c2VjcmV0"}),
           "agent": "a", "session": "s", "at": "2026-07-09T00:00:00"}
    out = aa.export_jsonl([rec])
    assert "c2VjcmV0" not in out
    assert "result" not in out
    # only the summary fields survive
    assert set(json.loads(out)["response"]) == {"present", "bytes", "sha256_12"}


def test_export_keeps_auditable_call_arguments():
    """The whole point of an audit trail: what k8s_execute_command actually ran."""
    rec = {"kind": "call", "tool": "k8s_execute_command",
           "args": {"command": "vault status", "namespace": "vault"},
           "agent": "a", "session": "s", "at": "2026-07-09T00:00:00"}
    out = json.loads(aa.export_jsonl([rec]))
    assert out["args"]["command"] == "vault status"


def test_export_of_empty_record_is_empty_not_crash():
    assert aa.export_jsonl([]) == ""
