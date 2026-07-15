#!/usr/bin/env python3
"""Mine candidate Q&A pairs from an agent's conversation history (Evaluation E1).

    ./scripts/mine-eval-corpus.py --agent homelab_knowledge

Emits, as JSONL, every (question, final-answer) pair an agent actually handled —
the RAW MATERIAL for a golden corpus, not the corpus itself. A human curates the
golden answer (see tests/eval-corpus/*.yaml); this only surfaces the real
questions so the corpus reflects what was actually asked, not what we imagine.

Why not just snapshot the agent's answers as golden: the agent's answer is the
thing being evaluated. Scoring it against itself is circular. So this tool labels
the mined answer `candidate_answer`, never `golden` — a reminder that a step of
human verification stands between this output and the corpus.

WHERE THE DATA COMES FROM
kagent's Postgres `event` table (ADK format). Each event has an `author`
("user" | the agent name | "system") and content.parts[].text. Within a session,
ordered by created_at, a `user` turn is a question and the agent turns until the
next user turn are its answer; the last non-partial agent turn is the final answer.

REDACTION
Question and answer text is free-form and can contain secrets — a user pasting a
token, an agent echoing one. So every text field is passed through the SAME
redactor the audit tool uses (scripts/agent-audit.py). This output is candidate
material for human review, but it is redacted first, on the same "safer, not safe"
footing as everything else that leaves the database.

Read-only: connects as the SELECT-only kagent_audit_ro role, like agent-audit.py.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

# Reuse the audit tool's redaction — one implementation, one set of tests.
_AUDIT = Path(__file__).resolve().parent / "agent-audit.py"
_spec = importlib.util.spec_from_file_location("agent_audit", _AUDIT)
aa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aa)


def _text_of(event_data: dict) -> str:
    parts = (event_data.get("content") or {}).get("parts") or []
    return " ".join(
        p["text"] for p in parts
        if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"].strip()
    ).strip()


def _redact_text(text: str) -> str:
    # redact_value treats a lone string under a neutral key: secret-shaped values
    # (tokens, PEM, high-entropy blobs) are replaced, ordinary prose is kept.
    return aa.redact_value("text", text)


def iter_pairs(rows, agent: str):
    """Yield {question, candidate_answer, session, at} from ordered event rows.

    rows: (created_at, author, session_id, data_json) ordered by (session, created_at)
    """
    cur_session = None
    pending_q = None
    answer_parts: list[str] = []
    q_at = None

    def flush():
        nonlocal pending_q, answer_parts, q_at
        if pending_q and answer_parts:
            yield_val = {
                "question": _redact_text(pending_q),
                "candidate_answer": _redact_text(" ".join(answer_parts)),
                "session": cur_session,
                "at": q_at,
            }
            pending_q, answer_parts, q_at = None, [], None
            return yield_val
        pending_q, answer_parts, q_at = None, [], None
        return None

    out = []
    for created_at, author, session_id, raw in rows:
        if session_id != cur_session:
            v = flush()
            if v:
                out.append(v)
            cur_session = session_id
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        text = _text_of(data)

        if author == "user":
            # a new question closes the previous pair
            v = flush()
            if v:
                out.append(v)
            if text:
                pending_q = text
                q_at = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
        elif author == agent:
            # skip the interstitial "I'll check..." partials; keep substantive text.
            # `partial` marks streaming fragments; the final turn has turn_complete.
            if text and not data.get("partial"):
                answer_parts.append(text)
    v = flush()
    if v:
        out.append(v)
    return out


def fetch_rows(conn, agent: str):
    sql = """
        SELECT e.created_at, s.agent_id, s.id, e.data
        FROM event e JOIN session s ON s.id = e.session_id
        WHERE s.agent_id ILIKE %(agent)s
        ORDER BY s.id, e.created_at
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"agent": f"%{agent}%"})
        rows = cur.fetchall()
    # normalise to (created_at, author, session_id, data); author is derived below
    out = []
    for created_at, agent_id, session_id, data in rows:
        try:
            author = json.loads(data).get("author")
        except (json.JSONDecodeError, TypeError):
            author = None
        out.append((created_at, author, session_id, data))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", default="homelab_knowledge",
                    help="agent_id substring to mine (default: homelab_knowledge)")
    args = ap.parse_args(argv)

    conn = aa.connect()
    with conn:
        rows = fetch_rows(conn, args.agent)
    pairs = iter_pairs(rows, args.agent)

    for p in pairs:
        print(json.dumps(p, sort_keys=True))
    print(f"# {len(pairs)} candidate Q&A pair(s) for {args.agent!r} — "
          f"CURATE before adding to the golden corpus", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
