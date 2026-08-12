import pytest

POLICY = """\
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: gateway-allow
  namespace: istio-ingress
  annotations:
    arigsela.com/wan-ip: "76.97.4.210"
spec:
  rules:
    - to:
        - operation:
            hosts:
              - argocd.arigsela.com
      from:
        - source:
            ipBlocks:
              - 76.97.4.210/32
              - 170.85.56.189/32
              - 104.28.177.82/32
"""


@pytest.mark.parametrize("value", ["76.97.4.210", "8.8.8.8", " 1.1.1.1 "])
def test_accepts_public_addresses(reconcile, value):
    assert reconcile.is_valid_public_ipv4(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "10.0.1.182",        # private
        "192.168.1.1",       # private
        "127.0.0.1",         # loopback
        "169.254.1.1",       # link-local
        "224.0.0.1",         # multicast
        "0.0.0.0",           # unspecified
        "",                  # empty
        "not-an-ip",         # junk
        "<html>error</html>",  # captive portal
        "76.97.4.210\n76.97.4.211",  # two values
        "2606:4700::1111",   # IPv6
        "100.64.1.5",        # RFC 6598 carrier-grade NAT
        "100.127.255.254",   # RFC 6598, top of the range
    ],
)
def test_rejects_everything_else(reconcile, value):
    assert reconcile.is_valid_public_ipv4(value) is False


@pytest.mark.parametrize("value", ["100.64.1.5", "100.100.7.9", "100.127.255.254"])
def test_rejects_rfc6598_cgnat_space(reconcile, value):
    """CGNAT addresses are NOT is_private, so the old enumeration of
    is_private/is_loopback/is_reserved/is_multicast/is_link_local/
    is_unspecified accepted every one of them.

    The stakes are not cosmetic: 100.64.0.0/10 is shared carrier space. Writing
    one of these into 21 public A records points the homelab at an address that
    is not routable from the internet, and allow-listing it in
    authorizationpolicy.yaml would grant the security boundary to every other
    subscriber sitting behind the same carrier NAT. The runbook lists CGNAT as
    a live possibility for this residential line, so this is a real input, not
    a hypothetical one.
    """
    import ipaddress

    assert ipaddress.IPv4Address(value).is_private is False, (
        "if this ever becomes True, the CGNAT check is being made by "
        "is_private rather than by is_global - re-check the reasoning above"
    )
    assert reconcile.is_valid_public_ipv4(value) is False


def test_still_rejects_multicast_which_is_global_does_not_cover(reconcile):
    """`is_global` alone is not sufficient: CPython does not count 224.0.0.0/4
    among its private networks, so IPv4Address("224.0.0.1").is_global is True.
    is_valid_public_ipv4 carries an explicit `not is_multicast` for exactly
    that reason - this test fails if someone simplifies it away."""
    import ipaddress

    assert ipaddress.IPv4Address("224.0.0.1").is_global is True
    assert reconcile.is_valid_public_ipv4("224.0.0.1") is False


def test_reads_declared_wan_ip(reconcile):
    assert reconcile.read_declared_wan_ip(POLICY) == "76.97.4.210"


def test_missing_annotation_raises(reconcile):
    with pytest.raises(KeyError):
        reconcile.read_declared_wan_ip("metadata:\n  name: x\nspec: {}\n")


def test_rewrite_moves_annotation_and_rule(reconcile):
    out = reconcile.rewrite_policy(POLICY, "76.97.4.210", "76.97.99.1")
    assert 'arigsela.com/wan-ip: "76.97.99.1"' in out
    assert "- 76.97.99.1/32" in out
    assert "76.97.4.210" not in out


def test_rewrite_leaves_the_other_addresses_alone(reconcile):
    out = reconcile.rewrite_policy(POLICY, "76.97.4.210", "76.97.99.1")
    assert "- 170.85.56.189/32" in out
    assert "- 104.28.177.82/32" in out


def test_rewrite_refuses_a_bad_new_address(reconcile):
    with pytest.raises(ValueError):
        reconcile.rewrite_policy(POLICY, "76.97.4.210", "10.0.0.5")


def test_rewrite_is_a_noop_when_already_current(reconcile):
    assert reconcile.rewrite_policy(POLICY, "76.97.4.210", "76.97.4.210") == POLICY


POLICY_WITH_DASHED_COMMENT = """\
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: gateway-allow
  namespace: istio-ingress
  annotations:
    arigsela.com/wan-ip: "76.97.4.210"
spec:
  rules:
    - to:
        - operation:
            hosts:
              - argocd.arigsela.com
      from:
        - source:
            ipBlocks:
              # historical: was - 76.97.4.210/32
              - 76.97.4.210/32
              - 170.85.56.189/32
              - 104.28.177.82/32
"""

DASHED_COMMENT_LINE = "              # historical: was - 76.97.4.210/32"

POLICY_WITH_DASHLESS_COMMENT = """\
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: gateway-allow
  namespace: istio-ingress
  annotations:
    arigsela.com/wan-ip: "76.97.4.210"
spec:
  rules:
    - to:
        - operation:
            hosts:
              - vault.local
      from:
        - source:
            ipBlocks:
              # 76.97.4.210/32 covers the hairpin path
              - 76.97.4.210/32
              - 10.0.1.0/24
"""

DASHLESS_COMMENT_LINE = "              # 76.97.4.210/32 covers the hairpin path"


def test_rewrite_leaves_a_dashed_comment_byte_identical(reconcile):
    assert DASHED_COMMENT_LINE in POLICY_WITH_DASHED_COMMENT.split("\n")
    out = reconcile.rewrite_policy(POLICY_WITH_DASHED_COMMENT, "76.97.4.210", "76.97.99.1")
    assert DASHED_COMMENT_LINE in out.split("\n")
    assert "              - 76.97.99.1/32" in out.split("\n")
    assert "              - 76.97.4.210/32" not in out.split("\n")


def test_rewrite_leaves_a_dashless_comment_alone_and_flags_it_stale(reconcile):
    lines = POLICY_WITH_DASHLESS_COMMENT.split("\n")
    comment_lineno = lines.index(DASHLESS_COMMENT_LINE) + 1
    out = reconcile.rewrite_policy(POLICY_WITH_DASHLESS_COMMENT, "76.97.4.210", "76.97.99.1")
    assert DASHLESS_COMMENT_LINE in out.split("\n")
    assert comment_lineno in reconcile.stale_comment_lines(POLICY_WITH_DASHLESS_COMMENT, "76.97.4.210")


def test_stale_comment_lines_is_empty_when_nothing_mentions_the_address(reconcile):
    assert reconcile.stale_comment_lines(POLICY, "76.97.4.210") == []


def test_rewrite_raises_when_old_ip_is_not_present(reconcile):
    with pytest.raises(ValueError):
        reconcile.rewrite_policy(POLICY, "9.9.9.9", "76.97.99.1")
