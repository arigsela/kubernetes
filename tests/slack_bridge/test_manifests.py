# tests/slack_bridge/test_manifests.py
"""The bridge's identity, the two doors it knocks on, and the n8n buttons (§T.120, §V.98)."""
from pathlib import Path
import hashlib
import json

import yaml

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "base-apps" / "slack-bridge"
DISPATCHER = REPO / "base-apps" / "dispatcher"
PRINCIPAL = "cluster.local/ns/slack-bridge/sa/slack-bridge"


def docs(path: Path, kind: str | None = None) -> list[dict]:
    out = [d for d in yaml.safe_load_all(path.read_text()) if d]
    return [d for d in out if kind is None or d.get("kind") == kind]


def one(path: Path, kind: str, name: str | None = None) -> dict:
    found = [d for d in docs(path, kind) if name is None or d["metadata"]["name"] == name]
    assert len(found) == 1, (path, kind, name)
    return found[0]


def test_deployment_checksum_matches_the_code_configmap():
    data = one(APP / "code-configmap.yaml", "ConfigMap")["data"]
    want = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    deploy = one(APP / "deployment.yaml", "Deployment")
    assert deploy["spec"]["template"]["metadata"]["annotations"]["checksum/code"] == want


def test_the_pod_is_the_bridge_principal_with_a_projected_token_and_no_api_token():
    pod = one(APP / "deployment.yaml", "Deployment")["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "slack-bridge" and pod["automountServiceAccountToken"] is False
    vol = next(v for v in pod["volumes"] if v["name"] == "kagent-token")
    src = vol["projected"]["sources"][0]["serviceAccountToken"]
    assert src["audience"] == "kagent" and src["expirationSeconds"] <= 3600 and src["path"] == "token"
    c = pod["containers"][0]
    assert next(m for m in c["volumeMounts"] if m["name"] == "kagent-token")["mountPath"] == "/var/run/kagent"
    env = {e["name"]: e for e in c["env"]}
    assert env["SLACK_BOT_TOKEN"]["valueFrom"]["secretKeyRef"] == {"name": "slack-bridge", "key": "bot-token"}
    assert env["SLACK_APP_TOKEN"]["valueFrom"]["secretKeyRef"] == {"name": "slack-bridge", "key": "app-token"}
    assert "KAGENT_TOKEN_FILE" not in env, "the projected token is the identity; never the dev X-User-Id path"
    assert env["DISPATCHER_DECIDE_BASE"]["value"].startswith("http://dispatcher-decide.dispatcher.svc.cluster.local:8082")
    assert env["OWNER_TIMEZONE"]["value"] == "America/New_York", "the owner's zone: 'last night at 11pm' must mean something"
    assert float(env["A2A_TIMEOUT"]["value"]) >= 900, "ask-hk's default; a deep investigation outlived 300s on 2026-10-15"
    sa = one(APP / "serviceaccount.yaml", "ServiceAccount", "slack-bridge")
    assert sa["metadata"]["namespace"] == "slack-bridge" and sa["automountServiceAccountToken"] is False
    assert one(APP / "deployment.yaml", "Deployment")["spec"]["replicas"] == 1, "Socket Mode: one connection"


def test_every_agent_the_bridge_may_pick_exists_in_git():
    env = {e["name"]: e.get("value") for e in one(APP / "deployment.yaml", "Deployment")["spec"]["template"]["spec"]["containers"][0]["env"]}
    agents = {a.strip() for a in env["AGENTS"].split(",")}
    in_git = {d["metadata"]["name"] for p in (REPO / "base-apps/kagent/agents").glob("*.yaml") for d in docs(p, "Agent")}
    assert agents <= in_git, agents - in_git
    assert env["DEFAULT_AGENT"] in agents


def test_namespace_is_ambient_and_admitted_to_the_controller():
    ns = one(APP / "namespace.yaml", "Namespace")
    assert ns["metadata"]["labels"]["istio.io/dataplane-mode"] == "ambient"
    np = one(REPO / "base-apps/kagent/network-policy-controller.yaml", "NetworkPolicy")
    froms = [f["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
             for rule in np["spec"]["ingress"] for f in rule["from"] if "namespaceSelector" in f]
    assert "slack-bridge" in froms


def test_secrets_come_from_a_dedicated_store_and_path():
    store = one(APP / "secret-store.yaml", "SecretStore")
    auth = store["spec"]["provider"]["vault"]["auth"]["kubernetes"]
    assert auth["role"] == "slack-bridge" and auth["serviceAccountRef"]["name"] == "eso-slack-bridge"
    es = one(APP / "external-secret.yaml", "ExternalSecret")
    assert es["spec"]["secretStoreRef"]["name"] == store["metadata"]["name"]
    assert {d["remoteRef"]["key"] for d in es["spec"]["data"]} == {"slack-bridge"}
    assert {d["secretKey"] for d in es["spec"]["data"]} == {"bot-token", "app-token"}


def test_application_excludes_the_docs_files():
    app = one(REPO / "base-apps/slack-bridge.yaml", "Application")
    assert app["spec"]["source"]["path"] == "base-apps/slack-bridge"
    assert app["spec"]["source"]["directory"]["exclude"] == "{catalog-info.yaml,mkdocs.yml}"


# ---------------------------------------------------------------- the dispatcher's door

def test_dispatcher_network_policy_admits_only_the_bridge_pod_on_8082():
    np = one(DISPATCHER / "network-policy.yaml", "NetworkPolicy")
    rules_8082 = [r for r in np["spec"]["ingress"] if any(p["port"] == 8082 for p in r["ports"])]
    assert len(rules_8082) == 1
    rule = rules_8082[0]
    assert rule["from"] == [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "slack-bridge"}},
                             "podSelector": {"matchLabels": {"app": "slack-bridge"}}}]
    assert {p["port"] for p in rule["ports"]} == {8082, 15008}


def test_dispatcher_authorization_policy_admits_only_the_bridge_principal_on_8082_and_keeps_the_rest_open():
    ap = one(DISPATCHER / "authorization-policy.yaml", "AuthorizationPolicy")
    assert ap["spec"]["selector"] == {"matchLabels": {"app": "dispatcher"}} and ap["spec"]["action"] == "ALLOW"
    rules = ap["spec"]["rules"]
    gated = [r for r in rules if any(op["operation"].get("ports") == ["8082"] for op in r["to"])]
    assert len(gated) == 1 and gated[0]["from"] == [{"source": {"principals": [PRINCIPAL]}}]
    rest = [r for r in rules if r is not gated[0]]
    assert rest == [{"to": [{"operation": {"notPorts": ["8082"]}}]}], "Grafana (plaintext) and the gateway must still pass"


def test_decide_port_has_its_own_service_and_the_public_one_never_exposes_it():
    public = one(DISPATCHER / "service.yaml", "Service", "dispatcher")
    assert {p["port"] for p in public["spec"]["ports"]} == {8080, 4180}
    decide = one(DISPATCHER / "service.yaml", "Service", "dispatcher-decide")
    assert [p["port"] for p in decide["spec"]["ports"]] == [8082]
    pod = one(DISPATCHER / "deployment.yaml", "Deployment")["spec"]["template"]["spec"]
    env = {e["name"]: e.get("value") for e in pod["containers"][0]["env"]}
    assert env["DECIDE_PORT"] == "8082"
    assert {p["containerPort"] for p in pod["containers"][0]["ports"]} >= {8080, 8082}
    routes = [d for p in DISPATCHER.glob("httproute*.yaml") for d in docs(p, "HTTPRoute")]
    for r in routes:
        for rule in r["spec"]["rules"]:
            for b in rule.get("backendRefs", []):
                assert b["name"] != "dispatcher-decide" and b.get("port") != 8082


# ---------------------------------------------------------------- the n8n buttons

def _workflow():
    cm = one(REPO / "base-apps/n8n/workflows-configmap.yaml", "ConfigMap")
    return cm["data"], json.loads(cm["data"]["agent-findings.json"])


def test_agent_findings_renders_buttons_only_with_a_decision():
    _, w = _workflow()
    js = next(n for n in w["nodes"] if n["name"] == "Format Finding")["parameters"]["jsCode"]
    assert "p.decision" in js and "'d:' + dec.ctx + ':' + dec.nonce" in js
    assert "action_id: 'approve'" in js and "action_id: 'reject'" in js
    assert "if (dec && dec.ctx && dec.nonce)" in js, "no decision, no buttons"
    assert "text, unfurl_links" in js, "text stays the fallback"


def test_n8n_checksum_covers_the_edited_workflow():
    data, _ = _workflow()
    want = hashlib.sha256("".join(data[k] for k in sorted(data)).encode()).hexdigest()[:16]
    deploy = one(REPO / "base-apps/n8n/deployments.yaml", "Deployment")
    assert deploy["spec"]["template"]["metadata"]["annotations"]["checksum/workflows"] == want
