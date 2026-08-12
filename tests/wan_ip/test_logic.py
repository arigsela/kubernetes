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
    ],
)
def test_rejects_everything_else(reconcile, value):
    assert reconcile.is_valid_public_ipv4(value) is False


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
