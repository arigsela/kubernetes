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
# ---------------------------------------------------------------- decide

def policy():
    return pm.load_policy(POLICY_FILE.read_text())


def pr(**over):
    base = {"number": 760, "state": "open", "draft": False, "mergeable": True, "merged": False,
            "user": {"login": "arigsela-renovate[bot]"}, "labels": [{"name": "renovate"}, {"name": "review:skip"}],
            "head": {"sha": "abc123", "repo": {"full_name": "arigsela/kubernetes"}},
            "base": {"sha": "base456", "repo": {"full_name": "arigsela/kubernetes"}}}
    base.update(over)
    return base


def files(*names):
    return [{"filename": n} for n in names]


def checks(*conclusions, pending=0):
    runs = [{"name": f"job-{i}", "status": "completed", "conclusion": c} for i, c in enumerate(conclusions)]
    runs += [{"name": f"pending-{i}", "status": "in_progress", "conclusion": None} for i in range(pending)]
    return runs


def test_renovate_skip_green_is_auto():
    d = pm.decide(pr(), files("base-apps/donetick/deployment.yaml"), checks("success", "skipped"), policy())
    assert d.klass == "auto" and d.reasons == [] and d.head_sha == "abc123"


def test_deny_prefix_wins_over_skip_label():
    d = pm.decide(pr(), files("base-apps/vault/statefulsets.yaml"), checks("success"), policy())
    assert d.klass == "owner" and any("deny" in r for r in d.reasons)
    d = pm.decide(pr(), files("terraform/roots/asela-cluster/ecr.tf"), checks("success"), policy())
    assert d.klass == "owner"


def test_pending_check_is_owner_not_auto():
    d = pm.decide(pr(), files("base-apps/donetick/deployment.yaml"), checks("success", pending=1), policy())
    assert d.klass == "owner" and "checks pending" in d.reasons


def test_failed_or_absent_checks_are_owner():
    assert pm.decide(pr(), files("x.md"), checks("success", "failure"), policy()).klass == "owner"
    assert pm.decide(pr(), files("x.md"), [], policy()).klass == "owner"


@pytest.mark.parametrize("over, reason", [
    ({"user": {"login": "arigsela"}}, "author"),
    ({"labels": [{"name": "renovate"}, {"name": "review:skim"}]}, "label"),
    ({"draft": True}, "draft"),
    ({"state": "closed"}, "not open"),
    ({"mergeable": False}, "mergeable"),
    ({"head": {"sha": "abc123", "repo": {"full_name": "someone/fork"}}}, "fork"),
])
def test_each_condition_is_a_reason(over, reason):
    d = pm.decide(pr(**over), files("x.md"), checks("success"), policy())
    assert d.klass == "owner" and any(reason in r for r in d.reasons), d.reasons


def test_too_many_files_is_owner():
    d = pm.decide(pr(), files("a.md", "b.md", "c.md"), checks("success"), policy())
    assert d.klass == "owner" and any("files" in r for r in d.reasons)
