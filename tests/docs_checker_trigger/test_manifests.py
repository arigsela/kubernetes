# tests/docs_checker_trigger/test_manifests.py
"""The weekly docs-checker trigger's manifests (§T.132, V102): schedule and limits, the minimum
RBAC, a memory ConfigMap Argo CD never reverts, the generated code ConfigMap and its checksum,
and the dismissals file the agent reads."""
import hashlib
import subprocess
import sys

import yaml

from conftest import REPO, load_script

TRIGGER = "base-apps/kagent/docs-checker-trigger.yaml"
CODE = "base-apps/kagent/docs-checker-trigger-code-configmap.yaml"
GEN = REPO / "scripts" / "gen-docs-checker-trigger-configmap.py"


def _docs(rel):
    return [d for d in yaml.safe_load_all((REPO / rel).read_text()) if d]


def _one(rel, kind, name=None):
    docs = [d for d in _docs(rel) if d.get("kind") == kind and (name is None or d["metadata"]["name"] == name)]
    assert len(docs) == 1, (rel, kind, name)
    return docs[0]


def _pod(cron):
    return cron["spec"]["jobTemplate"]["spec"]["template"]


def test_cronjob_runs_mondays_1400_utc_once_with_bounded_time():
    cron = _one(TRIGGER, "CronJob", "docs-checker-trigger")
    assert cron["metadata"]["namespace"] == "kagent"
    spec = cron["spec"]
    assert spec["schedule"] == "0 14 * * 1" and spec["timeZone"] == "Etc/UTC"
    assert spec["concurrencyPolicy"] == "Forbid"
    assert spec["startingDeadlineSeconds"] == 3600
    job = spec["jobTemplate"]["spec"]
    assert job["activeDeadlineSeconds"] == 1800 and job["backoffLimit"] == 1
    pod = _pod(cron)["spec"]
    assert pod["serviceAccountName"] == "docs-checker-trigger" and pod["restartPolicy"] == "Never"


def test_cronjob_container_identity_run_id_and_timeout():
    cron = _one(TRIGGER, "CronJob", "docs-checker-trigger")
    pod = _pod(cron)["spec"]
    c = pod["containers"][0]
    assert c["image"].startswith("python:3.12-slim")
    assert c["command"] == ["python", "-u", "/opt/docs-checker-trigger/docs_checker_trigger.py"]
    env = {e["name"]: e for e in c["env"]}
    # The run id is the Job's name: a retried pod of the same Job shares it, the next week's does not.
    assert env["RUN_ID"]["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.labels['batch.kubernetes.io/job-name']"
    assert env["KAGENT_TOKEN_FILE"]["value"] == "/var/run/kagent/token"
    assert env["KAGENT_A2A_BASE"]["value"] == "http://kagent-controller.kagent.svc.cluster.local:8083"
    assert env["N8N_WEBHOOK_BASE"]["value"] == "http://n8n.n8n.svc.cluster.local:5678/webhook"
    # The trigger gives up on the agent with time left to alert before the Job's deadline kills it
    # (a pod killed by activeDeadlineSeconds posts nothing). docs-checker's own run timeout is 15 min.
    timeout = float(env["RUN_TIMEOUT"]["value"])
    assert 900 < timeout <= _one(TRIGGER, "CronJob")["spec"]["jobTemplate"]["spec"]["activeDeadlineSeconds"] - 300
    vols = {v["name"]: v for v in pod["volumes"]}
    src = vols["kagent-token"]["projected"]["sources"][0]["serviceAccountToken"]
    assert src["audience"] == "kagent" and src["expirationSeconds"] <= 3600
    assert vols["code"]["configMap"]["name"] == "docs-checker-trigger-code"
    assert c["securityContext"]["readOnlyRootFilesystem"] is True
    assert c["securityContext"]["allowPrivilegeEscalation"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert c["resources"]["limits"]["memory"]


def test_rbac_is_get_and_update_on_the_memory_configmap_only():
    sa = _one(TRIGGER, "ServiceAccount", "docs-checker-trigger")
    assert sa["metadata"]["namespace"] == "kagent"
    role = _one(TRIGGER, "Role", "docs-checker-trigger")
    assert role["metadata"]["namespace"] == "kagent"
    assert role["rules"] == [{"apiGroups": [""], "resources": ["configmaps"],
                              "resourceNames": ["docs-checker-memory"], "verbs": ["get", "update"]}]
    rb = _one(TRIGGER, "RoleBinding", "docs-checker-trigger")
    assert rb["roleRef"] == {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "docs-checker-trigger"}
    assert rb["subjects"] == [{"kind": "ServiceAccount", "name": "docs-checker-trigger", "namespace": "kagent"}]
    # Nothing else in the file grants anything.
    assert {d["kind"] for d in _docs(TRIGGER)} == {"CronJob", "ServiceAccount", "Role", "RoleBinding", "ConfigMap"}


def test_memory_configmap_starts_empty_and_git_never_owns_its_data():
    """Git declares no data: Argo CD (ServerSideApply) owns only the fields git sets, so selfHeal
    never reverts what the trigger writes; an absent memory.json is the null snapshot (start fresh)."""
    cm = _one(TRIGGER, "ConfigMap", "docs-checker-memory")
    assert cm["metadata"]["namespace"] == "kagent"
    assert "data" not in cm and "binaryData" not in cm
    app = _one("base-apps/kagent-secrets.yaml", "Application", "kagent-secrets")
    assert "ServerSideApply=true" in app["spec"]["syncPolicy"]["syncOptions"]
    assert app["spec"]["source"]["path"] == "base-apps/kagent"


def test_trigger_names_match_the_script_defaults():
    tr = load_script("docs_checker_trigger", "scripts/docs_checker_trigger.py")
    assert tr.MEMORY_CONFIGMAP == "docs-checker-memory" and tr.NAMESPACE == "kagent"
    assert tr.AGENT == "kagent/docs-checker"
    _one("base-apps/kagent/agents/docs-checker.yaml", "Agent", "docs-checker")


def test_configmap_checksum_is_current():
    cm = _one(CODE, "ConfigMap", "docs-checker-trigger-code")
    want = hashlib.sha256("".join(cm["data"][k] for k in sorted(cm["data"])).encode()).hexdigest()[:16]
    cron = _one(TRIGGER, "CronJob", "docs-checker-trigger")
    assert _pod(cron)["metadata"]["annotations"]["checksum/code"] == want


def test_code_configmap_carries_the_scripts_verbatim():
    cm = _one(CODE, "ConfigMap", "docs-checker-trigger-code")
    assert cm["metadata"]["namespace"] == "kagent"
    assert set(cm["data"]) == {"docs_checker_trigger.py", "kagent_a2a.py"}
    assert cm["data"]["docs_checker_trigger.py"] == (REPO / "scripts/docs_checker_trigger.py").read_text()
    assert cm["data"]["kagent_a2a.py"] == (REPO / "scripts/kagent_a2a.py").read_text()


def test_generator_check_passes_on_the_committed_output():
    r = subprocess.run([sys.executable, str(GEN), "--repo-root", str(REPO), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_generator_check_fails_on_drift(tmp_path):
    for rel in ("scripts/docs_checker_trigger.py", "scripts/kagent_a2a.py", TRIGGER, CODE):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text((REPO / rel).read_text())
    (tmp_path / "scripts/docs_checker_trigger.py").write_text((REPO / "scripts/docs_checker_trigger.py").read_text() + "# drift\n")
    r = subprocess.run([sys.executable, str(GEN), "--repo-root", str(tmp_path), "--check"], capture_output=True, text=True)
    assert r.returncode == 1 and CODE in r.stderr


def test_dismissals_file_is_an_empty_valid_list_in_the_format_docs_checker_reads():
    """docs-checker's dismissals.py: a mapping with `version: 1` and a `dismissed` list (an
    entry is {doc, claim, reason, date, evidence_hash | expires}); anything else is an error the
    report lists and nothing is applied."""
    text = (REPO / "docs/docs-checker-dismissed.yaml").read_text()
    data = yaml.safe_load(text)
    assert data == {"version": 1, "dismissed": []}
    for key in ("doc", "claim", "reason", "date", "evidence_hash", "expires"):
        assert key in text, f"the how-to comment names {key}"
