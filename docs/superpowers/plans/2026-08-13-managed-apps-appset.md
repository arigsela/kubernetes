# `managed-apps` ApplicationSet Pilot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move 12 boilerplate Argo CD Applications from the `master-app` app-of-apps onto a single ApplicationSet driven by a git files generator, without changing the behaviour of any of them.

**Architecture:** A new `ApplicationSet` at `base-apps/managed-apps.yaml` (applied by the existing `master-app`) reads 12 config files from `appsets/managed-apps/*.yaml` via a git files generator. Each config supplies `name`, `sourcePath`, `namespace`, and `syncOptions`; everything else comes from a shared template. The 12 original `base-apps/<app>.yaml` Application files are deleted in the same commit that adds the ApplicationSet, after a separate earlier commit has stripped their finalizers so pruning cannot cascade into live AWS resources.

**Tech Stack:** Argo CD 3.5.x ApplicationSet (`argoproj.io/v1alpha1`), Go templating with `templatePatch`, Python 3.12 + pytest 8.3.3 + PyYAML 6.0.2 for validation, GitHub Actions.

**Design doc:** `docs/superpowers/specs/2026-08-13-managed-apps-appset-design.md`

## Global Constraints

- **No direct `kubectl` mutation.** All cluster changes go through Git (`CLAUDE.md`). `kubectl get` for verification is fine; `kubectl apply/edit/delete` is not.
- **Zero behaviour change.** Every generated Application must be spec-equivalent to the file it replaces. This is enforced by golden tests in Task 2.
- **Repo URL is `https://github.com/arigsela/kubernetes`, branch `main`** — verbatim, in both the generator and the template.
- **Config field is `sourcePath`, never `path`.** The git files generator injects a built-in `path` object; a config key named `path` collides with it silently.
- **`syncOptions` is required in every config file**, even when empty (`[]`). This permits `goTemplateOptions: ["missingkey=error"]`.
- **Pinned CI dependency versions:** `pyyaml==6.0.2`, `pytest==8.3.3`, Python `3.12` — match the existing jobs in `.github/workflows/validate.yaml`.
- **Task 3 must not land until the Task 1 cluster gate passes.** Deleting an Application that still carries `resources-finalizer.argocd.argoproj.io` cascades into Crossplane CRs with default `deletionPolicy: Delete`, destroying three real S3 buckets and their IAM users.

## The 12 applications

| Application | `sourcePath` | `namespace` | `syncOptions` |
|---|---|---|---|
| `agent-audit-aws-infrastructure` | `base-apps/agent-audit-aws-infrastructure` | `postgresql` | `CreateNamespace=true`, `ServerSideApply=true` |
| `argo-rollouts-config` | `base-apps/argo-rollouts` | `argo-rollouts` | *(empty)* |
| `argo-workflow-tasks` | `base-apps/argo-workflow-tasks` | `argo-workflows` | *(empty)* |
| `argo-workflows-aws-infrastructure` | `base-apps/argo-workflows-aws-infrastructure` | `argo-workflows` | *(empty)* |
| `argo-workflows-config` | `base-apps/argo-workflows` | `argo-workflows` | *(empty)* |
| `crossplane-aws-provider` | `base-apps/crossplane-aws-provider` | `crossplane-system` | *(empty)* |
| `crossplane-compositions` | `base-apps/crossplane-compositions` | `crossplane-system` | `CreateNamespace=false` |
| `crossplane-functions` | `base-apps/crossplane-functions` | `crossplane-system` | `CreateNamespace=false` |
| `crossplane-system` | `base-apps/crossplane-system` | `crossplane-system` | `CreateNamespace=true` |
| `ecr-auth` | `base-apps/ecr-auth` | `kube-system` | *(empty)* |
| `kyverno-policies` | `base-apps/kyverno-policies` | `kyverno` | *(empty)* |
| `loki-aws-infrastructure` | `base-apps/loki-aws-infrastructure` | `logging` | `CreateNamespace=true`, `ServerSideApply=true` |

Note that `argo-rollouts-config` and `argo-workflows-config` have a `sourcePath` that does not match their name. This is deliberate and must be preserved — naming them after their directories would produce `argo-rollouts` and `argo-workflows`, colliding with the existing Helm-chart Applications of those exact names.

## File Structure

| File | Responsibility |
|---|---|
| `base-apps/<app>.yaml` × 12 | **Modified in Task 1** (finalizer removed), **deleted in Task 3** |
| `tests/appset/golden/<app>.yaml` × 12 | Frozen copy of each Application's `spec`, captured in Task 1. The contract that "zero behaviour change" is measured against |
| `tests/appset/conftest.py` | Shared fixtures: repo root, config loading, template expansion |
| `tests/appset/test_managed_apps.py` | Schema, path, uniqueness, and golden-equivalence assertions |
| `tests/appset/test_disjoint.py` | Asserts config names do not overlap top-level `base-apps/*.yaml` Application names. Separate file because it is the one assertion that cannot pass until Task 3 |
| `appsets/managed-apps/<app>.yaml` × 12 | Per-application config consumed by the generator |
| `base-apps/managed-apps.yaml` | The ApplicationSet. Created in Task 3, not before |
| `.github/workflows/validate.yaml` | Gains an `appset-validate` job and an extended `PATHSPEC` |
| `docs/managed-apps-appset.md` | Runbook |
| `base-apps/README.md` | Corrected pattern description and app inventory |

---

### Task 1: Capture goldens and strip finalizers (Phase 0)

This is the safety gate. It changes nothing about how the 12 applications run — it only removes the finalizer that would turn a future prune into a cascading delete of live AWS resources.

**Files:**
- Create: `tests/appset/golden/<app>.yaml` × 12
- Modify: `base-apps/<app>.yaml` × 12 (remove the `finalizers` block)

**Interfaces:**
- Consumes: nothing
- Produces: `tests/appset/golden/<app>.yaml`, each a YAML document containing exactly the `spec` mapping of the corresponding Application, used by Task 2's `test_golden_equivalence`

- [ ] **Step 1: Create the golden directory and capture the 12 specs**

Run this from the repository root. It extracts only `spec` (finalizers live in `metadata`, so the goldens are unaffected by Step 3):

```bash
mkdir -p tests/appset/golden
python3 - <<'PY'
import pathlib, yaml

APPS = [
    "agent-audit-aws-infrastructure", "argo-rollouts-config", "argo-workflow-tasks",
    "argo-workflows-aws-infrastructure", "argo-workflows-config", "crossplane-aws-provider",
    "crossplane-compositions", "crossplane-functions", "crossplane-system",
    "ecr-auth", "kyverno-policies", "loki-aws-infrastructure",
]

for app in APPS:
    src = pathlib.Path("base-apps") / f"{app}.yaml"
    doc = yaml.safe_load(src.read_text())
    assert doc["kind"] == "Application", f"{src} is not an Application"
    assert doc["metadata"]["name"] == app, f"{src} name mismatch"
    dst = pathlib.Path("tests/appset/golden") / f"{app}.yaml"
    dst.write_text(yaml.safe_dump(doc["spec"], sort_keys=True, default_flow_style=False))
    print(f"wrote {dst}")
PY
```

Expected: 12 `wrote tests/appset/golden/...` lines, no assertion errors.

- [ ] **Step 2: Verify the goldens look right**

Run: `cat tests/appset/golden/loki-aws-infrastructure.yaml`

Expected — exactly this (keys sorted alphabetically by `sort_keys=True`):

```yaml
destination:
  namespace: logging
  server: https://kubernetes.default.svc
project: default
source:
  path: base-apps/loki-aws-infrastructure
  repoURL: https://github.com/arigsela/kubernetes
  targetRevision: main
syncPolicy:
  automated:
    prune: true
    selfHeal: true
  syncOptions:
  - CreateNamespace=true
  - ServerSideApply=true
```

Also run: `ls tests/appset/golden/ | wc -l` → expected `12`.

- [ ] **Step 3: Remove the finalizer block from all 12 Application files**

In each of the 12 `base-apps/<app>.yaml` files, delete these two lines and the `finalizers:` key above them:

```yaml
  finalizers:
    - resources-finalizer.argocd.argoproj.io
```

So `metadata` goes from:

```yaml
metadata:
  finalizers:
    - resources-finalizer.argocd.argoproj.io
  name: ecr-auth
  namespace: argo-cd
```

to:

```yaml
metadata:
  name: ecr-auth
  namespace: argo-cd
```

Leave every other line untouched, including comments. Two files (`crossplane-compositions.yaml`, `crossplane-functions.yaml`) have leading header comments above `apiVersion` — keep them.

- [ ] **Step 4: Verify no finalizers remain and nothing else changed**

Run:
```bash
grep -l "resources-finalizer" base-apps/agent-audit-aws-infrastructure.yaml \
  base-apps/argo-rollouts-config.yaml base-apps/argo-workflow-tasks.yaml \
  base-apps/argo-workflows-aws-infrastructure.yaml base-apps/argo-workflows-config.yaml \
  base-apps/crossplane-aws-provider.yaml base-apps/crossplane-compositions.yaml \
  base-apps/crossplane-functions.yaml base-apps/crossplane-system.yaml \
  base-apps/ecr-auth.yaml base-apps/kyverno-policies.yaml \
  base-apps/loki-aws-infrastructure.yaml
```
Expected: no output, exit status 1 (grep found nothing).

Run: `git diff --stat base-apps/`
Expected: exactly 12 files changed, 24 deletions, 0 insertions.

- [ ] **Step 5: Confirm the goldens still match the modified files**

The goldens captured `spec`; Step 3 only touched `metadata`. Confirm:

```bash
python3 - <<'PY'
import pathlib, yaml
for g in sorted(pathlib.Path("tests/appset/golden").glob("*.yaml")):
    app = g.stem
    live = yaml.safe_load((pathlib.Path("base-apps") / f"{app}.yaml").read_text())
    assert live["spec"] == yaml.safe_load(g.read_text()), f"{app}: spec drifted"
    assert "finalizers" not in live["metadata"], f"{app}: finalizer still present"
print("all 12 specs match goldens, no finalizers")
PY
```
Expected: `all 12 specs match goldens, no finalizers`

- [ ] **Step 6: Commit**

```bash
git add tests/appset/golden base-apps/
git commit -m "argo-cd: drop resources-finalizer from the 12 managed-apps candidates

Phase 0 of the managed-apps ApplicationSet pilot. Removing the finalizer
before the Application files are deleted means master-app's prune orphans
their resources instead of cascading into Crossplane CRs -- three of these
apps front real S3 buckets and IAM users with the default deletionPolicy:
Delete.

The goldens freeze each Application's spec so the ApplicationSet-generated
replacements can be proven equivalent.

See docs/superpowers/specs/2026-08-13-managed-apps-appset-design.md"
```

- [ ] **Step 7: CLUSTER GATE — merge, sync, and verify before starting Task 3**

Push and merge this commit to `main`, let `master-app` sync, then verify **read-only** that no finalizer remains on any of the 12 live Applications:

```bash
kubectl get application -n argo-cd -o \
  jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.finalizers}{"\n"}{end}' \
  | grep -E '^(agent-audit-aws-infrastructure|argo-rollouts-config|argo-workflow-tasks|argo-workflows-aws-infrastructure|argo-workflows-config|crossplane-aws-provider|crossplane-compositions|crossplane-functions|crossplane-system|ecr-auth|kyverno-policies|loki-aws-infrastructure)\b'
```

Expected: 12 lines, each with an empty second column. If any line shows `["resources-finalizer.argocd.argoproj.io"]`, **stop** — Task 3 must not proceed. Tasks 2 and 4 are safe to work on meanwhile; only Task 3 is gated.

---

### Task 2: Config files, validation tests, and CI wiring

Everything here is inert — no ApplicationSet exists yet, so nothing consumes the configs and nothing reaches the cluster. This task is safe to land independently of the Task 1 gate.

**Files:**
- Create: `appsets/managed-apps/<app>.yaml` × 12
- Create: `tests/appset/conftest.py`
- Create: `tests/appset/test_managed_apps.py`
- Modify: `.github/workflows/validate.yaml:31` (PATHSPEC), `:71` (kubeconform filter), and a new job appended after `agent-audit-validate`

**Interfaces:**
- Consumes: `tests/appset/golden/<app>.yaml` from Task 1
- Produces:
  - `appsets/managed-apps/<app>.yaml` — mapping with keys `name: str`, `sourcePath: str`, `namespace: str`, `syncOptions: list[str]`
  - `conftest.py` fixtures: `repo_root() -> pathlib.Path`, `configs() -> dict[str, dict]` keyed by config filename stem, `expand(cfg: dict) -> dict` returning the expected Application `spec`
  - Task 3 consumes `repo_root` and `configs`

- [ ] **Step 1: Write the failing tests**

Create `tests/appset/conftest.py`:

```python
"""Fixtures for the managed-apps ApplicationSet config tests.

`expand` is a Python restatement of base-apps/managed-apps.yaml's template
plus its templatePatch. It is deliberately small: the ApplicationSet template
is reviewed once by a human, and this mirrors it so the CONFIGS can be checked
against the golden specs without a cluster. If the template changes, this must
change with it.
"""
import pathlib

import pytest
import yaml

REPO_URL = "https://github.com/arigsela/kubernetes"
TARGET_REVISION = "main"
DEST_SERVER = "https://kubernetes.default.svc"

CONFIG_DIR = "appsets/managed-apps"
GOLDEN_DIR = "tests/appset/golden"


@pytest.fixture(scope="session")
def repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def configs(repo_root) -> dict:
    out = {}
    for path in sorted((repo_root / CONFIG_DIR).glob("*.yaml")):
        out[path.stem] = yaml.safe_load(path.read_text())
    return out


@pytest.fixture(scope="session")
def goldens(repo_root) -> dict:
    out = {}
    for path in sorted((repo_root / GOLDEN_DIR).glob("*.yaml")):
        out[path.stem] = yaml.safe_load(path.read_text())
    return out


def expand(cfg: dict) -> dict:
    """Render the Application spec the ApplicationSet would produce."""
    spec = {
        "project": "default",
        "source": {
            "repoURL": REPO_URL,
            "targetRevision": TARGET_REVISION,
            "path": cfg["sourcePath"],
        },
        "destination": {
            "server": DEST_SERVER,
            "namespace": cfg["namespace"],
        },
        "syncPolicy": {
            "automated": {"prune": True, "selfHeal": True},
        },
    }
    # templatePatch renders `syncOptions:` with no items when the list is
    # empty, which is YAML null; a merge patch treats null as "delete the
    # key", and the template never set it, so the field is simply absent.
    if cfg["syncOptions"]:
        spec["syncPolicy"]["syncOptions"] = list(cfg["syncOptions"])
    return spec
```

Create `tests/appset/test_managed_apps.py`:

```python
"""Contract tests for appsets/managed-apps/*.yaml.

These configs drive base-apps/managed-apps.yaml's git files generator. A
malformed config does not fail loudly at apply time -- goTemplateOptions
missingkey=error stops the render for ALL apps in the set and the existing
Applications simply stop reconciling. These tests are the loud failure.
"""
import pathlib

import pytest
import yaml

from conftest import expand

REQUIRED_KEYS = {"name", "sourcePath", "namespace", "syncOptions"}

EXPECTED_APPS = {
    "agent-audit-aws-infrastructure",
    "argo-rollouts-config",
    "argo-workflow-tasks",
    "argo-workflows-aws-infrastructure",
    "argo-workflows-config",
    "crossplane-aws-provider",
    "crossplane-compositions",
    "crossplane-functions",
    "crossplane-system",
    "ecr-auth",
    "kyverno-policies",
    "loki-aws-infrastructure",
}


def test_expected_configs_present(configs):
    assert set(configs) == EXPECTED_APPS


def test_every_config_has_exactly_the_required_keys(configs):
    for stem, cfg in configs.items():
        assert set(cfg) == REQUIRED_KEYS, f"{stem}: keys are {sorted(cfg)}"


def test_field_types(configs):
    for stem, cfg in configs.items():
        assert isinstance(cfg["name"], str) and cfg["name"], stem
        assert isinstance(cfg["sourcePath"], str) and cfg["sourcePath"], stem
        assert isinstance(cfg["namespace"], str) and cfg["namespace"], stem
        assert isinstance(cfg["syncOptions"], list), stem
        for opt in cfg["syncOptions"]:
            assert isinstance(opt, str), f"{stem}: syncOption {opt!r} is not a string"


def test_filename_matches_name(configs):
    for stem, cfg in configs.items():
        assert cfg["name"] == stem, f"{stem}: name is {cfg['name']!r}"


def test_names_are_unique(configs):
    names = [cfg["name"] for cfg in configs.values()]
    assert len(names) == len(set(names))


def test_source_path_is_a_non_empty_directory(configs, repo_root):
    for stem, cfg in configs.items():
        d = repo_root / cfg["sourcePath"]
        assert d.is_dir(), f"{stem}: {cfg['sourcePath']} is not a directory"
        assert any(d.glob("*.yaml")), f"{stem}: {cfg['sourcePath']} has no manifests"


def test_golden_equivalence(configs, goldens):
    """Every generated spec must equal the spec of the file it replaces."""
    assert set(configs) == set(goldens)
    for stem, cfg in configs.items():
        assert expand(cfg) == goldens[stem], f"{stem}: spec would change"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/appset/ -q`

Expected: FAIL. `test_expected_configs_present` fails with `AssertionError: assert set() == {...}` because `appsets/managed-apps/` does not exist yet, and the other tests fail or pass vacuously on empty input.

- [ ] **Step 3: Write the 12 config files**

Create each file under `appsets/managed-apps/`. Comments carried over from the Application files they replace are preserved verbatim.

`appsets/managed-apps/agent-audit-aws-infrastructure.yaml`:
```yaml
name: agent-audit-aws-infrastructure
sourcePath: base-apps/agent-audit-aws-infrastructure
# The AccessKey's connection secret is written into postgresql, where the
# export CronJob runs alongside the SELECT-only DB credential.
namespace: postgresql
syncOptions:
  - CreateNamespace=true
  - ServerSideApply=true
```

`appsets/managed-apps/argo-rollouts-config.yaml`:
```yaml
name: argo-rollouts-config
# Named -config to avoid colliding with the argo-rollouts Helm chart
# Application, which owns the same namespace.
sourcePath: base-apps/argo-rollouts
namespace: argo-rollouts
syncOptions: []
```

`appsets/managed-apps/argo-workflow-tasks.yaml`:
```yaml
name: argo-workflow-tasks
sourcePath: base-apps/argo-workflow-tasks
namespace: argo-workflows
syncOptions: []
```

`appsets/managed-apps/argo-workflows-aws-infrastructure.yaml`:
```yaml
name: argo-workflows-aws-infrastructure
sourcePath: base-apps/argo-workflows-aws-infrastructure
namespace: argo-workflows
syncOptions: []
```

`appsets/managed-apps/argo-workflows-config.yaml`:
```yaml
name: argo-workflows-config
# Named -config to avoid colliding with the argo-workflows Helm chart
# Application, which owns the same namespace.
sourcePath: base-apps/argo-workflows
namespace: argo-workflows
syncOptions: []
```

`appsets/managed-apps/crossplane-aws-provider.yaml`:
```yaml
name: crossplane-aws-provider
sourcePath: base-apps/crossplane-aws-provider
namespace: crossplane-system
syncOptions: []
```

`appsets/managed-apps/crossplane-compositions.yaml`:
```yaml
# XRDs and Compositions (cluster-scoped resources).
name: crossplane-compositions
sourcePath: base-apps/crossplane-compositions
namespace: crossplane-system
syncOptions:
  - CreateNamespace=false
```

`appsets/managed-apps/crossplane-functions.yaml`:
```yaml
# Crossplane composition functions.
# Synced before crossplane-compositions (lower wave on the Function CR inside).
name: crossplane-functions
sourcePath: base-apps/crossplane-functions
namespace: crossplane-system
syncOptions:
  - CreateNamespace=false
```

`appsets/managed-apps/crossplane-system.yaml`:
```yaml
# base-apps/crossplane-system is an umbrella Helm chart (Chart.yaml declaring a
# crossplane dependency), not a plain manifest directory. Argo auto-detects
# that from the path; nothing extra is needed here.
name: crossplane-system
sourcePath: base-apps/crossplane-system
namespace: crossplane-system
syncOptions:
  - CreateNamespace=true
```

`appsets/managed-apps/ecr-auth.yaml`:
```yaml
name: ecr-auth
sourcePath: base-apps/ecr-auth
namespace: kube-system
syncOptions: []
```

`appsets/managed-apps/kyverno-policies.yaml`:
```yaml
name: kyverno-policies
sourcePath: base-apps/kyverno-policies
namespace: kyverno
syncOptions: []
```

`appsets/managed-apps/loki-aws-infrastructure.yaml`:
```yaml
name: loki-aws-infrastructure
sourcePath: base-apps/loki-aws-infrastructure
namespace: logging
syncOptions:
  - CreateNamespace=true
  # ServerSideApply for better handling of managed fields
  - ServerSideApply=true
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/appset/ -q`

Expected: PASS, 7 passed. If `test_golden_equivalence` fails, a config's `sourcePath`, `namespace`, or `syncOptions` does not match the Application it replaces — fix the config, not the golden.

- [ ] **Step 5: Extend the CI pathspec so `appsets/` is linted**

In `.github/workflows/validate.yaml`, change line 31 from:

```bash
          PATHSPEC=('base-apps/*.yaml' 'base-apps/**/*.yaml')
```

to:

```bash
          # appsets/*/*.yaml holds ApplicationSet generator configs. They are
          # plain data (no 'kind'), so kubernetes-validate skips them below,
          # but yamllint must still cover them.
          PATHSPEC=('base-apps/*.yaml' 'base-apps/**/*.yaml' 'appsets/*/*.yaml')
```

- [ ] **Step 6: Exclude `appsets/` from kubeconform**

In the same file, change the filter on line 71 from:

```bash
          FILES=$(echo "${{ needs.changed-files.outputs.yaml_files }}" | tr ' ' '\n' | grep -v '/mkdocs\.yml$' || true)
```

to:

```bash
          FILES=$(echo "${{ needs.changed-files.outputs.yaml_files }}" | tr ' ' '\n' | grep -v -e '/mkdocs\.yml$' -e '^appsets/' || true)
```

and extend the comment above it (currently mentioning only `mkdocs.yml`) to read:

```bash
          # base-apps/<app>/mkdocs.yml is Backstage TechDocs config and
          # appsets/*/*.yaml are ApplicationSet generator configs. Neither is a
          # Kubernetes manifest (no kind), so kubeconform would fail them. Drop
          # both before validating; yamllint still covers them.
```

- [ ] **Step 7: Add the `appset-validate` CI job**

Append to `.github/workflows/validate.yaml`, after the `agent-audit-validate` job:

```yaml
  # The ApplicationSet configs are not schema-validated by kubernetes-validate
  # (they have no kind), and a malformed one does not fail loudly at apply time:
  # goTemplateOptions missingkey=error halts the render for ALL 12 apps and the
  # existing Applications silently stop reconciling. These tests are the loud
  # failure, including a golden check that no config changes an app's spec.
  appset-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: pip install pyyaml==6.0.2 pytest==8.3.3
      - name: Run managed-apps ApplicationSet config tests
        run: python -m pytest tests/appset/ -q
```

- [ ] **Step 8: Verify the workflow file is still valid YAML**

Run: `python -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/validate.yaml')); print(sorted(d['jobs']))"`

Expected: a list of job names including `appset-validate`.

- [ ] **Step 9: Lint the new config files the way CI will**

Run: `pip install yamllint==1.35.1 && yamllint -c .yamllint.yaml appsets/managed-apps/`

Expected: no output (clean).

- [ ] **Step 10: Commit**

```bash
git add appsets/managed-apps tests/appset .github/workflows/validate.yaml
git commit -m "appsets: add managed-apps generator configs and their tests

Twelve config files describing the Applications the managed-apps
ApplicationSet will generate, plus the contract tests for them. Nothing
consumes these yet -- the ApplicationSet itself lands with the swap.

The golden test is the load-bearing one: it proves each config expands to
exactly the spec of the Application file it will replace.

CI: appsets/ joins the changed-files pathspec so yamllint covers it, and is
excluded from kubeconform since the configs have no kind."
```

---

### Task 3: The swap (Phase 1)

**Do not start until the Task 1 Step 7 cluster gate has passed.**

**Files:**
- Create: `base-apps/managed-apps.yaml`
- Create: `tests/appset/test_disjoint.py`
- Delete: `base-apps/<app>.yaml` × 12

**Interfaces:**
- Consumes: `repo_root` and `configs` fixtures from `tests/appset/conftest.py` (Task 2)
- Produces: the live `managed-apps` ApplicationSet

- [ ] **Step 1: Write the failing test**

Create `tests/appset/test_disjoint.py`:

```python
"""master-app and the managed-apps ApplicationSet must never own the same name.

master-app applies every top-level base-apps/*.yaml. If an Application name
also appears in appsets/managed-apps/, two controllers write the same object
and fight over it. This test is the only thing standing between a future
copy-paste and that outcome.
"""
import yaml


def _top_level_application_names(repo_root):
    names = set()
    for path in sorted((repo_root / "base-apps").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text())
        if isinstance(doc, dict) and doc.get("kind") == "Application":
            names.add(doc["metadata"]["name"])
    return names


def test_appset_names_disjoint_from_app_of_apps(configs, repo_root):
    generated = {cfg["name"] for cfg in configs.values()}
    hand_written = _top_level_application_names(repo_root)
    overlap = generated & hand_written
    assert not overlap, f"owned by both master-app and managed-apps: {sorted(overlap)}"


def test_applicationset_manifest_exists_and_is_wellformed(repo_root):
    path = repo_root / "base-apps" / "managed-apps.yaml"
    doc = yaml.safe_load(path.read_text())
    assert doc["kind"] == "ApplicationSet"
    assert doc["metadata"]["name"] == "managed-apps"
    assert doc["metadata"]["namespace"] == "argo-cd"
    assert doc["spec"]["goTemplate"] is True
    assert doc["spec"]["goTemplateOptions"] == ["missingkey=error"]
    assert doc["spec"]["syncPolicy"]["preserveResourcesOnDeletion"] is True
    files = doc["spec"]["generators"][0]["git"]["files"]
    assert files == [{"path": "appsets/managed-apps/*.yaml"}]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/appset/test_disjoint.py -q`

Expected: FAIL, both tests. `test_appset_names_disjoint_from_app_of_apps` fails with all 12 names in the overlap set, because the hand-written Application files still exist. `test_applicationset_manifest_exists_and_is_wellformed` fails with `FileNotFoundError` for `base-apps/managed-apps.yaml`.

- [ ] **Step 3: Create the ApplicationSet**

Create `base-apps/managed-apps.yaml`:

```yaml
# Generates the Applications for base-apps subdirectories that need no
# per-app Argo CD configuration -- no Helm values, no ignoreDifferences, no
# sync waves. Their shape is entirely described by the config files in
# appsets/managed-apps/, which master-app cannot see (it reads only top-level
# base-apps/*.yaml, and does not recurse).
#
# Adding an app here means adding a config file, not an Application manifest.
# Anything needing bespoke Argo CD config stays a hand-written
# base-apps/<app>.yaml instead.
#
# See docs/managed-apps-appset.md for the runbook.
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: managed-apps
  namespace: argo-cd
spec:
  goTemplate: true
  # A typo'd config key fails the render loudly instead of silently producing
  # an Application with a missing field.
  goTemplateOptions: ["missingkey=error"]
  generators:
    - git:
        repoURL: https://github.com/arigsela/kubernetes
        revision: main
        files:
          - path: appsets/managed-apps/*.yaml
  syncPolicy:
    # Generated Applications get NO resources-finalizer. If this generator ever
    # returns empty -- path typo, branch rename, repo unreachable -- the
    # Applications are deleted but their resources keep running. Three of these
    # apps front real S3 buckets and IAM users via Crossplane CRs that have no
    # deletionPolicy: Orphan, so a cascade here would be unrecoverable.
    preserveResourcesOnDeletion: true
  template:
    metadata:
      name: '{{ .name }}'
    spec:
      project: default
      source:
        repoURL: https://github.com/arigsela/kubernetes
        targetRevision: main
        path: '{{ .sourcePath }}'
      destination:
        server: https://kubernetes.default.svc
        namespace: '{{ .namespace }}'
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
  # syncOptions varies in length across apps, and Go control flow is not
  # permitted inside the structured template above -- only here. An empty list
  # renders `syncOptions:` (YAML null), which a merge patch treats as "delete
  # the key"; the template never sets it, so those apps simply have none.
  templatePatch: |
    spec:
      syncPolicy:
        syncOptions:
          {{- range .syncOptions }}
          - {{ . }}
          {{- end }}
```

- [ ] **Step 4: Delete the 12 hand-written Application files**

```bash
git rm base-apps/agent-audit-aws-infrastructure.yaml \
       base-apps/argo-rollouts-config.yaml \
       base-apps/argo-workflow-tasks.yaml \
       base-apps/argo-workflows-aws-infrastructure.yaml \
       base-apps/argo-workflows-config.yaml \
       base-apps/crossplane-aws-provider.yaml \
       base-apps/crossplane-compositions.yaml \
       base-apps/crossplane-functions.yaml \
       base-apps/crossplane-system.yaml \
       base-apps/ecr-auth.yaml \
       base-apps/kyverno-policies.yaml \
       base-apps/loki-aws-infrastructure.yaml
```

- [ ] **Step 5: Run the full appset suite to verify it passes**

Run: `python -m pytest tests/appset/ -q`

Expected: PASS, 9 passed (7 from Task 2 plus the 2 in `test_disjoint.py`).

- [ ] **Step 6: Verify the file count and that no app directory was touched**

Run: `ls base-apps/*.yaml | wc -l`
Expected: `39` (38 remaining Applications + `managed-apps.yaml`).

Run: `git status --short base-apps/ | grep -v '^D ' | grep -v 'managed-apps.yaml'`
Expected: no output — the only `base-apps/` changes are the 12 deletions and the one addition.

- [ ] **Step 7: Verify the ApplicationSet passes the same checks CI runs**

```bash
yamllint -c .yamllint.yaml base-apps/managed-apps.yaml
```
Expected: no output.

```bash
kubeconform -summary -strict -ignore-missing-schemas \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  -kubernetes-version 1.33.0 base-apps/managed-apps.yaml
```
Expected: summary reporting 1 resource, 0 errors (the ApplicationSet schema may be reported as skipped, which `-ignore-missing-schemas` permits).

- [ ] **Step 8: Commit**

```bash
git add base-apps/managed-apps.yaml tests/appset/test_disjoint.py
git commit -m "base-apps: hand the 12 boilerplate apps to a managed-apps ApplicationSet

Deletes the 12 hand-written Application manifests and adds the ApplicationSet
that regenerates them from appsets/managed-apps/. Their finalizers were
removed in Phase 0, so master-app's prune orphans the resources rather than
cascading; the generated Applications then adopt them, reporting Synced with
no change to anything running.

test_disjoint is the guardrail against a future copy-paste putting the same
Application name under both master-app and the ApplicationSet.

Rollback is a straight git revert of this commit."
```

- [ ] **Step 9: Verify the rollback is clean before merging**

Per the design's acceptance criterion 7, the revert must be known-good before the swap lands rather than improvised afterwards. Dry-run it against the commit you just made, then discard:

```bash
SWAP_SHA=$(git rev-parse HEAD)
git revert --no-commit --no-edit "$SWAP_SHA" && echo "revert applies cleanly"
git status --short
git revert --quit && git reset --hard "$SWAP_SHA"
```

Expected: `revert applies cleanly`, `git status --short` shows the 12 Application files restored and `base-apps/managed-apps.yaml` deleted, and the final line returns the tree to the swap commit with nothing staged.

Record `git revert $SWAP_SHA` in the PR description, along with the note that `preserveResourcesOnDeletion: true` means the revert deletes and recreates Application objects without touching workloads.

- [ ] **Step 10: CLUSTER VERIFICATION — after merge and sync**

Confirm all 12 are generated, Synced, and Healthy:

```bash
kubectl get applications -n argo-cd \
  -l argocd.argoproj.io/application-set-name=managed-apps \
  -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status
```
Expected: 12 rows, all `Synced` / `Healthy`.

Then prove zero behaviour change against the goldens:

```bash
python3 - <<'PY'
import json, pathlib, subprocess, yaml

out = subprocess.run(
    ["kubectl", "get", "applications", "-n", "argo-cd",
     "-l", "argocd.argoproj.io/application-set-name=managed-apps", "-o", "json"],
    capture_output=True, text=True, check=True,
).stdout
live = {i["metadata"]["name"]: i["spec"] for i in json.loads(out)["items"]}

drift = []
for g in sorted(pathlib.Path("tests/appset/golden").glob("*.yaml")):
    expected = yaml.safe_load(g.read_text())
    actual = live.get(g.stem)
    if actual is None:
        drift.append(f"{g.stem}: not generated")
    elif actual != expected:
        drift.append(f"{g.stem}: {expected} != {actual}")

print("\n".join(drift) if drift else f"all {len(live)} specs match goldens")
PY
```
Expected: `all 12 specs match goldens`.

If a diff appears only in fields Argo defaults server-side, record it in the PR rather than editing the goldens.

---

### Task 4: Runbook and README correction

**Files:**
- Create: `docs/managed-apps-appset.md`
- Modify: `base-apps/README.md`

**Interfaces:**
- Consumes: the ApplicationSet from Task 3
- Produces: nothing consumed by other tasks

- [ ] **Step 1: Write the runbook**

Create `docs/managed-apps-appset.md` with exactly this content (the outer fence is four backticks so the nested YAML blocks are part of the file):

````markdown
# The `managed-apps` ApplicationSet

`base-apps/managed-apps.yaml` generates Argo CD Applications for the
`base-apps/` subdirectories that need no per-app Argo CD configuration. It is
driven by a git files generator reading `appsets/managed-apps/*.yaml`.

Everything else in `base-apps/` is still a hand-written Application picked up
by `master-app`. The two are disjoint, and `tests/appset/test_disjoint.py`
enforces that.

## What belongs here

An app qualifies only if its Application manifest needs none of: a remote Helm
chart source, inline `helm.values`, `ignoreDifferences`, a `sync-wave`
annotation, or `directory.exclude`. Anything with one of those stays a
hand-written `base-apps/<app>.yaml`.

## Config schema

All four keys are required. `syncOptions` must be present even when empty.

```yaml
name: loki-aws-infrastructure           # Application name; must match the filename stem
sourcePath: base-apps/loki-aws-infrastructure   # NOT `path` -- see below
namespace: logging                      # destination namespace
syncOptions:                            # [] if none
  - CreateNamespace=true
  - ServerSideApply=true
```

The field is `sourcePath`, not `path`, because the git files generator injects
its own `path` object (`.path.filename`, `.path.segments`, ...). A config key
named `path` would be shadowed silently.

`name` and `sourcePath` are independent on purpose: `argo-rollouts-config`
sources `base-apps/argo-rollouts`, because naming it after its directory would
collide with the `argo-rollouts` Helm chart Application.

## Adding an app

1. Create `appsets/managed-apps/<name>.yaml` with the four keys.
2. Capture the expected spec in `tests/appset/golden/<name>.yaml` if you are
   converting an existing Application; for a brand-new app, write the golden to
   match what you expect and let `test_golden_equivalence` hold you to it.
3. Run `python -m pytest tests/appset/ -q`.
4. Open a PR and check the **Preview Apps** tab on the ApplicationSet in the
   Argo CD UI before merging. It shows exactly which Applications the change
   would produce.

## Removing an app

Delete its config file and its golden. `applicationsSync` is at its default, so
the Application is deleted on the next reconcile. Because
`preserveResourcesOnDeletion: true` is set, **its Kubernetes resources are not
deleted** — they keep running, unmanaged. Clean them up deliberately if that is
what you want.

## Rollback

`git revert` the commit. Deleting the ApplicationSet cascades to its generated
Applications, but `preserveResourcesOnDeletion: true` means the resources
survive; restoring the hand-written `base-apps/<app>.yaml` files puts them back
under `master-app`.

## Failure modes

**The generator matches nothing** (path typo, branch rename, repo unreachable):
all 12 Applications are deleted. Workloads and Crossplane CRs keep running.
Restoring the glob restores the Applications. This is why
`preserveResourcesOnDeletion: true` is not optional here — three of these apps
front real S3 buckets and IAM users via Crossplane CRs with the default
`deletionPolicy: Delete`.

**A malformed config**: `goTemplateOptions: ["missingkey=error"]` halts the
render for *all* apps in the set. Existing Applications keep running but stop
reconciling from the ApplicationSet, which is quiet — `tests/appset/` is the
loud failure, and the Preview Apps tab is the pre-merge check.

**Never set `recurse: true` on `master-app`.** The config files live outside
`base-apps/` precisely so that `master-app` cannot reach them; recursing would
still not reach `appsets/`, but recursing over `base-apps/` would start applying
per-app files that were never meant to be manifests.

## Why `templatePatch`

`syncOptions` varies in length across apps. Go control flow (`{{- range }}`,
`{{- if }}`) is not permitted inside the structured `template` field — Argo
renders only that field's string values, so list length cannot vary. Control
flow works only inside `templatePatch`. An empty `syncOptions` renders YAML
null, which a merge patch treats as "delete the key"; since the template never
sets it, those apps end up with no `syncOptions` at all.

## Design

`docs/superpowers/specs/2026-08-13-managed-apps-appset-design.md`
````

- [ ] **Step 2: Correct `base-apps/README.md`**

Three fixes, scoped to the pattern description and the app inventory.

First, the opening paragraph currently reads:

> This directory contains ArgoCD Application manifests and their corresponding Kubernetes resources. The `master-app` ApplicationSet automatically discovers and deploys any `.yaml` file in this directory.

Replace with:

```markdown
This directory contains Argo CD Application manifests and their corresponding
Kubernetes resources. The `master-app` Application (defined in
`terraform/modules/application-sets/application-sets.tf`) discovers and deploys
every top-level `.yaml` file in this directory. It does not recurse, so
subdirectories are only reached via the Application that points at them.

Twelve apps that need no per-app Argo CD configuration are instead generated by
the `managed-apps` ApplicationSet from configs in `appsets/managed-apps/` — see
`docs/managed-apps-appset.md`.
```

Second, delete the stale entries from the "Production Applications" and
"Infrastructure Components" lists: `chores-tracker-backend`,
`chores-tracker-frontend`, `mysql-rds-backup`, and `nginx-ingress`. None exist
in the tree.

Third, delete the "Disabled Applications" section entirely. It claims `n8n`,
`postgresql`, `oncall-agent`, and `k8s-monitor` are `.yaml.disabled`; the first
three are live (`base-apps/n8n.yaml`, `postgresql.yaml`, `oncall-agent.yaml`)
and the fourth does not exist.

- [ ] **Step 3: Verify the claims in the README edit**

```bash
ls base-apps/n8n.yaml base-apps/postgresql.yaml base-apps/oncall-agent.yaml
ls base-apps/chores-tracker-backend.yaml base-apps/nginx-ingress.yaml 2>&1 | tail -1
grep -c "ApplicationSet" base-apps/README.md
```
Expected: the first three exist; the second command reports "No such file"; the
grep count is 1 (the new `managed-apps` reference only).

- [ ] **Step 4: Lint the markdown links resolve**

```bash
test -f docs/managed-apps-appset.md && \
test -f docs/superpowers/specs/2026-08-13-managed-apps-appset-design.md && \
test -f appsets/managed-apps/loki-aws-infrastructure.yaml && \
test -f tests/appset/test_disjoint.py && echo "all referenced paths exist"
```
Expected: `all referenced paths exist`

- [ ] **Step 5: Commit**

```bash
git add docs/managed-apps-appset.md base-apps/README.md
git commit -m "docs: runbook for managed-apps, and correct base-apps/README

The README called master-app an ApplicationSet, which is exactly the confusion
this pilot would deepen -- it is a plain Application over base-apps/*.yaml. It
also listed four apps that no longer exist and marked three live apps as
disabled."
```

---

## Verification checklist

Against the design's acceptance criteria (§8):

| # | Criterion | Verified by |
|---|---|---|
| 1 | 12 Applications generated, Synced, Healthy | Task 3 Step 10 |
| 2 | `base-apps/` has 38 Applications + `managed-apps.yaml` | Task 3 Step 6 |
| 3 | `tests/appset/` passes in CI; `validate.yaml` green | Task 2 Steps 4/8, Task 3 Step 5 |
| 4 | Preview Apps tab inspected before merging Phase 1 | Task 3, before Step 10 — open the PR and check the tab |
| 5 | Deliberate break — a config missing `namespace` is caught by Preview | Post-landing, on a throwaway branch |
| 6 | Live specs match goldens | Task 3 Step 10 |
| 7 | Revert commit prepared and reviewed pre-merge | Task 3 Step 8 |

Criterion 5 is the actual experiment and is deliberately left until after
landing: push a branch that deletes the `namespace:` line from
`appsets/managed-apps/ecr-auth.yaml`, open a PR, and check whether the Preview
Apps tab surfaces the render failure. Do not merge it. That result is what
decides whether this pattern is worth extending.
