import base64
import urllib.error

import pytest


def test_branch_name_is_deterministic_per_address(reconcile):
    """Idempotency depends on this: same rotation, same branch, so a second run
    finds the existing PR instead of opening another."""
    assert reconcile.branch_name("76.97.99.1") == "automation/wan-ip-76.97.99.1"
    assert reconcile.branch_name("76.97.99.1") == reconcile.branch_name("76.97.99.1")


def test_detect_uses_the_first_source_that_returns_a_public_address(reconcile):
    calls = []

    def fetch(url):
        calls.append(url)
        return "76.97.99.1\n"

    assert reconcile.detect_wan_ip(fetch) == "76.97.99.1"
    assert len(calls) == 1


def test_detect_falls_through_a_bad_response(reconcile):
    def fetch(url):
        return "<html>captive portal</html>" if "amazonaws" in url else "76.97.99.1"

    assert reconcile.detect_wan_ip(fetch) == "76.97.99.1"


def test_detect_falls_through_an_exception(reconcile):
    def fetch(url):
        if "amazonaws" in url:
            raise OSError("network unreachable")
        return "76.97.99.1"

    assert reconcile.detect_wan_ip(fetch) == "76.97.99.1"


def test_detect_raises_when_every_source_is_unusable(reconcile):
    def fetch(url):
        return "10.0.0.1"

    with pytest.raises(RuntimeError):
        reconcile.detect_wan_ip(fetch)


def test_notify_never_raises(reconcile):
    """A failed notification must not fail the run - DNS is already fixed by
    then, and losing the alert is strictly better than losing the reconcile."""
    def post(payload):
        raise OSError("n8n is down")

    reconcile.notify({"new": "76.97.99.1"}, post)


def test_open_allowlist_pr_short_circuits_and_writes_nothing_when_a_pr_already_exists(
    reconcile, monkeypatch
):
    """A rotation that sits unmerged for a day must not open hundreds of PRs.

    If this dedup regresses, every 5-minute run opens a new PR for the same
    rotation - a flood, not a crash, so nothing in code review would catch it.
    """
    calls = []

    def fake_github(method, path, body=None):
        calls.append((method, path))
        if method == "GET" and path.startswith("/pulls?"):
            return [{"html_url": "https://github.com/arigsela/kubernetes/pull/42"}]
        raise AssertionError("unexpected call: %s %s" % (method, path))

    monkeypatch.setattr(reconcile, "github", fake_github)

    result = reconcile.open_allowlist_pr("76.97.4.210", "76.97.99.1")

    assert result == ("https://github.com/arigsela/kubernetes/pull/42", False)
    assert calls, "expected the existing-PR lookup to have happened"
    assert all(method not in ("POST", "PUT") for method, _ in calls)


POLICY_FOR_PR_TEST = (
    "apiVersion: security.istio.io/v1\n"
    "kind: AuthorizationPolicy\n"
    "metadata:\n"
    "  name: gateway-allow\n"
    "  namespace: istio-ingress\n"
    "  annotations:\n"
    '    arigsela.com/wan-ip: "76.97.4.210"\n'
    "spec:\n"
    "  rules:\n"
    "    - to:\n"
    "        - operation:\n"
    "            hosts:\n"
    "              - argocd.arigsela.com\n"
    "      from:\n"
    "        - source:\n"
    "            ipBlocks:\n"
    "              - 76.97.4.210/32\n"
)


def test_open_allowlist_pr_creates_a_new_pr_when_none_exists(reconcile, monkeypatch):
    encoded = base64.b64encode(POLICY_FOR_PR_TEST.encode()).decode()

    def fake_github(method, path, body=None):
        if method == "GET" and path.startswith("/pulls?"):
            return []
        if method == "GET" and path == "/git/ref/heads/main":
            return {"object": {"sha": "deadbeef"}}
        if method == "POST" and path == "/git/refs":
            return {}
        if method == "GET" and path.startswith("/contents/"):
            return {"content": encoded, "sha": "filesha123"}
        if method == "PUT" and path.startswith("/contents/"):
            return {}
        if method == "POST" and path == "/pulls":
            return {"html_url": "https://github.com/arigsela/kubernetes/pull/99"}
        raise AssertionError("unexpected call: %s %s" % (method, path))

    monkeypatch.setattr(reconcile, "github", fake_github)

    url, created = reconcile.open_allowlist_pr("76.97.4.210", "76.97.99.1")

    assert created is True
    assert url == "https://github.com/arigsela/kubernetes/pull/99"


def _ref_already_exists():
    return urllib.error.HTTPError(
        "https://api.github.com/repos/x/git/refs", 422,
        "Reference already exists", {}, None)


def test_open_allowlist_pr_commits_against_the_branch_when_it_already_exists(
    reconcile, monkeypatch
):
    """The permanent 409 crash-loop.

    PUT /contents needs the SHA of the blob it replaces ON THE TARGET BRANCH.
    Reading that SHA from `main` works only while the branch still points at
    main's tree; once the branch carries its own commit to this path, GitHub
    answers 409 Conflict. That leaves the PR uncreated, so the existing-PR
    short-circuit never engages, so the next run does exactly the same thing -
    forever, with no PR ever opened.

    Here the branch survives from an earlier attempt with an unrelated edit to
    the same file (differing blob SHA, same declared address). The commit must
    go on with the BRANCH's SHA.
    """
    branch_text = POLICY_FOR_PR_TEST.replace(
        "            ipBlocks:\n",
        "            ipBlocks:\n              # left over from an earlier attempt\n")
    assert branch_text != POLICY_FOR_PR_TEST
    puts = []

    def fake_github(method, path, body=None):
        if method == "GET" and path.startswith("/pulls?"):
            return []
        if method == "GET" and path == "/git/ref/heads/main":
            return {"object": {"sha": "deadbeef"}}
        if method == "POST" and path == "/git/refs":
            raise _ref_already_exists()
        if method == "GET" and path.startswith("/contents/"):
            if path.endswith("?ref=main"):
                return {"content": base64.b64encode(POLICY_FOR_PR_TEST.encode()).decode(),
                        "sha": "mainblob"}
            return {"content": base64.b64encode(branch_text.encode()).decode(),
                    "sha": "branchblob"}
        if method == "PUT" and path.startswith("/contents/"):
            puts.append(body)
            return {}
        if method == "POST" and path == "/pulls":
            return {"html_url": "https://github.com/arigsela/kubernetes/pull/7"}
        raise AssertionError("unexpected call: %s %s" % (method, path))

    monkeypatch.setattr(reconcile, "github", fake_github)

    url, created = reconcile.open_allowlist_pr("76.97.4.210", "76.97.99.1")

    assert created is True
    assert url == "https://github.com/arigsela/kubernetes/pull/7"
    assert len(puts) == 1
    assert puts[0]["sha"] == "branchblob", (
        "the commit must carry the branch's blob SHA; main's SHA is what GitHub "
        "409s on, permanently")
    assert puts[0]["branch"] == "automation/wan-ip-76.97.99.1"
    # And the edit is built on the branch's content, so the earlier attempt's
    # line is preserved rather than reverted.
    committed = base64.b64decode(puts[0]["content"]).decode()
    assert "# left over from an earlier attempt" in committed
    assert "- 76.97.99.1/32" in committed


def test_open_allowlist_pr_still_opens_the_pr_when_the_branch_is_already_committed(
    reconcile, monkeypatch
):
    """The partial failure this is most likely to hit: branch created, commit
    landed, then POST /pulls failed. The branch already carries exactly the
    rotation we want, so there is nothing left to commit - but the PR still
    does not exist, and only opening it ends the loop."""
    branch_text = POLICY_FOR_PR_TEST.replace("76.97.4.210", "76.97.99.1")
    calls = []

    def fake_github(method, path, body=None):
        calls.append((method, path))
        if method == "GET" and path.startswith("/pulls?"):
            return []
        if method == "GET" and path == "/git/ref/heads/main":
            return {"object": {"sha": "deadbeef"}}
        if method == "POST" and path == "/git/refs":
            raise _ref_already_exists()
        if method == "GET" and path.startswith("/contents/"):
            if path.endswith("?ref=main"):
                return {"content": base64.b64encode(POLICY_FOR_PR_TEST.encode()).decode(),
                        "sha": "mainblob"}
            return {"content": base64.b64encode(branch_text.encode()).decode(),
                    "sha": "branchblob"}
        if method == "POST" and path == "/pulls":
            return {"html_url": "https://github.com/arigsela/kubernetes/pull/8"}
        raise AssertionError("unexpected call: %s %s" % (method, path))

    monkeypatch.setattr(reconcile, "github", fake_github)

    url, created = reconcile.open_allowlist_pr("76.97.4.210", "76.97.99.1")

    assert (url, created) == ("https://github.com/arigsela/kubernetes/pull/8", True)
    assert not any(method == "PUT" for method, _ in calls), (
        "the branch already carries this rotation - re-committing it is what "
        "produces the 409")


def test_open_allowlist_pr_returns_nothing_when_main_already_declares_the_address(
    reconcile, monkeypatch
):
    """No branch, and main is already correct: there is genuinely nothing to
    propose. This must stay distinct from the branch-exists case above."""
    already = POLICY_FOR_PR_TEST.replace("76.97.4.210", "76.97.99.1")

    def fake_github(method, path, body=None):
        if method == "GET" and path.startswith("/pulls?"):
            return []
        if method == "GET" and path == "/git/ref/heads/main":
            return {"object": {"sha": "deadbeef"}}
        if method == "POST" and path == "/git/refs":
            return {}
        if method == "GET" and path.startswith("/contents/"):
            return {"content": base64.b64encode(already.encode()).decode(), "sha": "s"}
        raise AssertionError("unexpected call: %s %s" % (method, path))

    monkeypatch.setattr(reconcile, "github", fake_github)

    assert reconcile.open_allowlist_pr("76.97.4.210", "76.97.99.1") == (None, False)
