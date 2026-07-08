# Agent-Ready Docs Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a git-native, co-located documentation framework (top-level atlas + per-directory indexes + a per-app three-file contract) that AI agents can traverse for triage, Q&A, and operational how-to, enforced by a CI validator, validated on a 4-app pilot.

**Architecture:** Documentation lives beside the manifests it describes (`base-apps/<app>/`). Each in-scope app carries three files: `catalog-info.yaml` (structured facts, Backstage schema), `docs.md` (narrative, YAML frontmatter), `runbook.md` (operational, YAML frontmatter). A top-level `INFRASTRUCTURE_ATLAS.md` plus per-directory `_INDEX.md` files form the navigation layer. A standalone Python validator (`scripts/validate-agent-docs.py`) enforces the contract (file presence + catalog-info structure), frontmatter validity, `sources:` path resolution, staleness, index coverage, and the Argo CD `backstage.io` exclusion; it and its pytest suite are wired into `.github/workflows/validate.yaml`. (Terraform changes are separately gated by the repo's existing `terraform-validate.yaml` workflow.) The validator is built early (Task 2) so it doubles as the test harness for every doc-authoring task.

**Tech Stack:** Markdown + YAML frontmatter, Backstage entity schema (`backstage.io/v1alpha1`), Python 3 standard library + PyYAML for the validator, pytest for validator tests, GitHub Actions for CI.

> **Implementation note (read before trusting the per-task example content below).** The `docs.md`/`runbook.md`/`catalog-info.yaml` bodies shown in Tasks 4–7 are the *initial drafts*. During implementation they were rewritten against the live manifests, which corrected several facts that were stale in this plan (and in the source `CLAUDE.md`): chores-tracker uses **PostgreSQL/CloudNativePG** (not MySQL) and an Argo **`Rollout`** (not a Deployment); the app-of-apps "master-app" is defined in **`terraform/modules/application-sets/`** (there is no `base-apps/master-app.yaml`); cert-manager's `letsencrypt-prod`/`letsencrypt-staging` issuers use **HTTP-01 via nginx**, with a separate `letsencrypt-route53` **DNS-01** issuer; and the Vault `dependsOn` ref is **`resource:vault/vault`**. **The committed files under `base-apps/<app>/` are authoritative** — where they differ from the examples below, the committed files are correct. Two GitOps-safety mechanisms and expanded validator checks (catalog-info structure, Argo CD `backstage.io` exclusion) were also added during review; see the Global Constraints and "Post-review additions" section.

## Global Constraints

- **No workload/manifest behavior change; one scoped control-plane change.** This plan touches documentation, a Python validator + tests, one CI job, and exactly one Argo CD config change (below). It must not change any Kubernetes workload, Helm, Crossplane, Vault/ExternalSecret, ingress, IAM, or RBAC behavior. Do not edit any existing `*.yaml` manifest under `base-apps/` except to add new `catalog-info.yaml` files. The single Terraform edit is the Argo CD `resource.exclusions` addition described next — it changes only what Argo CD ignores, not any running workload.
- **GitOps safety — Argo CD must ignore `catalog-info.yaml`.** These files are Backstage entities (`apiVersion: backstage.io/v1alpha1`), not Kubernetes manifests, but they live inside Argo CD-synced app directories (`base-apps/<app>/`). Without action, Argo CD would try to apply them and fail sync (no `backstage.io` CRD exists). The framework therefore requires the Argo CD config to exclude the `backstage.io` group globally via `resource.exclusions` in `terraform/roots/asela-cluster/argocd.tf` (the same mechanism already used for Crossplane kinds). The validator enforces this: it fails if any `catalog-info.yaml` exists without the exclusion configured. Because the global exclusion is applied out-of-band (Terraform/Atlantis) while the docs sync on merge, each of the 4 pilot Applications also sets `spec.source.directory.exclude: catalog-info.yaml` — an in-band guard that makes the merge safe regardless of Terraform apply timing. The global exclusion then covers all future apps.
- **Contract files, exact names:** every in-scope app directory has exactly `catalog-info.yaml`, `docs.md`, `runbook.md`.
- **Frontmatter required keys** (in `docs.md`/`runbook.md`): `app`, `catalog_entity`, `kind` (∈ `docs|runbook`), `namespace`, `last_reviewed` (ISO `YYYY-MM-DD`), `status` (∈ `current|wip|deprecated`), `tags` (list), `sources` (list of repo-relative paths that must exist).
- **Structured-vs-narrative separation:** owner, dependencies, namespace, lifecycle are authored only in `catalog-info.yaml`; prose only in markdown. Markdown may reference catalog facts but not restate them as source.
- **catalog-info.yaml** uses `apiVersion: backstage.io/v1alpha1`, includes annotation `agent-docs/path: docs.md`, and `metadata.name` must equal the frontmatter `catalog_entity` in the sibling `docs.md`/`runbook.md`.
- **Pilot in-scope apps (exactly 4):** `chores-tracker-backend`, `vault`, `argo-cd`, `cert-manager`.
- **Staleness threshold:** 180 days; default mode is **warn** (non-failing) during rollout.
- **CLAUDE.md:** must end under 200 lines and contain `@AGENTS.md`.
- **Taxonomy defaults:** `owner: platform` for shared infra; `system` groups related components. No existing `catalog-info.yaml` convention exists in-repo to match.
- **Commit style:** Conventional Commits, imperative subject (e.g. `docs(agent-atlas): ...`, `feat(agent-docs): ...`). Work happens on branch `docs/agent-ready-docs-framework` (already checked out); do not commit to `main`.

---

## File Structure

**Created:**
- `templates/agent-docs/catalog-info.yaml` — copyable structured-facts template.
- `templates/agent-docs/docs.md` — copyable narrative template with frontmatter.
- `templates/agent-docs/runbook.md` — copyable operational template with frontmatter.
- `templates/agent-docs/README.md` — the frontmatter schema reference + how to use the templates.
- `scripts/validate-agent-docs.py` — the CI validator (pure functions + CLI entrypoint).
- `scripts/agent-docs-scope.txt` — newline-delimited list of in-scope app names for contract-presence checks.
- `tests/agent-docs/test_validate_agent_docs.py` — pytest tests for the validator.
- `tests/agent-docs/fixtures/` — good/bad fixture trees used by the tests.
- `INFRASTRUCTURE_ATLAS.md` — top-level navigation front door.
- `base-apps/_INDEX.md`, `terraform/_INDEX.md`, `docs/_INDEX.md` — per-directory indexes.
- `base-apps/{chores-tracker-backend,vault,argo-cd,cert-manager}/{catalog-info.yaml,docs.md,runbook.md}` — the 4 pilot contracts (12 files).

**Modified:**
- `CLAUDE.md` — add atlas pointer + `@AGENTS.md` import; trim redundant inline command examples to land under 200 lines.
- `.github/workflows/validate.yaml` — add an `agent-docs-validate` job.

---

## Task 1: Contract templates + schema reference + scope file

**Files:**
- Create: `templates/agent-docs/catalog-info.yaml`
- Create: `templates/agent-docs/docs.md`
- Create: `templates/agent-docs/runbook.md`
- Create: `templates/agent-docs/README.md`
- Create: `scripts/agent-docs-scope.txt`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: the canonical file shapes every later doc task copies from; the scope file `scripts/agent-docs-scope.txt` (one app name per line) that Task 2's validator reads.

- [ ] **Step 1: Create the catalog-info template**

Create `templates/agent-docs/catalog-info.yaml`:

```yaml
# Structured-facts layer for an app. Copy into base-apps/<app>/catalog-info.yaml.
# Authoritative for: owner, dependencies, namespace, lifecycle. Do NOT restate these in markdown.
apiVersion: backstage.io/v1alpha1
kind: Component            # Component for services; Resource for stateful infra (e.g. vault)
metadata:
  name: REPLACE_ME         # must equal catalog_entity in the sibling docs.md/runbook.md
  namespace: REPLACE_ME    # Kubernetes namespace the app runs in
  annotations:
    agent-docs/path: docs.md
  tags: []                 # short lowercase tokens, e.g. [fastapi, mysql]
spec:
  type: service            # service | database | infrastructure
  lifecycle: production    # production | experimental | deprecated
  owner: platform          # team or person; default 'platform' for shared infra
  system: REPLACE_ME       # groups related components, e.g. chores-tracker
  dependsOn: []            # e.g. [resource:default/vault]
```

- [ ] **Step 2: Create the docs.md template**

Create `templates/agent-docs/docs.md`:

```markdown
---
app: REPLACE_ME
catalog_entity: REPLACE_ME
kind: docs
namespace: REPLACE_ME
last_reviewed: 2026-07-08
status: current
tags: []
sources:
  - base-apps/REPLACE_ME/deployments.yaml
---

# REPLACE_ME

## What it is
One paragraph: what this app does and why it exists.

## Architecture & data flow
How it is deployed and how data/requests move through it. Reference the `sources:` files rather than restating YAML.

## Where config lives
Point to the authoritative manifests and any Vault paths / ExternalSecrets.

## Gotchas & tribal knowledge
Non-obvious facts an operator needs. Be explicit — agents do not infer omitted context.
```

- [ ] **Step 3: Create the runbook.md template**

Create `templates/agent-docs/runbook.md`:

```markdown
---
app: REPLACE_ME
catalog_entity: REPLACE_ME
kind: runbook
namespace: REPLACE_ME
last_reviewed: 2026-07-08
status: current
tags: []
sources:
  - base-apps/REPLACE_ME/deployments.yaml
---

# REPLACE_ME — Runbook

## Failure modes
### Symptom: <observable symptom>
- **Check:** <command or manifest to inspect>
- **Fix:** <remediation steps>

## How-to
### Deploy / update
<steps, GitOps-first>

### Rotate secrets / scale / restart
<steps>
```

- [ ] **Step 4: Create the schema reference README**

Create `templates/agent-docs/README.md`:

```markdown
# Agent-Docs Contract

Every in-scope `base-apps/<app>/` directory carries three files:

| File | Layer | Authoritative for |
|---|---|---|
| `catalog-info.yaml` | Structured (Backstage entity) | owner, dependencies, namespace, lifecycle |
| `docs.md` | Narrative | architecture, config locations, tribal knowledge |
| `runbook.md` | Operational | failure modes (symptom → check → fix), how-to |

## Frontmatter schema (docs.md / runbook.md)

| Key | Type | Rule |
|---|---|---|
| `app` | string | matches the `base-apps/<app>` directory name |
| `catalog_entity` | string | equals `metadata.name` in the sibling `catalog-info.yaml` |
| `kind` | enum | `docs` or `runbook` |
| `namespace` | string | Kubernetes namespace |
| `last_reviewed` | date | ISO `YYYY-MM-DD`; drives the 180-day staleness check |
| `status` | enum | `current`, `wip`, or `deprecated` |
| `tags` | list | short lowercase tokens |
| `sources` | list | repo-relative paths to authoritative files; each must exist |

## Rules
- Structured facts live only in `catalog-info.yaml`; prose only in markdown.
- The atlas and docs are a navigation/summary layer. `sources:` files remain authoritative — when a summary looks wrong, go to the source.
- Adding an app to the contract: copy the three templates, fill them in, add the app name to `scripts/agent-docs-scope.txt`, and add a row to `base-apps/_INDEX.md`.
```

- [ ] **Step 5: Create the scope file**

Create `scripts/agent-docs-scope.txt`:

```text
chores-tracker-backend
vault
argo-cd
cert-manager
```

- [ ] **Step 6: Commit**

```bash
git add templates/agent-docs scripts/agent-docs-scope.txt
git commit -m "docs(agent-docs): add contract templates, schema reference, and scope file"
```

---

## Task 2: The CI validator (TDD)

**Files:**
- Create: `scripts/validate-agent-docs.py`
- Create: `tests/agent-docs/test_validate_agent_docs.py`
- Create: `tests/agent-docs/fixtures/good/` and `tests/agent-docs/fixtures/bad_*/` (created inside test setup, see steps)
- Test: `tests/agent-docs/test_validate_agent_docs.py`

**Interfaces:**
- Consumes: `scripts/agent-docs-scope.txt` (Task 1).
- Produces: a module exposing pure functions used by tests and a CLI:
  - `parse_frontmatter(text: str) -> dict` — returns the YAML frontmatter block as a dict; raises `ValueError` if absent/malformed.
  - `validate_frontmatter(fm: dict) -> list[str]` — returns a list of human-readable error strings (empty = valid).
  - `check_app_contract(repo_root: Path, app: str) -> list[str]` — presence + frontmatter + link + catalog-name-match errors for one in-scope app.
  - `check_index_coverage(repo_root: Path) -> list[str]` — every `base-apps/<dir>` has a row in `base-apps/_INDEX.md`.
  - `check_staleness(repo_root: Path, apps: list[str], today: date, max_age_days: int = 180) -> list[str]` — returns staleness warnings.
  - `main(argv) -> int` — exit code 0 on pass, 1 on any hard error; staleness prints warnings but does not fail unless `--staleness-fails` is passed.

- [ ] **Step 1: Write the failing test for frontmatter parsing/validation**

Create `tests/agent-docs/test_validate_agent_docs.py`:

```python
from datetime import date
from pathlib import Path

# The script filename has hyphens; import it by path via importlib.
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "validate_agent_docs",
    Path(__file__).resolve().parents[2] / "scripts" / "validate-agent-docs.py",
)
validate_agent_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_agent_docs)


GOOD_FM = """---
app: demo
catalog_entity: demo
kind: docs
namespace: demo-ns
last_reviewed: 2026-07-08
status: current
tags: [x]
sources:
  - base-apps/demo/deployments.yaml
---
body
"""


def test_parse_frontmatter_returns_dict():
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    assert fm["app"] == "demo"
    assert fm["kind"] == "docs"


def test_validate_frontmatter_accepts_good():
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    assert validate_agent_docs.validate_frontmatter(fm) == []


def test_validate_frontmatter_rejects_bad_kind():
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    fm["kind"] = "notes"
    errors = validate_agent_docs.validate_frontmatter(fm)
    assert any("kind" in e for e in errors)


def test_validate_frontmatter_rejects_bad_date():
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    fm["last_reviewed"] = "yesterday"
    errors = validate_agent_docs.validate_frontmatter(fm)
    assert any("last_reviewed" in e for e in errors)


def test_missing_frontmatter_raises():
    import pytest
    with pytest.raises(ValueError):
        validate_agent_docs.parse_frontmatter("no frontmatter here")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pip install pyyaml pytest && python -m pytest tests/agent-docs/ -v`
Expected: FAIL — `scripts/validate-agent-docs.py` does not exist yet (import error).

- [ ] **Step 3: Write the minimal validator to pass the frontmatter tests**

Create `scripts/validate-agent-docs.py`:

```python
#!/usr/bin/env python3
"""Validate the agent-docs contract across the repo.

Checks (hard failures, exit 1): contract-file presence for in-scope apps,
frontmatter validity, link/source resolution, catalog_entity name match,
and base-apps index coverage. Staleness is a warning unless --staleness-fails.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

REQUIRED_KEYS = ["app", "catalog_entity", "kind", "namespace", "last_reviewed", "status", "tags", "sources"]
KIND_VALUES = {"docs", "runbook"}
STATUS_VALUES = {"current", "wip", "deprecated"}


def parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        raise ValueError("missing YAML frontmatter (must start with '---')")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("malformed frontmatter: no closing '---'")
    data = yaml.safe_load(parts[1])
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    return data


def validate_frontmatter(fm: dict) -> list[str]:
    errors = []
    for key in REQUIRED_KEYS:
        if key not in fm:
            errors.append(f"missing required key: {key}")
    if fm.get("kind") not in KIND_VALUES:
        errors.append(f"kind must be one of {sorted(KIND_VALUES)}, got {fm.get('kind')!r}")
    if fm.get("status") not in STATUS_VALUES:
        errors.append(f"status must be one of {sorted(STATUS_VALUES)}, got {fm.get('status')!r}")
    lr = fm.get("last_reviewed")
    if not _is_iso_date(lr):
        errors.append(f"last_reviewed must be an ISO date YYYY-MM-DD, got {lr!r}")
    if not isinstance(fm.get("tags"), list):
        errors.append("tags must be a list")
    if not isinstance(fm.get("sources"), list) or not fm.get("sources"):
        errors.append("sources must be a non-empty list")
    return errors


def _is_iso_date(value) -> bool:
    if isinstance(value, date):
        return True
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()
```

- [ ] **Step 4: Run the frontmatter tests to verify they pass**

Run: `python -m pytest tests/agent-docs/test_validate_agent_docs.py -v -k frontmatter`
Expected: PASS for the frontmatter/parse tests.

- [ ] **Step 5: Write the failing tests for contract, links, index coverage, staleness**

Append to `tests/agent-docs/test_validate_agent_docs.py`:

```python
def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _make_repo(tmp_path: Path) -> Path:
    # Minimal fake repo: one in-scope app 'demo' with a valid contract.
    root = tmp_path
    app = root / "base-apps" / "demo"
    _write(app / "deployments.yaml", "kind: Deployment\n")
    _write(app / "catalog-info.yaml",
           "apiVersion: backstage.io/v1alpha1\nkind: Component\n"
           "metadata:\n  name: demo\n  namespace: demo-ns\n"
           "  annotations:\n    agent-docs/path: docs.md\nspec:\n  owner: platform\n")
    _write(app / "docs.md", GOOD_FM)
    runbook = GOOD_FM.replace("kind: docs", "kind: runbook")
    _write(app / "runbook.md", runbook)
    _write(root / "base-apps" / "_INDEX.md",
           "| app | purpose | namespace | docs | runbook | catalog |\n"
           "|---|---|---|---|---|---|\n"
           "| demo | x | demo-ns | docs.md | runbook.md | catalog-info.yaml |\n")
    _write(root / "scripts" / "agent-docs-scope.txt", "demo\n")
    return root


def test_check_app_contract_passes_on_good_repo(tmp_path):
    root = _make_repo(tmp_path)
    assert validate_agent_docs.check_app_contract(root, "demo") == []


def test_check_app_contract_flags_missing_runbook(tmp_path):
    root = _make_repo(tmp_path)
    (root / "base-apps" / "demo" / "runbook.md").unlink()
    errors = validate_agent_docs.check_app_contract(root, "demo")
    assert any("runbook.md" in e for e in errors)


def test_check_app_contract_flags_dangling_source(tmp_path):
    root = _make_repo(tmp_path)
    (root / "base-apps" / "demo" / "deployments.yaml").unlink()
    errors = validate_agent_docs.check_app_contract(root, "demo")
    assert any("deployments.yaml" in e for e in errors)


def test_check_app_contract_flags_catalog_name_mismatch(tmp_path):
    root = _make_repo(tmp_path)
    ci = root / "base-apps" / "demo" / "catalog-info.yaml"
    ci.write_text(ci.read_text().replace("name: demo", "name: wrong"))
    errors = validate_agent_docs.check_app_contract(root, "demo")
    assert any("catalog_entity" in e or "name" in e for e in errors)


def test_check_index_coverage_flags_missing_row(tmp_path):
    root = _make_repo(tmp_path)
    # add an app dir with no index row
    _write(root / "base-apps" / "orphan" / "x.yaml", "kind: X\n")
    errors = validate_agent_docs.check_index_coverage(root)
    assert any("orphan" in e for e in errors)


def test_check_staleness_warns_when_old(tmp_path):
    root = _make_repo(tmp_path)
    warnings = validate_agent_docs.check_staleness(root, ["demo"], date(2030, 1, 1), 180)
    assert any("demo" in w for w in warnings)
```

- [ ] **Step 6: Run to verify the new tests fail**

Run: `python -m pytest tests/agent-docs/ -v`
Expected: FAIL — `check_app_contract`, `check_index_coverage`, `check_staleness` are not defined.

- [ ] **Step 7: Implement the remaining checks + CLI**

Append to `scripts/validate-agent-docs.py`:

```python
CONTRACT_FILES = ["catalog-info.yaml", "docs.md", "runbook.md"]


def _read_frontmatter_file(path: Path) -> tuple[dict | None, list[str]]:
    try:
        fm = parse_frontmatter(path.read_text())
    except ValueError as exc:
        return None, [f"{path}: {exc}"]
    return fm, [f"{path}: {e}" for e in validate_frontmatter(fm)]


def check_app_contract(repo_root: Path, app: str) -> list[str]:
    errors: list[str] = []
    app_dir = repo_root / "base-apps" / app
    for fname in CONTRACT_FILES:
        if not (app_dir / fname).is_file():
            errors.append(f"{app}: missing contract file {fname}")
    if errors:
        return errors  # can't check further without the files

    catalog = yaml.safe_load((app_dir / "catalog-info.yaml").read_text()) or {}
    catalog_name = (catalog.get("metadata") or {}).get("name")

    for md in ("docs.md", "runbook.md"):
        fm, fm_errors = _read_frontmatter_file(app_dir / md)
        errors.extend(fm_errors)
        if fm is None:
            continue
        if fm.get("catalog_entity") != catalog_name:
            errors.append(
                f"{app}/{md}: catalog_entity {fm.get('catalog_entity')!r} "
                f"does not match catalog-info.yaml metadata.name {catalog_name!r}")
        for src in fm.get("sources", []) or []:
            # .exists() (not .is_file()) so a directory source such as
            # terraform/modules/argocd is accepted.
            if not (repo_root / src).exists():
                errors.append(f"{app}/{md}: sources path does not exist: {src}")
    return errors


def check_index_coverage(repo_root: Path) -> list[str]:
    index = repo_root / "base-apps" / "_INDEX.md"
    if not index.is_file():
        return ["base-apps/_INDEX.md is missing"]
    index_text = index.read_text()
    errors = []
    for child in sorted((repo_root / "base-apps").iterdir()):
        if not child.is_dir():
            continue
        if f"| {child.name} " not in index_text and f"|{child.name}|" not in index_text:
            errors.append(f"base-apps/_INDEX.md has no row for app '{child.name}'")
    return errors


def check_staleness(repo_root: Path, apps: list[str], today: date, max_age_days: int = 180) -> list[str]:
    warnings = []
    for app in apps:
        for md in ("docs.md", "runbook.md"):
            path = repo_root / "base-apps" / app / md
            if not path.is_file():
                continue
            try:
                fm = parse_frontmatter(path.read_text())
                reviewed = _as_date(fm["last_reviewed"])
            except (ValueError, KeyError):
                continue
            age = (today - reviewed).days
            if age > max_age_days:
                warnings.append(f"{app}/{md}: last_reviewed {reviewed} is {age} days old (>{max_age_days})")
    return warnings


def _load_scope(repo_root: Path) -> list[str]:
    scope = repo_root / "scripts" / "agent-docs-scope.txt"
    return [ln.strip() for ln in scope.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate the agent-docs contract.")
    parser.add_argument("--repo-root", default=".", type=Path)
    parser.add_argument("--staleness-fails", action="store_true")
    parser.add_argument("--today", default=None, help="override today (YYYY-MM-DD) for testing")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    today = _as_date(args.today) if args.today else date.today()
    apps = _load_scope(repo_root)

    errors: list[str] = []
    for app in apps:
        errors.extend(check_app_contract(repo_root, app))
    errors.extend(check_index_coverage(repo_root))

    warnings = check_staleness(repo_root, apps, today)

    for w in warnings:
        print(f"WARN: {w}")
    for e in errors:
        print(f"ERROR: {e}")

    if args.staleness_fails:
        errors.extend(warnings)
    if errors:
        print(f"\nagent-docs validation FAILED with {len(errors)} error(s).")
        return 1
    print(f"\nagent-docs validation passed ({len(apps)} apps in scope, {len(warnings)} warning(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 8: Run all validator tests to verify they pass**

Run: `python -m pytest tests/agent-docs/ -v`
Expected: PASS (all tests green).

- [ ] **Step 9: Commit**

```bash
git add scripts/validate-agent-docs.py tests/agent-docs/
git commit -m "feat(agent-docs): add contract validator with pytest coverage"
```

---

## Task 3: Atlas skeleton + per-directory indexes

**Files:**
- Create: `INFRASTRUCTURE_ATLAS.md`
- Create: `base-apps/_INDEX.md`
- Create: `terraform/_INDEX.md`
- Create: `docs/_INDEX.md`

**Interfaces:**
- Consumes: the contract file names (Task 1) for the index columns; the validator's `check_index_coverage` rule (Task 2) — `base-apps/_INDEX.md` must contain a `| <app> ` row for every directory under `base-apps/`.
- Produces: the navigation layer the pilot doc tasks (4–7) link into.

- [ ] **Step 1: Create the atlas front door**

Create `INFRASTRUCTURE_ATLAS.md`:

```markdown
# Infrastructure Atlas

> **For agents:** Start here. Traverse: this atlas → a directory `_INDEX.md` → an app's `docs.md`/`runbook.md` → the `sources:` files listed in that doc. This atlas is a **navigation/summary layer**; the `sources:` files are authoritative. If a summary here looks wrong, go read the source.

## 1. System context
- Kubernetes API: `https://192.168.0.100:6443`
- GitOps: Argo CD watches this repo; `base-apps/master-app.yaml` creates an Application per `.yaml` in `base-apps/`.
- Secrets: HashiCorp Vault at `vault.vault.svc.cluster.local:8200` (KV v2, path `k8s-secrets`), surfaced via External Secrets Operator.
- Terraform state: S3 bucket `asela-terraform-states`.

## 2. Platform topology
- **Argo CD** (`base-apps/argo-cd/`) — control plane; master-app pattern.
- **base-apps/** — one Application per app; each app directory holds its manifests and (in-scope) its agent-docs contract.
- **terraform/** — `roots/asela-cluster` is the active root; reusable `modules/`.
- **Crossplane** — declarative cloud resources.

## 3. GitOps data flow
`git commit` → Argo CD detects drift → syncs manifests to the cluster (`prune: true`, `selfHeal: true`). Secrets resolve at runtime: `SecretStore` + `ExternalSecret` → Vault.

## 4. Cross-cutting concerns
- **Secrets:** Vault + External Secrets Operator; per-namespace `SecretStore`.
- **TLS/certs:** cert-manager (`base-apps/cert-manager/`) with Route 53 DNS-01.
- **Ingress/mesh:** nginx-ingress and Istio ambient mesh.
- **Observability:** logging/Loki/coroot.

## 5. Known gaps
| Gap | Recommendation | Source |
|---|---|---|
| Only 4 apps carry the agent-docs contract | Backfill remaining apps under CI gating | `scripts/agent-docs-scope.txt` |

## 6. Source registry
| Domain | Authoritative location |
|---|---|
| App manifests | `base-apps/<app>/` |
| Argo CD Applications | `base-apps/<app>.yaml`, `base-apps/master-app.yaml` |
| Infrastructure | `terraform/roots/asela-cluster/`, `terraform/modules/` |
| Secret wiring | per-app `secret-store.yaml` / `external-secret*.yaml` |
| Doc contract & index | `templates/agent-docs/README.md`, `base-apps/_INDEX.md` |

## 7. App index
See `base-apps/_INDEX.md` for the per-app index, `terraform/_INDEX.md` and `docs/_INDEX.md` for those trees.
```

- [ ] **Step 2: Generate the base-apps index with a row for every app directory**

Run this to list every app directory (each needs a row so `check_index_coverage` passes):

```bash
cd /Users/arisela/git/kubernetes
for d in $(find base-apps -maxdepth 1 -mindepth 1 -type d | sort); do echo "| $(basename "$d") | | | | | |"; done
```

Create `base-apps/_INDEX.md` with a header then one row per directory. Pilot apps get their columns filled; all others get a stub row (name only). Header:

```markdown
# base-apps Index

One row per Argo CD app directory. Doc columns are relative to `base-apps/<app>/`.
Pilot apps carry the full agent-docs contract; others are stubs pending backfill.

| app | purpose | namespace | docs | runbook | catalog |
|---|---|---|---|---|---|
| chores-tracker-backend | FastAPI chores backend (MySQL, Vault, JWT) | chores-tracker | docs.md | runbook.md | catalog-info.yaml |
| vault | In-cluster secret backend (KV v2) | vault | docs.md | runbook.md | catalog-info.yaml |
| argo-cd | GitOps control plane | argo-cd | docs.md | runbook.md | catalog-info.yaml |
| cert-manager | TLS certs via Route 53 DNS-01 | cert-manager | docs.md | runbook.md | catalog-info.yaml |
```

Then append a stub row `| <name> | | | | | |` for every other directory printed by the command above.

- [ ] **Step 3: Create the terraform and docs indexes**

Create `terraform/_INDEX.md`:

```markdown
# terraform Index

| path | purpose |
|---|---|
| `roots/asela-cluster/` | Active Terraform root (S3 backend `asela-terraform-states`) |
| `modules/argocd/` | Argo CD install/config module |
| `modules/application-sets/` | ApplicationSet definitions |
| `modules/kube-secrets/` | Kubernetes secret provisioning |
```

Create `docs/_INDEX.md`:

```markdown
# docs Index

| doc | purpose |
|---|---|
| `agent-ready-docs-review.md` | Research review: docs-as-code for agents |
| `superpowers/specs/2026-07-08-agent-ready-docs-framework-design.md` | Design spec for this framework |
| `superpowers/plans/2026-07-08-agent-ready-docs-framework.md` | This implementation plan |
```

- [ ] **Step 4: Run the validator to confirm index coverage passes**

Run: `python scripts/validate-agent-docs.py --repo-root . --today 2026-07-08`
Expected: contract errors for the 4 pilot apps (their docs don't exist yet — that's fine, next tasks), but **no** `_INDEX.md has no row for app` errors. Confirm there are zero index-coverage errors in the output.

- [ ] **Step 5: Commit**

```bash
git add INFRASTRUCTURE_ATLAS.md base-apps/_INDEX.md terraform/_INDEX.md docs/_INDEX.md
git commit -m "docs(agent-atlas): add infrastructure atlas and directory indexes"
```

---

## Task 4: Pilot contract — chores-tracker-backend

**Files:**
- Create: `base-apps/chores-tracker-backend/catalog-info.yaml`
- Create: `base-apps/chores-tracker-backend/docs.md`
- Create: `base-apps/chores-tracker-backend/runbook.md`

**Interfaces:**
- Consumes: templates (Task 1), validator (Task 2). Real source files present in this dir: `deployments.yaml`, `services.yaml`, `configmaps.yaml`, `external_secrets.yaml`, `secret-store.yaml`, `nginx-ingress.yaml`, `virtualservice.yaml`, `crossplane_resources.yaml`.
- Produces: the reusable app archetype other backfills copy.

- [ ] **Step 1: Create catalog-info.yaml**

```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: chores-tracker-backend
  namespace: chores-tracker
  annotations:
    agent-docs/path: docs.md
  tags: [fastapi, mysql, jwt]
spec:
  type: service
  lifecycle: production
  owner: platform
  system: chores-tracker
  dependsOn:
    - resource:default/vault
```

- [ ] **Step 2: Create docs.md**

```markdown
---
app: chores-tracker-backend
catalog_entity: chores-tracker-backend
kind: docs
namespace: chores-tracker
last_reviewed: 2026-07-08
status: current
tags: [fastapi, mysql, jwt]
sources:
  - base-apps/chores-tracker-backend/deployments.yaml
  - base-apps/chores-tracker-backend/external_secrets.yaml
  - base-apps/chores-tracker-backend/secret-store.yaml
  - base-apps/chores-tracker-backend/virtualservice.yaml
---

# chores-tracker-backend

## What it is
FastAPI/Python backend for the Chores Tracker app: JWT auth, MySQL persistence, HTMX-driven frontend served separately.

## Architecture & data flow
Deployed via Argo CD from this directory. Requests arrive through nginx-ingress and the Istio `VirtualService` (`virtualservice.yaml`) and reach the Deployment (`deployments.yaml`) → MySQL. Config is in `configmaps.yaml`.

## Where config lives
- Runtime config: `configmaps.yaml`.
- Secrets: `external_secrets.yaml` + `secret-store.yaml` resolve DB credentials/JWT keys from Vault (path under `k8s-secrets`).
- Cloud resources: `crossplane_resources.yaml`.

## Gotchas & tribal knowledge
- DB credentials come from Vault via ExternalSecrets — a failed sync surfaces as the pod crashlooping on startup, not as an ingress error.
- The frontend is a separate app (`chores-tracker-frontend`); backend changes may need a matching frontend deploy.
```

- [ ] **Step 3: Create runbook.md**

```markdown
---
app: chores-tracker-backend
catalog_entity: chores-tracker-backend
kind: runbook
namespace: chores-tracker
last_reviewed: 2026-07-08
status: current
tags: [fastapi, mysql, jwt]
sources:
  - base-apps/chores-tracker-backend/deployments.yaml
  - base-apps/chores-tracker-backend/external_secrets.yaml
---

# chores-tracker-backend — Runbook

## Failure modes
### Symptom: pod CrashLoopBackOff on startup
- **Check:** `kubectl -n chores-tracker get externalsecret,secret` — confirm the ExternalSecret synced and the target Secret exists.
- **Fix:** if the ExternalSecret is not Ready, verify the Vault role/path in `secret-store.yaml`; once Vault resolves, ESO recreates the Secret and the pod recovers.

### Symptom: 502/503 through ingress
- **Check:** Deployment ready replicas (`kubectl -n chores-tracker get deploy`) and the `VirtualService` route in `virtualservice.yaml`.
- **Fix:** scale/restart the Deployment; confirm the mesh/ingress selectors match the Service.

## How-to
### Deploy / update
Edit manifests in this directory, commit to a branch, open a PR. Argo CD syncs on merge to `main`.

### Rotate secrets
Update the value in Vault; ESO re-syncs within the `refreshInterval` in `external_secrets.yaml`. Restart the Deployment to pick up new env-injected values if needed.
```

- [ ] **Step 4: Validate this app passes**

Run: `python scripts/validate-agent-docs.py --repo-root . --today 2026-07-08 2>&1 | grep chores-tracker-backend || echo "no errors for chores-tracker-backend"`
Expected: `no errors for chores-tracker-backend` (all its sources exist, frontmatter valid, catalog name matches).

- [ ] **Step 5: Commit**

```bash
git add base-apps/chores-tracker-backend/catalog-info.yaml base-apps/chores-tracker-backend/docs.md base-apps/chores-tracker-backend/runbook.md
git commit -m "docs(agent-docs): add contract for chores-tracker-backend"
```

---

## Task 5: Pilot contract — vault

**Files:**
- Create: `base-apps/vault/catalog-info.yaml`
- Create: `base-apps/vault/docs.md`
- Create: `base-apps/vault/runbook.md`

**Interfaces:**
- Consumes: templates (Task 1), validator (Task 2). Real source files: `statefulsets.yaml`, `services.yaml`, `configmaps.yaml`, `ingress.yaml`, `namespace.yaml`, `service_accounts.yaml`, `cluster_role_bindings.yaml`.
- Produces: the stateful-infra archetype (uses `kind: Resource`).

- [ ] **Step 1: Create catalog-info.yaml**

```yaml
apiVersion: backstage.io/v1alpha1
kind: Resource
metadata:
  name: vault
  namespace: vault
  annotations:
    agent-docs/path: docs.md
  tags: [secrets, stateful, kv-v2]
spec:
  type: infrastructure
  lifecycle: production
  owner: platform
  system: platform-secrets
```

- [ ] **Step 2: Create docs.md**

```markdown
---
app: vault
catalog_entity: vault
kind: docs
namespace: vault
last_reviewed: 2026-07-08
status: current
tags: [secrets, stateful, kv-v2]
sources:
  - base-apps/vault/statefulsets.yaml
  - base-apps/vault/services.yaml
  - base-apps/vault/configmaps.yaml
---

# vault

## What it is
In-cluster HashiCorp Vault: the secret backend for the whole platform. KV v2 engine at path `k8s-secrets`; Kubernetes auth method for service-account-based access.

## Architecture & data flow
Runs as a StatefulSet (`statefulsets.yaml`) in the `vault` namespace, reachable at `vault.vault.svc.cluster.local:8200` (`services.yaml`). Apps read secrets through External Secrets Operator, which authenticates via each namespace's `SecretStore` and Vault role.

## Where config lives
- Server config: `configmaps.yaml`.
- Access: `service_accounts.yaml`, `cluster_role_bindings.yaml`.
- External exposure: `ingress.yaml`.

## Gotchas & tribal knowledge
- Vault sealing blocks every downstream ExternalSecret; a cluster-wide "secrets not syncing" symptom usually traces back here.
- Vault roles are expected to match namespace names for ESO access.
```

- [ ] **Step 3: Create runbook.md**

```markdown
---
app: vault
catalog_entity: vault
kind: runbook
namespace: vault
last_reviewed: 2026-07-08
status: current
tags: [secrets, stateful, kv-v2]
sources:
  - base-apps/vault/statefulsets.yaml
  - base-apps/vault/services.yaml
---

# vault — Runbook

## Failure modes
### Symptom: many apps' ExternalSecrets stop syncing at once
- **Check:** `kubectl -n vault get pods` and Vault seal status (`vault status` in the pod). A sealed or down Vault breaks all ESO syncs.
- **Fix:** unseal Vault (see `docs/vault-auto-unseal-plan.md`); once unsealed, ESO resumes syncing.

### Symptom: one namespace's ExternalSecrets fail but others work
- **Check:** that namespace's `SecretStore` role vs the Vault role/policy.
- **Fix:** align the Vault role name with the namespace and confirm the policy grants the `k8s-secrets` path.

## How-to
### Deploy / update
Edit manifests here and PR; Argo CD syncs on merge. Treat StatefulSet changes cautiously — they can restart Vault.

### Restart safely
Draining/restarting the Vault pod re-seals it; plan for an unseal step immediately after.
```

- [ ] **Step 4: Validate this app passes**

Run: `python scripts/validate-agent-docs.py --repo-root . --today 2026-07-08 2>&1 | grep '^ERROR.*vault' || echo "no errors for vault"`
Expected: `no errors for vault`.

- [ ] **Step 5: Commit**

```bash
git add base-apps/vault/catalog-info.yaml base-apps/vault/docs.md base-apps/vault/runbook.md
git commit -m "docs(agent-docs): add contract for vault"
```

---

## Task 6: Pilot contract — argo-cd

**Files:**
- Create: `base-apps/argo-cd/catalog-info.yaml`
- Create: `base-apps/argo-cd/docs.md`
- Create: `base-apps/argo-cd/runbook.md`

**Interfaces:**
- Consumes: templates (Task 1), validator (Task 2). This dir currently holds only `ingress.yaml`; the Argo CD install itself is managed via `base-apps/argo-cd.yaml` and Terraform (`terraform/modules/argocd/`). Use those as sources.
- Produces: the control-plane archetype.

- [ ] **Step 1: Create catalog-info.yaml**

```yaml
apiVersion: backstage.io/v1alpha1
kind: Resource
metadata:
  name: argo-cd
  namespace: argo-cd
  annotations:
    agent-docs/path: docs.md
  tags: [gitops, control-plane]
spec:
  type: infrastructure
  lifecycle: production
  owner: platform
  system: platform-gitops
```

- [ ] **Step 2: Create docs.md**

```markdown
---
app: argo-cd
catalog_entity: argo-cd
kind: docs
namespace: argo-cd
last_reviewed: 2026-07-08
status: current
tags: [gitops, control-plane]
sources:
  - base-apps/argo-cd.yaml
  - base-apps/argo-cd/ingress.yaml
  - terraform/modules/argocd
---

# argo-cd

## What it is
The GitOps control plane. Argo CD watches this repo and syncs `base-apps/` to the cluster using the master-app pattern (`base-apps/master-app.yaml` creates one Application per `.yaml`).

## Architecture & data flow
Installed/configured via Terraform (`terraform/modules/argocd/`) and the `base-apps/argo-cd.yaml` Application. Ingress for the UI is `base-apps/argo-cd/ingress.yaml`. All apps use `syncPolicy.automated` with `prune` and `selfHeal`.

## Where config lives
- Install/config: `terraform/modules/argocd/`.
- App-of-apps: `base-apps/master-app.yaml`.
- UI ingress: `base-apps/argo-cd/ingress.yaml`.

## Gotchas & tribal knowledge
- Because `selfHeal` is on, manual `kubectl` edits are reverted — all change must go through git.
- A stuck/broken Argo CD affects every app's sync; triage it before chasing individual app symptoms.
```

- [ ] **Step 3: Create runbook.md**

```markdown
---
app: argo-cd
catalog_entity: argo-cd
kind: runbook
namespace: argo-cd
last_reviewed: 2026-07-08
status: current
tags: [gitops, control-plane]
sources:
  - base-apps/argo-cd.yaml
  - base-apps/argo-cd/ingress.yaml
---

# argo-cd — Runbook

## Failure modes
### Symptom: an app is OutOfSync / not deploying
- **Check:** the Application status (`kubectl -n argo-cd get applications`); look for sync errors or a bad source path.
- **Fix:** correct the manifest/path in git and push; if `selfHeal` is fighting a manual change, revert the manual change.

### Symptom: nothing is syncing across all apps
- **Check:** Argo CD server/repo-server/application-controller pods in `argo-cd`.
- **Fix:** restart the failing controller; verify repo connectivity and credentials.

## How-to
### Deploy a new app
Add `base-apps/<app>.yaml` (Application) + `base-apps/<app>/` manifests; the master-app creates the Application on sync.

### Change Argo CD config
Edit `terraform/modules/argocd/` and apply via the normal Terraform/Atlantis flow.
```

- [ ] **Step 4: Validate this app passes**

Run: `python scripts/validate-agent-docs.py --repo-root . --today 2026-07-08 2>&1 | grep '^ERROR.*argo-cd' || echo "no errors for argo-cd"`
Expected: `no errors for argo-cd`. Note `terraform/modules/argocd` is a directory; the validator's source check uses `.exists()` (Task 2 Step 7), so directory sources are accepted.

- [ ] **Step 5: Commit**

```bash
git add base-apps/argo-cd/catalog-info.yaml base-apps/argo-cd/docs.md base-apps/argo-cd/runbook.md
git commit -m "docs(agent-docs): add contract for argo-cd"
```

---

## Task 7: Pilot contract — cert-manager

**Files:**
- Create: `base-apps/cert-manager/catalog-info.yaml`
- Create: `base-apps/cert-manager/docs.md`
- Create: `base-apps/cert-manager/runbook.md`

**Interfaces:**
- Consumes: templates (Task 1), validator (Task 2). Real source files: `external-secret.yaml`, `secret-store.yaml`, `letsencrypt-prod.yaml`, `letsencrypt-staging.yaml`, `letsencrypt-route53.yaml`.
- Produces: the cross-cutting-infra archetype.

- [ ] **Step 1: Create catalog-info.yaml**

```yaml
apiVersion: backstage.io/v1alpha1
kind: Resource
metadata:
  name: cert-manager
  namespace: cert-manager
  annotations:
    agent-docs/path: docs.md
  tags: [tls, certificates, route53]
spec:
  type: infrastructure
  lifecycle: production
  owner: platform
  system: platform-networking
  dependsOn:
    - resource:default/vault
```

- [ ] **Step 2: Create docs.md**

```markdown
---
app: cert-manager
catalog_entity: cert-manager
kind: docs
namespace: cert-manager
last_reviewed: 2026-07-08
status: current
tags: [tls, certificates, route53]
sources:
  - base-apps/cert-manager/letsencrypt-prod.yaml
  - base-apps/cert-manager/letsencrypt-route53.yaml
  - base-apps/cert-manager/external-secret.yaml
  - base-apps/cert-manager/secret-store.yaml
---

# cert-manager

## What it is
Automated TLS certificate management. Issues/renews certs from Let's Encrypt using DNS-01 challenges via AWS Route 53.

## Architecture & data flow
The ClusterIssuers (`letsencrypt-prod.yaml`, `letsencrypt-staging.yaml`) use the Route 53 solver config (`letsencrypt-route53.yaml`). AWS credentials for the DNS-01 solver come from Vault via `external-secret.yaml` + `secret-store.yaml`. cert-manager watches Certificate/Ingress resources cluster-wide and provisions Secrets holding the issued certs.

## Where config lives
- Issuers: `letsencrypt-prod.yaml`, `letsencrypt-staging.yaml`.
- DNS-01 solver: `letsencrypt-route53.yaml`.
- Route 53 credentials: `external-secret.yaml` + `secret-store.yaml`.

## Gotchas & tribal knowledge
- DNS-01 depends on Route 53 credentials from Vault — if the ExternalSecret is unhealthy, issuance silently stalls in `pending`.
- Use the staging issuer while testing to avoid Let's Encrypt rate limits.
```

- [ ] **Step 3: Create runbook.md**

```markdown
---
app: cert-manager
catalog_entity: cert-manager
kind: runbook
namespace: cert-manager
last_reviewed: 2026-07-08
status: current
tags: [tls, certificates, route53]
sources:
  - base-apps/cert-manager/letsencrypt-prod.yaml
  - base-apps/cert-manager/external-secret.yaml
---

# cert-manager — Runbook

## Failure modes
### Symptom: a Certificate stays in `pending`/not Ready
- **Check:** `kubectl describe certificate <name> -n <ns>` and the CertificateRequest/Order/Challenge chain; confirm the DNS-01 challenge is progressing.
- **Fix:** verify the Route 53 ExternalSecret is Ready (`kubectl -n cert-manager get externalsecret`); if the AWS creds are stale, fix the Vault value so the solver can create the DNS record.

### Symptom: renewals failing / cert expiring soon
- **Check:** issuer status and Route 53 permissions.
- **Fix:** re-run issuance by deleting the failing CertificateRequest so cert-manager retries with valid credentials.

## How-to
### Add a new certificate
Reference the `letsencrypt-prod` ClusterIssuer from your Ingress/Certificate. Test with `letsencrypt-staging` first.

### Rotate Route 53 credentials
Update the value in Vault; ESO re-syncs the solver Secret. Re-trigger any stuck challenges afterward.
```

- [ ] **Step 4: Validate this app passes**

Run: `python scripts/validate-agent-docs.py --repo-root . --today 2026-07-08 2>&1 | grep '^ERROR.*cert-manager' || echo "no errors for cert-manager"`
Expected: `no errors for cert-manager`.

- [ ] **Step 5: Run the full validator to confirm zero hard errors**

Run: `python scripts/validate-agent-docs.py --repo-root . --today 2026-07-08`
Expected: `agent-docs validation passed (4 apps in scope, 0 warning(s)).` and exit code 0.

- [ ] **Step 6: Commit**

```bash
git add base-apps/cert-manager/catalog-info.yaml base-apps/cert-manager/docs.md base-apps/cert-manager/runbook.md
git commit -m "docs(agent-docs): add contract for cert-manager"
```

---

## Task 8: Wire CLAUDE.md ↔ AGENTS.md and land under 200 lines

**Files:**
- Modify: `CLAUDE.md` (currently 204 lines)

**Interfaces:**
- Consumes: `INFRASTRUCTURE_ATLAS.md` (Task 3), `AGENTS.md` (exists), `templates/agent-docs/README.md` (Task 1).
- Produces: a CLAUDE.md that points agents at the atlas, imports AGENTS.md, and is under 200 lines.

- [ ] **Step 1: Add the atlas pointer + AGENTS.md import at the top**

In `CLAUDE.md`, immediately after the `# CLAUDE.md` title line, insert:

```markdown

**Start here:** Read [`INFRASTRUCTURE_ATLAS.md`](INFRASTRUCTURE_ATLAS.md) first — it is the navigation front door (system context, topology, per-app index). For an app, follow `base-apps/_INDEX.md` → the app's `docs.md`/`runbook.md`. The agent-docs contract is documented in [`templates/agent-docs/README.md`](templates/agent-docs/README.md).

@AGENTS.md
```

- [ ] **Step 2: Trim redundant inline command examples to get under 200 lines**

The `## Common Development Tasks` section (the `### Deploy a New Application`, `### Update an Existing Application`, and `### Add Secret Management to Application` subsections with their large heredoc blocks) duplicates guidance already in `AGENTS.md` and the atlas. Replace those three subsections with a single pointer, keeping `### Run Terraform Commands` and `### Branch Management`:

Replace everything from the line `### Deploy a New Application` through the end of the `### Add Secret Management to Application` block (i.e. up to but not including `### Run Terraform Commands`) with:

```markdown
### Deploy / update apps & secrets
See `AGENTS.md` for build/test/validation commands, and `templates/agent-docs/README.md` for the per-app doc contract. Deploy pattern: add `base-apps/<app>.yaml` (Argo CD Application) + `base-apps/<app>/` manifests; Argo CD's master-app creates the Application on sync. Secret pattern: per-namespace `secret-store.yaml` + `external-secret*.yaml` resolving from Vault (`k8s-secrets`).
```

- [ ] **Step 3: Verify line count is under 200**

Run: `wc -l CLAUDE.md`
Expected: a number **< 200**. If still ≥ 200, additionally condense the `### Directory Structure` bullet list (keep it, but remove blank lines) until under 200.

- [ ] **Step 4: Verify the import and pointer are present**

Run: `grep -n '@AGENTS.md' CLAUDE.md && grep -n 'INFRASTRUCTURE_ATLAS.md' CLAUDE.md`
Expected: both grep lines return a match.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(agent-docs): point CLAUDE.md at the atlas and import AGENTS.md"
```

---

## Task 9: Wire the validator into CI

**Files:**
- Modify: `.github/workflows/validate.yaml`

**Interfaces:**
- Consumes: `scripts/validate-agent-docs.py` (Task 2), all pilot contracts and indexes (Tasks 3–7).
- Produces: a CI job `agent-docs-validate` that blocks merge on contract/index/link errors (staleness stays warn-only).

- [ ] **Step 1: Add the job**

Append this job to `.github/workflows/validate.yaml` (sibling of the existing `yaml-lint`, `kubernetes-validate`, `ingress-policy` jobs; it does not depend on `changed-files` because presence/index checks are repo-global and fast):

```yaml
  agent-docs-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: pip install pyyaml==6.0.2
      - name: Validate agent-docs contract
        run: python scripts/validate-agent-docs.py --repo-root .
```

- [ ] **Step 2: Lint the workflow file locally**

Run: `yamllint -c .yamllint.yaml .github/workflows/validate.yaml`
Expected: no errors (fix indentation/line-length if flagged).

- [ ] **Step 3: Dry-run the exact CI command locally**

Run: `python scripts/validate-agent-docs.py --repo-root .`
Expected: `agent-docs validation passed (4 apps in scope, ...)` and exit code 0 (`echo $?` → 0).

- [ ] **Step 4: Prove the gate actually fails on a broken contract**

Run:
```bash
cp base-apps/vault/docs.md /tmp/vault-docs-backup.md
python - <<'PY'
import pathlib
p = pathlib.Path("base-apps/vault/docs.md")
p.write_text(p.read_text().replace("kind: docs", "kind: bogus"))
PY
python scripts/validate-agent-docs.py --repo-root .; echo "exit=$?"
cp /tmp/vault-docs-backup.md base-apps/vault/docs.md
```
Expected: an `ERROR` mentioning `kind` and `exit=1`; after restoring, `python scripts/validate-agent-docs.py --repo-root .` passes again (exit 0). Confirm `git status` shows `base-apps/vault/docs.md` unmodified after restore.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/validate.yaml
git commit -m "ci(agent-docs): validate the agent-docs contract on PRs"
```

---

## Final verification

- [ ] Run the full validator: `python scripts/validate-agent-docs.py --repo-root .` → exit 0, 4 apps in scope, 0 warnings.
- [ ] Run validator tests: `python -m pytest tests/agent-docs/ -v` → all pass.
- [ ] `wc -l CLAUDE.md` → < 200; `grep '@AGENTS.md' CLAUDE.md` → match.
- [ ] `git log --oneline` shows one commit per task, all on `docs/agent-ready-docs-framework` (none on `main`).
- [ ] Confirm the only modified existing `base-apps/**/*.yaml` manifests are the 4 pilot Applications receiving a `directory.exclude: catalog-info.yaml` guard (`base-apps/chores-tracker-backend.yaml`, `base-apps/vault.yaml`, `base-apps/argo-cd.yaml`, `base-apps/cert-manager-config.yaml`); no other existing manifest is modified.
- [ ] For the Terraform change (`terraform/roots/asela-cluster/argocd.tf`), run the repo's standard Terraform validation (mirrored by Atlantis on the PR): `terraform fmt -check -recursive terraform/`, `cd terraform/roots/asela-cluster && terraform init -backend=false && terraform validate`, and `tflint --format compact`.

## Post-review additions (GitOps safety)

Added during review to make co-located `catalog-info.yaml` safe under Argo CD (these are not part of the original Task 1–9 sequence; they resolve a deployment hazard the reviews surfaced):

- **Argo CD `resource.exclusions`** (`terraform/roots/asela-cluster/argocd.tf`): exclude `backstage.io` `Component`/`Resource` so Argo CD ignores catalog-info objects cluster-wide. Enforced by the validator (`check_argocd_backstage_exclusion`, which parses the heredoc — a commented or malformed entry fails).
- **Per-app `directory.exclude: catalog-info.yaml`** on the 4 pilot Applications: in-band guard so a merge is safe regardless of when the Terraform change is applied.

## Success criteria (from the spec)

- The 4 pilot apps each have a valid, cross-linked three-file contract. ✅ Tasks 4–7.
- Atlas → index → app traversal reaches any pilot runbook in a few hops. ✅ Task 3 + 4–7.
- The validator passes on the pilot and fails on a deliberately broken contract. ✅ Task 9 Step 4.
- CLAUDE.md is under 200 lines and imports AGENTS.md. ✅ Task 8.
- No workload behavior changes; the only control-plane change is the scoped Argo CD `backstage.io` `resource.exclusions` addition, which must be applied before the co-located `catalog-info.yaml` files sync (see the GitOps-safety constraint). ✅ Final verification last checkbox.
