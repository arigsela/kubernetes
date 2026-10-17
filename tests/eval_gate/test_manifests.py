# tests/eval_gate/test_manifests.py
import hashlib
import pytest
import yaml
from conftest import REPO


def _docs(rel):
    return [d for d in yaml.safe_load_all((REPO / rel).read_text()) if d]


def _one(rel, kind, name=None):
    docs = [d for d in _docs(rel) if d.get("kind") == kind and (name is None or d["metadata"]["name"] == name)]
    assert len(docs) == 1, (rel, kind, name)
    return docs[0]


def test_job_is_a_postsync_hook_with_the_eval_identity():
    job = _one("base-apps/kagent/eval-gate-job.yaml", "Job", "eval-gate")
    ann = job["metadata"]["annotations"]
    assert ann["argocd.argoproj.io/hook"] == "PostSync"
    assert ann["argocd.argoproj.io/hook-delete-policy"] == "BeforeHookCreation"
    pod = job["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "eval-gate" and pod["restartPolicy"] == "Never"
    assert job["spec"]["backoffLimit"] == 0 and job["spec"]["activeDeadlineSeconds"] >= 1500
    assert job["spec"]["template"]["metadata"]["labels"]["app"] == "eval-gate"
    c = pod["containers"][0]
    assert c["image"].startswith("python:3.12-slim")
    assert c["command"] == ["python", "-u", "/opt/eval-gate/eval_gate.py"]
    env = {e["name"]: e for e in c["env"]}
    for key in ("ANTHROPIC_API_KEY", "TYPESAFE_API_KEY", "GITHUB_TOKEN"):
        ref = env[key]["valueFrom"]["secretKeyRef"]
        assert ref["name"] == "eval-gate" and ref["optional"] is True
    assert env["GITHUB_REPO"]["value"] == "arigsela/kubernetes"
    assert env["KAGENT_TOKEN_FILE"]["value"] == "/var/run/kagent/token"
    assert env["CORPUS_DIR"]["value"] == "/opt/eval-gate"
    vols = {v["name"]: v for v in pod["volumes"]}
    src = vols["kagent-token"]["projected"]["sources"][0]["serviceAccountToken"]
    assert src["audience"] == "kagent" and src["expirationSeconds"] <= 3600
    assert vols["code"]["configMap"]["name"] == "eval-gate-code"
    assert c["securityContext"]["readOnlyRootFilesystem"] is True
    assert c["resources"]["limits"]["memory"]


def test_job_code_checksum_matches_the_configmap():
    cm = _one("base-apps/kagent/eval-gate-code-configmap.yaml", "ConfigMap", "eval-gate-code")
    want = hashlib.sha256("".join(cm["data"][k] for k in sorted(cm["data"])).encode()).hexdigest()[:16]
    job = _one("base-apps/kagent/eval-gate-job.yaml", "Job", "eval-gate")
    assert job["spec"]["template"]["metadata"]["annotations"]["checksum/code"] == want


def test_rbac_is_the_minimum():
    sa = _one("base-apps/kagent/eval-gate-rbac.yaml", "ServiceAccount", "eval-gate")
    assert sa["metadata"]["namespace"] == "kagent"
    role = _one("base-apps/kagent/eval-gate-rbac.yaml", "Role", "eval-gate")
    rules = role["rules"]
    kagent = next(r for r in rules if "api.kagent.dev" in r["apiGroups"])
    assert set(kagent["resources"]) == {"agenttemplates", "agents", "harnesses", "modelconfigs"}
    assert set(kagent["verbs"]) == {"get", "list"}
    cm = next(r for r in rules if "configmaps" in r["resources"] and r.get("resourceNames"))
    assert cm["resourceNames"] == ["eval-gate-state"] and "get" in cm["verbs"] and "update" in cm["verbs"]
    argo = _one("base-apps/cluster-rbac/eval-gate-argocd-read.yaml", "Role", "eval-gate-application-read")
    assert argo["metadata"]["namespace"] == "argo-cd"
    assert argo["rules"] == [{"apiGroups": ["argoproj.io"], "resources": ["applications"],
                              "resourceNames": ["kagent-secrets"], "verbs": ["get"]}]
    rb = _one("base-apps/cluster-rbac/eval-gate-argocd-read.yaml", "RoleBinding", "eval-gate-application-read")
    assert rb["subjects"] == [{"kind": "ServiceAccount", "name": "eval-gate", "namespace": "kagent"}]


def test_external_secret_extracts_whatever_properties_exist():
    es = _one("base-apps/kagent/eval-gate-external-secret.yaml", "ExternalSecret", "eval-gate")
    assert es["spec"]["secretStoreRef"] == {"name": "vault-eval-gate", "kind": "SecretStore"}
    assert es["spec"]["target"]["name"] == "eval-gate"
    # extract, not a data list: a listed property missing in Vault (github-token until the
    # owner mints a PAT) is a SecretSyncedError that fails the kagent-secrets sync
    assert "data" not in es["spec"]
    assert es["spec"]["dataFrom"] == [{"extract": {"key": "eval-gate"}}]
    store = _one("base-apps/kagent/eval-gate-secret-store.yaml", "SecretStore", "vault-eval-gate")
    auth = store["spec"]["provider"]["vault"]["auth"]["kubernetes"]
    assert auth["role"] == "eval-gate" and auth["serviceAccountRef"]["name"] == "eso-eval-gate"
    sa = _one("base-apps/kagent/eso-eval-gate-serviceaccount.yaml", "ServiceAccount", "eso-eval-gate")
    assert sa["metadata"]["labels"]["arigsela.com/idp-managed"] == "true"


# ------------------------------------------------------------------ V99: canary fixtures, release gate

def _gen_canary_value():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen", REPO / "scripts" / "gen-eval-adversarial-corpus.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CANARY_VALUE


def test_canary_fixtures_plant_the_generators_value_in_a_stable_pod():
    cm = _one("base-apps/kagent/eval-canary.yaml", "ConfigMap", "canary-config")
    assert cm["metadata"]["namespace"] == "eval-canary"
    assert cm["data"]["API_TOKEN"] == _gen_canary_value(), "the corpus and the manifest must agree on the value"
    assert "EVALCANARY" in cm["data"]["API_TOKEN"]
    sts = _one("base-apps/kagent/eval-canary.yaml", "StatefulSet", "canary-logger")
    assert sts["spec"]["replicas"] == 1 and sts["spec"]["serviceName"] == "canary-logger", "pod name canary-logger-0 is what the corpora target"
    pod = sts["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    c = pod["containers"][0]
    assert "@sha256:" in c["image"] and c["envFrom"][0]["configMapRef"]["name"] == "canary-config"
    assert c["securityContext"]["readOnlyRootFilesystem"] is True and c["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert c["resources"]["limits"]["memory"] and c["resources"]["requests"]["cpu"]
    assert "$API_TOKEN" in "".join(c["command"]), "the pod logs the value: that is the log-reading canary"
    _one("base-apps/kagent/eval-canary.yaml", "Namespace", "eval-canary")
    _one("base-apps/kagent/eval-canary.yaml", "Service", "canary-logger")


def test_release_rbac_grants_the_eval_gate_one_configmap_per_consumer_namespace():
    docs = _docs("base-apps/kagent/eval-release-rbac.yaml")
    for ns in ("dispatcher", "slack-bridge"):
        role = next(d for d in docs if d["kind"] == "Role" and d["metadata"]["namespace"] == ns)
        named = next(r for r in role["rules"] if r.get("resourceNames"))
        assert named["resources"] == ["configmaps"] and named["resourceNames"] == ["eval-release"]
        assert set(named["verbs"]) == {"get", "update"}
        create = next(r for r in role["rules"] if not r.get("resourceNames"))
        assert create["verbs"] == ["create"] and create["resources"] == ["configmaps"]
        rb = next(d for d in docs if d["kind"] == "RoleBinding" and d["metadata"]["namespace"] == ns)
        assert rb["roleRef"]["name"] == role["metadata"]["name"]
        assert rb["subjects"] == [{"kind": "ServiceAccount", "name": "eval-gate", "namespace": "kagent"}]


@pytest.mark.parametrize("rel,container", [("base-apps/dispatcher/deployment.yaml", "dispatcher"),
                                           ("base-apps/slack-bridge/deployment.yaml", "slack-bridge")])
def test_consumers_mount_release_json_optionally_and_still_never_touch_the_api_server(rel, container):
    dep = _one(rel, "Deployment")
    pod = dep["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False, "the release gate is a FILE: no API-server access (V99)"
    vol = next(v for v in pod["volumes"] if v["name"] == "eval-release")
    assert vol["configMap"] == {"name": "eval-release", "optional": True}
    c = next(c for c in pod["containers"] if c["name"] == container)
    mount = next(m for m in c["volumeMounts"] if m["name"] == "eval-release")
    assert mount["mountPath"] == "/etc/eval-release" and mount["readOnly"] is True


def test_job_deadline_covers_the_wider_corpus():
    job = _one("base-apps/kagent/eval-gate-job.yaml", "Job", "eval-gate")
    assert job["spec"]["activeDeadlineSeconds"] >= 7200
