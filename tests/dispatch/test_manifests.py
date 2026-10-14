"""The Deployment restarts when the table or the code ConfigMap changes (Argo CD syncs
ConfigMaps, nothing else restarts a pod that read them at start): checksum annotations,
the same pattern as Grafana's checksum/alerting."""
from pathlib import Path
import hashlib
import json

import yaml

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "base-apps" / "dispatcher"


def _configmap_data(name: str) -> dict:
    doc = [d for d in yaml.safe_load_all((APP / name).read_text()) if d and d.get("kind") == "ConfigMap"][0]
    return doc["data"]


def checksum(name: str) -> str:
    return hashlib.sha256(json.dumps(_configmap_data(name), sort_keys=True).encode()).hexdigest()


def test_deployment_checksums_match_the_configmaps():
    deploy = [d for d in yaml.safe_load_all((APP / "deployment.yaml").read_text()) if d and d.get("kind") == "Deployment"][0]
    annotations = deploy["spec"]["template"]["metadata"].get("annotations") or {}
    for key, file in (("checksum/table", "dispatch-table.yaml"), ("checksum/code", "code-configmap.yaml")):
        want = checksum(file)
        assert annotations.get(key) == want, f"set {key}: {want} in base-apps/dispatcher/deployment.yaml"


def _one(file: str, kind: str) -> dict:
    docs = [d for d in yaml.safe_load_all((APP / file).read_text()) if d and d.get("kind") == kind]
    assert len(docs) == 1, (file, kind)
    return docs[0]


def test_deployment_runs_as_the_dispatcher_service_account_with_a_projected_token():
    pod = _one("deployment.yaml", "Deployment")["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "dispatcher"
    vol = next(v for v in pod["volumes"] if v["name"] == "kagent-token")
    src = vol["projected"]["sources"][0]["serviceAccountToken"]
    assert src["audience"] == "kagent" and src["expirationSeconds"] <= 3600 and src["path"] == "token"
    mount = next(m for m in pod["containers"][0]["volumeMounts"] if m["name"] == "kagent-token")
    assert mount["mountPath"] == "/var/run/kagent" and mount["readOnly"] is True
    env = {e["name"]: e.get("value") for e in pod["containers"][0]["env"]}
    assert "DISPATCH_USER" not in env
    sas = [d for d in yaml.safe_load_all((APP / "serviceaccount.yaml").read_text())
           if d and d["kind"] == "ServiceAccount"]
    sa = next(d for d in sas if d["metadata"]["name"] == "dispatcher")
    assert sa["metadata"]["namespace"] == "dispatcher" and sa["automountServiceAccountToken"] is False


def test_network_policy_admits_only_grafana_and_a_labelled_replay_pod():
    """Review finding I1 (2026-10-14): the webhook is a write-capable prompt once a row proposes;
    anything in the cluster could POST a forged alert. Grafana runs outside the mesh (logging is
    not ambient, SA default), so the gate is a NetworkPolicy on the pod, not an identity policy."""
    docs = [d for d in yaml.safe_load_all((APP / "network-policy.yaml").read_text()) if d]
    np = next(d for d in docs if d["kind"] == "NetworkPolicy")
    assert np["metadata"]["namespace"] == "dispatcher"
    assert np["spec"]["podSelector"] == {"matchLabels": {"app": "dispatcher"}}
    assert np["spec"]["policyTypes"] == ["Ingress"]
    rules = np["spec"]["ingress"]
    assert len(rules) == 3, "the webhook rule, the approvals-page rule (§T.119) and the decide rule (§T.120)"
    froms = rules[0]["from"]
    # Grafana's pod carries `app: grafana` (the chart's own label), not app.kubernetes.io/name;
    # the first policy used the wrong one and blocked Grafana (live, 2026-10-14).
    assert {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "logging"}},
            "podSelector": {"matchLabels": {"app": "grafana"}}} in froms
    assert {"podSelector": {"matchLabels": {"dispatcher/replay": "true"}}} in froms
    assert len(froms) == 2
    # 8080 for Grafana (outside the mesh, plaintext); 15008 for an in-mesh source (the replay
    # pod in this ambient namespace): ztunnel delivers it over HBONE on 15008 and the policy is
    # enforced before ztunnel unwraps it ("connection reset by peer" without it).
    assert rules[0]["ports"] == [{"port": 8080, "protocol": "TCP"}, {"port": 15008, "protocol": "TCP"}]


# ------------------------------------------------ the approvals page (§T.119, §V.97)

INGRESS = REPO / "base-apps/istio-ingress"
DEX = REPO / "base-apps/dex"


def _docs(file: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((APP / file).read_text()) if d]


def test_network_policy_second_rule_admits_the_gateway_to_the_sidecar_only():
    np = _one("network-policy.yaml", "NetworkPolicy")
    rule = np["spec"]["ingress"][1]
    assert rule["from"] == [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "istio-ingress"}}}]
    # 4180 is the sidecar; 15008 is HBONE (the gateway is in the mesh). Never 8080, never 8081.
    assert rule["ports"] == [{"port": 4180, "protocol": "TCP"}, {"port": 15008, "protocol": "TCP"}]


def test_sidecar_is_oauth2_proxy_on_dex_with_a_localhost_upstream_and_an_emails_file():
    pod = _one("deployment.yaml", "Deployment")["spec"]["template"]["spec"]
    names = [c["name"] for c in pod["containers"]]
    assert names == ["dispatcher", "oauth2-proxy"]
    proxy = pod["containers"][1]
    assert proxy["image"].startswith("quay.io/oauth2-proxy/oauth2-proxy:") and "@sha256:" in proxy["image"]
    args = proxy["args"]
    for flag in ("--http-address=0.0.0.0:4180", "--upstream=http://127.0.0.1:8081/", "--provider=oidc",
                 "--oidc-issuer-url=https://dex.arigsela.com", "--client-id=dispatcher",
                 "--client-secret-file=/etc/oauth2-proxy/secrets/client-secret",
                 "--cookie-secret-file=/etc/oauth2-proxy/secrets/cookie-secret",
                 "--authenticated-emails-file=/etc/oauth2-proxy/secrets/allowed-emails",
                 "--redirect-url=https://dispatcher.arigsela.com/oauth2/callback",
                 "--code-challenge-method=S256", "--cookie-name=_dispatcher", "--cookie-secure=true",
                 "--cookie-samesite=lax"):
        assert flag in args, flag
    assert [p["containerPort"] for p in proxy["ports"]] == [4180]
    mount = next(m for m in proxy["volumeMounts"] if m["name"] == "oauth2")
    assert mount["mountPath"] == "/etc/oauth2-proxy/secrets" and mount["readOnly"] is True
    vol = next(v for v in pod["volumes"] if v["name"] == "oauth2")
    assert vol["secret"]["secretName"] == "dispatcher-web-oauth2"
    env = {e["name"]: e.get("value") for e in pod["containers"][0]["env"]}
    assert env["APPROVALS_PORT"] == "8081" and env["APPROVALS_URL"] == "https://dispatcher.arigsela.com/approvals"
    ports = [p["containerPort"] for p in pod["containers"][0]["ports"]]
    assert ports == [8080, 8082], "8081 is never a container port: localhost only; 8082 is the bridge's door (§T.120)"


def test_service_exposes_the_webhook_and_the_sidecar_never_8081_or_8082():
    svcs = {d["metadata"]["name"]: d for d in _docs("service.yaml") if d["kind"] == "Service"}
    assert set(svcs) == {"dispatcher", "dispatcher-decide"}
    assert sorted((p["name"], p["port"]) for p in svcs["dispatcher"]["spec"]["ports"]) == [("http", 8080), ("web", 4180)]
    # The decide door (§T.120) has a Service of its own so the public route can never reach it.
    assert [(p["name"], p["port"]) for p in svcs["dispatcher-decide"]["spec"]["ports"]] == [("decide", 8082)]


def test_oauth2_secret_comes_from_vault_through_its_own_role():
    es = next(d for d in _docs("external-secret.yaml") if d["kind"] == "ExternalSecret")
    assert es["spec"]["target"]["name"] == "dispatcher-web-oauth2"
    assert es["spec"]["secretStoreRef"] == {"name": "vault-dispatcher-web", "kind": "SecretStore"}
    assert {(d["secretKey"], d["remoteRef"]["key"], d["remoteRef"]["property"]) for d in es["spec"]["data"]} == {
        ("client-secret", "dispatcher-web", "oauth2-client-secret"),
        ("cookie-secret", "dispatcher-web", "oauth2-cookie-secret"),
        ("allowed-emails", "dispatcher-web", "allowed-emails")}
    store = _one("secret-store.yaml", "SecretStore")
    auth = store["spec"]["provider"]["vault"]["auth"]["kubernetes"]
    assert auth["role"] == "dispatcher-web" and auth["serviceAccountRef"] == {"name": "eso-dispatcher-web"}
    sas = {d["metadata"]["name"] for d in _docs("serviceaccount.yaml") if d["kind"] == "ServiceAccount"}
    assert sas == {"dispatcher", "eso-dispatcher-web"}


def test_host_route_certificate_grant_listener_and_allow_list():
    route = _one("httproute.yaml", "HTTPRoute")
    assert route["spec"]["hostnames"] == ["dispatcher.arigsela.com"]
    assert route["spec"]["parentRefs"][0]["sectionName"] == "https-dispatcher"
    assert route["spec"]["rules"][0]["backendRefs"] == [{"name": "dispatcher", "port": 4180}]
    cert = _one("certificate.yaml", "Certificate")
    assert cert["spec"]["secretName"] == "dispatcher-web-tls" and cert["spec"]["dnsNames"] == ["dispatcher.arigsela.com"]
    grant = _one("reference-grant.yaml", "ReferenceGrant")
    assert grant["spec"]["to"][0]["name"] == "dispatcher-web-tls"
    gw = next(d for d in yaml.safe_load_all((INGRESS / "gateway.yaml").read_text()) if d and d["kind"] == "Gateway")
    listener = next(l for l in gw["spec"]["listeners"] if l["name"] == "https-dispatcher")
    assert listener["hostname"] == "dispatcher.arigsela.com"
    assert listener["tls"]["certificateRefs"] == [{"kind": "Secret", "name": "dispatcher-web-tls", "namespace": "dispatcher"}]
    ap = next(d for d in yaml.safe_load_all((INGRESS / "authorizationpolicy.yaml").read_text())
              if d and d["kind"] == "AuthorizationPolicy" and d["metadata"]["name"] == "gateway-allow")
    hosts = [h for r in ap["spec"]["rules"] for t in r.get("to", []) for h in t["operation"].get("hosts", [])]
    assert "dispatcher.arigsela.com" in hosts and "dispatcher.arigsela.com:*" in hosts


def test_dex_has_the_dispatcher_client_and_its_secret():
    cm = next(d for d in yaml.safe_load_all((DEX / "configmap.yaml").read_text()) if d and d["kind"] == "ConfigMap")
    cfg = yaml.safe_load(cm["data"]["config.yaml"])
    client = next(c for c in cfg["staticClients"] if c["id"] == "dispatcher")
    assert client["secretEnv"] == "DISPATCHER_CLIENT_SECRET"
    assert client["redirectURIs"] == ["https://dispatcher.arigsela.com/oauth2/callback"]
    es = next(d for d in yaml.safe_load_all((DEX / "external-secret.yaml").read_text()) if d and d["kind"] == "ExternalSecret")
    assert {"secretKey": "DISPATCHER_CLIENT_SECRET", "remoteRef": {"key": "dex", "property": "dispatcher-client-secret"}} \
        in es["spec"]["data"]


def test_sidecar_does_not_depend_on_dex_at_startup_and_never_skips_auth():
    """Review 2026-10-14 finding 4: a sidecar that cannot start takes the webhook out of the Service.
    Explicit endpoints replace OIDC discovery; and no flag may widen who gets in."""
    pod = _one("deployment.yaml", "Deployment")["spec"]["template"]["spec"]
    args = pod["containers"][1]["args"]
    for flag in ("--skip-oidc-discovery", "--login-url=https://dex.arigsela.com/auth",
                 "--redeem-url=https://dex.arigsela.com/token", "--oidc-jwks-url=https://dex.arigsela.com/keys"):
        assert flag in args, flag
    for bad in ("--skip-auth", "--email-domain", "--skip-jwt-bearer", "--allowed-group"):
        assert not any(a.startswith(bad) for a in args), bad
