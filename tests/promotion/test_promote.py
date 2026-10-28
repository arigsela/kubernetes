# tests/promotion/test_promote.py
"""scripts/promote.py: the two classes of PR that merge without a human, decided from the policy at main."""
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
    for must in ("terraform/", ".github/", "appsets/", "base-apps/vault", "base-apps/kagent", "base-apps/admission-policies",
                 "base-apps/n8n"):
        assert must in policy["auto"]["deny_prefixes"], must
    assert policy["auto"]["merge_method"] == "rebase"
    assert policy["auto"]["max_changed_files"] <= 4


def policy_text(**auto):
    """A block-style policy (the only YAML shape promote.py's parser accepts) with overrides."""
    fields = {"authors": "[b]", "require_labels": "[x]", "deny_prefixes": "[.github/]",
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
    text = "version: 1  # the schema\nauto:\n  authors: [b]\n  require_labels: [x]\n  deny_prefixes:\n    - .github/   # first\n    - b\n  max_changed_files: 1\n  merge_method: rebase\nowner:\n  approvers: [me]\n"
    policy = pm.load_policy(text)
    assert policy["auto"]["deny_prefixes"] == [".github/", "b"]
    assert policy["owner"]["approvers"] == ["me"]
# ---------------------------------------------------------------- decide

def policy():
    return pm.load_policy(POLICY_FILE.read_text())


def pr(**over):
    base = {"number": 760, "state": "open", "draft": False, "mergeable": True, "merged": False,
            "user": {"login": "arigsela-renovate[bot]"}, "labels": [{"name": "renovate"}, {"name": "review:skip"}],
            "head": {"sha": "abc123", "repo": {"full_name": "arigsela/kubernetes"}},
            "base": {"sha": "base456", "ref": "main", "repo": {"full_name": "arigsela/kubernetes"}}}
    base.update(over)
    return base


IMAGE_BUMP = "@@ -10,1 +10,1 @@\n-          image: ghcr.io/x/app:1.2.3\n+          image: ghcr.io/x/app:1.2.4"


def files(*names, patch=IMAGE_BUMP):
    """Changed files; non-docs files carry an image-tag-bump patch unless told otherwise."""
    return [{"filename": n} if n.endswith(".md") or n.startswith("docs/") else {"filename": n, "patch": patch}
            for n in names]


def commits_of(pr_obj, *logins, committer=None):
    """The PR's commits as /pulls/{n}/commits returns them: by the PR author unless told otherwise."""
    logins = logins or ((pr_obj.get("user") or {}).get("login"),)
    return [{"sha": f"c{i}{'0' * 6}", "author": {"login": login},
             "committer": {"login": committer or login}} for i, login in enumerate(logins)]


def decide(pr_obj, files_list, runs, policy_obj, statuses=None, commits=None):
    """pm.decide with the PR's own commits unless a test supplies others (§V.95)."""
    return pm.decide(pr_obj, files_list, runs, policy_obj, statuses,
                     commits_of(pr_obj) if commits is None else commits)


def checks(*conclusions, pending=0):
    runs = [{"name": f"job-{i}", "status": "completed", "conclusion": c} for i, c in enumerate(conclusions)]
    runs += [{"name": f"pending-{i}", "status": "in_progress", "conclusion": None} for i in range(pending)]
    return runs


def test_renovate_skip_green_is_auto():
    d = decide(pr(), files("base-apps/donetick/deployment.yaml"), checks("success", "skipped"), policy())
    assert d.klass == "auto" and d.reasons == [] and d.head_sha == "abc123"


def test_deny_prefix_wins_over_skip_label():
    d = decide(pr(), files("base-apps/vault/statefulsets.yaml"), checks("success"), policy())
    assert d.klass == "owner" and any("deny" in r for r in d.reasons)
    d = decide(pr(), files("terraform/roots/asela-cluster/ecr.tf"), checks("success"), policy())
    assert d.klass == "owner"


def test_pending_check_is_owner_not_auto():
    d = decide(pr(), files("base-apps/donetick/deployment.yaml"), checks("success", pending=1), policy())
    assert d.klass == "owner" and "checks pending" in d.reasons


def test_failed_or_absent_checks_are_owner():
    assert decide(pr(), files("x.md"), checks("success", "failure"), policy()).klass == "owner"
    assert decide(pr(), files("x.md"), [], policy()).klass == "owner"


@pytest.mark.parametrize("over, reason", [
    ({"user": {"login": "someone-else"}}, "author"),
    ({"labels": [{"name": "renovate"}, {"name": "review:skim"}]}, "label"),
    ({"draft": True}, "draft"),
    ({"state": "closed"}, "not open"),
    ({"mergeable": False}, "mergeable"),
    ({"head": {"sha": "abc123", "repo": {"full_name": "someone/fork"}}}, "fork"),
])
def test_each_condition_is_a_reason(over, reason):
    d = decide(pr(**over), files("x.md"), checks("success"), policy())
    assert d.klass == "owner" and any(reason in r for r in d.reasons), d.reasons


def test_too_many_files_is_owner():
    d = decide(pr(), files("a.md", "b.md", "c.md"), checks("success"), policy())
    assert d.klass == "owner" and any("files" in r for r in d.reasons)
# ---------------------------------------------------------------- github + run

class FakeGitHub:
    """Records every call; answers from a small table keyed by (method, path)."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, method, url, headers, body):
        path = url.replace("https://api.github.com", "")
        self.calls.append((method, path, json.loads(body) if body else None, headers.get("Accept")))
        status, payload = self.answers[(method, path)]
        return status, (payload if isinstance(payload, bytes) else json.dumps(payload).encode())


def answers_for(pr_obj, files_list, runs, policy_text=None, statuses=None, commits=None):
    policy_text = POLICY_FILE.read_text() if policy_text is None else policy_text
    n = pr_obj["number"]
    return {
        ("GET", f"/repos/arigsela/kubernetes/pulls/{n}"): (200, pr_obj),
        ("GET", f"/repos/arigsela/kubernetes/pulls/{n}/commits?per_page=100"): (200, commits_of(pr_obj) if commits is None else commits),
        ("GET", f"/repos/arigsela/kubernetes/pulls/{n}/files?per_page=100"): (200, files_list),
        ("GET", f"/repos/arigsela/kubernetes/commits/{pr_obj['head']['sha']}/check-runs?per_page=100"): (200, {"check_runs": runs}),
        ("GET", f"/repos/arigsela/kubernetes/contents/.github/promotion-policy.yaml?ref={pr_obj['base']['ref']}"): (200, policy_text.encode()),
        ("GET", f"/repos/arigsela/kubernetes/commits/{pr_obj['head']['sha']}/status"): (200, {"state": "success", "statuses": statuses or []}),
        ("GET", f"/repos/arigsela/kubernetes/issues/{n}/comments?per_page=100"): (200, []),
        ("PUT", f"/repos/arigsela/kubernetes/pulls/{n}/merge"): (200, {"merged": True, "sha": "m1"}),
        ("POST", f"/repos/arigsela/kubernetes/issues/{n}/comments"): (201, {"id": 1}),
    }


def test_run_auto_merges_by_rebase_pinned_to_head_and_comments():
    fake = FakeGitHub(answers_for(pr(), files("base-apps/donetick/deployment.yaml"), checks("success")))
    rc = pm.run("arigsela/kubernetes", 760, "tok", transport=fake)
    assert rc == 0
    merge = next(c for c in fake.calls if c[0] == "PUT")
    assert merge[2] == {"merge_method": "rebase", "sha": "abc123"}
    comment = next(c for c in fake.calls if c[0] == "POST")
    assert comment[2]["body"].startswith(pm.COMMENT_MARKER)
    assert "auto" in comment[2]["body"] and "policy read at `main`" in comment[2]["body"]


def test_merge_sends_head_sha():
    fake = FakeGitHub(answers_for(pr(head={"sha": "feedface", "repo": {"full_name": "arigsela/kubernetes"}}),
                                  files("x.md"), checks("success")))
    pm.run("arigsela/kubernetes", 760, "tok", transport=fake)
    assert next(c for c in fake.calls if c[0] == "PUT")[2]["sha"] == "feedface"


def test_run_owner_neither_merges_nor_comments():
    fake = FakeGitHub(answers_for(pr(labels=[{"name": "review:read"}]), files("x.md"), checks("success")))
    assert pm.run("arigsela/kubernetes", 760, "tok", transport=fake) == 0
    assert all(c[0] == "GET" for c in fake.calls)


def test_policy_is_read_at_the_base_ref_not_the_pr_head():
    fake = FakeGitHub(answers_for(pr(), files("x.md"), checks("success")))
    pm.run("arigsela/kubernetes", 760, "tok", transport=fake)
    assert any("contents/.github/promotion-policy.yaml?ref=main" in c[1] for c in fake.calls)
    assert not any("?ref=abc123" in c[1] for c in fake.calls)


def test_bad_base_policy_is_owner_with_reason_and_no_merge():
    fake = FakeGitHub(answers_for(pr(), files("x.md"), checks("success"), policy_text="version: 9\n"))
    assert pm.run("arigsela/kubernetes", 760, "tok", transport=fake) == 0
    assert all(c[0] == "GET" for c in fake.calls)


def test_dry_run_decides_but_does_not_merge():
    fake = FakeGitHub(answers_for(pr(), files("x.md"), checks("success")))
    assert pm.run("arigsela/kubernetes", 760, "tok", dry_run=True, transport=fake) == 0
    assert all(c[0] == "GET" for c in fake.calls)


def test_already_merged_pr_is_a_noop():
    fake = FakeGitHub(answers_for(pr(merged=True, state="closed"), files("x.md"), checks("success")))
    assert pm.run("arigsela/kubernetes", 760, "tok", transport=fake) == 0
    assert all(c[0] == "GET" for c in fake.calls)


# ---------------------------------------------------------------- review fixes

import yaml

WORKFLOWS = REPO / ".github" / "workflows"


def test_promote_triggers_on_workflow_names_that_exist():
    """workflow_run.workflows matches the `name:` of a workflow, not its file name."""
    promote = yaml.safe_load((WORKFLOWS / "promote.yaml").read_text())
    wanted = promote[True]["workflow_run"]["workflows"]          # `on:` parses as the key True
    names = {yaml.safe_load(f.read_text()).get("name") for f in WORKFLOWS.glob("*.yaml")}
    for w in wanted:
        assert w in names, f"{w!r} is not the name: of any workflow in .github/workflows ({sorted(n for n in names if n)})"
    assert "Validate Manifests" in wanted and "PR Triage" in wanted


def statuses(**contexts):
    return [{"context": c, "state": s} for c, s in contexts.items()]


def test_pending_commit_status_is_owner():
    d = decide(pr(), files("base-apps/donetick/deployment.yaml"), checks("success"), policy(),
                  statuses=statuses(**{"renovate/stability-days": "pending"}))
    assert d.klass == "owner" and any("renovate/stability-days" in r and "pending" in r for r in d.reasons)


def test_failed_commit_status_is_owner_and_success_statuses_pass():
    ok = decide(pr(), files("base-apps/donetick/deployment.yaml"), checks("success"), policy(),
                   statuses=statuses(**{"renovate/stability-days": "success", "atlantis/plan": "success"}))
    assert ok.klass == "auto"
    bad = decide(pr(), files("base-apps/donetick/deployment.yaml"), checks("success"), policy(),
                    statuses=statuses(**{"atlantis/plan": "failure"}))
    assert bad.klass == "owner" and any("atlantis/plan" in r for r in bad.reasons)


def test_run_reads_commit_statuses_and_stays_owner_while_pending():
    fake = FakeGitHub(answers_for(pr(), files("base-apps/donetick/deployment.yaml"), checks("success"),
                                  statuses=statuses(**{"renovate/stability-days": "pending"})))
    assert pm.run("arigsela/kubernetes", 760, "tok", transport=fake) == 0
    assert all(c[0] == "GET" for c in fake.calls)
    assert any("/commits/abc123/status" in c[1] for c in fake.calls)


def test_auto_requires_a_deterministic_skip_shaped_diff_not_just_the_label():
    """review:skip can come from the classifier too; auto additionally re-derives always_skip:
    every file is docs, or its patch changes only image:/tag: value lines, <= 4 lines in total."""
    replicas = "@@ -5,1 +5,1 @@\n-  replicas: 1\n+  replicas: 2"
    d = decide(pr(), files("base-apps/donetick/deployment.yaml", patch=replicas), checks("success"), policy())
    assert d.klass == "owner" and any("not an image-tag bump" in r for r in d.reasons)
    five = "@@ -1,5 +1,5 @@\n" + "\n".join(f"-  tag: v{i}\n+  tag: v{i+1}" for i in range(3))   # 6 changed lines
    d = decide(pr(), files("base-apps/donetick/deployment.yaml", patch=five), checks("success"), policy())
    assert d.klass == "owner" and any("6 changed lines" in r for r in d.reasons)
    nopatch = [{"filename": "base-apps/donetick/deployment.yaml"}]
    d = decide(pr(), nopatch, checks("success"), policy())
    assert d.klass == "owner"
    docs_only = decide(pr(), files("docs/x.md", "README.md"), checks("success"), policy())
    assert docs_only.klass == "auto"


def test_renovate_config_is_deny_listed():
    d = decide(pr(), files("renovate.json5", patch="@@ -1 +1 @@\n-a\n+b"), checks("success"), policy())
    assert d.klass == "owner" and any("deny prefix" in r for r in d.reasons)


class SequencedGitHub(FakeGitHub):
    """Like FakeGitHub, but a list value answers successive calls to the same path in order."""

    def __call__(self, method, url, headers, body):
        path = url.replace("https://api.github.com", "")
        key = (method, path)
        if isinstance(self.answers.get(key), list) and self.answers[key]:
            status, payload = self.answers[key].pop(0) if len(self.answers[key]) > 1 else self.answers[key][0]
            self.calls.append((method, path, json.loads(body) if body else None, headers.get("Accept")))
            return status, (payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        return super().__call__(method, url, headers, body)


def test_mergeable_null_is_refetched_before_deciding():
    answers = answers_for(pr(), files("base-apps/donetick/deployment.yaml"), checks("success"))
    answers[("GET", "/repos/arigsela/kubernetes/pulls/760")] = [(200, pr(mergeable=None)), (200, pr(mergeable=None)), (200, pr())]
    fake = SequencedGitHub(answers)
    naps = []
    assert pm.run("arigsela/kubernetes", 760, "tok", transport=fake, sleep=naps.append) == 0
    assert len([c for c in fake.calls if c[1] == "/repos/arigsela/kubernetes/pulls/760"]) == 3
    assert naps == [3.0, 3.0]
    assert any(c[0] == "PUT" for c in fake.calls), "decided auto once mergeable was known"


def test_mergeable_still_null_after_retries_is_owner():
    answers = answers_for(pr(), files("base-apps/donetick/deployment.yaml"), checks("success"))
    answers[("GET", "/repos/arigsela/kubernetes/pulls/760")] = [(200, pr(mergeable=None))]
    fake = SequencedGitHub(answers)
    assert pm.run("arigsela/kubernetes", 760, "tok", transport=fake, sleep=lambda s: None) == 0
    assert all(c[0] == "GET" for c in fake.calls)
# ---------------------------------------------------------------- the docs-only class (2026-10-13)

def owner_pr(**over):
    base = {"user": {"login": "arigsela"}, "labels": [{"name": "review:skip"}]}
    base.update(over)
    return pr(**base)


def test_repo_policy_has_the_owner_docs_class_and_denies_the_instruction_files():
    p = policy()
    docs = p["auto_docs"]
    assert docs["authors"] == ["arigsela"] and docs["require_labels"] == ["review:skip"]
    for must in (".github/", "CLAUDE.md", "AGENTS.md", ".claude/"):
        assert any(must.startswith(d) or d.startswith(must) for d in docs["deny_prefixes"]), must


def test_load_policy_requires_every_class_to_deny_the_workflows_dir():
    text = policy_text(deny_prefixes="[.github/]") + "auto_docs:\n  authors: [o]\n  require_labels: [x]\n  deny_prefixes: [CLAUDE.md]\n  max_changed_files: 5\n"
    with pytest.raises(pm.PolicyError, match=r"auto_docs.*\.github/"):
        pm.load_policy(text)
    text = policy_text(deny_prefixes="[x/]")
    with pytest.raises(pm.PolicyError, match=r"auto.*\.github/"):
        pm.load_policy(text)


def test_owner_docs_only_skip_green_is_auto():
    d = decide(owner_pr(), files("README.md", "docs/platform/promotion.md", "index.md"),
                  checks("success", "skipped"), policy())
    assert d.klass == "auto" and d.reasons == [] and d.rule == "docs-only"


def test_renovate_bump_still_reports_its_rule():
    d = decide(pr(), files("base-apps/donetick/deployment.yaml"), checks("success"), policy())
    assert d.klass == "auto" and d.rule == "renovate-bump"


@pytest.mark.parametrize("name", ["CLAUDE.md", "AGENTS.md", ".github/README.md", ".claude/skills/x.md"])
def test_owner_docs_class_never_merges_the_instruction_files(name):
    d = decide(owner_pr(), files(name), checks("success"), policy())
    assert d.klass == "owner" and any("deny" in r for r in d.reasons), d.reasons


def test_owner_docs_class_rejects_a_non_docs_file():
    d = decide(owner_pr(), files("README.md", "base-apps/donetick/deployment.yaml"),
                  checks("success"), policy())
    assert d.klass == "owner" and any("not docs" in r for r in d.reasons), d.reasons


def test_owner_docs_class_has_its_own_file_limit():
    many = files(*[f"docs/n{i}.md" for i in range(11)])
    d = decide(owner_pr(), many, checks("success"), policy())
    assert d.klass == "owner" and any("files" in r for r in d.reasons)
    ten = files(*[f"docs/n{i}.md" for i in range(10)])
    assert decide(owner_pr(), ten, checks("success"), policy()).klass == "auto"


def test_owner_docs_class_needs_the_skip_label_and_green_checks():
    d = decide(owner_pr(labels=[{"name": "review:skim"}]), files("README.md"), checks("success"), policy())
    assert d.klass == "owner" and any("label" in r for r in d.reasons)
    d = decide(owner_pr(), files("README.md"), checks("success", "failure"), policy())
    assert d.klass == "owner"


def test_third_party_author_is_owner_and_both_classes_say_why():
    d = decide(pr(user={"login": "someone"}), files("README.md"), checks("success"), policy())
    assert d.klass == "owner"
    assert any(r.startswith("renovate-bump:") and "author" in r for r in d.reasons), d.reasons
    assert any(r.startswith("docs-only:") and "author" in r for r in d.reasons), d.reasons


def test_run_auto_docs_merges_and_the_comment_names_the_class():
    fake = FakeGitHub(answers_for(owner_pr(), files("README.md", "docs/index.md"), checks("success")))
    assert pm.run("arigsela/kubernetes", 760, "tok", transport=fake) == 0
    assert any(c[0] == "PUT" for c in fake.calls)
    comment = next(c for c in fake.calls if c[0] == "POST")
    assert "docs-only" in comment[2]["body"]


def test_implementer_app_pr_is_never_auto():
    """§V.95: an agent-authored PR has no promotion class, however green and however small."""
    app_pr = pr(user={"login": "arigsela-implementer[bot]"}, labels=[{"name": "review:skip"}])
    d = decide(app_pr, files("docs/platform/promotion.md"), checks("success"), policy())
    assert d.klass == "owner" and any("author" in r for r in d.reasons)
    d = decide(app_pr, files("base-apps/donetick/deployment.yaml"), checks("success"), policy())
    assert d.klass == "owner"


# ------------------------------------------------- commit authors, not just the PR author (§V.95)

def test_renovate_pr_with_a_commit_by_another_identity_is_owner():
    """A propose-class agent pushing onto an open Renovate branch must not ride its class."""
    p = pr()
    d = decide(p, files("base-apps/donetick/deployment.yaml"), checks("success"), policy(),
               commits=commits_of(p, "arigsela-renovate[bot]", "arigsela-implementer[bot]"))
    assert d.klass == "owner" and any("commit c1" in r and "arigsela-implementer[bot]" in r for r in d.reasons)


def test_owner_docs_pr_with_a_foreign_commit_is_owner():
    p = owner_pr()
    d = decide(p, files("README.md"), checks("success"), policy(),
               commits=commits_of(p, "arigsela", "arigsela-implementer[bot]"))
    assert d.klass == "owner"


def test_commits_not_fetched_is_owner():
    d = pm.decide(pr(), files("base-apps/donetick/deployment.yaml"), checks("success"), policy(), None, None)
    assert d.klass == "owner" and any("commit" in r for r in d.reasons)


def test_web_flow_committer_is_accepted_for_the_authors_own_commit():
    p = pr()
    d = decide(p, files("base-apps/donetick/deployment.yaml"), checks("success"), policy(),
               commits=commits_of(p, "arigsela-renovate[bot]", committer="web-flow"))
    assert d.klass == "auto"


def test_foreign_committer_is_owner():
    p = pr()
    d = decide(p, files("base-apps/donetick/deployment.yaml"), checks("success"), policy(),
               commits=commits_of(p, "arigsela-renovate[bot]", committer="arigsela-implementer[bot]"))
    assert d.klass == "owner"


def test_run_fetches_the_commits_and_refuses_a_foreign_one():
    p = pr()
    fake = FakeGitHub(answers_for(p, files("base-apps/donetick/deployment.yaml"), checks("success"),
                                  commits=commits_of(p, "arigsela-renovate[bot]", "arigsela-implementer[bot]")))
    assert pm.run("arigsela/kubernetes", 760, "tok", transport=fake) == 0
    assert any("/pulls/760/commits?per_page=100" in c[1] for c in fake.calls)
    assert not [c for c in fake.calls if c[0] == "PUT"], "must not merge"
