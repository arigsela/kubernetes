# tests/eval_gate/test_manifests.py
import hashlib
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
