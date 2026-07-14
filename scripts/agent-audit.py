#!/usr/bin/env python3
"""Agent action record — who called what, with what arguments, was it approved.

kagent already persists all of this; nothing reads it. This is the read side.

    ./scripts/agent-audit.py --ungated          # the query that matters
    ./scripts/agent-audit.py --cost
    ./scripts/agent-audit.py --agent k8s_agent --since 7d

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
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

TAXONOMY = "base-apps/kyverno-policies/agent-capability-taxonomy.yaml"

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


def load_gated_tools(repo_root: Path) -> set[str]:
    """Tools the capability contract says MUST be behind requireApproval.

    Single source of truth: the same taxonomy Kyverno enforces at admission. If a
    tool is write/destructive there, an invocation of it with no approval event is
    a finding here. The two cannot drift.
    """
    import yaml  # local import: only needed for --ungated

    path = repo_root / TAXONOMY
    cm = next(
        d for d in yaml.safe_load_all(path.read_text())
        if d and d.get("kind") == "ConfigMap"
    )
    gated: set[str] = set()
    for classification in ("write", "destructive"):
        gated |= set(json.loads(cm["data"][classification]))
    return gated


# --------------------------------------------------------------- extraction


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
                fc = node.get("function_call")
                if isinstance(fc, dict) and fc.get("name"):
                    found.append({
                        "kind": "call",
                        "tool": fc["name"],
                        "args": redact_args(fc.get("args")),
                    })
                fr = node.get("function_response")
                if isinstance(fr, dict) and fr.get("name"):
                    found.append({
                        "kind": "response",
                        "tool": fr["name"],
                        "response": summarize_response(fr.get("response")),
                    })
                um = node.get("usage_metadata")
                if isinstance(um, dict) and um.get("total_token_count"):
                    found.append({"kind": "usage", "tokens": um["total_token_count"]})
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


# ------------------------------------------------------------------- queries


def fetch_events(conn, since=None, agent=None):
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


def report_ungated(records, gated: set[str]) -> list[dict]:
    """Gated tools invoked in a session that raised NO approval request.

    This is the k8s_agent finding, generalised. A `write`/`destructive` tool ran and
    nobody was asked — either requireApproval was missing, or it was silently
    stripped (which is exactly what Argo was doing for days).
    """
    approvals_by_session: set[str] = {
        r["session"] for r in records
        if r["kind"] == "call" and r["tool"] == CONFIRM_TOOL
    }
    out = []
    for r in records:
        if r["kind"] != "call" or r["tool"] not in gated:
            continue
        if r["session"] in approvals_by_session:
            continue
        out.append(r)
    return out


def report_cost(records) -> dict[str, int]:
    tokens: dict[str, int] = defaultdict(int)
    for r in records:
        if r["kind"] == "usage":
            tokens[r["agent"]] += r["tokens"]
    return dict(tokens)


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
            "  export AGENT_AUDIT_DSN=\"postgresql://$U:$P@127.0.0.1:5432/kagent\""
        )
    return psycopg.connect(dsn)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="7d / 24h / 30m")
    ap.add_argument("--agent", help="substring match on agent_id")
    ap.add_argument("--ungated", action="store_true",
                    help="gated tools invoked with NO approval request in the session")
    ap.add_argument("--cost", action="store_true", help="per-agent token rollup")
    ap.add_argument("--format", choices=["table", "json"], default="table")
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args(argv)

    with connect() as conn:
        rows = fetch_events(conn, since=parse_since(args.since), agent=args.agent)
    records = list(iter_calls(rows))

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
        gated = load_gated_tools(args.repo_root)
        findings = report_ungated(records, gated)
        if args.format == "json":
            print(json.dumps(findings, indent=2))
        else:
            if not findings:
                print("no ungated invocations of write/destructive tools ✓")
                return 0
            print(f"{'when':<22} {'agent':<38} {'tool':<26} args")
            for r in findings:
                print(f"{r['at'][:19]:<22} {r['agent']:<38} {r['tool']:<26} "
                      f"{json.dumps(r['args'])[:60]}")
            print(f"\n{len(findings)} gated tool invocation(s) with NO approval request.")
        # A finding is a failure: this is the requireApproval-stripping incident.
        return 1 if findings else 0

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
