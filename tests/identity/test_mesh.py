# tests/identity/test_mesh.py
"""Callers as principals (V91, V92): the mesh verifies every caller's token and sets the
identity header; the policy files say so and name exactly the two issuers."""
from conftest import load, one, REPO
import json

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
    assert kagent["istio.io/ingress-use-waypoint"] == "true", "the ingress Gateway must route through the waypoint too"


def test_waypoint_logs_every_request():
    tel = one("base-apps/kagent/telemetry-waypoint.yaml", "Telemetry", "waypoint-access-logs")
    assert tel["spec"]["targetRefs"][0]["name"] == "kagent-waypoint"
    assert tel["spec"]["accessLogging"][0]["providers"] == [{"name": "envoy"}]


def test_waypoint_exists_for_services():
    wp = one("base-apps/kagent/waypoint.yaml", "Gateway", "kagent-waypoint")
    assert wp["spec"]["gatewayClassName"] == "istio-waypoint"
    # "all": pod-addressed traffic from an enrolled namespace must traverse the policy too,
    # or a pod in dispatcher/backstage could curl the controller's pod IP with any X-User-Id
    assert wp["metadata"]["labels"]["istio.io/waypoint-for"] == "all"
    assert wp["spec"]["listeners"][0]["protocol"] == "HBONE"


def test_request_authentication_names_exactly_the_two_issuers():
    ra = one("base-apps/kagent/request-authentication.yaml", "RequestAuthentication", "kagent-callers")
    assert ra["spec"]["targetRefs"] == [{"group": "gateway.networking.k8s.io", "kind": "Gateway",
                                        "name": "kagent-waypoint"}]
    rules = {r["issuer"]: r for r in ra["spec"]["jwtRules"]}
    assert set(rules) == {DEX, K8S}
    assert rules[DEX]["audiences"] == ["kubernetes"] and rules[DEX]["jwksUri"] == f"{DEX}/keys"
    assert rules[K8S]["audiences"] == ["kagent"]
    # inline on purpose: istiod cannot verify the API server's certificate (see the manifest)
    keys = json.loads(rules[K8S]["jwks"])["keys"]
    assert keys and all(k.get("kty") == "RSA" and k.get("kid") for k in keys)


def test_request_authentication_outputs_claim_header():
    ra = one("base-apps/kagent/request-authentication.yaml", "RequestAuthentication", "kagent-callers")
    out = {r["issuer"]: r["outputClaimToHeaders"] for r in ra["spec"]["jwtRules"]}
    assert out[DEX] == [{"header": "x-user-id", "claim": "email"}]
    assert out[K8S] == [{"header": "x-user-id", "claim": "sub"}]
    # never forward: the human's Dex token is a kubectl credential, and the controller stores
    # the Authorization header on the session and re-sends it to the actor (review 2026-10-13)
    assert all(r["forwardOriginalToken"] is False for r in ra["spec"]["jwtRules"])


def _policy():
    return one("base-apps/kagent/authorization-policy.yaml", "AuthorizationPolicy", "kagent-controller")


def test_authorization_policy_is_allow_on_the_waypoint_with_no_match_all_rule():
    ap = _policy()
    assert ap["spec"]["targetRefs"][0]["name"] == "kagent-waypoint" and ap["spec"]["action"] == "ALLOW"
    assert {} not in ap["spec"]["rules"], "the observe-mode rule must be gone"


def test_user_paths_need_a_verified_principal():
    ap = _policy()
    user_rules = [r for r in ap["spec"]["rules"]
                  if any("/agents/*" in t["operation"].get("paths", []) for t in r.get("to", []))]
    assert user_rules, "a rule for /agents/* is required"
    allowed_principals = {"cluster.local/ns/kagent/sa/kagent-ui", "cluster.local/ns/kagent/sa/default"}
    for r in user_rules:
        src = r["from"][0]["source"]
        assert src.get("requestPrincipals") or set(src.get("principals", [])) <= allowed_principals, r
        assert r["to"][0]["operation"]["ports"] == ["8083"], "controller rules are scoped to its port"


def test_everything_that_is_not_the_controller_passes_the_waypoint():
    """The waypoint fronts every Service in kagent; the controller's own calls to the MCP
    servers were refused (403) when this rule was missing (2026-10-13)."""
    ap = _policy()
    assert any(r.get("to") and r["to"][0]["operation"].get("notPorts") == ["8083"] and "from" not in r
               for r in ap["spec"]["rules"])


def test_the_controller_may_call_itself():
    ap = _policy()
    assert any(r.get("from") and r["from"][0]["source"].get("principals") == ["cluster.local/ns/kagent/sa/kagent-controller"]
               for r in ap["spec"]["rules"])


def test_dex_rule_requires_email_claim():
    ap = _policy()
    dex = [r for r in ap["spec"]["rules"]
           if r.get("from") and r["from"][0]["source"].get("requestPrincipals") == ["https://dex.arigsela.com/*"]]
    assert dex and any(w["key"] == "request.auth.claims[email]" and w["values"] == ["*"] for w in dex[0]["when"])


def test_agent_name_header_only_from_the_workers():
    deny = one("base-apps/kagent/authorization-policy.yaml", "AuthorizationPolicy", "kagent-controller-deny-agent-header")
    assert deny["spec"]["action"] == "DENY"
    rule = deny["spec"]["rules"][0]
    assert rule["when"][0] == {"key": "request.headers[x-agent-name]", "values": ["*"]}
    assert set(rule["from"][0]["source"]["notPrincipals"]) == {"cluster.local/ns/kagent/sa/default",
                                                               "cluster.local/ns/kagent/sa/kagent-controller"}
    assert rule["to"][0]["operation"]["ports"] == ["8083"]


def test_backstage_exception_is_dated():
    text = (REPO / "base-apps/kagent/authorization-policy.yaml").read_text()
    if "ns/backstage/sa/default" in text:
        assert "EXCEPTION until" in text and "arigsela/backstage" in text


def test_no_strict_peer_authentication_in_kagent():
    """STRICT cut the Substrate runtime (ate-system, unenrolled) off from the controller
    (2026-10-13: "failed to save task state: connection reset"). The decision and the residual
    risk are in the policy header and SPEC; a STRICT object here reopens that incident."""
    import glob
    for f in glob.glob(str(REPO / "base-apps/kagent/*.yaml")):
        for d in load(f.replace(str(REPO) + "/", "")):
            assert d.get("kind") != "PeerAuthentication", f


def test_network_policy_limits_controller_ingress():
    np = one("base-apps/kagent/network-policy-controller.yaml", "NetworkPolicy", "kagent-controller-ingress")
    assert np["spec"]["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": "kagent", "app.kubernetes.io/component": "controller"}
    allowed = {p["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
               for rule in np["spec"]["ingress"] for p in rule["from"]}
    assert allowed == {"istio-ingress", "istio-system", "kagent", "dispatcher", "backstage", "ate-system",
                       "slack-bridge",   # the Slack bridge's own principal (§T.120, §V.98)
                       "hk-demo"}        # the hk demo page's own principal (§T.136)


def test_waypoint_route_refuses_user_id_query():
    route = one("base-apps/kagent/httproute-waypoint.yaml", "HTTPRoute", "kagent-controller-waypoint")
    assert route["spec"]["parentRefs"][0] == {"group": "", "kind": "Service", "name": "kagent-controller"}
    first = route["spec"]["rules"][0]
    assert first["matches"][0]["queryParams"] == [{"type": "RegularExpression", "name": "user_id", "value": ".*"}]
    assert first["backendRefs"][0]["name"] == "refused"      # a Service with no endpoints: 503, never the controller
    svc = one("base-apps/kagent/refused-service.yaml", "Service", "refused")
    assert "selector" not in svc["spec"], "no endpoints, ever: the route must resolve but nothing may answer"
    assert route["spec"]["rules"][-1]["backendRefs"][0]["name"] == "kagent-controller"


def test_issuer_discovery_is_readable_by_istiod():
    crb = one("base-apps/cluster-rbac/service-account-issuer-discovery.yaml", "ClusterRoleBinding",
              "service-account-issuer-discovery-unauthenticated")
    assert crb["roleRef"]["name"] == "system:service-account-issuer-discovery"
    assert {s["name"] for s in crb["subjects"]} == {"system:unauthenticated"}


def test_controller_auth_mode_is_insecure_on_purpose():
    text = (REPO / "base-apps/kagent.yaml").read_text()
    assert "mode: insecure" in text and "the mesh is the identity authority" in text


def test_backstage_presents_a_projected_token():
    """T110: the portal sends its own token; the exception rule for its principal is gone."""
    dep = one("base-apps/backstage/deployments.yaml", "Deployment", "backstage")
    pod = dep["spec"]["template"]["spec"]
    vol = next(v for v in pod["volumes"] if v["name"] == "kagent-token")
    src = vol["projected"]["sources"][0]["serviceAccountToken"]
    assert src["audience"] == "kagent" and src["expirationSeconds"] <= 3600 and src["path"] == "token"
    c = pod["containers"][0]
    env = {e["name"]: e.get("value") for e in c.get("env", [])}
    assert env["KAGENT_TOKEN_FILE"] == "/var/run/kagent/token"
    assert any(m["mountPath"] == "/var/run/kagent" and m["readOnly"] for m in c["volumeMounts"])
    text = (REPO / "base-apps/kagent/authorization-policy.yaml").read_text()
    assert "ns/backstage/sa/default" not in text, "the Backstage exception rule must go with the token"
