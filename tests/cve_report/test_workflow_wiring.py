"""Guards on the DAG wiring around the aggregate step.

One image scan failing must not cost the whole week's report. On 2026-09-27 and
2026-10-04 the ollama/ollama:0.20.5 scan hit Trivy's timeout. The report task
used a plain `dependencies: [scan]`, which needs every scan to succeed, so Argo
omitted it and nothing reached S3, Backstage or Slack.
"""
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / "base-apps" / "argo-workflow-tasks" / "image-scan.yaml"


def _workflow_template():
    docs = [d for d in yaml.safe_load_all(WORKFLOW.read_text()) if d]
    return next(d for d in docs if d["kind"] == "WorkflowTemplate")


def _template(name):
    return next(t for t in _workflow_template()["spec"]["templates"] if t["name"] == name)


def _dag_task(name):
    return next(t for t in _template("main")["dag"]["tasks"] if t["name"] == name)


def test_report_runs_however_the_scans_ended():
    report = _dag_task("report")
    assert "dependencies" not in report, \
        "a plain dependency on scan needs every scan to succeed"
    for result in ("scan.Succeeded", "scan.Failed", "scan.Errored"):
        assert result in report.get("depends", ""), \
            f"report must still run when the scan group ends as {result}"


def test_dag_does_not_mix_depends_and_dependencies():
    # Argo rejects the whole template if one DAG uses both ("cannot use both
    # 'depends' and 'dependencies' in the same DAG template"), and CI does not
    # run `argo lint`, so nothing else would catch it before Argo CD syncs.
    tasks = _template("main")["dag"]["tasks"]
    assert not [t["name"] for t in tasks if "dependencies" in t]


def test_report_receives_the_discovered_image_list():
    report = _dag_task("report")
    params = {p["name"]: p["value"]
              for p in report.get("arguments", {}).get("parameters", [])}
    assert "{{tasks.discover.outputs.result}}" in params.values(), \
        "without the discovered list, a scan that wrote no report is invisible"
    assert "--expected" in _template("aggregate")["script"]["source"]
