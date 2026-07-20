# New Application Golden-Path Template — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Backstage scaffolder template (`templates/new-app/`) that renders a complete `base-apps/<app>/` GitOps app and opens a PR to `arigsela/kubernetes` — correct-by-construction against every CI gate.

**Architecture:** `kind: Template` + Nunjucks `skeleton*/` dirs, delivered via `publish:github:pull-request`. A local **render-test harness** (Python + Jinja2 mimicking Backstage's `${{ }}` delimiters) renders the skeleton with sample inputs and runs the repo's own validators, proving the generated PR is green before any deploy. The template + skeleton live in the kubernetes repo (url `catalog.location`); a one-time backstage-image change registers the location.

**Tech Stack:** Backstage scaffolder (Nunjucks), Python 3.12 + Jinja2 + pytest (render harness), the repo's existing validators, GitHub Actions.

## Global Constraints

- **Skeleton templating syntax** (Backstage Nunjucks): variables `${{ values.x }}`; control flow `{% if values.x %}…{% endif %}`, `{% for k, v in values.map %}`. Stay within this common subset (also valid in Jinja2) so the render harness is a faithful proxy. Templated **file/dir names** use `${{ values.name }}` literally in the path.
- **The rendered output MUST pass every gate** in `.github/workflows/validate.yaml`: `yaml-lint` (block-style, 2-space indent), `kubernetes-validate` (kubeconform; `mkdocs.yml` skipped), `ingress-policy` (Ingress needs `nginx.ingress.kubernetes.io/whitelist-source-range`), `agent-docs-validate`, `techdocs-validate` (`gen-techdocs.py --check`), `catalog-refs-validate`.
- **Base the skeleton on real manifests** (do not invent shapes): `base-apps/dex/{deployment,service,ingress,secret-store,external-secret,configmap}.yaml`, `base-apps/dex.yaml` (Argo App with `directory.exclude: '{catalog-info.yaml,mkdocs.yml}'`), `templates/agent-docs/{catalog-info.yaml,docs.md,runbook.md}` (frontmatter contract), and `scripts/gen-techdocs.py`'s `MKDOCS_TEMPLATE`.
- **Publish syntax** (mirror the removed application template): `publish:github:pull-request`, `repoUrl: github.com?repo=kubernetes&owner=arigsela`, output `${{ steps.publish.output.remoteUrl }}`.
- **`docs/index.md` == `docs.md` and `docs/runbook.md` == `runbook.md`** byte-for-byte (rendered from identical skeleton content) so `gen-techdocs.py --check` passes.
- Commit trailers on every commit:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b`.

## File Structure

**kubernetes repo** (branch `backstage-new-app-template`):
- Create `templates/new-app/template.yaml` — the `kind: Template`.
- Create `templates/new-app/skeleton/…` — core files (templated names):
  `base-apps/${{ values.name }}.yaml`, and under `base-apps/${{ values.name }}/`:
  `deployments.yaml`, `services.yaml`, `catalog-info.yaml`, `docs.md`, `runbook.md`,
  `mkdocs.yml`, `docs/index.md`, `docs/runbook.md`.
- Create `templates/new-app/skeleton-ingress/base-apps/${{ values.name }}/nginx-ingress.yaml`.
- Create `templates/new-app/skeleton-secrets/base-apps/${{ values.name }}/{secret-store.yaml,external-secret.yaml}`.
- Create `templates/new-app/skeleton-config/base-apps/${{ values.name }}/configmap.yaml`.
- Create `scripts/render-new-app.py` — render harness (Jinja2, `${{ }}` delimiters, templated paths, conditional skeleton dirs).
- Create `tests/new-app-template/test_render_new_app.py` — pytest: render canonical samples → run validators → assert green.
- Modify `.github/workflows/validate.yaml` — add a `new-app-template-validate` job.

**backstage repo** (branch `new-app-template-location`, one-time):
- Modify `app-config.yaml` + `app-config.production.yaml` — add the url `catalog.location` for the template.

---

## Task 1: Render-test harness

The gate for everything else: render a skeleton dir (+ conditional dirs) with values, into an output tree, mirroring Backstage's `fetch:template` (templated paths + `${{ }}`/`{% if %}`).

**Files:**
- Create: `scripts/render-new-app.py`
- Create: `tests/new-app-template/test_render_new_app.py`

**Interfaces:**
- Produces: `render(skeleton_dirs: list[Path], values: dict, out_dir: Path) -> list[Path]` — renders each skeleton dir into `out_dir`, templating file/dir names and contents; returns written paths. CLI: `python3 scripts/render-new-app.py --template templates/new-app --values <json> --out <dir> [--ingress] [--secrets] [--config]`.

- [ ] **Step 1: Write the failing test**

Create `tests/new-app-template/test_render_new_app.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/arisela/git/kubernetes && python3 -m pytest tests/new-app-template/ -q`
Expected: FAIL — `render-new-app.py` and the skeleton don't exist yet.

- [ ] **Step 3: Write the render harness**

Create `scripts/render-new-app.py`:

```python
#!/usr/bin/env python3
"""Render the new-app scaffolder skeleton locally (proxy for Backstage's
fetch:template) so the output can be run through the repo's CI validators.

Backstage uses Nunjucks with variable delimiters `${{ }}`; Jinja2 configured the
same way renders the common subset we use (variables, {% if %}, {% for %}) and
templated file/dir NAMES. This is a faithful pre-deploy proxy; the authoritative
check remains Backstage's template-editor dry-run.
"""
import argparse
import json
import sys
from pathlib import Path

from jinja2 import Environment, StrictUndefined

_ENV = Environment(
    variable_start_string="${{",
    variable_end_string="}}",
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


def _render_str(text, values):
    return _ENV.from_string(text).render(values=values)


def render(skeleton_dirs, values, out_dir):
    """Render each skeleton dir into out_dir. Templates both file/dir names and
    file contents. Returns the list of written file paths."""
    written = []
    out_dir = Path(out_dir)
    for skel in skeleton_dirs:
        skel = Path(skel)
        for src in sorted(skel.rglob("*")):
            if src.is_dir():
                continue
            rel = src.relative_to(skel)
            # template each path segment (handles ${{ values.name }} in names)
            rel_rendered = Path(*[_render_str(part, values) for part in rel.parts])
            dst = out_dir / rel_rendered
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(_render_str(src.read_text(), values))
            written.append(dst)
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--template", required=True, help="templates/new-app dir")
    ap.add_argument("--values", required=True, help="JSON of parameter values")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ingress", action="store_true")
    ap.add_argument("--secrets", action="store_true")
    ap.add_argument("--config", action="store_true")
    args = ap.parse_args()
    tmpl = Path(args.template)
    dirs = [tmpl / "skeleton"]
    if args.ingress:
        dirs.append(tmpl / "skeleton-ingress")
    if args.secrets:
        dirs.append(tmpl / "skeleton-secrets")
    if args.config:
        dirs.append(tmpl / "skeleton-config")
    n = render(dirs, json.loads(args.values), Path(args.out))
    print(f"rendered {len(n)} files to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Create a minimal fixture skeleton to make Step-1's first test pass structurally**

(Real skeleton comes in Tasks 2–3; but the harness must be provably correct now. Add a throwaway fixture and a focused harness unit test.)

Create `tests/new-app-template/test_harness_unit.py`:

```python
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "render-new-app.py"
_spec = importlib.util.spec_from_file_location("render_new_app", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_templated_paths_and_content(tmp_path):
    skel = tmp_path / "skel"
    (skel / "base-apps" / "${{ values.name }}").mkdir(parents=True)
    (skel / "base-apps" / "${{ values.name }}.yaml").write_text("name: ${{ values.name }}\n")
    (skel / "base-apps" / "${{ values.name }}" / "f.yaml").write_text(
        "{% if values.on %}on: true{% endif %}\n")
    out = tmp_path / "out"
    mod.render([skel], {"name": "foo", "on": True}, out)
    assert (out / "base-apps" / "foo.yaml").read_text() == "name: foo\n"
    assert (out / "base-apps" / "foo" / "f.yaml").read_text() == "on: true\n"
```

- [ ] **Step 5: Run the harness unit test to verify it passes**

Run: `cd /Users/arisela/git/kubernetes && pip install jinja2 >/dev/null 2>&1; python3 -m pytest tests/new-app-template/test_harness_unit.py -q`
Expected: PASS. (The full `test_render_new_app.py` still fails until Tasks 2–3 create the real skeleton — that's expected.)

- [ ] **Step 6: Commit**

```bash
cd /Users/arisela/git/kubernetes
git add scripts/render-new-app.py tests/new-app-template/
git commit -m "$(printf 'feat(new-app): render-test harness for the scaffolder skeleton\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b')"
```

---

## Task 2: template.yaml + core skeleton

**Files:** create `templates/new-app/template.yaml` + the 9 core `skeleton/` files.

**Interfaces:** Consumes the render harness (Task 1) for testing. Produces the template + core skeleton that Task 3 extends and Task 4 registers.

- [ ] **Step 1: Write `templates/new-app/template.yaml`**

```yaml
apiVersion: scaffolder.backstage.io/v1beta3
kind: Template
metadata:
  name: new-application
  title: New Application
  description: Scaffold a complete base-apps GitOps app and open a PR.
  tags: [recommended, gitops, base-apps]
spec:
  type: service
  owner: group:default/platform
  parameters:
    - title: Identity
      required: [name, description, system]
      properties:
        name:
          title: Name
          type: string
          description: kebab-case app name (also the dir + namespace default)
          pattern: '^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'
        description:
          title: Description
          type: string
        system:
          title: System
          type: string
          ui:field: EntityPicker
          ui:options:
            catalogFilter:
              kind: System
        owner:
          title: Owner
          type: string
          default: group:default/platform
          ui:field: EntityPicker
          ui:options:
            catalogFilter:
              kind: [Group, User]
        tags:
          title: Tags
          type: array
          items: { type: string }
    - title: Workload
      required: [image, containerPort]
      properties:
        image: { title: Container image, type: string }
        containerPort: { title: Container port, type: integer, default: 8080 }
        replicas: { title: Replicas, type: integer, default: 1 }
        namespace:
          title: Namespace
          type: string
          description: defaults to the app name
    - title: Networking & Config
      properties:
        exposeIngress: { title: Expose via nginx Ingress, type: boolean, default: false }
        host:
          title: Ingress host label (<host>.arigsela.com)
          type: string
        needsConfig: { title: Add a ConfigMap (envFrom), type: boolean, default: false }
        needsSecrets: { title: Add Vault SecretStore + ExternalSecret, type: boolean, default: false }
    - title: Resources
      properties:
        cpuRequest: { title: CPU request, type: string, default: 100m }
        cpuLimit: { title: CPU limit, type: string, default: 500m }
        memRequest: { title: Memory request, type: string, default: 128Mi }
        memLimit: { title: Memory limit, type: string, default: 256Mi }
  steps:
    - id: fetch-core
      name: Render core
      action: fetch:template
      input:
        url: ./skeleton
        values: &vals
          name: ${{ parameters.name }}
          description: ${{ parameters.description }}
          image: ${{ parameters.image }}
          containerPort: ${{ parameters.containerPort }}
          replicas: ${{ parameters.replicas }}
          namespace: ${{ parameters.namespace if parameters.namespace else parameters.name }}
          system: ${{ parameters.system }}
          owner: ${{ parameters.owner }}
          tags: ${{ parameters.tags }}
          exposeIngress: ${{ parameters.exposeIngress }}
          host: ${{ parameters.host }}
          needsConfig: ${{ parameters.needsConfig }}
          needsSecrets: ${{ parameters.needsSecrets }}
          configData: ${{ parameters.configData if parameters.configData else {} }}
          cpuRequest: ${{ parameters.cpuRequest }}
          cpuLimit: ${{ parameters.cpuLimit }}
          memRequest: ${{ parameters.memRequest }}
          memLimit: ${{ parameters.memLimit }}
    - id: fetch-ingress
      name: Render ingress
      if: ${{ parameters.exposeIngress }}
      action: fetch:template
      input: { url: ./skeleton-ingress, values: *vals }
    - id: fetch-secrets
      name: Render secrets
      if: ${{ parameters.needsSecrets }}
      action: fetch:template
      input: { url: ./skeleton-secrets, values: *vals }
    - id: fetch-config
      name: Render config
      if: ${{ parameters.needsConfig }}
      action: fetch:template
      input: { url: ./skeleton-config, values: *vals }
    - id: publish
      name: Open PR
      action: publish:github:pull-request
      input:
        repoUrl: github.com?repo=kubernetes&owner=arigsela
        branchName: new-app/${{ parameters.name }}
        title: "feat(${{ parameters.name }}): onboard new application"
        description: |
          Scaffolded via the New Application template.
          System: ${{ parameters.system }} · Owner: ${{ parameters.owner }}
  output:
    links:
      - title: Pull request
        url: ${{ steps.publish.output.remoteUrl }}
```

> Note: `configData` isn't a top-level parameter above (a key/value map field needs a custom field or a JSON textarea). For v1, add a `configData` object property under "Networking & Config" as `type: object` with `additionalProperties: {type: string}`; the render harness already accepts it. Keep the `values` mapping as written.

- [ ] **Step 2: Write the 9 core skeleton files**

Create each under `templates/new-app/skeleton/`, templatizing the real manifests (Global Constraints list the sources). Key ones:

`base-apps/${{ values.name }}.yaml` (from `base-apps/dex.yaml`):

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  finalizers:
    - resources-finalizer.argocd.argoproj.io
  name: ${{ values.name }}
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/${{ values.name }}
    directory:
      exclude: '{catalog-info.yaml,mkdocs.yml}'
  destination:
    server: https://kubernetes.default.svc
    namespace: ${{ values.namespace }}
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

`base-apps/${{ values.name }}/deployments.yaml` (generic; `envFrom` only if `needsConfig`; tcpSocket probes to avoid assuming a health path):

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${{ values.name }}
  namespace: ${{ values.namespace }}
  labels:
    app: ${{ values.name }}
spec:
  replicas: ${{ values.replicas }}
  selector:
    matchLabels:
      app: ${{ values.name }}
  template:
    metadata:
      labels:
        app: ${{ values.name }}
    spec:
      containers:
        - name: ${{ values.name }}
          image: ${{ values.image }}
          ports:
            - name: http
              containerPort: ${{ values.containerPort }}
{% if values.needsConfig %}          envFrom:
            - configMapRef:
                name: ${{ values.name }}-config
{% endif %}          resources:
            requests:
              cpu: ${{ values.cpuRequest }}
              memory: ${{ values.memRequest }}
            limits:
              cpu: ${{ values.cpuLimit }}
              memory: ${{ values.memLimit }}
          readinessProbe:
            tcpSocket:
              port: ${{ values.containerPort }}
            initialDelaySeconds: 5
            periodSeconds: 10
```

`base-apps/${{ values.name }}/services.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: ${{ values.name }}
  namespace: ${{ values.namespace }}
  labels:
    app: ${{ values.name }}
spec:
  type: ClusterIP
  selector:
    app: ${{ values.name }}
  ports:
    - name: http
      port: 80
      targetPort: ${{ values.containerPort }}
```

`base-apps/${{ values.name }}/catalog-info.yaml` (from `templates/agent-docs/catalog-info.yaml`; `dependsOn` vault only if `needsSecrets`):

```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: ${{ values.name }}
  namespace: ${{ values.namespace }}
  annotations:
    agent-docs/path: docs.md
    backstage.io/techdocs-ref: dir:.
    backstage.io/kubernetes-label-selector: 'app=${{ values.name }}'
    backstage.io/kubernetes-namespace: ${{ values.namespace }}
  tags: ${{ values.tags }}
spec:
  type: service
  lifecycle: experimental
  owner: ${{ values.owner }}
  system: ${{ values.system }}
{% if values.needsSecrets %}  dependsOn:
    - resource:vault/vault
{% endif %}
```

`base-apps/${{ values.name }}/docs.md` and `docs/index.md` (identical; from `templates/agent-docs/docs.md`):

```markdown
---
type: "Kubernetes App Guide"
title: "${{ values.name }}"
description: "${{ values.description }}"
app: ${{ values.name }}
catalog_entity: ${{ values.name }}
kind: docs
namespace: ${{ values.namespace }}
last_reviewed: 2026-07-20
status: current
tags: ${{ values.tags }}
sources:
  - base-apps/${{ values.name }}/deployments.yaml
---

# ${{ values.name }}

## What it is
${{ values.description }}

## Architecture & data flow
_Fill in: how requests flow, dependencies, config sources._

## Where config lives
- Manifests: `base-apps/${{ values.name }}/`
```

`base-apps/${{ values.name }}/runbook.md` and `docs/runbook.md` (identical; from `templates/agent-docs/runbook.md`): same frontmatter with `type: "Kubernetes App Runbook"`, `title: "${{ values.name }} — Runbook"`, `kind: runbook`, then starter `## Failure modes` / `## Checks` / `## Fixes` sections.

`base-apps/${{ values.name }}/mkdocs.yml` (exactly `gen-techdocs.py`'s `MKDOCS_TEMPLATE` with `site_name: ${{ values.name }}`):

```yaml
site_name: ${{ values.name }}
docs_dir: docs
nav:
  - Overview: index.md
  - Runbook: runbook.md
plugins:
  - techdocs-core
```

- [ ] **Step 3: Render + validate against sample values**

Run:
```bash
cd /Users/arisela/git/kubernetes
pip install jinja2 >/dev/null 2>&1
python3 -m pytest tests/new-app-template/ -q
```
Expected: PASS — including `test_rendered_output_passes_repo_validators` (yamllint, gen-techdocs --check, agent-docs, ingress whitelist all green on the rendered sample). Fix skeleton whitespace/indent until green (block-style YAML; watch the `{% if %}` indentation so the rendered YAML stays 2-space correct).

- [ ] **Step 4: Confirm no un-rendered markers + catalog refs resolve**

Run:
```bash
cd /Users/arisela/git/kubernetes
python3 scripts/render-new-app.py --template templates/new-app --values '{"name":"sample-app","description":"x","image":"nginx","containerPort":8080,"replicas":1,"namespace":"sample-app","system":"default/platform-tooling","owner":"group:default/platform","tags":["nginx"],"needsConfig":false,"needsSecrets":true,"cpuRequest":"100m","cpuLimit":"500m","memRequest":"128Mi","memLimit":"256Mi"}' --out /tmp/na --secrets
grep -RnE '\$\{\{|\{%' /tmp/na && echo "UNRENDERED!" || echo "clean"
```
Expected: `clean`.

- [ ] **Step 5: Commit**

```bash
cd /Users/arisela/git/kubernetes
git add templates/new-app/template.yaml templates/new-app/skeleton/
git commit -m "$(printf 'feat(new-app): template.yaml + core skeleton\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b')"
```

---

## Task 3: Conditional skeletons (ingress / secrets / config)

**Files:** `templates/new-app/skeleton-ingress/…`, `skeleton-secrets/…`, `skeleton-config/…`.

- [ ] **Step 1: `skeleton-ingress/base-apps/${{ values.name }}/nginx-ingress.yaml`** (from `base-apps/dex/ingress.yaml`; MUST keep `whitelist-source-range`):

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${{ values.name }}
  namespace: ${{ values.namespace }}
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/whitelist-source-range: "10.0.0.0/8"
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - ${{ values.host }}.arigsela.com
      secretName: ${{ values.name }}-tls
  rules:
    - host: ${{ values.host }}.arigsela.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: ${{ values.name }}
                port:
                  number: 80
```

> The default `whitelist-source-range` is `10.0.0.0/8` (internal). Note in the PR body that the author should widen it to their home IPs if the app must be reachable externally (see `base-apps/dex/ingress.yaml` for the standard allowlist).

- [ ] **Step 2: `skeleton-secrets/base-apps/${{ values.name }}/secret-store.yaml`** (from dex; role = namespace):

```yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: ${{ values.namespace }}
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "${{ values.namespace }}"
          serviceAccountRef:
            name: "default"
```

- [ ] **Step 3: `skeleton-secrets/base-apps/${{ values.name }}/external-secret.yaml`** (generic; `dataFrom` extract pulls all properties under the Vault key `<name>`):

```yaml
# Seed the Vault key k8s-secrets/${{ values.name }} with your app's secrets;
# every property under it is synced into the ${{ values.name }}-secrets Secret.
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: ${{ values.name }}-secrets
  namespace: ${{ values.namespace }}
spec:
  refreshInterval: "1h"
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: ${{ values.name }}-secrets
    creationPolicy: Owner
  dataFrom:
    - extract:
        key: ${{ values.name }}
```

- [ ] **Step 4: `skeleton-config/base-apps/${{ values.name }}/configmap.yaml`** (from `configData` map):

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${{ values.name }}-config
  namespace: ${{ values.namespace }}
data:
{% for k, v in values.configData.items() %}  ${{ k }}: "${{ v }}"
{% endfor %}
```

- [ ] **Step 5: Full render + validate (all toggles on)**

Run: `cd /Users/arisela/git/kubernetes && python3 -m pytest tests/new-app-template/ -q`
Expected: PASS — `test_render_produces_expected_files` (all 12 files) and `test_rendered_output_passes_repo_validators` green with ingress+secrets+config. Also run kubeconform on the rendered manifests:
```bash
python3 scripts/render-new-app.py --template templates/new-app --values '{"name":"sample-app","description":"x","image":"nginx:1.27","containerPort":8080,"replicas":1,"namespace":"sample-app","system":"default/platform-tooling","owner":"group:default/platform","tags":["nginx"],"needsConfig":true,"configData":{"LOG_LEVEL":"info"},"needsSecrets":true,"exposeIngress":true,"host":"sample-app","cpuRequest":"100m","cpuLimit":"500m","memRequest":"128Mi","memLimit":"256Mi"}' --out /tmp/na2 --ingress --secrets --config
find /tmp/na2/base-apps -name '*.yaml' ! -name 'mkdocs.yml' ! -name 'catalog-info.yaml' | xargs kubeconform -strict -ignore-missing-schemas -summary
```
Expected: kubeconform reports the Deployment/Service/Ingress/ConfigMap/SecretStore/ExternalSecret Valid or Skipped, 0 Errors.

- [ ] **Step 6: Commit**

```bash
cd /Users/arisela/git/kubernetes
git add templates/new-app/skeleton-ingress templates/new-app/skeleton-secrets templates/new-app/skeleton-config
git commit -m "$(printf 'feat(new-app): conditional ingress/secrets/config skeletons\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b')"
```

---

## Task 4: CI job + backstage location + delivery

- [ ] **Step 1: Add the `new-app-template-validate` CI job** to `.github/workflows/validate.yaml` (after `techdocs-validate`):

```yaml
  new-app-template-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: |
          pip install pyyaml==6.0.2 pytest==8.3.3 jinja2==3.1.4
          curl -sL https://github.com/yannh/kubeconform/releases/download/v0.6.7/kubeconform-linux-amd64.tar.gz | tar xz
          sudo mv kubeconform /usr/local/bin/
          pip install yamllint==1.35.1
      - name: Render the template and run the repo validators on the output
        run: python -m pytest tests/new-app-template/ -q
```

- [ ] **Step 2: Commit (kubernetes) + run the full validator sweep**

```bash
cd /Users/arisela/git/kubernetes
git add .github/workflows/validate.yaml
git commit -m "$(printf 'ci(new-app): render + validate the scaffolder skeleton\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b')"
python3 -m pytest tests/new-app-template/ tests/techdocs/ tests/catalog-refs/ -q
```

- [ ] **Step 3: Register the location in backstage config** (branch `new-app-template-location` in `/Users/arisela/git/backstage`, from `homepage` — the deployed source of truth; verify with `git log`):

In `app-config.yaml` (after the api-entities url location) and `app-config.production.yaml` (matching spot), add:

```yaml
    # New Application golden-path scaffolder template (arigsela/kubernetes).
    - type: url
      target: https://github.com/arigsela/kubernetes/blob/main/templates/new-app/template.yaml
      rules:
        - allow: [Template]
```

Validate: `yarn backstage-cli config:check --lax` → no errors. Commit.

**Delivery (controller):** build **v1.4.11** from the backstage branch (`yarn build:all` → `docker buildx build --platform linux/amd64 --push --provenance=false` → confirm `linux/amd64`); open the backstage PR; open a kubernetes PR bumping `deployments.yaml` to v1.4.11.

---

## Task 5: Post-deploy verification (manual)

- [ ] **Step 1:** After deploy, open Backstage **Create** → "New Application" → fill the form → **Dry Run** (template editor) with all toggles on. Confirm the rendered files match the local harness output (no errors).
- [ ] **Step 2:** Do a real run → it opens a PR on `arigsela/kubernetes`. Confirm that PR's CI (all gates) is **green with no manual fixes**.
- [ ] **Step 3:** Merge the test PR → Argo creates the app; Backstage ingests the Component (catalog-info + Docs tab). Then clean up the test app if desired.

## Self-Review

- **Spec coverage:** form/steps → Task 2 template.yaml; core files → Task 2; conditionals → Task 3; correct-by-construction proof → Task 1 harness + Task 4 CI job; location + delivery → Task 4; dry-run/PR/merge verification → Task 5. All spec sections covered.
- **Placeholder scan:** complete code for the harness, template.yaml, and every skeleton file; `configData` field caveat called out explicitly (not a TODO).
- **Consistency:** `render(skeleton_dirs, values, out_dir)` signature used identically in harness + both tests; `docs/index.md`==`docs.md` and `docs/runbook.md`==`runbook.md` enforced by the test; `${{ }}`/`{% %}` subset used throughout; `repoUrl`/`remoteUrl` mirror the real application template.
