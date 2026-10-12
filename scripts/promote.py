#!/usr/bin/env python3
"""Promotion: merge the one declared class of pull request without a human (ADP path 5).

    scripts/promote.py --repo arigsela/kubernetes --pr 123          # decide and, if auto, merge
    scripts/promote.py --repo arigsela/kubernetes --pr 123 --dry-run

Reads .github/promotion-policy.yaml at the PR's BASE commit (a PR cannot loosen its own
promotion), decides `auto` or `owner` with reasons, and for `auto` merges by rebase pinned to
the head SHA it evaluated, then leaves one sticky comment. For `owner` it logs and exits 0:
the triage comment already tells the human what to read. Exit 0 on a decision, 1 on an error.

Token: GITHUB_TOKEN (the promotion GitHub App's installation token in the workflow). The
policy is YAML but this script must run in the workflow with no pip: it parses the small
subset the policy uses (see _parse_policy_yaml). pyyaml is used only in the tests.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

API = "https://api.github.com"
COMMENT_MARKER = "<!-- promotion -->"
OK_CONCLUSIONS = {"success", "skipped", "neutral"}


class PolicyError(ValueError):
    pass


# ------------------------------------------------------------------ the policy

def _parse_policy_yaml(text: str) -> dict:
    """A two-level YAML subset: `key:`, `  key: value`, `  key: [a, b]`, `    - item`, `#` comments.
    Enough for promotion-policy.yaml, and nothing else; a richer file must fail loudly."""
    doc: dict = {}
    section: dict | None = None
    list_key: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        if indent == 0:
            key, _, value = body.partition(":")
            value = value.strip()
            if value:
                doc[key] = _scalar(value)
                section = None
            else:
                section = doc.setdefault(key, {})
            list_key = None
        elif body.startswith("- "):
            if section is None or list_key is None:
                raise PolicyError(f"list item outside a list: {raw!r}")
            section[list_key].append(_scalar(body[2:].strip()))
        else:
            if section is None:
                raise PolicyError(f"indented key outside a section: {raw!r}")
            key, _, value = body.partition(":")
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                section[key] = [_scalar(v.strip()) for v in value[1:-1].split(",") if v.strip()]
                list_key = None
            elif value:
                section[key] = _scalar(value)
                list_key = None
            else:
                section[key] = []
                list_key = key
    return doc


def _scalar(value: str):
    if value and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    if value.isdigit():
        return int(value)
    return value


def load_policy(text: str) -> dict:
    policy = _parse_policy_yaml(text)
    if policy.get("version") != 1:
        raise PolicyError("version must be 1")
    auto = policy.get("auto")
    if not isinstance(auto, dict):
        raise PolicyError("auto section is required")
    for key in ("authors", "require_labels", "deny_prefixes"):
        if not isinstance(auto.get(key), list) or not auto[key]:
            raise PolicyError(f"auto.{key} must be a non-empty list")
    if auto.get("merge_method") != "rebase":
        raise PolicyError("auto.merge_method must be rebase (the main ruleset allows nothing else)")
    if not isinstance(auto.get("max_changed_files"), int) or auto["max_changed_files"] < 1:
        raise PolicyError("auto.max_changed_files must be a positive integer")
    policy.setdefault("owner", {})
    return policy
# ------------------------------------------------------------------ the decision

@dataclass
class Decision:
    klass: str                       # "auto" or "owner"
    reasons: list[str] = field(default_factory=list)
    head_sha: str = ""


def decide(pr: dict, files: list[dict], check_runs: list[dict], policy: dict) -> Decision:
    """`auto` only when every condition holds; otherwise `owner` with every failed one listed."""
    auto = policy["auto"]
    reasons: list[str] = []
    head_sha = ((pr.get("head") or {}).get("sha")) or ""
    if pr.get("state") != "open" or pr.get("merged"):
        reasons.append("not open")
    if pr.get("draft"):
        reasons.append("draft")
    if pr.get("mergeable") is not True:
        reasons.append("GitHub does not report it mergeable")
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    base_repo = ((pr.get("base") or {}).get("repo") or {}).get("full_name")
    if not head_repo or head_repo != base_repo:
        reasons.append("head is a fork")
    author = (pr.get("user") or {}).get("login")
    if author not in auto["authors"]:
        reasons.append(f"author {author!r} is not in auto.authors")
    labels = {lab.get("name") for lab in (pr.get("labels") or [])}
    for need in auto["require_labels"]:
        if need not in labels:
            reasons.append(f"label {need} missing")
    names = [f.get("filename") or "" for f in files]
    if len(names) > auto["max_changed_files"]:
        reasons.append(f"{len(names)} changed files > max {auto['max_changed_files']}")
    for name in names:
        hit = next((p for p in auto["deny_prefixes"] if name.startswith(p)), None)
        if hit:
            reasons.append(f"{name} is under deny prefix {hit}")
    if not check_runs:
        reasons.append("no check runs on the head commit")
    pending = [c.get("name") for c in check_runs if c.get("status") != "completed"]
    if pending:
        reasons.append("checks pending")
    bad = [c.get("name") for c in check_runs
           if c.get("status") == "completed" and c.get("conclusion") not in OK_CONCLUSIONS]
    if bad:
        reasons.append(f"checks not green: {', '.join(sorted(str(b) for b in bad))}")
    return Decision("auto" if not reasons else "owner", reasons, head_sha)
# ------------------------------------------------------------------ github

def http_transport(method: str, url: str, headers: dict, body: bytes | None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


class GitHub:
    def __init__(self, repo: str, token: str, transport=None):
        self.repo, self.token = repo, token
        self.transport = transport or http_transport

    def _call(self, method: str, path: str, body: dict | None = None, accept: str = "application/vnd.github+json"):
        headers = {"Authorization": f"Bearer {self.token}", "Accept": accept,
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "promote.py"}
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        status, raw = self.transport(method, API + path, headers, data)
        if status >= 400:
            raise RuntimeError(f"GitHub {method} {path} -> {status}: {raw[:300]!r}")
        return raw

    def get(self, path: str):
        return json.loads(self._call("GET", path) or b"null")

    def get_text(self, path: str, accept: str = "application/vnd.github.raw+json") -> str:
        return self._call("GET", path, accept=accept).decode()

    def put(self, path: str, body: dict):
        return json.loads(self._call("PUT", path, body) or b"null")

    def post(self, path: str, body: dict):
        return json.loads(self._call("POST", path, body) or b"null")

    def patch(self, path: str, body: dict):
        return json.loads(self._call("PATCH", path, body) or b"null")


def policy_at(gh: GitHub, ref: str) -> dict:
    """The policy as of `ref` (the PR's base SHA): a PR cannot loosen its own promotion."""
    text = gh.get_text(f"/repos/{gh.repo}/contents/.github/promotion-policy.yaml?ref={ref}")
    return load_policy(text)


def merge(gh: GitHub, number: int, head_sha: str, method: str) -> dict:
    """Merge pinned to the SHA that was evaluated; a push in between makes GitHub refuse (409)."""
    return gh.put(f"/repos/{gh.repo}/pulls/{number}/merge", {"merge_method": method, "sha": head_sha})


def upsert_comment(gh: GitHub, number: int, body: str) -> None:
    existing = gh.get(f"/repos/{gh.repo}/issues/{number}/comments?per_page=100")
    mine = next((c for c in existing if str(c.get("body", "")).startswith(COMMENT_MARKER)), None)
    if mine:
        gh.patch(f"/repos/{gh.repo}/issues/comments/{mine['id']}", {"body": body})
    else:
        gh.post(f"/repos/{gh.repo}/issues/{number}/comments", {"body": body})


def log(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True), flush=True)


def run(repo: str, number: int, token: str, *, dry_run: bool = False, transport=None) -> int:
    gh = GitHub(repo, token, transport)
    pr = gh.get(f"/repos/{repo}/pulls/{number}")
    if pr.get("merged"):
        log({"event": "promotion", "pr": number, "class": "noop", "reasons": ["already merged"]})
        return 0
    head_sha = pr["head"]["sha"]
    base_sha = pr["base"]["sha"]
    try:
        policy = policy_at(gh, base_sha)
    except (PolicyError, RuntimeError) as err:
        log({"event": "promotion", "pr": number, "class": "owner", "reasons": [f"policy at base unreadable: {err}"]})
        return 0
    files = gh.get(f"/repos/{repo}/pulls/{number}/files?per_page=100")
    runs = gh.get(f"/repos/{repo}/commits/{head_sha}/check-runs?per_page=100").get("check_runs") or []
    decision = decide(pr, files, runs, policy)
    log({"event": "promotion", "pr": number, "class": decision.klass, "reasons": decision.reasons,
         "head": head_sha, "policy_at": base_sha, "dry_run": dry_run})
    if decision.klass != "auto" or dry_run:
        return 0
    result = merge(gh, number, decision.head_sha, policy["auto"]["merge_method"])
    checks_line = ", ".join(sorted(str(r.get("name")) for r in runs))
    upsert_comment(gh, number, (
        f"{COMMENT_MARKER}\n### promotion: auto\n"
        f"Merged by the promotion policy ({policy['auto']['merge_method']}, head `{head_sha[:12]}`), "
        f"policy read at base `{base_sha[:12]}`.\n"
        f"**Why:** author in `auto.authors`, label(s) {', '.join(policy['auto']['require_labels'])} present, "
        f"{len(files)} file(s) outside every deny prefix, every check green.\n"
        f"<details><summary>Checks</summary>\n\n{checks_line}\n</details>\n"
        f"<sub>scripts/promote.py · .github/promotion-policy.yaml</sub>"))
    log({"event": "promotion", "pr": number, "merged": bool(result.get("merged")), "sha": result.get("sha")})
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--dry-run", action="store_true", help="decide and log, never merge or comment")
    args = ap.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("promote: GITHUB_TOKEN is required", file=sys.stderr)
        return 1
    try:
        return run(args.repo, args.pr, token, dry_run=args.dry_run)
    except RuntimeError as err:
        print(f"promote: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
