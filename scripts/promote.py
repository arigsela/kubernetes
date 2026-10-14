#!/usr/bin/env python3
"""Promotion: merge the declared classes of pull request without a human (ADP path 5).

    scripts/promote.py --repo arigsela/kubernetes --pr 123          # decide and, if auto, merge
    scripts/promote.py --repo arigsela/kubernetes --pr 123 --dry-run

Reads .github/promotion-policy.yaml at the PR's base REF, main (a PR cannot loosen its own
promotion, and a tightening applies to open PRs at once), decides `auto` or `owner` with reasons, and for `auto` merges by rebase pinned to
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
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

API = "https://api.github.com"
COMMENT_MARKER = "<!-- promotion -->"
OK_CONCLUSIONS = {"success", "skipped", "neutral"}


class PolicyError(ValueError):
    pass


# The classes that merge without a human, in evaluation order: policy section -> rule name.
CLASS_SECTIONS = {"auto": "renovate-bump", "auto_docs": "docs-only"}


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
    if auto.get("merge_method") != "rebase":
        raise PolicyError("auto.merge_method must be rebase (the main ruleset allows nothing else)")
    for name in CLASS_SECTIONS:
        section = policy.get(name)
        if section is None and name != "auto":
            continue                                  # the docs class is optional
        if not isinstance(section, dict):
            raise PolicyError(f"{name} must be a section")
        for key in ("authors", "require_labels", "deny_prefixes"):
            if not isinstance(section.get(key), list) or not section[key]:
                raise PolicyError(f"{name}.{key} must be a non-empty list")
        if not isinstance(section.get("max_changed_files"), int) or section["max_changed_files"] < 1:
            raise PolicyError(f"{name}.max_changed_files must be a positive integer")
        if not any(".github/".startswith(d) for d in section["deny_prefixes"]):
            raise PolicyError(f"{name}.deny_prefixes must cover .github/ (the workflows and this policy)")
    policy.setdefault("owner", {})
    return policy
# ------------------------------------------------------------------ the decision

@dataclass
class Decision:
    klass: str                       # "auto" or "owner"
    reasons: list[str] = field(default_factory=list)
    head_sha: str = ""
    rule: str | None = None          # which class admitted an "auto" (renovate-bump / docs-only)


def is_docs(name: str) -> bool:
    """triage's always_skip.paths: `docs/**` or `**/*.md`."""
    return name.endswith(".md") or name.startswith("docs/")


SKIP_LINE = re.compile(r"^[+-]\s*(-\s+)?(image|tag):\s")   # a changed image:/tag: value line
SKIP_MAX_LINES = 4


def skip_shaped(files: list[dict]) -> list[str]:
    """Reasons the diff is NOT triage's deterministic always_skip shape (empty = it is).

    pr-triage labels `skip` deterministically when every file is docs, or only image:/tag:
    value lines changed with at most 4 changed lines in total; its classifier can also say
    `skip`. The class that merges without a human re-derives the deterministic rule here
    so that a model's P(skip) alone never promotes."""
    reasons: list[str] = []
    changed = 0
    for f in files:
        name = f.get("filename") or ""
        if is_docs(name):
            continue
        patch = f.get("patch")
        if not isinstance(patch, str):
            reasons.append(f"{name}: no patch to inspect (binary or too large), not an image-tag bump")
            continue
        lines = [ln for ln in patch.splitlines()
                 if (ln.startswith("+") or ln.startswith("-")) and not ln.startswith(("+++", "---"))]
        changed += len(lines)
        bad = [ln for ln in lines if not SKIP_LINE.match(ln)]
        if bad:
            reasons.append(f"{name}: not an image-tag bump ({len(bad)} changed line(s) are not image:/tag: values)")
    if changed > SKIP_MAX_LINES:
        reasons.append(f"{changed} changed lines > {SKIP_MAX_LINES} for an image-tag bump")
    return reasons


def docs_only(files: list[dict]) -> list[str]:
    """Reasons the diff is NOT documentation only (empty = every file is docs)."""
    return [f"{f.get('filename')}: not docs" for f in files if not is_docs(f.get("filename") or "")]


SHAPES = {"renovate-bump": skip_shaped, "docs-only": docs_only}


# GitHub records itself as the committer of commits made through its API or web UI.
WEB_COMMITTER = "web-flow"


def _commit_reasons(rule: str, section: dict, commits: list[dict] | None) -> list[str]:
    """Every commit on the branch is by a class author (§V.95). The PR author alone is not
    enough: a branch is writable by anyone with Contents write (the implementer App, a
    propose-class agent), and a commit pushed onto an open Renovate or owner branch would
    otherwise ride that branch's class into an unread merge. None = not fetched = owner."""
    if commits is None:
        return ["commit authors not fetched"]
    reasons = []
    for c in commits:
        sha = (c.get("sha") or "?")[:7]
        author = (c.get("author") or {}).get("login")
        committer = (c.get("committer") or {}).get("login")
        if author not in section["authors"]:
            reasons.append(f"commit {sha} by {author!r} is not in {rule} authors")
        elif committer not in section["authors"] and committer != WEB_COMMITTER:
            reasons.append(f"commit {sha} committed by {committer!r} is not in {rule} authors")
    return reasons


def _class_reasons(rule: str, section: dict, pr: dict, files: list[dict],
                   commits: list[dict] | None) -> list[str]:
    """The conditions one class adds on top of the common ones."""
    reasons: list[str] = []
    author = (pr.get("user") or {}).get("login")
    if author not in section["authors"]:
        reasons.append(f"author {author!r} is not in {rule} authors")
    reasons += _commit_reasons(rule, section, commits)
    labels = {lab.get("name") for lab in (pr.get("labels") or [])}
    for need in section["require_labels"]:
        if need not in labels:
            reasons.append(f"label {need} missing")
    names = [f.get("filename") or "" for f in files]
    if len(names) > section["max_changed_files"]:
        reasons.append(f"{len(names)} changed files > max {section['max_changed_files']}")
    for name in names:
        hit = next((p for p in section["deny_prefixes"] if name.startswith(p)), None)
        if hit:
            reasons.append(f"{name} is under deny prefix {hit}")
    reasons += SHAPES[rule](files)
    return reasons


def decide(pr: dict, files: list[dict], check_runs: list[dict], policy: dict,
           statuses: list[dict] | None = None, commits: list[dict] | None = None) -> Decision:
    """`auto` when every common condition holds and at least one class admits the PR;
    otherwise `owner` with every failed condition listed, per class."""
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
    if not check_runs:
        reasons.append("no check runs on the head commit")
    pending = [c.get("name") for c in check_runs if c.get("status") != "completed"]
    if pending:
        reasons.append("checks pending")
    bad = [c.get("name") for c in check_runs
           if c.get("status") == "completed" and c.get("conclusion") not in OK_CONCLUSIONS]
    if bad:
        reasons.append(f"checks not green: {', '.join(sorted(str(b) for b in bad))}")
    # Commit statuses are not check runs: Renovate's stability gate (renovate/stability-days,
    # minimumReleaseAge) and Atlantis report there. Every status must be success.
    for status in statuses or []:
        state = status.get("state")
        if state != "success":
            reasons.append(f"status {status.get('context')} is {state or 'unknown'}"
                           + (" (pending)" if state == "pending" else ""))
    per_class = {rule: _class_reasons(rule, policy[section], pr, files, commits)
                 for section, rule in CLASS_SECTIONS.items() if section in policy}
    admitted = next((rule for rule, why in per_class.items() if not why), None)
    if not reasons and admitted:
        return Decision("auto", [], head_sha, admitted)
    for rule, why in per_class.items():
        reasons += [f"{rule}: {r}" for r in why]
    return Decision("owner", reasons, head_sha)
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
    """The policy as of `ref` (the PR's base ref, main): a PR cannot loosen its own promotion."""
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


MERGEABLE_RETRIES = 5
MERGEABLE_WAIT = 3.0


def run(repo: str, number: int, token: str, *, dry_run: bool = False, transport=None,
        sleep=time.sleep) -> int:
    gh = GitHub(repo, token, transport)
    pr = gh.get(f"/repos/{repo}/pulls/{number}")
    # GitHub computes `mergeable` lazily; the first GET after a push can say null. Ask again
    # a few times rather than deciding `owner` and waiting for the next push.
    for _ in range(MERGEABLE_RETRIES):
        if pr.get("merged") or pr.get("mergeable") is not None:
            break
        sleep(MERGEABLE_WAIT)
        pr = gh.get(f"/repos/{repo}/pulls/{number}")
    if pr.get("merged"):
        log({"event": "promotion", "pr": number, "class": "noop", "reasons": ["already merged"]})
        return 0
    head_sha = pr["head"]["sha"]
    # The policy is read at the base REF (main), which the PR cannot change: un-loosenable by
    # the PR, and a tightening applies to already-open PRs immediately (base.sha would lag
    # until the PR's next rebase).
    base_ref = pr["base"]["ref"]
    try:
        policy = policy_at(gh, base_ref)
    except (PolicyError, RuntimeError) as err:
        log({"event": "promotion", "pr": number, "class": "owner", "reasons": [f"policy at {base_ref} unreadable: {err}"]})
        return 0
    files = gh.get(f"/repos/{repo}/pulls/{number}/files?per_page=100")
    runs = gh.get(f"/repos/{repo}/commits/{head_sha}/check-runs?per_page=100").get("check_runs") or []
    statuses = gh.get(f"/repos/{repo}/commits/{head_sha}/status").get("statuses") or []
    commits = gh.get(f"/repos/{repo}/pulls/{number}/commits?per_page=100")
    decision = decide(pr, files, runs, policy, statuses, commits)
    log({"event": "promotion", "pr": number, "class": decision.klass, "rule": decision.rule,
         "reasons": decision.reasons, "head": head_sha, "policy_at": base_ref, "dry_run": dry_run})
    if decision.klass != "auto" or dry_run:
        return 0
    result = merge(gh, number, decision.head_sha, policy["auto"]["merge_method"])
    checks_line = ", ".join(sorted(str(r.get("name")) for r in runs)
                            + sorted(str(s.get("context")) for s in statuses))
    section = next(s for s, r in CLASS_SECTIONS.items() if r == decision.rule)
    shape = ("every file documentation (`*.md` or `docs/`)" if decision.rule == "docs-only"
             else "shaped like a docs-only or image-tag-bump change")
    upsert_comment(gh, number, (
        f"{COMMENT_MARKER}\n### promotion: auto ({decision.rule})\n"
        f"Merged by the promotion policy ({policy['auto']['merge_method']}, head `{head_sha[:12]}`), "
        f"policy read at `{base_ref}`.\n"
        f"**Why:** author in `{section}.authors`, label(s) {', '.join(policy[section]['require_labels'])} present, "
        f"{len(files)} file(s) outside every deny prefix and {shape}, "
        f"every check and status green.\n"
        f"<details><summary>Checks and statuses</summary>\n\n{checks_line}\n</details>\n"
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
