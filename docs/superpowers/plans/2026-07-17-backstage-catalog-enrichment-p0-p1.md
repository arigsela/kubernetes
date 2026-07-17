# Backstage Catalog Enrichment — Phase 0 + Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the platform taxonomy resolve in dev and production, and add live-fetched `kind: API` entities for the two FastAPI backends and dex — gated by a new dangling-reference validator.

**Architecture:** Two repos. In `arigsela/kubernetes`: a new `catalog/api-entities.yaml` (ingested via a url location), `providesApis`/`consumesApis` fields on existing Component entities, and a `scripts/validate-catalog-refs.py` validator with pytest + CI job. In `arigsela/backstage`: catalog-location + `reading.allow` config so the taxonomy loads in production and `$text` can fetch internal spec endpoints. This is the first increment of the 6-phase roadmap in `docs/superpowers/specs/2026-07-17-backstage-catalog-enrichment-design.md`.

**Tech Stack:** Backstage (catalog YAML + `app-config`), Python 3.12 + PyYAML + pytest (validator), GitHub Actions (CI), yamllint.

## Global Constraints

- **Block-style YAML only.** The repo's `.yamllint.yaml` extends `default`; flow-style `{ ... }` braces fail the `braces` rule (see commit `1b48d9b`). Use block mappings/sequences. 2-space indent; `line-length` ≤ 200.
- **Backstage config arrays REPLACE across layers, objects merge.** Any `catalog.locations` change must be applied to **both** `app-config.yaml` and `app-config.production.yaml`. `backend.reading.allow` goes in `app-config.yaml` only (production inherits it via object merge).
- **API entities live in `namespace: default`**, referenced fully-qualified as `default/<name>` (consistent with the taxonomy's default-namespace convention).
- **`$text` uses internal cluster service URLs** (`*.svc.cluster.local`); each host must appear in `backend.reading.allow`.
- **Do NOT add `kind: API` as extra documents inside `base-apps/*/catalog-info.yaml`** — `scripts/validate-agent-docs.py:94` reads them with single-document `yaml.safe_load` and the discovery provider only scans that one file per app.
- **Cross-plan coordination:** the platform taxonomy YAML (`catalog/platform-entities.yaml` — the `platform`/`products` Domains, the 6 missing Systems incl. `weather-kitchen`, the `arigsela` User) is delivered by the parallel fork branch. The `weather-kitchen-backend-api`'s `system: default/weather-kitchen` and several pre-existing Component `system:` refs resolve only once that fork is merged. Land the taxonomy fork **before** enabling the hard repo-root CI gate in Task 4.
- Commit messages end with the two trailers used across this repo:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b`.

---

## File Structure

**`arigsela/kubernetes`** (working dir `/Users/arisela/git/kubernetes`, branch `backstage-catalog-enrichment`):
- Create: `scripts/validate-catalog-refs.py` — dangling-reference validator.
- Create: `tests/catalog-refs/test_validate_catalog_refs.py` — pytest (fixture-based).
- Create: `catalog/api-entities.yaml` — the 3 `kind: API` entities.
- Modify: `base-apps/chores-tracker-backend/catalog-info.yaml` — add `providesApis`.
- Modify: `base-apps/chores-tracker-frontend/catalog-info.yaml` — add `consumesApis`.
- Modify: `base-apps/weather-kitchen-backend/catalog-info.yaml` — add `providesApis`.
- Modify: `base-apps/weather-kitchen-frontend/catalog-info.yaml` — add `consumesApis`.
- Modify: `base-apps/dex/catalog-info.yaml` — add `providesApis`.
- Modify: `.github/workflows/validate.yaml` — add `catalog-refs-validate` job.

**`arigsela/backstage`** (working dir `/Users/arisela/git/backstage`, new branch `catalog-enrichment-p0-p1`):
- Modify: `app-config.yaml` — widen taxonomy location rule; add api-entities location; add `backend.reading.allow`.
- Modify: `app-config.production.yaml` — add taxonomy location; add api-entities location.

---

## Task 1: Dangling-reference validator (kubernetes repo)

Builds the test harness for all catalog work: a script that fails when any entity reference doesn't resolve. Fixture-based pytest makes it deterministic regardless of fork state.

**Files:**
- Create: `scripts/validate-catalog-refs.py`
- Create: `tests/catalog-refs/test_validate_catalog_refs.py`

**Interfaces:**
- Produces: `scan(repo_root) -> (defined: set[tuple[str,str,str]], unresolved: list[tuple[str,str,str]])` where each `unresolved` item is `(path, field, ref_str)`; and `main()` (argparse `--repo-root`, returns int exit code). Later tasks/CI call `python scripts/validate-catalog-refs.py --repo-root .`.

- [ ] **Step 1: Write the failing test**

Create `tests/catalog-refs/test_validate_catalog_refs.py`:

```python
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate-catalog-refs.py"
_spec = importlib.util.spec_from_file_location("validate_catalog_refs", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _taxonomy(root: Path) -> None:
    _write(
        root,
        "catalog/platform-entities.yaml",
        """
apiVersion: backstage.io/v1alpha1
kind: Group
metadata:
  name: platform
spec:
  type: team
  children: []
---
apiVersion: backstage.io/v1alpha1
kind: System
metadata:
  name: chores-tracker
spec:
  owner: platform
""".lstrip(),
    )


def test_all_references_resolve(tmp_path):
    _taxonomy(tmp_path)
    _write(
        tmp_path,
        "base-apps/app/catalog-info.yaml",
        """
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: app
  namespace: chores-tracker
spec:
  type: service
  owner: group:default/platform
  system: default/chores-tracker
""".lstrip(),
    )
    _defined, unresolved = mod.scan(str(tmp_path))
    assert unresolved == []


def test_missing_system_is_flagged(tmp_path):
    _taxonomy(tmp_path)
    _write(
        tmp_path,
        "base-apps/app/catalog-info.yaml",
        """
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: app
  namespace: chores-tracker
spec:
  type: service
  owner: group:default/platform
  system: default/does-not-exist
""".lstrip(),
    )
    _defined, unresolved = mod.scan(str(tmp_path))
    assert any(ref == "default/does-not-exist" for _p, field, ref in unresolved if field == "system")


def test_provides_and_consumes_api_resolve(tmp_path):
    _taxonomy(tmp_path)
    _write(
        tmp_path,
        "catalog/api-entities.yaml",
        """
apiVersion: backstage.io/v1alpha1
kind: API
metadata:
  name: app-api
spec:
  type: openapi
  owner: group:default/platform
  system: default/chores-tracker
  definition:
    $text: http://app.chores-tracker.svc.cluster.local/openapi.json
""".lstrip(),
    )
    _write(
        tmp_path,
        "base-apps/app/catalog-info.yaml",
        """
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: app
  namespace: chores-tracker
spec:
  type: service
  owner: group:default/platform
  system: default/chores-tracker
  providesApis:
    - default/app-api
""".lstrip(),
    )
    _defined, unresolved = mod.scan(str(tmp_path))
    assert unresolved == []


def test_missing_api_is_flagged(tmp_path):
    _taxonomy(tmp_path)
    _write(
        tmp_path,
        "base-apps/app/catalog-info.yaml",
        """
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: app
  namespace: chores-tracker
spec:
  type: service
  owner: group:default/platform
  system: default/chores-tracker
  consumesApis:
    - default/ghost-api
""".lstrip(),
    )
    _defined, unresolved = mod.scan(str(tmp_path))
    assert any(ref == "default/ghost-api" for _p, field, ref in unresolved if field == "consumesApis")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/arisela/git/kubernetes && python -m pytest tests/catalog-refs/ -q`
Expected: FAIL — `ModuleNotFoundError` / `FileNotFoundError` for `scripts/validate-catalog-refs.py` (script not created yet).

- [ ] **Step 3: Write the validator**

Create `scripts/validate-catalog-refs.py`:

```python
#!/usr/bin/env python3
"""Validate that every Backstage catalog entity reference resolves to an entity
defined in git.

Scans catalog/*.yaml and base-apps/*/catalog-info.yaml, builds the set of
defined entities, then checks every relation reference (owner, system, domain,
subcomponentOf, parent, dependsOn, dependencyOf, providesApis, consumesApis,
memberOf, children) against that set. Exits non-zero if any reference is
unresolved.

Scope: git-defined entities only. References to live-ingested entities (kagent
Agents, Crossplane claims) are not in git; add them to _ALLOWLIST if referenced.
"""
import argparse
import sys
from pathlib import Path

import yaml

# scalar spec field -> default kind when a ref omits its "<kind>:" prefix
_SCALAR_FIELDS = {
    "owner": "group",
    "system": "system",
    "domain": "domain",
    "subcomponentOf": "component",
    "parent": "group",
}
# list spec field -> default kind
_LIST_FIELDS = {
    "dependsOn": "component",
    "dependencyOf": "component",
    "providesApis": "api",
    "consumesApis": "api",
    "memberOf": "group",
    "children": "group",
}

_ALLOWLIST = set()  # (kind, namespace, name) tuples for live-ingested refs


def _parse_ref(ref, default_kind, source_ns):
    """[<kind>:][<namespace>/]<name> -> (kind, namespace, name), all lowercased."""
    kind = default_kind
    namespace = source_ns
    rest = ref
    if ":" in rest:
        kind, rest = rest.split(":", 1)
    if "/" in rest:
        namespace, rest = rest.split("/", 1)
    return (kind.lower(), namespace.lower(), rest.lower())


def _iter_entities(repo_root):
    """Yield (path, entity_dict) for every catalog entity defined in git."""
    root = Path(repo_root)
    paths = sorted((root / "catalog").glob("*.yaml"))
    paths += sorted((root / "base-apps").glob("*/catalog-info.yaml"))
    for path in paths:
        for doc in yaml.safe_load_all(path.read_text()):
            if isinstance(doc, dict) and doc.get("kind") and isinstance(doc.get("metadata"), dict):
                yield path, doc


def scan(repo_root):
    """Return (defined: set, unresolved: list of (path, field, ref_str))."""
    entities = list(_iter_entities(repo_root))
    defined = set()
    for _path, e in entities:
        name = e["metadata"].get("name")
        if not name:
            continue
        ns = (e["metadata"].get("namespace") or "default").lower()
        defined.add((e["kind"].lower(), ns, name.lower()))

    unresolved = []
    for path, e in entities:
        source_ns = (e["metadata"].get("namespace") or "default").lower()
        spec = e.get("spec") or {}
        for field, default_kind in _SCALAR_FIELDS.items():
            ref = spec.get(field)
            if isinstance(ref, str):
                key = _parse_ref(ref, default_kind, source_ns)
                if key not in defined and key not in _ALLOWLIST:
                    unresolved.append((str(path), field, ref))
        for field, default_kind in _LIST_FIELDS.items():
            for ref in spec.get(field) or []:
                if isinstance(ref, str):
                    key = _parse_ref(ref, default_kind, source_ns)
                    if key not in defined and key not in _ALLOWLIST:
                        unresolved.append((str(path), field, ref))
    return defined, unresolved


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args()
    _defined, unresolved = scan(args.repo_root)
    if unresolved:
        print("Unresolved catalog entity references:")
        for path, field, ref in unresolved:
            print(f"  {path}: spec.{field} -> {ref!r}")
        print(f"\n{len(unresolved)} unresolved reference(s).")
        return 1
    print("All catalog entity references resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/arisela/git/kubernetes && python -m pytest tests/catalog-refs/ -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/arisela/git/kubernetes
git add scripts/validate-catalog-refs.py tests/catalog-refs/test_validate_catalog_refs.py
git commit -m "$(printf 'feat(catalog): add dangling-reference validator\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b')"
```

---

## Task 2: Phase 0 — taxonomy plumbing (backstage repo)

Make the platform taxonomy load in **both** dev and production, and admit `Domain` + `User`. Without this, the fork's new entities are silently rejected (dev rule) and never loaded at all (prod).

**Files:**
- Modify: `app-config.yaml` (the taxonomy location rule)
- Modify: `app-config.production.yaml` (add the taxonomy location)

**Interfaces:**
- Consumes: `catalog/platform-entities.yaml` in `arigsela/kubernetes` (delivered by the fork).
- Produces: a production catalog that loads `Group`/`System`/`Domain`/`User` from that file.

- [ ] **Step 1: Create the backstage working branch**

```bash
cd /Users/arisela/git/backstage
git checkout main && git pull --ff-only
git checkout -b catalog-enrichment-p0-p1
```

- [ ] **Step 2: Widen the taxonomy location rule (`app-config.yaml`)**

Find (around line 242):

```yaml
    - type: url
      target: https://github.com/arigsela/kubernetes/blob/main/catalog/platform-entities.yaml
      rules:
        - allow: [Group, System]
```

Replace the rule line with:

```yaml
        - allow: [Group, System, Domain, User]
```

- [ ] **Step 3: Add the taxonomy location to production (`app-config.production.yaml`)**

In `app-config.production.yaml`, inside `catalog.locations:`, immediately after the self-registration block:

```yaml
    - type: file
      target: ./catalog-info.yaml
```

insert:

```yaml
    # Platform taxonomy (Group + Systems + Domains + User) from the kubernetes
    # repo. Production redefines catalog.locations and arrays REPLACE (not merge),
    # so this MUST be restated here or the taxonomy never loads in production.
    - type: url
      target: https://github.com/arigsela/kubernetes/blob/main/catalog/platform-entities.yaml
      rules:
        - allow: [Group, System, Domain, User]
```

- [ ] **Step 4: Validate the config schema**

Run: `cd /Users/arisela/git/backstage && yarn backstage-cli config:check --lax`
Expected: completes with no schema errors (env-var substitution is skipped by `--lax`; we are checking structure, not values).

- [ ] **Step 5: Commit**

```bash
cd /Users/arisela/git/backstage
git add app-config.yaml app-config.production.yaml
git commit -m "$(printf 'fix(catalog): load platform taxonomy in prod + admit Domain/User\n\nProduction redefined catalog.locations without the platform-entities url\nlocation (arrays replace across layers), so the Group/Systems never loaded\nin prod. Add it there and widen the rule to admit Domain + User.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b')"
```

---

## Task 3: Phase 1 — API entities + provider/consumer relations (kubernetes repo)

Define the 3 `kind: API` entities and wire the Components to them. Uses Task 1's validator as the test.

**Files:**
- Create: `catalog/api-entities.yaml`
- Modify: `base-apps/chores-tracker-backend/catalog-info.yaml`
- Modify: `base-apps/chores-tracker-frontend/catalog-info.yaml`
- Modify: `base-apps/weather-kitchen-backend/catalog-info.yaml`
- Modify: `base-apps/weather-kitchen-frontend/catalog-info.yaml`
- Modify: `base-apps/dex/catalog-info.yaml`

**Interfaces:**
- Consumes: `default/platform` Group + `default/chores-tracker` / `default/weather-kitchen` / `default/platform-automation` Systems (the last two from the fork).
- Produces: API entities `default/chores-tracker-backend-api`, `default/weather-kitchen-backend-api`, `default/dex-oidc-api`; Components gain `providesApis`/`consumesApis`.

- [ ] **Step 1: Create `catalog/api-entities.yaml`**

```yaml
# Backstage-only API entities for the platform's HTTP/OIDC APIs. NOT Kubernetes
# manifests and NOT under base-apps/, so Argo CD never syncs them. Ingested via a
# url catalog Location (arigsela/backstage app-config). spec.definition uses the
# $text placeholder to live-fetch each spec from the app's in-cluster service, so
# the Backstage backend (running in-cluster) resolves it; the hosts are allow-
# listed via backend.reading.allow. All in the default namespace so the pilots'
# fully-qualified refs (default/<name>) resolve.
apiVersion: backstage.io/v1alpha1
kind: API
metadata:
  name: chores-tracker-backend-api
  description: Chores Tracker FastAPI backend (OpenAPI).
  tags: [openapi, fastapi]
spec:
  type: openapi
  lifecycle: production
  owner: group:default/platform
  system: default/chores-tracker
  definition:
    $text: http://chores-tracker-backend.chores-tracker.svc.cluster.local/openapi.json
---
apiVersion: backstage.io/v1alpha1
kind: API
metadata:
  name: weather-kitchen-backend-api
  description: Weather Kitchen FastAPI backend (OpenAPI).
  tags: [openapi, fastapi]
spec:
  type: openapi
  lifecycle: production
  owner: group:default/platform
  system: default/weather-kitchen
  definition:
    $text: http://weather-kitchen-backend.weather-kitchen.svc.cluster.local/openapi.json
---
apiVersion: backstage.io/v1alpha1
kind: API
metadata:
  name: dex-oidc-api
  description: Dex OpenID Connect provider (discovery document).
  tags: [oidc, openid-connect]
spec:
  type: openid-connect
  lifecycle: production
  owner: group:default/platform
  system: default/platform-automation
  definition:
    $text: http://dex.dex.svc.cluster.local:5556/.well-known/openid-configuration
```

- [ ] **Step 2: Add `providesApis` to `base-apps/chores-tracker-backend/catalog-info.yaml`**

Change the `spec:` block from:

```yaml
spec:
  type: service
  lifecycle: production
  owner: group:default/platform
  system: default/chores-tracker
  dependsOn:
    - resource:vault/vault
```

to:

```yaml
spec:
  type: service
  lifecycle: production
  owner: group:default/platform
  system: default/chores-tracker
  providesApis:
    - default/chores-tracker-backend-api
  dependsOn:
    - resource:vault/vault
```

- [ ] **Step 3: Add `consumesApis` to `base-apps/chores-tracker-frontend/catalog-info.yaml`**

Change the `spec:` block from:

```yaml
spec:
  type: website
  lifecycle: production
  owner: group:default/platform
  system: default/chores-tracker
  dependsOn:
    - component:chores-tracker/chores-tracker-backend
```

to:

```yaml
spec:
  type: website
  lifecycle: production
  owner: group:default/platform
  system: default/chores-tracker
  consumesApis:
    - default/chores-tracker-backend-api
  dependsOn:
    - component:chores-tracker/chores-tracker-backend
```

- [ ] **Step 4: Add `providesApis` to `base-apps/weather-kitchen-backend/catalog-info.yaml`**

Change the `spec:` block from:

```yaml
spec:
  type: service
  lifecycle: production
  owner: group:default/platform
  system: default/weather-kitchen
  dependsOn:
    - resource:vault/vault
```

to:

```yaml
spec:
  type: service
  lifecycle: production
  owner: group:default/platform
  system: default/weather-kitchen
  providesApis:
    - default/weather-kitchen-backend-api
  dependsOn:
    - resource:vault/vault
```

- [ ] **Step 5: Add `consumesApis` to `base-apps/weather-kitchen-frontend/catalog-info.yaml`**

Change the `spec:` block from:

```yaml
spec:
  type: website
  lifecycle: production
  owner: group:default/platform
  system: default/weather-kitchen
  dependsOn:
    - component:weather-kitchen/weather-kitchen-backend
```

to:

```yaml
spec:
  type: website
  lifecycle: production
  owner: group:default/platform
  system: default/weather-kitchen
  consumesApis:
    - default/weather-kitchen-backend-api
  dependsOn:
    - component:weather-kitchen/weather-kitchen-backend
```

- [ ] **Step 6: Add `providesApis` to `base-apps/dex/catalog-info.yaml`**

Change the `spec:` block from:

```yaml
spec:
  type: service
  lifecycle: production
  owner: group:default/platform
  system: default/platform-automation
  dependsOn:
    - resource:vault/vault
```

to:

```yaml
spec:
  type: service
  lifecycle: production
  owner: group:default/platform
  system: default/platform-automation
  providesApis:
    - default/dex-oidc-api
  dependsOn:
    - resource:vault/vault
```

- [ ] **Step 7: Lint the changed/created YAML**

Run:
```bash
cd /Users/arisela/git/kubernetes
yamllint -c .yamllint.yaml catalog/api-entities.yaml \
  base-apps/chores-tracker-backend/catalog-info.yaml \
  base-apps/chores-tracker-frontend/catalog-info.yaml \
  base-apps/weather-kitchen-backend/catalog-info.yaml \
  base-apps/weather-kitchen-frontend/catalog-info.yaml \
  base-apps/dex/catalog-info.yaml
```
Expected: no errors (warnings for line-length are acceptable per config).

- [ ] **Step 8: Confirm the new API references resolve (validator)**

Run: `cd /Users/arisela/git/kubernetes && python scripts/validate-catalog-refs.py --repo-root . 2>&1 | grep -E 'chores-tracker-backend-api|weather-kitchen-backend-api|dex-oidc-api' || echo "no unresolved API refs"`
Expected: `no unresolved API refs` — the three `providesApis`/`consumesApis` targets are defined in `catalog/api-entities.yaml`.

> Note: the full validator run against the repo root will still list pre-existing dangling **System** refs (`platform-ai`, `platform-automation`, `platform-data`, `platform-observability`, `platform-tooling`, `weather-kitchen`) until the taxonomy fork is merged. That is expected and is closed by the fork + Task 2, not by this task. This step only asserts *this task* added no new unresolved refs.

- [ ] **Step 9: Run the agent-docs validator (unchanged contract must still pass)**

Run: `cd /Users/arisela/git/kubernetes && python scripts/validate-agent-docs.py --repo-root .`
Expected: passes — the edits are single-document additions of `spec` fields; `catalog/api-entities.yaml` is not under `base-apps/` so the contract does not touch it.

- [ ] **Step 10: Commit**

```bash
cd /Users/arisela/git/kubernetes
git add catalog/api-entities.yaml base-apps/chores-tracker-backend/catalog-info.yaml \
  base-apps/chores-tracker-frontend/catalog-info.yaml \
  base-apps/weather-kitchen-backend/catalog-info.yaml \
  base-apps/weather-kitchen-frontend/catalog-info.yaml \
  base-apps/dex/catalog-info.yaml
git commit -m "$(printf 'feat(catalog): add API entities for FastAPI backends + dex OIDC\n\nDefine kind:API entities in catalog/api-entities.yaml (live-fetched via $text\nfrom in-cluster services) and wire providesApis/consumesApis on the chores/\nweather Components and dex.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b')"
```

---

## Task 4: Phase 1 — api-entities location + reading.allow + CI gate

Register the API location so Backstage ingests it, allow-list the `$text` hosts, and turn on the repo-root CI gate now that all references can resolve.

**Files:**
- Modify: `app-config.yaml` (backstage) — api-entities location + `backend.reading.allow`
- Modify: `app-config.production.yaml` (backstage) — api-entities location
- Modify: `.github/workflows/validate.yaml` (kubernetes) — `catalog-refs-validate` job

**Interfaces:**
- Consumes: `catalog/api-entities.yaml` (Task 3), the three service hosts.
- Produces: an ingested API catalog + an enforced dangling-ref CI gate.

- [ ] **Step 1: Add the api-entities location to `app-config.yaml` (backstage)**

Immediately after the widened platform-entities `- type: url` block (from Task 2), insert:

```yaml
    # Platform API entities (kind: API) from the kubernetes repo. Not under
    # base-apps/, so discovered via this dedicated url location rather than the
    # base-apps discovery provider.
    - type: url
      target: https://github.com/arigsela/kubernetes/blob/main/catalog/api-entities.yaml
      rules:
        - allow: [API]
```

- [ ] **Step 2: Add `backend.reading.allow` to `app-config.yaml` (backstage)**

Inside the `backend:` block, after the `cors:` section, add:

```yaml
  # URL-reader allow-list for the $text-fetched API specs (catalog/api-entities.yaml).
  # The reader rejects any host not listed here. Internal svc URLs => no auth/CORS.
  # Only in this base config: production inherits it (backend object deep-merges;
  # prod does not override backend.reading).
  reading:
    allow:
      - host: chores-tracker-backend.chores-tracker.svc.cluster.local
      - host: weather-kitchen-backend.weather-kitchen.svc.cluster.local
      - host: dex.dex.svc.cluster.local:5556
```

- [ ] **Step 3: Add the api-entities location to `app-config.production.yaml` (backstage)**

Immediately after the platform-entities `- type: url` block added in Task 2, insert the same api-entities location (arrays replace across layers, so it must be restated):

```yaml
    - type: url
      target: https://github.com/arigsela/kubernetes/blob/main/catalog/api-entities.yaml
      rules:
        - allow: [API]
```

- [ ] **Step 4: Validate the config schema**

Run: `cd /Users/arisela/git/backstage && yarn backstage-cli config:check --lax`
Expected: no schema errors (notably `backend.reading.allow` validates as an array of `{host}` objects).

- [ ] **Step 5: Commit the backstage changes**

```bash
cd /Users/arisela/git/backstage
git add app-config.yaml app-config.production.yaml
git commit -m "$(printf 'feat(catalog): ingest API entities + allow-list $text spec hosts\n\nRegister catalog/api-entities.yaml as a url location in dev+prod and add\nbackend.reading.allow for the in-cluster service hosts the API $text\nplaceholders fetch from.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b')"
```

- [ ] **Step 6: Add the `catalog-refs-validate` CI job (kubernetes repo)**

> Prerequisite: the taxonomy fork is merged to `main` (or included on this branch), so the repo-root validation is green. If it is not yet merged, do Steps 6–8 in the PR that lands the taxonomy.

In `.github/workflows/validate.yaml`, after the `agent-docs-validate:` job, add:

```yaml
  catalog-refs-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: pip install pyyaml==6.0.2 pytest==8.3.3
      - name: Run catalog-refs validator tests
        run: python -m pytest tests/catalog-refs/ -q
      - name: Validate catalog references resolve
        run: python scripts/validate-catalog-refs.py --repo-root .
```

- [ ] **Step 7: Verify the full validator run is green against the repo**

Run: `cd /Users/arisela/git/kubernetes && python -m pytest tests/catalog-refs/ -q && python scripts/validate-catalog-refs.py --repo-root .`
Expected: `4 passed` then `All catalog entity references resolve.` (requires the taxonomy fork present — see prerequisite).

- [ ] **Step 8: Commit the CI job**

```bash
cd /Users/arisela/git/kubernetes
git add .github/workflows/validate.yaml
git commit -m "$(printf 'ci(catalog): enforce dangling-reference validator\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b')"
```

---

## Task 5: Integration verification (post-deploy, manual)

After the backstage image/config is deployed and Argo CD has synced the catalog YAML, confirm the enrichment took effect. These are live checks, not code.

**Files:** none (verification only).

- [ ] **Step 1: Taxonomy resolves in the live catalog**

Ask the read-only catalog MCP (the `homelab-knowledge` agent — "ask hk"): *"Does the `platform` Group and the `platform-observability` System resolve, and what owns `kagent`? Any catalog entities with kind `Location` reporting processing errors?"*
Expected: Group/Systems/Domains/User resolve; no orphan/placeholder entities for the platform Systems.

- [ ] **Step 2: API entities ingested and specs rendered**

In Backstage, open `/api-docs`. Expected: `chores-tracker-backend-api`, `weather-kitchen-backend-api`, `dex-oidc-api` appear. Open `chores-tracker-backend-api` → the OpenAPI definition renders (Swagger UI), confirming `$text` resolved through `reading.allow`.

- [ ] **Step 3: Relations wired**

Open the `chores-tracker-backend` Component page → "Provided APIs" lists `chores-tracker-backend-api`; open `chores-tracker-frontend` → "Consumed APIs" lists it. Check the catalog graph shows the frontend → API → backend edges.

- [ ] **Step 4: No catalog processing errors**

Confirm (via the catalog page's "processing errors" or backend logs) there are no unresolved-reference or `$text`/reading errors for the new entities.

---

## Self-Review

- **Spec coverage:** Phase 0 (widen rule + prod location) → Task 2. Phase 1 API entities via `catalog/api-entities.yaml` + `$text` → Task 3; location + `reading.allow` → Task 4; provider/consumer relations → Task 3. Cross-cutting dangling-ref validator → Task 1 + Task 4 CI job. Live verification → Task 5. Phases 2–4 and Phase 5 are out of scope for this plan (subsequent plans), per the roadmap.
- **Placeholder scan:** no TBD/TODO; all code and config shown in full; commands have expected output.
- **Type consistency:** `scan(repo_root) -> (defined, unresolved)` and the `(path, field, ref_str)` tuple shape are used identically in Task 1's tests and Task 3/4 invocations. API entity names (`chores-tracker-backend-api`, `weather-kitchen-backend-api`, `dex-oidc-api`) match between `catalog/api-entities.yaml` and every `providesApis`/`consumesApis` reference. `default/<name>` fully-qualified form used consistently.
- **Coordination risk flagged:** the taxonomy fork dependency is called out in Global Constraints and Tasks 3/4 (Steps 8/6-7) so the hard CI gate is not enabled before the taxonomy lands.
