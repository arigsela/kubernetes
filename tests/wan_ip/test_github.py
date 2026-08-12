import base64

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


STEADY_STATE_POLICY = (
    "metadata:\n"
    "  annotations:\n"
    '    arigsela.com/wan-ip: "76.97.4.210"\n'
)


def test_main_is_silent_in_steady_state(reconcile, monkeypatch):
    """This runs 288 times a day. If this regresses, it notifies on every run
    forever - invisible in code review, only visible as a flood."""
    called = {"aws": False, "pr": False, "notify": False}

    monkeypatch.setattr(reconcile, "detect_wan_ip", lambda: "76.97.4.210")
    monkeypatch.setattr(reconcile, "read_policy_from_main", lambda: STEADY_STATE_POLICY)

    def fake_aws_json(args):
        called["aws"] = True
        return {}

    def fake_open_allowlist_pr(old_ip, new_ip):
        called["pr"] = True
        return None, False

    def fake_notify(payload, post=None):
        called["notify"] = True

    monkeypatch.setattr(reconcile, "aws_json", fake_aws_json)
    monkeypatch.setattr(reconcile, "open_allowlist_pr", fake_open_allowlist_pr)
    monkeypatch.setattr(reconcile, "notify", fake_notify)

    assert reconcile.main() == 0
    assert called == {"aws": False, "pr": False, "notify": False}


def test_main_does_not_notify_when_pr_already_existed_and_dns_already_matched(
    reconcile, monkeypatch
):
    """The between-rotation-and-merge state: PR already open, Route 53 already
    fixed. This runs every 5 minutes until the human merges - alerting here
    would train the operator to ignore the notification."""
    monkeypatch.setattr(reconcile, "detect_wan_ip", lambda: "76.97.99.1")
    monkeypatch.setattr(reconcile, "read_policy_from_main", lambda: STEADY_STATE_POLICY)

    zone = {
        "ResourceRecordSets": [
            {
                "Name": "argocd.arigsela.com.",
                "Type": "A",
                "TTL": 300,
                "ResourceRecords": [{"Value": "76.97.99.1"}],
            },
        ]
    }
    monkeypatch.setattr(reconcile, "aws_json", lambda args: zone)
    monkeypatch.setattr(
        reconcile,
        "open_allowlist_pr",
        lambda old_ip, new_ip: ("https://example.invalid/pr/1", False),
    )

    notified = []
    monkeypatch.setattr(
        reconcile, "notify", lambda payload, post=None: notified.append(payload)
    )

    assert reconcile.main() == 0
    assert notified == []
