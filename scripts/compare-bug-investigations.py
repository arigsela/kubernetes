#!/usr/bin/env python3
"""Compare kagent's and the n8n POC's investigations of the same Taskboard issues (§T.152).

    scripts/compare-bug-investigations.py --repo arigsela/taskboard --issues 51 52 53 [--json out.json]

Reads each issue's comments and prints one markdown row per issue and system: kagent's comment
(`<!-- bug-investigator mode=kagent -->`) and the n8n POC's (`mode=n8nagent`). Columns: verdict,
confidence, verified/total evidence links, the change and code the cause points at, turns (tool
calls), duration, and for n8n the kagent fix branches its agent read while it ran ("contaminated").
Every other comment (the GitHub Action's mode=full/n8n, fix and alert comments) is ignored.
Read-only; the token comes from GH_TOKEN or `gh auth token`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

API = "https://api.github.com"
SYSTEMS = ("kagent", "n8nagent")
MARKER_RE = re.compile(r"<!-- bug-investigator mode=(kagent|n8nagent) -->")
VERDICTS = {"Likely cause found": "likely_cause", "Not a bug: works as documented": "not_a_bug",
            "Needs more information": "needs_info", "Unclear": "unclear"}
HEAD_RE = re.compile(r"^\*\*(.+?)\*\* · confidence (.+?) · verified (\d+) of (\d+) evidence links$", re.M)
CHANGE_RE = re.compile(r"^- \*\*Change:\*\* \[(PR #\d+|commit `[0-9a-f]+`)", re.M)
CODE_RE = re.compile(r"^- \*\*Code:\*\* \[`([^`]+)`\]", re.M)
FOOT_RE = re.compile(r"<sub>([^\n]*?)</sub>\s*$")
FLAG_RE = re.compile(r"^⚠️ While it ran, this agent read kagent's fix pull request branch\(es\) (.+?); compare with care\.$", re.M)
COLUMNS = ("issue", "system", "verdict", "confidence", "verified", "change", "code", "turns", "duration", "contaminated")


def parse_comment(body: str) -> dict | None:
    """The fields of a kagent or n8n POC investigation comment; None for any other comment."""
    m = MARKER_RE.search(body or "")
    if not m:
        return None
    mode = m.group(1)
    head = HEAD_RE.search(body)
    change = CHANGE_RE.search(body)
    code = CODE_RE.search(body)
    foot = FOOT_RE.search(body)
    turns = duration = None
    if foot:
        t = re.search(r"(\d+) turns", foot.group(1))
        d = re.search(r"(\d+)m (\d+)s", foot.group(1))
        turns = int(t.group(1)) if t else None
        duration = int(d.group(1)) * 60 + int(d.group(2)) if d else None
    flag = FLAG_RE.search(body)
    return {
        "mode": mode,
        "verdict": VERDICTS.get(head.group(1), head.group(1)) if head else None,
        "confidence": head.group(2) if head else None,
        "verified": int(head.group(3)) if head else None,
        "total": int(head.group(4)) if head else None,
        "change": change.group(1) if change else None,
        "code": code.group(1) if code else None,
        "turns": turns,
        "duration_s": duration,
        "contaminated": (flag.group(1).split(", ") if flag else []) if mode == "n8nagent" else None,
    }


def rows_for(issue: int, comments: list[dict]) -> list[dict]:
    """One row per system, kagent first; the latest comment of each counts."""
    found = {}
    for c in comments:
        p = parse_comment(c.get("body") or "")
        if p:
            found[p["mode"]] = p
    rows = []
    for system in SYSTEMS:
        p = found.get(system)
        rows.append({"issue": issue, "system": system, **{k: v for k, v in p.items() if k != "mode"}} if p
                    else {"issue": issue, "system": system, "missing": True})
    return rows


def _cell(row: dict, col: str) -> str:
    if row.get("missing"):
        return {"issue": str(row["issue"]), "system": row["system"], "verdict": "(no comment)"}.get(col, "")
    if col == "verified":
        return f"{row['verified']}/{row['total']}" if row.get("total") is not None else ""
    if col == "duration":
        s = row.get("duration_s")
        return f"{s // 60}m {s % 60:02d}s" if s is not None else ""
    if col == "contaminated":
        c = row.get("contaminated")
        return "n/a" if c is None else (", ".join(c) or "none")
    v = row.get(col)
    return "" if v is None else str(v).replace("|", "\\|")


def table(rows: list[dict]) -> str:
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    lines += ["| " + " | ".join(_cell(r, c) for c in COLUMNS) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def token() -> str:
    if os.environ.get("GH_TOKEN"):
        return os.environ["GH_TOKEN"]
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()


def comments(repo: str, issue: int, tok: str) -> list[dict]:
    out, page = [], 1
    while True:
        req = urllib.request.Request(f"{API}/repos/{repo}/issues/{issue}/comments?per_page=100&page={page}",
                                     headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            batch = json.load(r)
        out += batch
        if len(batch) < 100:
            return out
        page += 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="arigsela/taskboard")
    ap.add_argument("--issues", type=int, nargs="+", required=True)
    ap.add_argument("--json", help="also write the rows to this file")
    args = ap.parse_args(argv)
    tok = token()
    rows = [r for n in args.issues for r in rows_for(n, comments(args.repo, n, tok))]
    sys.stdout.write(table(rows))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(rows, f, indent=2)
            f.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
