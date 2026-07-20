import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "render-new-app.py"
_spec = importlib.util.spec_from_file_location("render_new_app", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

REPO = Path(__file__).resolve().parents[2]
SKEL = REPO / "templates" / "new-app"

SAMPLE = {
    "name": "sample-app",
    "description": "A sample scaffolded app.",
    "image": "nginx:1.27",
    "containerPort": 8080,
    "replicas": 1,
    "namespace": "sample-app",
    "system": "default/platform-tooling",
    "owner": "group:default/platform",
    "tags": ["nginx"],
    "exposeIngress": True,
    "host": "sample-app",
    "needsConfig": True,
    "configData": {"LOG_LEVEL": "info"},
    "needsSecrets": True,
    "cpuRequest": "100m", "cpuLimit": "500m",
    "memRequest": "128Mi", "memLimit": "256Mi",
}


def _render(tmp: Path, values: dict, ingress: bool, secrets: bool, config: bool) -> Path:
    dirs = [SKEL / "skeleton"]
    if ingress:
        dirs.append(SKEL / "skeleton-ingress")
    if secrets:
        dirs.append(SKEL / "skeleton-secrets")
    if config:
        dirs.append(SKEL / "skeleton-config")
    mod.render(dirs, values, tmp)
    return tmp


def test_render_produces_expected_files(tmp_path):
    _render(tmp_path, SAMPLE, True, True, True)
    app = tmp_path / "base-apps" / "sample-app"
    assert (tmp_path / "base-apps" / "sample-app.yaml").is_file()
    for f in ["deployments.yaml", "services.yaml", "catalog-info.yaml", "docs.md",
              "runbook.md", "mkdocs.yml", "docs/index.md", "docs/runbook.md",
              "nginx-ingress.yaml", "secret-store.yaml", "external-secret.yaml",
              "configmap.yaml"]:
        assert (app / f).is_file(), f"missing {f}"
    # techdocs copies must equal the canonical docs (gen-techdocs --check contract)
    assert (app / "docs" / "index.md").read_text() == (app / "docs.md").read_text()
    assert (app / "docs" / "runbook.md").read_text() == (app / "runbook.md").read_text()
    # no un-rendered template markers remain
    for p in app.rglob("*"):
        if p.is_file():
            assert "${{" not in p.read_text() and "{%" not in p.read_text(), f"unrendered: {p}"


def test_rendered_output_passes_repo_validators(tmp_path):
    # Render into a throwaway COPY of the repo so the real validators run against it.
    import shutil
    work = tmp_path / "repo"
    shutil.copytree(REPO, work, ignore=shutil.ignore_patterns(
        ".git", "node_modules", ".superpowers", "base-apps"), dirs_exist_ok=False)
    # Bring base-apps but only the taxonomy + a couple deps the sample refs need.
    (work / "base-apps").mkdir(exist_ok=True)
    shutil.copytree(REPO / "catalog", work / "catalog", dirs_exist_ok=True)
    for dep in ["vault"]:
        shutil.copytree(REPO / "base-apps" / dep, work / "base-apps" / dep, dirs_exist_ok=True)
    dirs = [SKEL / "skeleton", SKEL / "skeleton-ingress", SKEL / "skeleton-secrets", SKEL / "skeleton-config"]
    mod.render(dirs, SAMPLE, work)
    app = work / "base-apps" / "sample-app"

    # yamllint (block style)
    r = subprocess.run(["yamllint", "-c", str(REPO / ".yamllint.yaml")] +
                       [str(p) for p in app.rglob("*.yaml")] + [str(work / "base-apps" / "sample-app.yaml")],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"yamllint:\n{r.stdout}\n{r.stderr}"
    # gen-techdocs --check (docs/ copies in sync)
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "gen-techdocs.py"),
                        "--repo-root", str(work), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, f"gen-techdocs:\n{r.stdout}"
    # agent-docs contract
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "validate-agent-docs.py"),
                        "--repo-root", str(work)], capture_output=True, text=True)
    assert r.returncode == 0, f"agent-docs:\n{r.stdout}"
    # ingress whitelist gate
    ing = (app / "nginx-ingress.yaml").read_text()
    assert "whitelist-source-range" in ing
