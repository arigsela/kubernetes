# tests/bug_investigator_trigger/test_manifests.py
"""The trigger's manifests: schedule and limits, the one credential (a Taskboard-only installation
token), the mounts the package needs, the generated code ConfigMap and its checksum, and the
alert rule and dispatch row that share the trigger's expression."""
import json
import subprocess
import sys

import yaml

from conftest import REPO, load_script

t = load_script("bug_investigator_trigger", "scripts/bug_investigator_trigger.py")
gen = load_script("gen_bug_investigator_trigger_configmap", "scripts/gen-bug-investigator-trigger-configmap.py")

TRIGGER = "base-apps/kagent/bug-investigator-trigger.yaml"
CODE = "base-apps/kagent/bug-investigator-trigger-code-configmap.yaml"
GEN = REPO / "scripts" / "gen-bug-investigator-trigger-configmap.py"


def _docs(rel):
    return [d for d in yaml.safe_load_all((REPO / rel).read_text()) if d]


def _one(kind, name=None, rel=TRIGGER):
    docs = [d for d in _docs(rel) if d.get("kind") == kind and (name is None or d["metadata"]["name"] == name)]
    assert len(docs) == 1, (rel, kind, name)
    return docs[0]


def _pod():
    return _one("CronJob")["spec"]["jobTemplate"]["spec"]["template"]


def _env():
    return {e["name"]: e for e in _pod()["spec"]["containers"][0]["env"]}


def test_cronjob_runs_every_two_minutes_one_at_a_time_without_retries():
    cron = _one("CronJob", "bug-investigator-trigger")
    assert cron["metadata"]["namespace"] == "kagent"
    spec = cron["spec"]
    assert spec["schedule"] == "*/2 * * * *" and spec["concurrencyPolicy"] == "Forbid"
    job = spec["jobTemplate"]["spec"]
    assert job["backoffLimit"] == 0
    # Both A2A calls fit in the Job's deadline with time to log.
    env = _env()
    budget = float(env["AGENT_TIMEOUT"]["value"]) + float(env["FIX_TIMEOUT"]["value"])
    assert budget + 120 <= job["activeDeadlineSeconds"]
    pod = _pod()["spec"]
    assert pod["serviceAccountName"] == "bug-investigator-trigger" and pod["restartPolicy"] == "Never"
    assert pod["securityContext"]["runAsNonRoot"] is True


def test_container_runs_the_tested_script_on_the_full_python_image_pinned():
    c = _pod()["spec"]["containers"][0]
    image = c["image"]
    assert image.startswith("python:3.12@sha256:") and "slim" not in image   # git is in the full image
    assert c["command"][-1] == "/opt/bug-investigator-trigger/bug_investigator_trigger.py"
    assert c["securityContext"]["readOnlyRootFilesystem"] is True
    env = _env()
    assert env["KAGENT_A2A_BASE"]["value"] == "http://kagent-controller.kagent.svc.cluster.local:8083"
    assert env["KAGENT_TOKEN_FILE"]["value"] == "/var/run/kagent/token"
    assert env["GH_TOKEN_FILE"]["value"] == "/var/run/github/token"
    assert env["PROMETHEUS_URL"]["value"] == "http://prometheus.logging.svc.cluster.local:9090"
    assert env["LOKI_URL"]["value"] == "http://loki.logging.svc.cluster.local:3100"
    assert env["NAMESPACE"]["value"] == "taskboard" and env["PYTHONPATH"]["value"] == "/core/src"
    # Everything git and gh write lands on the emptyDir.
    for name in ("HOME", "GH_CONFIG_DIR", "XDG_CACHE_HOME", "WORK_DIR"):
        assert env[name]["value"].startswith("/work"), name


def test_mounts_follow_the_core_contract_and_the_two_tokens():
    pod = _pod()["spec"]
    mounts = {m["mountPath"]: m for m in pod["containers"][0]["volumeMounts"]}
    vols = {v["name"]: v for v in pod["volumes"]}
    assert mounts["/core/src/bug_investigator"]["name"] == "core"
    assert mounts["/core/prompt.md"]["subPath"] == "prompt.md"
    assert mounts["/core/findings.schema.json"]["subPath"] == "findings.schema.json"
    assert vols["core"]["configMap"]["name"] == "bug-investigator-core"
    assert vols["code"]["configMap"]["name"] == "bug-investigator-trigger-code"
    proj = vols["kagent-token"]["projected"]["sources"][0]["serviceAccountToken"]
    assert proj["audience"] == "kagent" and proj["path"] == "token"
    assert vols["github-token"]["secret"]["secretName"] == "bug-investigator-trigger-github"
    assert mounts["/var/run/github"]["readOnly"] is True
    assert "emptyDir" in vols["work"]


def test_the_only_api_access_is_get_and_update_on_its_memory_configmap():
    """§T.148: no automounted token; one Role, get/update on ConfigMap bug-investigator-memory only,
    bound to this service account; no ClusterRole. The memory ConfigMap carries no data in git."""
    sa = _one("ServiceAccount", "bug-investigator-trigger")
    assert sa["automountServiceAccountToken"] is False
    docs = _docs(TRIGGER)
    assert not [d for d in docs if d["kind"] in ("ClusterRole", "ClusterRoleBinding")]
    roles = [d for d in docs if d["kind"] == "Role"]
    assert len(roles) == 1 and roles[0]["rules"] == [
        {"apiGroups": [""], "resources": ["configmaps"], "resourceNames": ["bug-investigator-memory"],
         "verbs": ["get", "update"]}]
    rb = _one("RoleBinding", "bug-investigator-trigger")
    assert rb["roleRef"]["name"] == roles[0]["metadata"]["name"]
    assert rb["subjects"] == [{"kind": "ServiceAccount", "name": "bug-investigator-trigger", "namespace": "kagent"}]
    cm = _one("ConfigMap", "bug-investigator-memory")
    assert "data" not in cm


def test_the_github_token_is_taskboard_only_and_narrow():
    g = _one("GithubAccessToken", "bug-investigator-trigger")
    spec = g["spec"]
    # arigsela-implementer (docs/platform/implementer.md): IDs are not secrets.
    assert spec["appID"] == "5316560" and spec["installID"] == "171383761"
    assert spec["repositories"] == ["taskboard"]
    assert spec["permissions"] == {"issues": "write", "contents": "read", "pull_requests": "read", "metadata": "read"}
    ref = spec["auth"]["privateKey"]["secretRef"]
    assert ref == {"name": "implementer-github-app", "key": "GITHUB_APP_PRIVATE_KEY"}
    es = _one("ExternalSecret", "bug-investigator-trigger-github")
    gref = es["spec"]["dataFrom"][0]["sourceRef"]["generatorRef"]
    assert gref["kind"] == "GithubAccessToken" and gref["name"] == "bug-investigator-trigger"
    # Installation tokens live an hour: re-minted well inside that.
    assert es["spec"]["refreshInterval"] in ("30m", "20m", "15m")
    assert es["spec"]["target"]["name"] == "bug-investigator-trigger-github"


def test_the_private_key_secret_is_the_implementers():
    es = _one("ExternalSecret", "implementer-github-app", rel="base-apps/kagent/implementer-mcp-external-secret.yaml")
    assert es["spec"]["target"]["name"] == "implementer-github-app"


def test_code_configmap_is_generated_and_the_checksum_pinned():
    r = subprocess.run([sys.executable, str(GEN), "--repo-root", str(REPO), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    code = (REPO / CODE).read_text()
    cm = yaml.safe_load(code.split("---\n", 1)[1])
    assert set(cm["data"]) == {"bug_investigator_trigger.py", "kagent_a2a.py"}
    assert cm["data"]["bug_investigator_trigger.py"] == (REPO / "scripts/bug_investigator_trigger.py").read_text()
    assert _pod()["metadata"]["annotations"]["checksum/code"] == gen.checksum(code)


def _rule(title):
    cm = yaml.safe_load((REPO / "base-apps/logging/grafana-alerting.yaml").read_text())
    for key, text in cm["data"].items():
        doc = yaml.safe_load(text)
        for g in doc.get("groups") or []:
            for r in g.get("rules") or []:
                if r.get("title") == title:
                    return r
    raise AssertionError(f"no Grafana rule titled {title!r}")


def test_the_grafana_rule_uses_the_triggers_expression():
    rule = _rule(t.ALERT_TITLE)
    query = [d for d in rule["data"] if d["refId"] == "query"][0]
    assert query["datasourceUid"] == "PBFA97CFB590B2093"
    assert " ".join(query["model"]["expr"].split()) == " ".join(t.ALERT_EXPR.split())


def test_the_dispatcher_routes_the_alert_to_none_because_the_trigger_handles_it():
    cm = yaml.safe_load((REPO / "base-apps/dispatcher/dispatch-table.yaml").read_text())
    table = json.loads(cm["data"]["dispatch.json"])
    rows = [r for r in table["rules"] if r["match"].get("alertname") == t.ALERT_TITLE]
    assert len(rows) == 1 and rows[0]["route"] == "none"
    assert "bug-investigator trigger" in rows[0]["why"]
