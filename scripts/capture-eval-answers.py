#!/usr/bin/env python3
"""Capture fresh answers from a kagent agent for the eval corpus.

    ./scripts/capture-eval-answers.py --out /tmp/answers.jsonl
    ./scripts/score-eval.py --answers /tmp/answers.jsonl --judge

Talks A2A v1 JSON-RPC (`SendMessage`) to the agent's endpoint on the kagent 1.0
controller, /agents/<namespace>/<agent>, through kagent-mcp.arigsela.com (IP
allow-listed). From off-network, port-forward and pass the origin:

    kubectl port-forward -n kagent svc/kagent-controller 8083:8083 &
    ./scripts/capture-eval-answers.py --endpoint http://127.0.0.1:8083 --out /tmp/answers.jsonl

The client is scripts/kagent_a2a.py, shared with scripts/ask-hk. One session
per question (no contextId), so answers do not bleed into each other. In the
cluster the eval job presents its projected token (--token-file) and its sessions
belong to system:serviceaccount:kagent:eval-gate; from the laptop the kubelogin
(Dex) token is sent by default and sessions belong to you; --no-token keeps the
X-User-Id path for a port-forward. kagent 1.0 keys memory by (agent, user), so the
`memory` category expects state saved earlier by the SAME identity: seed it with
    scripts/ask-hk --as-serviceaccount kagent/eval-gate "My favorite color is octarine. Please save that."
for the job, or with plain scripts/ask-hk for yourself, or pass
`--category repo-factual --category security-refusal` to leave it out.
The output is NOT committed: an answer can contain anything the agent said,
and the scorer's whole point is to check that it said nothing secret.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import kagent_a2a as a2a  # noqa: E402

CORPUS_DIR = "tests/eval-corpus"
DEFAULT_AGENT = "kagent/homelab-knowledge"
DEFAULT_ENDPOINT = a2a.PUBLIC_BASE + a2a.agent_path(DEFAULT_AGENT)
DEFAULT_USER = "eval-capture"


def load_entries(repo_root: Path) -> list[dict]:
    import yaml  # lazy: the eval-gate job has no pyyaml (it gets the corpus as JSON)
    out = []
    for path in sorted((repo_root / CORPUS_DIR).glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        out.extend(doc.get("entries") or [])
    return out


def extract_text(result: dict) -> str | None:
    """The answer in a SendMessage result: artifact text, else status-message text.

    None for a failed, canceled or rejected task (its status message is the
    error, not an answer), for a task that has not finished, and when the agent
    said nothing in text."""
    return a2a.parse_result(result).answer


def send_message(endpoint: str, question: str, *, transport=None,
                 timeout: float = a2a.DEFAULT_TIMEOUT, user: str | None = DEFAULT_USER,
                 token: str | None = None, token_file: str | None = None) -> dict:
    """One SendMessage in a new session; returns the JSON-RPC result."""
    return a2a.send_message(endpoint, question, user=None if (token or token_file) else user,
                            token=token, token_file=token_file, timeout=timeout, transport=transport)


def capture(entries: list[dict], endpoint: str, *, send=None, timeout: float = a2a.DEFAULT_TIMEOUT,
            token_file: str | None = None, log=None) -> dict[str, str | None]:
    """One new session per question; None where the agent failed or said nothing."""
    send = send or send_message
    out: dict[str, str | None] = {}
    for e in entries:
        try:
            out[e["id"]] = extract_text(send(endpoint, e["question"], timeout=timeout,
                                             token_file=token_file))
        except (RuntimeError, OSError, ValueError) as err:
            if log:
                log({"event": "eval-capture", "id": e["id"], "error": str(err)[:300]})
            out[e["id"]] = None
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path, help="answers.jsonl to write")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                    help="agent URL, or a bare origin to append /agents/%s to "
                         "(default: %%(default)s)" % DEFAULT_AGENT)
    ap.add_argument("--user", default=DEFAULT_USER,
                    help="X-User-Id for --no-token (default: %(default)s)")
    tok = ap.add_mutually_exclusive_group()
    tok.add_argument("--token-file", help="bearer token file (the job's projected token)")
    tok.add_argument("--no-token", action="store_true",
                     help="send only X-User-Id (a port-forward, no mesh in front)")
    ap.add_argument("--timeout", type=float, default=a2a.DEFAULT_TIMEOUT,
                    help="seconds per question (default: %(default)s)")
    ap.add_argument("--category", action="append", help="only these categories (repeatable)")
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args(argv)

    endpoint = a2a.resolve_endpoint(args.endpoint, DEFAULT_AGENT)
    entries = load_entries(args.repo_root)
    if args.category:
        entries = [e for e in entries if e.get("category") in set(args.category)]

    token = None
    if not args.no_token and not args.token_file:
        token = a2a.acquire_token()          # kubelogin, $KAGENT_TOKEN, $KAGENT_TOKEN_FILE

    missing = 0
    with args.out.open("w") as fh:
        for e in entries:
            try:
                answer = extract_text(send_message(endpoint, e["question"], user=args.user,
                                                   token=token, token_file=args.token_file,
                                                   timeout=args.timeout))
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
