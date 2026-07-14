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
