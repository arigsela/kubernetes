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
