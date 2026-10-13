# tests/identity/test_mesh.py
"""Callers as principals (V91, V92): the mesh verifies every caller's token and sets the
identity header; the policy files say so and name exactly the two issuers."""
from conftest import load, one, REPO

DEX = "https://dex.arigsela.com"
K8S = "https://kubernetes.default.svc.cluster.local"


def test_kagent_dispatcher_and_backstage_are_in_the_ambient_mesh():
    for app in ("base-apps/kagent-secrets.yaml", "base-apps/backstage.yaml"):
        spec = load(app)[0]["spec"]
        labels = spec["syncPolicy"]["managedNamespaceMetadata"]["labels"]
        assert labels["istio.io/dataplane-mode"] == "ambient", app
        assert "CreateNamespace=true" in spec["syncPolicy"]["syncOptions"], app
    ns = one("base-apps/dispatcher/namespace.yaml", "Namespace", "dispatcher")
    assert ns["metadata"]["labels"]["istio.io/dataplane-mode"] == "ambient"
    kagent = load("base-apps/kagent-secrets.yaml")[0]["spec"]["syncPolicy"]["managedNamespaceMetadata"]["labels"]
    assert kagent["istio.io/use-waypoint"] == "kagent-waypoint"


def test_waypoint_exists_for_services():
    wp = one("base-apps/kagent/waypoint.yaml", "Gateway", "kagent-waypoint")
    assert wp["spec"]["gatewayClassName"] == "istio-waypoint"
    assert wp["metadata"]["labels"]["istio.io/waypoint-for"] == "service"
    assert wp["spec"]["listeners"][0]["protocol"] == "HBONE"


def test_request_authentication_names_exactly_the_two_issuers():
    ra = one("base-apps/kagent/request-authentication.yaml", "RequestAuthentication", "kagent-callers")
    assert ra["spec"]["targetRefs"] == [{"group": "gateway.networking.k8s.io", "kind": "Gateway",
                                        "name": "kagent-waypoint"}]
    rules = {r["issuer"]: r for r in ra["spec"]["jwtRules"]}
    assert set(rules) == {DEX, K8S}
    assert rules[DEX]["audiences"] == ["kubernetes"] and rules[DEX]["jwksUri"] == f"{DEX}/keys"
    assert rules[K8S]["audiences"] == ["kagent"]
    assert "jwksUri" in rules[K8S] or "jwks" in rules[K8S]


def test_request_authentication_outputs_claim_header():
    ra = one("base-apps/kagent/request-authentication.yaml", "RequestAuthentication", "kagent-callers")
    out = {r["issuer"]: r["outputClaimToHeaders"] for r in ra["spec"]["jwtRules"]}
    assert out[DEX] == [{"header": "x-user-id", "claim": "email"}]
    assert out[K8S] == [{"header": "x-user-id", "claim": "sub"}]
    assert all(r["forwardOriginalToken"] is True for r in ra["spec"]["jwtRules"])


def test_authorization_policy_targets_the_waypoint():
    ap = one("base-apps/kagent/authorization-policy.yaml", "AuthorizationPolicy", "kagent-controller")
    assert ap["spec"]["targetRefs"][0]["name"] == "kagent-waypoint"
    assert ap["spec"]["action"] == "ALLOW"


def test_issuer_discovery_is_readable_by_istiod():
    crb = one("base-apps/cluster-rbac/service-account-issuer-discovery.yaml", "ClusterRoleBinding",
              "service-account-issuer-discovery-unauthenticated")
    assert crb["roleRef"]["name"] == "system:service-account-issuer-discovery"
    assert {s["name"] for s in crb["subjects"]} == {"system:unauthenticated"}


def test_controller_auth_mode_is_insecure_on_purpose():
    text = (REPO / "base-apps/kagent.yaml").read_text()
    assert "mode: insecure" in text and "the mesh is the identity authority" in text
