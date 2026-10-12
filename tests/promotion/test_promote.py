# tests/promotion/test_promote.py
"""scripts/promote.py: the one class of PR that merges without a human, decided from the base commit."""
from pathlib import Path
import importlib.util
import json
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("promote", REPO / "scripts" / "promote.py")
pm = importlib.util.module_from_spec(_spec)
sys.modules["promote"] = pm   # dataclasses resolve the module by name at class creation
_spec.loader.exec_module(pm)

POLICY_FILE = REPO / ".github" / "promotion-policy.yaml"


def test_repo_policy_loads_and_denies_the_dangerous_prefixes():
    policy = pm.load_policy(POLICY_FILE.read_text())
    assert policy["version"] == 1
    assert policy["auto"]["authors"] == ["arigsela-renovate[bot]"]
    assert policy["auto"]["require_labels"] == ["review:skip"]
    for must in ("terraform/", ".github/", "appsets/", "base-apps/vault", "base-apps/kagent", "base-apps/admission-policies"):
        assert must in policy["auto"]["deny_prefixes"], must
    assert policy["auto"]["merge_method"] == "rebase"
    assert policy["auto"]["max_changed_files"] <= 4


def policy_text(**auto):
    """A block-style policy (the only YAML shape promote.py's parser accepts) with overrides."""
    fields = {"authors": "[b]", "require_labels": "[x]", "deny_prefixes": "[a]",
              "max_changed_files": "1", "merge_method": "rebase"}
    fields.update(auto)
    return "version: 1\nauto:\n" + "".join(f"  {k}: {v}\n" for k, v in fields.items())


@pytest.mark.parametrize("text, message", [
    ("version: 2\nauto:\n  authors: [b]\n", "version"),
    (policy_text(authors="[]"), "authors"),
    (policy_text(require_labels="[]"), "require_labels"),
    (policy_text(merge_method="squash"), "merge_method"),
    (policy_text(max_changed_files="0"), "max_changed_files"),
])
def test_load_policy_rejects_bad_policies(text, message):
    with pytest.raises(pm.PolicyError, match=message):
        pm.load_policy(text)


def test_parser_reads_block_lists_and_ignores_comments():
    text = "version: 1  # the schema\nauto:\n  authors: [b]\n  require_labels: [x]\n  deny_prefixes:\n    - a/   # first\n    - b\n  max_changed_files: 1\n  merge_method: rebase\nowner:\n  approvers: [me]\n"
    policy = pm.load_policy(text)
    assert policy["auto"]["deny_prefixes"] == ["a/", "b"]
    assert policy["owner"]["approvers"] == ["me"]
