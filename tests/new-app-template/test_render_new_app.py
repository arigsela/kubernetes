import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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


def _copy_tracked_repo(dest: Path) -> Path:
    """Copy every git-tracked file in REPO into dest, preserving relative
    paths, so the real repo validators can run against a disposable copy.

    Deliberately NOT a shutil.copytree over the live working tree: this repo
    carries large local-only artifacts that are never committed --
    terraform/roots/*/.terraform (a gitignored provider cache, ~1.7GB on a
    machine that has run `terraform init`) and docs/reference/claude-agents
    (a gitignored ~60MB reference clone) chief among them. `git ls-files`
    gives exactly what CI's checkout would see (~5MB, 600ish files): .git,
    node_modules, and .superpowers are excluded for free because none of
    them are tracked.
    """
    files = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    for rel in files:
        src = REPO / rel
        if not src.is_file():
            continue
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return dest


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
    # Render into a throwaway COPY of the FULL repo (every existing base-apps/
    # app included) so the real validators see exactly what they'd see in CI:
    # validate-agent-docs.py's index-coverage check requires a row for every
    # base-apps/<app> dir, not just a stub subset.
    work = tmp_path / "repo"
    work.mkdir()
    _copy_tracked_repo(work)

    dirs = [SKEL / "skeleton", SKEL / "skeleton-ingress", SKEL / "skeleton-secrets", SKEL / "skeleton-config"]
    written = mod.render(dirs, SAMPLE, work)
    app = work / "base-apps" / "sample-app"

    # Regenerate base-apps/index.md for the newly-scaffolded app (mirrors the
    # CI auto-sync workflow added in Task 4) and put sample-app in scope so
    # the contract validators actually check its generated docs.
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "gen-okf.py"),
                        "--repo-root", str(work)], capture_output=True, text=True)
    assert r.returncode == 0, f"gen-okf (write):\n{r.stdout}\n{r.stderr}"
    scope = work / "scripts" / "agent-docs-scope.txt"
    scope.write_text(scope.read_text().rstrip("\n") + "\nsample-app\n")

    # yamllint (block style)
    r = subprocess.run(["yamllint", "-c", str(REPO / ".yamllint.yaml")] +
                       [str(p) for p in app.rglob("*.yaml")] + [str(work / "base-apps" / "sample-app.yaml")],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"yamllint:\n{r.stdout}\n{r.stderr}"
    # OKF bundle in sync (base-apps/index.md matches every app's doc frontmatter)
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "gen-okf.py"),
                        "--repo-root", str(work), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, f"gen-okf --check:\n{r.stdout}\n{r.stderr}"
    # agent-docs contract (contract files, frontmatter, catalog_entity match, sources resolve)
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "validate-agent-docs.py"),
                        "--repo-root", str(work)], capture_output=True, text=True)
    assert r.returncode == 0, f"agent-docs:\n{r.stdout}\n{r.stderr}"
    # catalog entity references resolve (system/owner/dependsOn:vault)
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "validate-catalog-refs.py"),
                        "--repo-root", str(work)], capture_output=True, text=True)
    assert r.returncode == 0, f"catalog-refs:\n{r.stdout}\n{r.stderr}"
    # ingress whitelist gate
    ing = (app / "nginx-ingress.yaml").read_text()
    assert "whitelist-source-range" in ing

    # kubeconform (CI's kubernetes-validate job, .github/workflows/validate.yaml).
    # mkdocs.yml has no 'kind' (Backstage TechDocs config, not a k8s manifest)
    # and catalog-info.yaml is a Backstage entity, not a k8s manifest -- CI
    # drops/skips both, so exclude them here too. `written` already includes
    # base-apps/sample-app.yaml (the Argo CD Application manifest, rendered
    # from the skeleton's top-level file), so no need to add it separately.
    if shutil.which("kubeconform") is None:
        pytest.skip("kubeconform not installed")
    kc_files = [str(p) for p in written
                if p.suffix in (".yaml", ".yml")
                and p.name not in ("mkdocs.yml", "catalog-info.yaml")]
    assert str(work / "base-apps" / "sample-app.yaml") in kc_files
    r = subprocess.run(
        ["kubeconform",
         "-strict",
         "-ignore-missing-schemas",
         "-schema-location", "default",
         "-schema-location",
         "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/"
         "{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json",
         "-kubernetes-version", "1.33.0"] + kc_files,
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"kubeconform:\n{r.stdout}\n{r.stderr}"
