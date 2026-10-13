# tests/identity/test_gateway.py
"""The external path is verified ONCE, at the waypoint (the ingress routes kagent-mcp through it,
istio.io/ingress-use-waypoint). A RequestAuthentication on the shared ingress Gateway is
Gateway-wide: it 401s every non-JWT bearer on every host (the n8n webhook token, argocd/grafana
API tokens) before the host's own rule runs, which is what the 2026-10-13 review found live.
The UI host still cannot carry an identity header through."""
from pathlib import Path
from conftest import load, one, REPO


def test_no_request_authentication_on_the_shared_ingress_gateway():
    for f in Path(REPO / "base-apps/istio-ingress").glob("*.yaml"):
        for d in load(str(f.relative_to(REPO))):
            assert d.get("kind") != "RequestAuthentication", f"{f}: a Gateway-wide JWT rule 401s foreign bearers on every host"


def test_kagent_mcp_rule_is_the_allow_list_only_and_single():
    ap = one("base-apps/istio-ingress/authorizationpolicy.yaml", "AuthorizationPolicy", "gateway-allow")
    rules = [r for r in ap["spec"]["rules"]
             if any("kagent-mcp.arigsela.com" in h for t in r.get("to", []) for h in t["operation"].get("hosts", []))]
    assert len(rules) == 1
    src = rules[0]["from"][0]["source"]
    assert src["ipBlocks"] and "requestPrincipals" not in src, "principals are checked at the waypoint, not the edge"


def test_ui_route_strips_identity_headers():
    route = one("base-apps/kagent/httproute.yaml", "HTTPRoute", "kagent")
    filters = route["spec"]["rules"][0]["filters"]
    mod = next(f for f in filters if f["type"] == "RequestHeaderModifier")["requestHeaderModifier"]
    assert {h.lower() for h in mod["remove"]} == {"x-user-id", "x-agent-name", "authorization"}
