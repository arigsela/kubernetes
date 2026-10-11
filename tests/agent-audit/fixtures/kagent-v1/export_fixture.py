#!/usr/bin/env python3
"""Snapshot a generated kagent_v1 database into events.json (see audit_fixture_test.go).

    python export_fixture.py <repo root> <sessions.json> <out events.json> <admin DSN>

Rows come through scripts/agent-audit.py's own V1_EVENTS_SELECT, so the fixture is
exactly what the audit reads from kagent 1.0. Needs psycopg; not run by CI.
"""
import base64
import importlib.util
import json
import sys

import psycopg

repo, sessions_path, out_path, dsn = sys.argv[1:5]
spec = importlib.util.spec_from_file_location("agent_audit", f"{repo}/scripts/agent-audit.py")
aa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aa)

with psycopg.connect(dsn) as conn:
    rows = conn.execute(aa.V1_EVENTS_SELECT + " ORDER BY e.created_at, e.sequence").fetchall()


def b64(value):
    return base64.b64encode(bytes(value)).decode() if value is not None else None


doc = {
    "_comment": (
        "kagent v1.0.0-alpha11 rows, as scripts/agent-audit.py V1_EVENTS_SELECT returns them. "
        "Written by kagent's own persistence code (go/core/internal/database, migrations run as "
        "kagent_owner into schema kagent) from events built with the Go runtime's helpers; "
        "generator: tests/agent-audit/fixtures/kagent-v1/audit_fixture_test.go. Synthetic content."
    ),
    "scenarios": json.load(open(sessions_path)),
    "columns": ["created_at", "session_id", "session_data_b64", "agent_name", "namespace", "data_b64"],
    "rows": [[r[0].isoformat(), r[1], b64(r[2]), r[3], r[4], b64(r[5])] for r in rows],
}
with open(out_path, "w") as f:
    json.dump(doc, f, indent=1)
    f.write("\n")
print(f"wrote {len(rows)} rows")
