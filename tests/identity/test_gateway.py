# tests/identity/test_gateway.py
"""The external path is checked at the ingress too (V91): kagent-mcp needs a verified token in
addition to the allow-list, and the UI host cannot carry an identity header through."""
from conftest import load, one

DEX = "https://dex.arigsela.com"
K8S = "https://kubernetes.default.svc.cluster.local"


def test_gateway_request_authentication_names_the_two_issuers():
    ra = one("base-apps/istio-ingress/request-authentication.yaml", "RequestAuthentication",
             "gateway-kagent-callers")
    assert ra["spec"]["targetRefs"][0] == {"group": "gateway.networking.k8s.io", "kind": "Gateway", "name": "main"}
    assert {r["issuer"] for r in ra["spec"]["jwtRules"]} == {DEX, K8S}
    assert all(r["forwardOriginalToken"] is True for r in ra["spec"]["jwtRules"])


def test_kagent_mcp_rule_requires_a_token_inside_the_allow_list_source():
    ap = one("base-apps/istio-ingress/authorizationpolicy.yaml", "AuthorizationPolicy", "gateway-allow")
    rules = [r for r in ap["spec"]["rules"]
             if any("kagent-mcp.arigsela.com" in h for t in r.get("to", []) for h in t["operation"].get("hosts", []))]
    assert len(rules) == 1, "exactly one rule for kagent-mcp; a second ALLOW rule would OR away the token"
    src = rules[0]["from"][0]["source"]
    assert src["ipBlocks"] and src["requestPrincipals"] == ["*"]
    assert ap["spec"]["action"] == "ALLOW"


def test_ui_route_strips_identity_headers():
    route = one("base-apps/kagent/httproute.yaml", "HTTPRoute", "kagent")
    filters = route["spec"]["rules"][0]["filters"]
    mod = next(f for f in filters if f["type"] == "RequestHeaderModifier")["requestHeaderModifier"]
    assert {h.lower() for h in mod["remove"]} == {"x-user-id", "x-agent-name", "authorization"}
