#!/usr/bin/env python3
"""Agent action record — who called what, with what arguments, was it approved.

kagent already persists all of this; nothing reads it. This is the read side.

    ./scripts/agent-audit.py --ungated          # the query that matters
                                                # (marks triaged findings: see ACKNOWLEDGED)
    ./scripts/agent-audit.py --cost
    ./scripts/agent-audit.py --agent k8s_agent --since 7d
    ./scripts/agent-audit.py --source v010 --ungated   # the kagent 0.10 archive

TWO SOURCES (--source)
----------------------
v1 (default) reads kagent 1.0: database `kagent_v1`, schema `kagent`, events stored
as A2A protobuf (see "kagent 1.0: the record" below). v010 reads the kagent 0.10
database `kagent`, schema `public`, events stored as ADK JSON; since the 1.0 cutover
it is a read-only archive and stays auditable with this flag. AGENT_AUDIT_DSN must
point at the matching database. Both produce the same record kinds, so the
redaction, the acknowledged file, the summary, the export and agent-audit-risk.py
work on either.

WHY IT EXISTS
-------------
The O0 spike ran one query against kagent's Postgres and found this:

    k8s_agent — gated tools actually invoked:
      k8s_execute_command    14 calls   on 2026-07-09
      k8s_delete_resource     1 call    on 2026-04-19

    approval requests raised by k8s_agent in ALL its history:  0

Fourteen arbitrary shell commands executed through a human-approval gate that was
silently doing nothing, because Argo was stripping `requireApproval` on every sync.
The evidence sat in that database for months. Nobody could see it because nothing
queried it. `--ungated` is that query, generalised.

REDACTION — read this before changing anything
----------------------------------------------
`event.data` contains full tool RESPONSES. `k8s_get_resource_yaml` against a Secret
returns its base64 payload verbatim; `k8s_get_pod_logs` returns whatever the app
logged. The raw event stream must be treated as CONTAINING LIVE CREDENTIALS.

The rule is: **redact at extraction, never at display.** This script never emits a
response body — not truncated, not "just for debugging". It emits the fact that a
response occurred, its length, and a hash. Downstream consumers (a dashboard, an
alert, an agent) read this output, never the table.

Arguments are the genuinely hard case and are kept DELIBERATELY: the single most
security-relevant fact in the whole record is what `k8s_execute_command` actually
ran, and dropping it would gut the audit. So argument values are pattern-redacted
and truncated instead.

    Argument redaction is BEST-EFFORT, not a guarantee.

Treat this output as internal. It is *safer*, not *safe*. Do not pipe it to Slack,
a public dashboard, or an agent-readable tool without reviewing the redaction
against what those arguments actually contain.

One external consumer exists by decision: agent-audit-risk.py sends the redacted
arguments of candidate sessions to TypeSafe (Jev). See its header for the review.

Read-only by construction: it logs in as the SELECT-only `kagent_audit_ro` role
(base-apps/postgresql/init-kagent-audit-role.yaml). An audit tool with write access
to the database it audits is not an audit tool.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import math
import os
import re
import struct
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

TAXONOMY = "base-apps/admission-policies/agent-capability-taxonomy.yaml"

# Ungated findings that were triaged and closed (see the file's header).
ACKNOWLEDGED = "scripts/agent-audit-acknowledged.yaml"

# The tool kagent uses to request a human confirmation for a gated call.
CONFIRM_TOOL = "adk_request_confirmation"

# ----------------------------------------------------------------- redaction

REDACTED = "<REDACTED>"

# Argument KEYS whose value is secret by name alone.
SECRET_KEY_RE = re.compile(
    r"(pass|passwd|password|secret|token|key|credential|auth|bearer|session|cookie"
    r"|private|signature|salt|nonce)",
    re.IGNORECASE,
)

# Value SHAPES that are secret regardless of the key they sit under.
PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+")
KNOWN_PREFIX_RE = re.compile(
    r"\b("
    r"hvs\.[A-Za-z0-9._-]{12,}"          # Vault
    r"|gh[pousr]_[A-Za-z0-9]{20,}"       # GitHub
    r"|sk-[A-Za-z0-9-]{20,}"             # OpenAI / Anthropic style
    r"|AKIA[0-9A-Z]{16}"                 # AWS access key id
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"     # Slack
    r")"
)
# Long unbroken base64-ish blobs — the shape of an encoded secret.
B64_BLOB_RE = re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b")

MAX_VALUE_LEN = 300


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = defaultdict(int)
    for ch in s:
        counts[ch] += 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _looks_like_secret_value(value: str) -> bool:
    """Shape-based detection, independent of the key name."""
    if PEM_RE.search(value) or JWT_RE.search(value) or KNOWN_PREFIX_RE.search(value):
        return True
    for blob in B64_BLOB_RE.findall(value):
        # A long, high-entropy, unbroken token is a secret until proven otherwise.
        if _shannon_entropy(blob) > 4.0:
            return True
        # Base64 that decodes to something binary-ish is also suspect.
        try:
            base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True)
        except (binascii.Error, ValueError):
            continue
        if _shannon_entropy(blob) > 3.5:
            return True
    return False


def redact_value(key: str, value):
    """Redact one argument value. FAIL-CLOSED: unclassifiable → redacted."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, list):
        return [redact_value(key, v) for v in value]
    if isinstance(value, dict):
        return {k: redact_value(k, v) for k, v in value.items()}
    if not isinstance(value, str):
        # Unknown type — do not guess.
        return REDACTED

    if SECRET_KEY_RE.search(key or ""):
        return REDACTED
    if _looks_like_secret_value(value):
        return REDACTED
    if len(value) > MAX_VALUE_LEN:
        return value[:MAX_VALUE_LEN] + f"…[+{len(value) - MAX_VALUE_LEN} chars]"
    return value


def redact_args(args: dict | None) -> dict:
    if not isinstance(args, dict):
        return {}
    return {k: redact_value(k, v) for k, v in args.items()}


def summarize_response(resp) -> dict:
    """A response is NEVER emitted. Only that it happened, how big, and a hash.

    This is categorical, not best-effort. Response bodies carry no audit value that
    could justify the risk — `k8s_get_resource_yaml` of a Secret lands here verbatim.
    """
    if resp is None:
        return {"present": False}
    body = resp if isinstance(resp, str) else json.dumps(resp, sort_keys=True)
    return {
        "present": True,
        "bytes": len(body),
        "sha256_12": hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()[:12],
    }


# ------------------------------------------------------------------ taxonomy


def load_gated_tools(repo_root: Path, taxonomy_path: Path | None = None) -> set[str]:
    """Tools the capability contract says MUST be behind requireApproval.

    Single source of truth: the same taxonomy admission enforces. If a
    tool is write/destructive there, an invocation of it with no approval event is
    a finding here. The two cannot drift.

    taxonomy_path lets the in-cluster CronJob point at the mounted ConfigMap
    instead of a repo checkout. Same content either way.
    """
    import yaml  # local import: only needed for --ungated

    path = taxonomy_path or (repo_root / TAXONOMY)
    cm = next(
        d for d in yaml.safe_load_all(path.read_text())
        if d and d.get("kind") == "ConfigMap"
    )
    gated: set[str] = set()
    for classification in ("write", "destructive"):
        gated |= set(json.loads(cm["data"][classification]))
    return gated


# --------------------------------------------------------------- extraction


def _either(node: dict, snake: str, camel: str):
    """A key as the Python ADK writes it (snake_case) or the Go ADK (camelCase).

    kagent 0.10's Python runtime stores event.model_dump_json(); its Go runtime
    (`runtime: go`, every agent since 2026-10-11) stores json.Marshal(event), and
    genai's Go types tag their fields in camelCase. Both land in the same `event`
    table, so a walker that knows only one spelling is blind to the other runtime.
    """
    value = node.get(snake)
    return value if value is not None else node.get(camel)


def iter_calls(event_rows):
    """Yield one redacted record per tool invocation.

    event_rows: (created_at, agent_id, session_id, data_json_text)
    """
    for created_at, agent_id, session_id, raw in event_rows:
        try:
            doc = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        found: list[dict] = []

        def walk(node):
            if isinstance(node, dict):
                fc = _either(node, "function_call", "functionCall")
                if isinstance(fc, dict) and fc.get("name"):
                    found.append({
                        "kind": "call",
                        "tool": fc["name"],
                        "args": redact_args(fc.get("args")),
                    })
                fr = _either(node, "function_response", "functionResponse")
                if isinstance(fr, dict) and fr.get("name"):
                    found.append({
                        "kind": "response",
                        "tool": fr["name"],
                        "response": summarize_response(fr.get("response")),
                    })
                um = _either(node, "usage_metadata", "usageMetadata")
                tokens = _either(um, "total_token_count", "totalTokenCount") if isinstance(um, dict) else None
                if tokens:
                    found.append({"kind": "usage", "tokens": tokens})
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for i in node:
                    walk(i)

        walk(doc)
        for rec in found:
            rec["at"] = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
            rec["agent"] = agent_id
            rec["session"] = session_id
            yield rec


# ------------------------------------------- kagent 1.0: the record (--source v1)
#
# kagent 1.0 (verified against v1.0.0-alpha11) keeps a conversation in its own
# database (`kagent_v1`, schema `kagent`) as A2A v1 PROTOBUF, not ADK JSON:
#
#   session_task_event.data  BYTEA  proto.Marshal(lf.a2a.v1.StreamResponse)
#                                   one of: task (creation snapshot), message (an
#                                   archived history message), status_update,
#                                   artifact_update
#   session.data             BYTEA  proto.Marshal(kagent.api.v1alpha1.Session);
#                                   field 15 is the Agent {namespace, name}
#   a2a_context              (id = session_task_event.history_id, context_id = the
#                                   session id; survives session deletion)
#
# (go/core/pkg/migrations/core/000001_initial.sql, go/core/internal/database/
# session_tasks.go + task_events.go, github.com/a2aproject/a2a-go/v2 a2apb/v1.)
#
# The Go runtime (go/adk) emits every ADK event as an artifact_update. A tool call is
# a DataPart {id, name, args} and its result a DataPart {id, name, response}, tagged
# by part metadata `kagent.dev/a2a/part-type` = function_call | function_response
# (go/api/a2a/tool_activity.go; the runtime deletes every adk_* key on the way out).
#
# APPROVALS. A tool bound with requireApproval returns the error "requires
# confirmation, please approve or reject" (adk tool.ErrConfirmationRequired) and the
# task goes input-required with a status message carrying the HITL extension
# https://kagent.dev/extensions/hitl/v1: {type: tool_approval_request, tools: [{id,
# call_id, name, args}]}. The human's answer comes back as a message with {type:
# tool_approval_response, approvals: [{id, approved}]}, which the runtime re-emits
# as a working status update "for history/audit" (go/adk/pkg/a2a/hitl.go,
# executor.go). A rejected call then returns the error "call is rejected". Unlike
# 0.10, every piece carries the tool call's id, so approval is decided PER CALL.
#
# The decoder below is a minimal protobuf wire reader (no dependency, so the
# CronJob and CI need nothing new). It reads only the fields named in _SCHEMAS and
# skips any other field number, so a new field in a later kagent cannot break it;
# corrupt bytes raise ProtoError and become a `decode-error` record, never a silent
# skip.

V1_SCHEMA = "kagent"
HITL_EXTENSION = "https://kagent.dev/extensions/hitl/v1"
# kagent's public key first; adk_type / kagent_type are what a runtime that does not
# canonicalise (the Python ADK fallback image) would write.
PART_TYPE_KEYS = ("kagent.dev/a2a/part-type", "adk_type", "kagent_type")
USAGE_KEYS = ("kagent.dev/a2a/usage", "adk_usage_metadata")
# The error texts google.golang.org/adk/v2 tool.confirmCall returns in place of a
# gated tool's result: the gate fired, or the human rejected the call.
GATE_MARKERS = (
    ("requires confirmation, please approve or reject", "confirmation-requested"),
    ("call is rejected", "rejected"),
)
# A session's Agent is known only while its `session` row exists. kagent's idle
# sweeper (sessionIdleTTL, default 168h) deletes the row but keeps the events.
UNATTRIBUTED = "unattributed"


class ProtoError(ValueError):
    """The bytes are not the protobuf message they should be."""


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = value = 0
    while True:
        if i >= len(buf):
            raise ProtoError("truncated varint")
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7
        if shift >= 64:
            raise ProtoError("varint too long")


def _fields(buf: bytes):
    """(field number, wire type, value) for each field of one encoded message."""
    i, end = 0, len(buf)
    while i < end:
        key, i = _varint(buf, i)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise ProtoError("field number 0")
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 1:
            value, i = buf[i:i + 8], i + 8
        elif wire == 2:
            size, i = _varint(buf, i)
            value, i = buf[i:i + size], i + size
        elif wire == 5:
            value, i = buf[i:i + 4], i + 4
        else:
            raise ProtoError(f"unsupported wire type {wire}")
        if i > end:
            raise ProtoError("truncated field")
        yield number, wire, value


def _need(wire: int, want: int) -> None:
    if wire != want:
        raise ProtoError(f"wire type {wire}, expected {want}")


def _text(value: bytes) -> str:
    return bytes(value).decode("utf-8", "replace")


def _pb_struct(buf: bytes) -> dict:
    """google.protobuf.Struct -> dict."""
    out = {}
    for number, wire, entry in _fields(buf):
        if number != 1:
            continue
        _need(wire, 2)
        key, val = "", None
        for n, w, v in _fields(entry):
            if n == 1:
                _need(w, 2)
                key = _text(v)
            elif n == 2:
                _need(w, 2)
                val = _pb_value(v)
        out[key] = val
    return out


def _pb_value(buf: bytes):
    """google.protobuf.Value -> None | float/int | str | bool | dict | list."""
    result = None
    for number, wire, value in _fields(buf):
        if number == 1:
            result = None
        elif number == 2:
            _need(wire, 1)
            num = struct.unpack("<d", value)[0]
            # Struct has only doubles; give back the integers JSON had in 0.10.
            result = int(num) if num.is_integer() and abs(num) < 2 ** 53 else num
        elif number == 3:
            _need(wire, 2)
            result = _text(value)
        elif number == 4:
            _need(wire, 0)
            result = bool(value)
        elif number == 5:
            _need(wire, 2)
            result = _pb_struct(value)
        elif number == 6:
            _need(wire, 2)
            items = []
            for n, w, v in _fields(value):
                if n == 1:
                    _need(w, 2)
                    items.append(_pb_value(v))
            result = items
    return result


# field number -> (name, kind, repeated). Kinds: str, int, bool, struct, value, or
# another schema. Field numbers from a2a-go v2.6.0 a2apb/v1 (lf.a2a.v1) and kagent
# proto/kagent/api/v1alpha1/{sessions,common}.proto.
_SCHEMAS = {
    "StreamResponse": {1: ("task", "Task", False), 2: ("message", "Message", False),
                       3: ("status_update", "TaskStatusUpdateEvent", False),
                       4: ("artifact_update", "TaskArtifactUpdateEvent", False)},
    "Task": {1: ("id", "str", False), 2: ("context_id", "str", False),
             3: ("status", "TaskStatus", False), 4: ("artifacts", "Artifact", True),
             5: ("history", "Message", True), 6: ("metadata", "struct", False)},
    "TaskStatus": {1: ("state", "int", False), 2: ("message", "Message", False)},
    "Message": {1: ("message_id", "str", False), 2: ("context_id", "str", False),
                3: ("task_id", "str", False), 4: ("role", "int", False),
                5: ("parts", "Part", True), 6: ("metadata", "struct", False),
                7: ("extensions", "str", True)},
    "Part": {1: ("text", "str", False), 4: ("data", "value", False),
             5: ("metadata", "struct", False)},
    "Artifact": {1: ("artifact_id", "str", False), 4: ("parts", "Part", True),
                 5: ("metadata", "struct", False)},
    "TaskStatusUpdateEvent": {1: ("task_id", "str", False), 2: ("context_id", "str", False),
                              3: ("status", "TaskStatus", False), 4: ("metadata", "struct", False)},
    "TaskArtifactUpdateEvent": {1: ("task_id", "str", False), 2: ("context_id", "str", False),
                                3: ("artifact", "Artifact", False), 4: ("append", "bool", False),
                                5: ("last_chunk", "bool", False), 6: ("metadata", "struct", False)},
    "Session": {15: ("agent", "ResourceReference", False)},
    "ResourceReference": {1: ("namespace", "str", False), 2: ("name", "str", False)},
}
ROLE_USER, ROLE_AGENT = 1, 2


def _decode(schema: str, buf: bytes) -> dict:
    out: dict = {}
    fields = _SCHEMAS[schema]
    for number, wire, value in _fields(buf):
        if number not in fields:
            continue
        name, kind, repeated = fields[number]
        if kind in ("int", "bool"):
            _need(wire, 0)
            item = bool(value) if kind == "bool" else value
        else:
            _need(wire, 2)
            if kind == "str":
                item = _text(value)
            elif kind == "struct":
                item = _pb_struct(value)
            elif kind == "value":
                item = _pb_value(value)
            else:
                item = _decode(kind, value)
        if repeated:
            out.setdefault(name, []).append(item)
        else:
            out[name] = item
    return out


def decode_event_v1(data) -> dict:
    """One session_task_event.data -> the StreamResponse as nested dicts."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ProtoError(f"expected bytes, got {type(data).__name__}")
    return _decode("StreamResponse", bytes(data))


def v1_agent_id(session_data, agent_name: str | None = None, namespace: str | None = None) -> str:
    """The session's Agent as `<namespace>__NS__<name>`, hyphens as underscores.

    That is the Go runtime's app name (go/adk/cmd/main.go deriveAppName) and the
    same string 0.10 stored in session.agent_id, so findings, acknowledgements and
    dashboards keep one identity across the archive and kagent 1.0. The Agent comes
    from session.data (kept for user-deleted sessions), else the session's prepared
    revision; with neither (idle-deleted session) it is UNATTRIBUTED.
    """
    ref: dict = {}
    if session_data:
        try:
            ref = _decode("Session", bytes(session_data)).get("agent") or {}
        except (ProtoError, struct.error):
            ref = {}
    ns, name = ref.get("namespace"), ref.get("name")
    if not name:
        ns, name = namespace, agent_name
    if not name:
        return UNATTRIBUTED
    return f"{(ns or '').replace('-', '_')}__NS__{name.replace('-', '_')}"


def v1_rows(rows):
    """V1_EVENTS_SELECT rows -> (created_at, agent_id, session_id, data) like 0.10's."""
    agents: dict[str, str] = {}
    for created_at, session_id, session_data, agent_name, namespace, data in rows:
        agent = agents.get(session_id)
        if agent is None:
            agent = agents[session_id] = v1_agent_id(session_data, agent_name, namespace)
        yield created_at, agent, session_id, data


def _part_type(part: dict) -> str | None:
    meta = part.get("metadata") or {}
    for key in PART_TYPE_KEYS:
        if isinstance(meta.get(key), str):
            return meta[key]
    return None


def _gate(response) -> str | None:
    error = response.get("error") if isinstance(response, dict) else None
    if isinstance(error, str):
        for marker, gate in GATE_MARKERS:
            if error.endswith(marker):
                return gate
    return None


def _confirm_record(approval_id, call_id, tool, args) -> dict:
    """An approval request, as 0.10 recorded it: a call of CONFIRM_TOOL whose args
    name the original call. for_call/for_tool repeat that outside the (redacted)
    args, so redaction can never break the per-call match."""
    call_id = call_id if isinstance(call_id, str) else ""
    tool = tool if isinstance(tool, str) else ""
    return {
        "kind": "call", "tool": CONFIRM_TOOL,
        "args": redact_args({"originalFunctionCall": {
            "id": call_id, "name": tool, "args": args if isinstance(args, dict) else {}}}),
        "call_id": approval_id, "for_call": call_id, "for_tool": tool,
    }


def _decision_record(approval_id, approved: bool) -> dict:
    # tool and call_id are filled in from the matching request (_resolve_approvals).
    return {"kind": "approval", "decision": "approved" if approved else "rejected",
            "tool": "", "call_id": "", "approval_id": approval_id}


def _confirmation_decision(response) -> bool | None:
    """{confirmed: bool}, or ADK web's {response: "<json ToolConfirmation>"}."""
    if not isinstance(response, dict):
        return None
    if isinstance(response.get("response"), str) and len(response) == 1:
        try:
            response = json.loads(response["response"])
        except json.JSONDecodeError:
            return None
    confirmed = response.get("confirmed") if isinstance(response, dict) else None
    return confirmed if isinstance(confirmed, bool) else None


def _tool_part_records(part: dict) -> list[dict]:
    kind, data = _part_type(part), part.get("data")
    if kind not in ("function_call", "function_response") or not isinstance(data, dict):
        return []
    name, call_id = data.get("name"), data.get("id")
    if not isinstance(name, str) or not name:
        return []
    call_id = call_id if isinstance(call_id, str) else ""
    if kind == "function_call":
        if name == CONFIRM_TOOL:
            args = data.get("args") if isinstance(data.get("args"), dict) else {}
            original = args.get("originalFunctionCall") if isinstance(args.get("originalFunctionCall"), dict) else {}
            return [_confirm_record(call_id, original.get("id"), original.get("name"), original.get("args"))]
        return [{"kind": "call", "tool": name, "args": redact_args(data.get("args")), "call_id": call_id}]
    response = data.get("response")
    if name == CONFIRM_TOOL:
        confirmed = _confirmation_decision(response)
        return [] if confirmed is None else [_decision_record(call_id, confirmed)]
    rec = {"kind": "response", "tool": name, "response": summarize_response(response), "call_id": call_id}
    gate = _gate(response)
    if gate:
        rec["gate"] = gate
    return [rec]


def _hitl_records(message: dict) -> list[dict]:
    payload = (message.get("metadata") or {}).get(HITL_EXTENSION)
    if not isinstance(payload, dict):
        return []
    if payload.get("type") == "tool_approval_request":
        nested = payload.get("nested") if isinstance(payload.get("nested"), dict) else {}
        tools = list(payload.get("tools") or []) + list(nested.get("tools") or [])
        return [_confirm_record(t["id"], t.get("call_id"), t.get("name"), t.get("args"))
                for t in tools if isinstance(t, dict) and isinstance(t.get("id"), str) and t["id"]]
    if payload.get("type") == "tool_approval_response":
        return [_decision_record(a["id"], a.get("approved") is True)
                for a in payload.get("approvals") or []
                if isinstance(a, dict) and isinstance(a.get("id"), str) and a["id"]]
    return []


def _usage_tokens(meta: dict) -> int | None:
    for key in USAGE_KEYS:
        usage = meta.get(key)
        if isinstance(usage, dict):
            tokens = usage.get("totalTokenCount", usage.get("total_token_count"))
            if isinstance(tokens, (int, float)) and not isinstance(tokens, bool) and tokens > 0:
                return int(tokens)
    return None


def _v1_messages(event: dict):
    if event.get("message"):
        yield event["message"]
    for key in ("status_update", "task"):
        status = (event.get(key) or {}).get("status") or {}
        if status.get("message"):
            yield status["message"]
    yield from (event.get("task") or {}).get("history") or []


def _v1_artifacts(event: dict):
    artifact = (event.get("artifact_update") or {}).get("artifact")
    if artifact:
        yield artifact
    yield from (event.get("task") or {}).get("artifacts") or []


def _dedupe_key(rec: dict):
    """The same fact reaches several rows: a status message is archived again as a
    history message, a task snapshot repeats its artifacts, a streamed artifact is
    re-sent whole. One record per fact per session."""
    kind = rec["kind"]
    if kind == "call" and rec["tool"] == CONFIRM_TOOL:
        return ("confirm", rec["call_id"]) if rec["call_id"] else None
    if kind == "call":
        return ("call", rec["call_id"]) if rec["call_id"] else None
    if kind == "approval":
        return ("approval", rec["approval_id"])
    if kind == "response" and rec["call_id"]:
        return ("response", rec["call_id"], rec.get("gate") or rec["response"].get("sha256_12"))
    return None


def _resolve_approvals(records: list[dict]) -> None:
    """Point each decision at the call it decided (via its request's approval id)."""
    requests = {(r["session"], r["call_id"]): r for r in records
                if r["kind"] == "call" and r["tool"] == CONFIRM_TOOL}
    for r in records:
        if r["kind"] == "approval":
            req = requests.get((r["session"], r["approval_id"]))
            if req is not None:
                r["tool"], r["call_id"] = req["for_tool"], req["for_call"]


def iter_calls_v1(event_rows) -> list[dict]:
    """One redacted record per fact, from kagent 1.0 rows.

    event_rows: (created_at, agent_id, session_id, protobuf bytes), oldest first
    (see fetch_events_v1). Kinds: `call` (call_id; an approval request is a call of
    CONFIRM_TOOL with for_call/for_tool), `response` (call_id, optional `gate`),
    `approval` (decision approved|rejected, the decided tool and call_id), `usage`,
    and `decode-error` (error class only) for a row that does not decode.
    Same redaction contract as iter_calls: no response body ever leaves here.
    """
    out: list[dict] = []
    seen: dict[tuple, dict] = {}
    for created_at, agent_id, session_id, raw in event_rows:
        base = {"at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
                "agent": agent_id, "session": session_id}
        try:
            event = decode_event_v1(raw)
        except (ProtoError, struct.error) as err:
            out.append({"kind": "decode-error", "error": type(err).__name__, **base})
            continue
        found: list[tuple[dict, tuple | None]] = []
        for message in _v1_messages(event):
            # A decision counts only from the human (a user message); a call, a
            # request or a result only from the agent's side. Nothing a tool returns
            # or a user pastes can forge the other half.
            from_user = message.get("role") == ROLE_USER
            recs = _hitl_records(message)
            for part in message.get("parts") or []:
                recs += _tool_part_records(part)
            found += [(rec, _dedupe_key(rec)) for rec in recs if (rec["kind"] == "approval") == from_user]
        for artifact in _v1_artifacts(event):
            for part in artifact.get("parts") or []:
                found += [(rec, _dedupe_key(rec)) for rec in _tool_part_records(part)
                          if rec["kind"] != "approval"]
            tokens = _usage_tokens(artifact.get("metadata") or {})
            if tokens:
                found.append(({"kind": "usage", "tokens": tokens}, ("usage", artifact.get("artifact_id"))))
        for rec, key in found:
            prior = seen.get((session_id, key)) if key else None
            if prior is not None:
                if rec["kind"] == "usage":
                    prior["tokens"] = rec["tokens"]   # a streamed artifact's last word wins
                continue
            rec.update(base)
            out.append(rec)
            if key:
                seen[(session_id, key)] = rec
    _resolve_approvals(out)
    return out


def gate_evidence(records) -> dict[tuple[str, str], dict]:
    """Per (session, call_id), from kagent 1.0 records: did the approval gate fire
    for this call, what did the human decide, and did the tool actually run.

    0.10 records carry no call ids and never appear here.
    """
    evidence: dict[tuple[str, str], dict] = {}

    def entry(session, call_id):
        return evidence.setdefault((session, call_id),
                                   {"requested": False, "decisions": [], "executed": False})

    for r in records:
        if r["kind"] == "call" and r.get("tool") == CONFIRM_TOOL and r.get("for_call"):
            entry(r["session"], r["for_call"])["requested"] = True
        elif r["kind"] == "approval" and r.get("call_id"):
            e = entry(r["session"], r["call_id"])
            e["requested"] = True
            e["decisions"].append(r["decision"])
        elif r["kind"] == "response" and r.get("call_id"):
            e = entry(r["session"], r["call_id"])
            if r.get("gate"):
                e["requested"] = True
                if r["gate"] == "rejected":
                    e["decisions"].append("rejected")
            else:
                e["executed"] = True
    return evidence


def approval_outcome(evidence: dict | None) -> str:
    """approved | rejected | pending (asked, not answered) | none (never asked)."""
    if not evidence or not evidence["requested"]:
        return "none"
    if "approved" in evidence["decisions"]:
        return "approved"
    if "rejected" in evidence["decisions"]:
        return "rejected"
    return "pending"


# ------------------------------------------------------------------- queries


SOURCES = ("v010", "v1")

# kagent 1.0's events with their session's Agent. LEFT JOINs: a session row can be
# gone (idle-deleted) while its events stay (they hang off a2a_context), and a
# deleted session keeps no prepared revision. Callers add WHERE and ORDER BY.
# Shared with agent-audit-web (records/db.py), which vendors this file.
V1_EVENTS_SELECT = f"""
    SELECT e.created_at, c.context_id::text, s.data, ar.agent_name, rr.namespace, e.data
    FROM {V1_SCHEMA}.session_task_event e
    JOIN {V1_SCHEMA}.a2a_context c ON c.id = e.history_id
    LEFT JOIN {V1_SCHEMA}.session s ON s.history_id = e.history_id
    LEFT JOIN {V1_SCHEMA}.runtime_instance ri ON ri.id = s.id
    LEFT JOIN {V1_SCHEMA}.agent_revision ar ON ar.revision = ri.prepared_revision
    LEFT JOIN {V1_SCHEMA}.runtime_revision rr ON rr.revision = ri.prepared_revision
"""


def fetch_events(conn, since=None, agent=None, source="v010"):
    """(created_at, agent_id, session_id, data) rows, oldest first.

    source v010: kagent 0.10's `event`/`session` in schema public (the archive
    database, `kagent`); data is ADK JSON text. source v1: kagent 1.0's schema
    `kagent` (database `kagent_v1`); data is protobuf bytes, for iter_calls_v1.
    """
    if source == "v1":
        return fetch_events_v1(conn, since=since, agent=agent)
    # The ::casts are required: Postgres cannot infer the type of a NULL bind
    # parameter and errors with "could not determine data type of parameter $1".
    sql = """
        SELECT e.created_at, s.agent_id, s.id, e.data
        FROM event e JOIN session s ON s.id = e.session_id
        WHERE (%(since)s::timestamptz IS NULL OR e.created_at >= %(since)s::timestamptz)
          AND (%(agent)s::text IS NULL OR s.agent_id ILIKE %(agent)s::text)
        ORDER BY e.created_at
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"since": since, "agent": f"%{agent}%" if agent else None})
        return cur.fetchall()


def fetch_events_v1(conn, since=None, agent=None):
    sql = V1_EVENTS_SELECT + """
        WHERE (%(since)s::timestamptz IS NULL OR e.created_at >= %(since)s::timestamptz)
        ORDER BY e.created_at, e.sequence
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"since": since})
        rows = list(v1_rows(cur.fetchall()))
    if agent:
        # The Agent is resolved from protobuf above, so the filter runs here.
        needle = agent.lower()
        rows = [r for r in rows if needle in r[1].lower()]
    return rows


def extract(rows, source: str) -> list[dict]:
    return iter_calls_v1(rows) if source == "v1" else list(iter_calls(rows))


def report_ungated(records, gated: set[str]) -> list[dict]:
    """Gated tools invoked without an approval.

    This is the k8s_agent finding, generalised. A `write`/`destructive` tool ran and
    nobody was asked — either requireApproval was missing, or it was silently
    stripped (which is exactly what Argo was doing for days).

    0.10 records (no call ids): the call's SESSION raised no approval request.
    kagent 1.0 records carry call ids, so each call is judged on its own: it is
    ungated when the gate never fired for it, or when it ran without an approved
    decision. A request still waiting for an answer is not a finding (the tool has
    not run). Session-level matching would let one approved delete excuse an
    unapproved patch in the same conversation.
    """
    approvals_by_session: set[str] = {
        r["session"] for r in records
        if r["kind"] == "call" and r["tool"] == CONFIRM_TOOL
    }
    evidence = gate_evidence(records)
    out = []
    for r in records:
        if r["kind"] != "call" or r["tool"] not in gated:
            continue
        if r.get("call_id"):
            e = evidence.get((r["session"], r["call_id"]))
            if e and e["requested"] and (not e["executed"] or "approved" in e["decisions"]):
                continue
        elif r["session"] in approvals_by_session:
            continue
        out.append(r)
    return out


# ------------------------------------------------------------ acknowledged


def _instant(value) -> datetime:
    """An ISO 8601 datetime; a naive one is UTC (kagent's created_at is UTC)."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_acknowledged(path: Path, required: bool = False,
                      now: datetime | None = None) -> list[dict]:
    """Findings that were triaged and closed, from the acknowledged file.

    Each entry names a session, its agent, and `through`: the instant of the last
    call being acknowledged. Every field is required and `through` must carry an
    explicit offset, because a malformed file reaching the CronJob would either
    crash it (no summary line, so the alert goes quiet) or hide real findings.
    `through` must also be in the past: a mistyped future instant would silently
    acknowledge every later call in that session, which is what must still alert.
    """
    now = now or datetime.now(timezone.utc)
    import yaml  # local import: only needed for --ungated/--summary

    if not path.exists():
        if required:
            raise FileNotFoundError(f"acknowledged file not found: {path}")
        return []
    doc = yaml.safe_load(path.read_text()) or {}
    acks, seen = [], set()
    for i, entry in enumerate(doc.get("acknowledged") or []):
        where = f"{path}: acknowledged[{i}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where}: must be a mapping")
        for field in ("session", "agent", "through", "reason"):
            if not str(entry.get(field) or "").strip():
                raise ValueError(f"{where}: missing {field}")
        through = str(entry["through"])
        try:
            parsed = datetime.fromisoformat(through)
        except ValueError:
            raise ValueError(f"{where}: through is not an ISO 8601 datetime: {through!r}")
        if parsed.tzinfo is None:
            raise ValueError(f"{where}: through needs an explicit offset: {through!r}")
        if parsed > now:
            raise ValueError(f"{where}: through is in the future: {through!r}")
        if entry["session"] in seen:
            raise ValueError(f"{where}: duplicate session {entry['session']}")
        seen.add(entry["session"])
        acks.append({**entry, "through": parsed})
    return acks


def partition_acknowledged(findings: list[dict], acks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split findings into (open, acknowledged).

    A finding is acknowledged only if its session is listed, its agent matches, and
    it happened at or before that entry's `through`. A later call in the same
    session (kagent sessions can be resumed) stays open, so it still alerts.

    An UNATTRIBUTED finding matches on session alone: kagent 1.0's idle sweeper
    deletes the session row that named the agent but keeps its events, and an
    acknowledged finding must not start alerting again a week later because of it.
    Session ids are UUIDs, so the session still pins it.
    """
    by_session = {a["session"]: a for a in acks}
    open_, acked = [], []
    for f in findings:
        ack = by_session.get(f["session"])
        covered = (ack is not None and f["agent"] in (ack["agent"], UNATTRIBUTED)
                   and _instant(f["at"]) <= ack["through"])
        (acked if covered else open_).append(f)
    return open_, acked


def report_cost(records) -> dict[str, int]:
    tokens: dict[str, int] = defaultdict(int)
    for r in records:
        if r["kind"] == "usage":
            tokens[r["agent"]] += r["tokens"]
    return dict(tokens)


def export_jsonl(records) -> str:
    """The durable-export payload: one redacted record per line (JSONL).

    Every record here already went through iter_calls, so it is redacted at
    EXTRACTION — no response bodies, arguments pattern-redacted. That is what makes
    it safe to write to long-term S3 storage: the durable audit copy is the redacted
    copy, never a raw dump. See the O4 design and scripts/agent-audit.py's redaction
    contract.

    JSONL (not a JSON array) so an append is a concatenation and a partial write
    truncates cleanly at a line boundary rather than corrupting the whole file.
    """
    return "\n".join(json.dumps(r, sort_keys=True) for r in records)


def extraction_health(rows: list, records: list[dict]) -> dict:
    """What the extraction saw, for the summary: a broken read must not look clean.

    A row that does not decode is counted. And because --summary scans ALL history,
    rows with no recognisable tool call at all mean the extractor no longer matches
    what kagent writes (a new event shape after an alpha bump), not a quiet week.
    """
    return {
        "events": len(rows),
        "tool_calls": sum(r["kind"] == "call" and r["tool"] != CONFIRM_TOOL for r in records),
        "decode_errors": sum(r["kind"] == "decode-error" for r in records),
    }


def health_problems(health: dict | None) -> list[str]:
    if not health:
        return []
    problems = []
    if health["decode_errors"]:
        problems.append(f"{health['decode_errors']} event(s) could not be decoded")
    if health["events"] and not health["tool_calls"]:
        problems.append(f"no tool call recognised in {health['events']} event(s): "
                        "the extraction may not match this kagent version")
    return problems


def summarize_findings(findings: list[dict], acknowledged: int = 0, *,
                       source: str | None = None, health: dict | None = None) -> dict:
    """An ARGUMENT-FREE summary, safe to emit to a log sink.

    This is the alerting payload (--summary), and the omission is the whole point.

    The full --ungated output carries tool ARGUMENTS. Those are only BEST-EFFORT
    redacted — a secret can hide in a `command` string in a shape no pattern
    matches. That is acceptable for a human running the tool against the read-only
    DB. It is NOT acceptable for a payload that lands in Loki and is read through
    Grafana, because that widens who can see it and how long it persists (30d).

    So the alert says WHAT and HOW MANY and WHO — never WITH WHAT. To see the
    arguments you must run the tool yourself, against the database, behind the
    SELECT-only credential. The blast radius of the alert is bounded by construction
    rather than by trusting the redactor.
    """
    by_agent_tool: dict[tuple[str, str], int] = defaultdict(int)
    for f in findings:
        by_agent_tool[(f["agent"], f["tool"])] += 1
    problems = health_problems(health)

    summary = {
        "check": "agent-audit-ungated",
        "severity": "warning" if findings or problems else "ok",
        "ungated_invocations": len(findings),
        # Triaged findings left out of `findings` (see ACKNOWLEDGED). Counted, not
        # listed, so the summary line in Loki still shows that they exist. The
        # Grafana alert itself only counts `severity: warning` lines.
        "acknowledged_invocations": acknowledged,
        "findings": sorted(
            (
                {"agent": agent, "tool": tool, "count": n}
                for (agent, tool), n in by_agent_tool.items()
            ),
            key=lambda d: (-d["count"], d["agent"], d["tool"]),
        ),
        "detail": (
            "Arguments are deliberately omitted. Run "
            "`agent-audit.py --ungated` against the database to see them."
        ),
    }
    if source:
        summary["source"] = source
    if health:
        # Counts only. `problems` makes severity a warning on its own: an audit that
        # could not read the record must not report "ok".
        summary.update(events_scanned=health["events"], tool_calls_seen=health["tool_calls"],
                       decode_errors=health["decode_errors"], problems=problems)
    return summary


# ---------------------------------------------------------------------- cli


def parse_since(s: str | None):
    if not s:
        return None
    m = re.fullmatch(r"(\d+)([dhm])", s)
    if not m:
        raise SystemExit(f"--since must look like 7d / 24h / 30m, got {s!r}")
    n, unit = int(m.group(1)), m.group(2)
    delta = {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]
    return datetime.now(timezone.utc) - delta


def connect():
    try:
        import psycopg
    except ImportError:
        raise SystemExit("pip install 'psycopg[binary]'")
    dsn = os.environ.get("AGENT_AUDIT_DSN")
    if not dsn:
        raise SystemExit(
            "AGENT_AUDIT_DSN is not set.\n"
            "Use the SELECT-only audit role (never kagent's read/write credential):\n"
            "  kubectl port-forward -n postgresql svc/postgresql 5432:5432 &\n"
            "  U=$(kubectl get secret kagent-audit-credentials -n postgresql -o jsonpath='{.data.audit-user}' | base64 -d)\n"
            "  P=$(kubectl get secret kagent-audit-credentials -n postgresql -o jsonpath='{.data.audit-password}' | base64 -d)\n"
            "  # kagent 1.0 (--source v1, the default): key audit-database-v1 (kagent_v1)\n"
            "  # the 0.10 archive (--source v010):      key audit-database    (kagent)\n"
            "  D=$(kubectl get secret kagent-audit-credentials -n postgresql -o jsonpath='{.data.audit-database-v1}' | base64 -d)\n"
            "  export AGENT_AUDIT_DSN=\"postgresql://$U:$P@127.0.0.1:5432/$D\""
        )
    return psycopg.connect(dsn)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=SOURCES, default="v1",
                    help="v1 (default): kagent 1.0, database kagent_v1, schema kagent. "
                         "v010: the kagent 0.10 archive, database kagent, schema public "
                         "(read-only since the 1.0 cutover). Point AGENT_AUDIT_DSN at "
                         "the matching database.")
    ap.add_argument("--since", help="7d / 24h / 30m")
    ap.add_argument("--agent", help="substring match on agent_id")
    ap.add_argument("--ungated", action="store_true",
                    help="gated tools invoked without an approval (v010: no approval "
                         "request in the session; v1: per call, see report_ungated)")
    ap.add_argument("--summary", action="store_true",
                    help="ARGUMENT-FREE JSON summary of --ungated, safe for a log "
                         "sink. This is what the CronJob emits: counts and names, "
                         "never arguments.")
    ap.add_argument("--cost", action="store_true", help="per-agent token rollup")
    ap.add_argument("--export", action="store_true",
                    help="emit the full REDACTED record as JSONL (for the durable "
                         "S3 export). No response bodies; arguments pattern-redacted.")
    ap.add_argument("--format", choices=["table", "json"], default="table")
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--taxonomy", type=Path,
                    help="path to the capability taxonomy ConfigMap manifest "
                         "(the in-cluster CronJob mounts it; defaults to the repo copy)")
    ap.add_argument("--acknowledged", type=Path,
                    help="triaged findings to leave out of --summary and mark in "
                         f"--ungated (the CronJob mounts it; defaults to {ACKNOWLEDGED} "
                         "in the repo, and a given path must exist)")
    args = ap.parse_args(argv)

    with connect() as conn:
        rows = fetch_events(conn, since=parse_since(args.since), agent=args.agent,
                            source=args.source)
    records = extract(rows, args.source)
    # v010 keeps its historical behaviour (undecodable rows are skipped); v1 counts
    # what it could not read and says so.
    health = extraction_health(rows, records) if args.source == "v1" else None

    if args.export:
        # All records — call, response-summary, usage (v1 also approval and
        # decode-error) — already redacted.
        print(export_jsonl(records))
        return 0

    if args.summary or args.ungated:
        acks = load_acknowledged(args.acknowledged or args.repo_root / ACKNOWLEDGED,
                                 required=args.acknowledged is not None)

    if args.summary:
        gated = load_gated_tools(args.repo_root, args.taxonomy)
        open_, acked = partition_acknowledged(report_ungated(records, gated), acks)
        summary = summarize_findings(open_, acknowledged=len(acked), source=args.source,
                                     health=health)
        print(json.dumps(summary))
        return 1 if summary["severity"] != "ok" else 0

    if args.cost:
        tokens = report_cost(records)
        if args.format == "json":
            print(json.dumps(tokens, indent=2))
        else:
            print(f"{'agent':<44} {'tokens':>12}")
            for a, t in sorted(tokens.items(), key=lambda kv: -kv[1]):
                print(f"{a:<44} {t:>12,}")
            print(f"{'TOTAL':<44} {sum(tokens.values()):>12,}")
        return 0

    if args.ungated:
        gated = load_gated_tools(args.repo_root, args.taxonomy)
        findings = report_ungated(records, gated)
        # The investigation view shows everything, acknowledged or not, and marks
        # which is which, so a new finding stands out among the triaged ones.
        open_, acked = partition_acknowledged(findings, acks)
        acked_ids = {id(f) for f in acked}
        evidence = gate_evidence(records)
        for f in findings:
            f["acknowledged"] = id(f) in acked_ids
            if f.get("call_id"):
                # none = the gate never fired; pending/rejected = it ran anyway.
                f["approval"] = approval_outcome(evidence.get((f["session"], f["call_id"])))
        for problem in health_problems(health):
            print(f"WARNING: {problem}", file=sys.stderr)
        if args.format == "json":
            print(json.dumps(findings, indent=2))
        else:
            if not findings:
                print("no ungated invocations of write/destructive tools ✓")
                return 1 if health_problems(health) else 0
            print(f"{'when':<22} {'agent':<38} {'tool':<26} {'ack':<4} {'gate':<8} args")
            for r in findings:
                print(f"{r['at'][:19]:<22} {r['agent']:<38} {r['tool']:<26} "
                      f"{'yes' if r['acknowledged'] else '':<4} {r.get('approval', ''):<8} "
                      f"{json.dumps(r['args'])[:60]}")
            print(f"\n{len(findings)} gated tool invocation(s) without an approval "
                  f"({len(acked)} acknowledged, {len(open_)} open).")
        # An open finding is a failure: this is the requireApproval-stripping incident.
        return 1 if open_ or health_problems(health) else 0

    calls = [r for r in records if r["kind"] == "call"]
    if args.format == "json":
        print(json.dumps(calls, indent=2))
    else:
        print(f"{'when':<22} {'agent':<38} {'tool':<26} args")
        for r in calls:
            print(f"{r['at'][:19]:<22} {r['agent']:<38} {r['tool']:<26} "
                  f"{json.dumps(r['args'])[:60]}")
        print(f"\n{len(calls)} tool invocation(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
