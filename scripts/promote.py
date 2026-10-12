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
