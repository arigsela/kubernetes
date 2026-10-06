#!/usr/bin/env python3
"""Capture fresh answers from a kagent agent for the eval corpus.

    kubectl port-forward -n kagent svc/kagent-controller 8083:8083 &
    ./scripts/capture-eval-answers.py --out /tmp/answers.jsonl
    ./scripts/score-eval.py --answers /tmp/answers.jsonl --judge

Talks A2A JSON-RPC (`message/send`) to the agent's endpoint, which the kagent
controller serves at /api/a2a/<namespace>/<agent>/. One session per question, so
answers do not bleed into each other. The `memory` category expects state saved
in an earlier conversation; pass `--category repo-factual --category
security-refusal` to leave it out when that state is not there.

The output is NOT committed: an answer can contain anything the agent said,
and the scorer's whole point is to check that it said nothing secret.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import uuid
from pathlib import Path

import yaml

CORPUS_DIR = "tests/eval-corpus"
DEFAULT_ENDPOINT = "http://127.0.0.1:8083/api/a2a/kagent/homelab-knowledge/"


def load_entries(repo_root: Path) -> list[dict]:
    out = []
    for path in sorted((repo_root / CORPUS_DIR).glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        out.extend(doc.get("entries") or [])
    return out


def _text_parts(parts) -> list[str]:
    return [p["text"] for p in (parts or [])
            if isinstance(p, dict) and p.get("kind") == "text" and isinstance(p.get("text"), str)]


FAILED_STATES = {"failed", "canceled", "rejected"}   # A2A TaskState values that carry an error


def extract_text(result: dict) -> str | None:
    """All text parts of the task's artifacts, else of its status message, else None.

    A failed, canceled or rejected task puts its error text in status.message;
    that is a tooling failure, not an answer, and must not be scored as one."""
    status = result.get("status") or {}
    if str(status.get("state", "")).lower() in FAILED_STATES:
        return None
    texts: list[str] = []
    for art in (result.get("artifacts") or []):
        texts += _text_parts(art.get("parts"))
    if not texts:
        texts = _text_parts(((result.get("status") or {}).get("message") or {}).get("parts"))
    return "\n".join(texts) if texts else None


def _http_post(url: str, payload: bytes, timeout: float) -> dict:
    req = urllib.request.Request(url, data=payload, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def send_message(endpoint: str, question: str, *, transport=None, timeout: float = 120) -> dict:
    rpc_id = str(uuid.uuid4())
    payload = json.dumps({
        "jsonrpc": "2.0", "id": rpc_id, "method": "message/send",
        "params": {"message": {"role": "user", "messageId": str(uuid.uuid4()),
                               "parts": [{"kind": "text", "text": question}]}},
    }).encode()
    body = (transport or _http_post)(endpoint, payload, timeout)
    if body.get("error"):
        raise RuntimeError(f"A2A error: {body['error'].get('message', body['error'])}")
    return body.get("result") or {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path, help="answers.jsonl to write")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--category", action="append", help="only these categories (repeatable)")
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args(argv)

    entries = load_entries(args.repo_root)
    if args.category:
        entries = [e for e in entries if e.get("category") in set(args.category)]

    missing = 0
    with args.out.open("w") as fh:
        for e in entries:
            try:
                answer = extract_text(send_message(args.endpoint, e["question"]))
            except (RuntimeError, OSError, ValueError) as err:
                print(f"  {e['id']}: {err}", file=sys.stderr)
                answer = None
            if answer is None:
                missing += 1
            print(f"  {'ok ' if answer else 'MISSING'} {e['id']}", file=sys.stderr)
            fh.write(json.dumps({"id": e["id"], "answer": answer}) + "\n")
    print(f"wrote {len(entries)} answers to {args.out}"
          + (f" ({missing} missing)" if missing else ""), file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
