#!/usr/bin/env python3
"""Risk triage of the agent action record — rules first, Jev second, human third.

    ./scripts/agent-audit.py --export --since 25h > /tmp/record.jsonl
    ./scripts/agent-audit-risk.py --records /tmp/record.jsonl --out /tmp/risk.jsonl
    ./scripts/agent-audit-risk.py --records /tmp/record.jsonl --dry-run --show-state

WHAT IT ADDS
------------
agent-audit.py knows one finding: a gated tool invoked with no approval request.
That is deterministic and stays where it is. This script asks the softer question
about every SESSION: how much human attention does it deserve? A read-only session
that listed pods needs none. A session that read a Secret, patched three namespaces
and ran a shell command in vault-0 needs a person, even if every call was approved.

HOW IT DECIDES
--------------
1. Rules. A session with no write/destructive call, no argument the redactor fired
   on, and no Secret read is `low`. No API call. Most sessions end here.
2. Jev (TypeSafe System One, the same classifier pr-triage uses). One request per
   candidate session: a three-way `attention` choice plus four yes/no flags, with
   calibrated probabilities and a confidence. Thresholds below.
3. A human. Low confidence is `review`, not a guess. There is deliberately no
   Claude tier: the subject is agent behaviour, and an LLM grading an LLM is weaker
   evidence than a person running agent-audit.py --ungated behind the SELECT-only
   credential.

WHAT LEAVES THE CLUSTER — the egress decision (2026-10-06)
-----------------------------------------------------------
The input is the REDACTED export: the same bytes `agent-audit.py --export` writes
to S3 and agent-audit-web renders. Response bodies were never extracted. Argument
values went through redact_args() (pattern + shape + key-name redaction, best
effort). The operator reviewed the exported arguments for the sessions this
script would send and accepted sending them to TypeSafe; TypeSafe is one more
processor of a record that already leaves the cluster. If that decision changes,
pass --argument-free: the state then carries tool names, classes and counts only.

WHAT IT EMITS
-------------
- `--out` JSONL: one `kind: risk` record per session (contract in risk_record()).
  Uploaded next to the daily export; agent-audit-web reads it (follow-up).
- stdout: ONE argument-free summary line — verdict counts, the `high` sessions by
  agent/session/flag names, token cost. Safe for Loki; the Grafana rule reads it.
- exit 1 when any session is `high`. A failed Job is the signal, as with --ungated.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml

CONFIRM_TOOL = "adk_request_confirmation"
SECRET_READ_TOOLS = {"k8s_get_resources", "k8s_get_resource_yaml", "k8s_describe_resource"}
REDACTED = "<REDACTED>"

# ---------------------------------------------------------------- loading


def load_records(lines: Iterable[str]) -> tuple[list[dict], int]:
    """Parse export JSONL. Returns (records, skipped_line_count)."""
    out, skipped = [], 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(doc, dict):
            out.append(doc)
        else:
            skipped += 1
    return out, skipped


def group_sessions(records: list[dict]) -> dict[str, list[dict]]:
    """Session id -> call records in `at` order. Only well-formed calls count."""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("kind") != "call" or not isinstance(r.get("args"), dict):
            continue
        if not all(isinstance(r.get(k), str) and r[k] for k in ("tool", "at", "agent", "session")):
            continue
        by[r["session"]].append(r)
    for calls in by.values():
        calls.sort(key=lambda c: c["at"])
    return dict(by)


def load_gated_tools(path: Path) -> dict[str, str]:
    """Tool -> class from the capability taxonomy ConfigMap (the one source)."""
    cm = next(d for d in yaml.safe_load_all(path.read_text())
              if d and d.get("kind") == "ConfigMap")
    out: dict[str, str] = {}
    for cls in ("read", "write", "destructive"):
        for name in json.loads(cm["data"][cls]):
            out[name] = cls
    return out
