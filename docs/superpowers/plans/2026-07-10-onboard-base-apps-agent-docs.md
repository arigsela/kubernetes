# Onboard 12 base-apps into Agent-Docs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Onboard 12 prioritized `base-apps` into the agent-docs framework — each with `catalog-info.yaml` + `docs.md` + `runbook.md` + an `_INDEX.md` row + a `scope.txt` entry + its Argo Application `directory.exclude` — grounded in real manifests, in 3 PRs of 4 apps.

**Architecture:** Apply one identical **per-app onboarding procedure** (below) to each app via a subagent that reads the app's manifests, then batch 4 apps into one PR. No framework changes; docs are additive and `catalog-info.yaml` is excluded from Argo sync.

**Tech Stack:** Backstage catalog entities (`backstage.io/v1alpha1`) · agent-docs frontmatter contract · `scripts/validate-agent-docs.py` · yamllint/kubeconform (CI) · Argo CD GitOps.

## Global Constraints

- **Grounded, never invented.** Every fact comes from the app's real manifests (`base-apps/<app>/*.yaml`, `base-apps/<app>.yaml`, and any `HelmRelease` `values`). Every path in `sources:` MUST exist. The validator checks `sources:` existence; the reviewer checks accuracy.
- **Match the pilots exactly** — compare each file against `base-apps/chores-tracker-backend/catalog-info.yaml`, `base-apps/vault/docs.md`, `base-apps/vault/runbook.md`.
- **catalog-info.yaml annotations:** only `agent-docs/path: docs.md` + descriptive `tags`. **Do NOT** add `backstage.io/managed-by-location` — Backstage generates that; the pilots don't include it.
- **Qualified refs:** `owner: group:default/platform`; `system: default/<system>`; `dependsOn` only for real dependencies, fully-qualified (`resource:<ns>/<name>`).
- **Entity kind:** `Resource` for platform capabilities others depend on (`nginx-ingress`, `postgresql`); `Component` for deployed apps/services (everything else here).
- **Every app.yaml needs a NEW `directory:` block** — none of the 12 currently have one.
- **Batches (one PR each):** B1 `nginx-ingress, postgresql, chores-tracker, chores-tracker-frontend`; B2 `weather-kitchen-backend, weather-kitchen-frontend, n8n, ollama`; B3 `backstage, atlantis, coroot, logging`.
- **`last_reviewed`** in frontmatter = today's date (`2026-07-10`).

---

## File Structure

Per onboarded app `<app>`:
- Create: `base-apps/<app>/catalog-info.yaml`, `base-apps/<app>/docs.md`, `base-apps/<app>/runbook.md`
- Modify: `base-apps/<app>.yaml` (add `spec.source.directory.exclude`)

Shared (edited once per batch, accumulating):
- Modify: `base-apps/_INDEX.md` (fill the app's stub row)
- Modify: `scripts/agent-docs-scope.txt` (append the app name)

---

## The per-app onboarding procedure (applied to every app)

A subagent onboarding `<app>` (namespace `<ns>`) does all of the following, reading the app's manifests first.

**A. Read the source of truth.** Read `base-apps/<app>.yaml` (the Argo Application: `path`, `destination.namespace`) and every file in `base-apps/<app>/`. If a manifest is a `HelmRelease`/Application, read its `values`/chart ref. Determine: namespace, workloads (Deployment/StatefulSet/etc. or Helm chart), key config, real dependencies (Vault? a DB? ingress?), and the top 2–3 realistic failure modes.

**B. `base-apps/<app>/catalog-info.yaml`** — match this shape (pick `Component`/`Resource`, fill from the manifests; include `dependsOn` only if real):
```yaml
apiVersion: backstage.io/v1alpha1
kind: Component            # or Resource for platform capabilities
metadata:
  name: <app>
  namespace: <ns>
  annotations:
    agent-docs/path: docs.md
  tags: [<3-4 real tags>]
spec:
  type: service            # service | website | library (Component); or a Resource type
  lifecycle: production
  owner: group:default/platform
  system: default/<system>
  dependsOn:               # OMIT this key entirely if there is no real dependency
    - resource:vault/vault
```

**C. `base-apps/<app>/docs.md`** — frontmatter then narrative:
```markdown
---
app: <app>
catalog_entity: <app>
kind: docs
namespace: <ns>
last_reviewed: 2026-07-10
status: current
tags: [<same tags>]
sources:
  - base-apps/<app>/<real-file>.yaml     # only files that exist
---

# <app>

<2-4 short sections: what it is; how it's deployed (manifests/Helm); key
configuration; how it wires to other apps — all from the manifests read in A.>
```

**D. `base-apps/<app>/runbook.md`** — frontmatter (`kind: runbook`) then real failure modes:
```markdown
---
app: <app>
catalog_entity: <app>
kind: runbook
namespace: <ns>
last_reviewed: 2026-07-10
status: current
tags: [<same tags>]
sources:
  - base-apps/<app>/<real-file>.yaml
---

# <app> runbook

## <Symptom, e.g. "Pods CrashLooping">
- **Check:** `kubectl -n <ns> ...`  (exact, runnable)
- **Fix:** <recommend a PR to arigsela/kubernetes; never a live mutation>
```

**E. `base-apps/_INDEX.md`** — replace the app's empty stub row (`| <app> | | | | | |`) with:
```
| <app> | <one-line purpose> | <ns> | docs.md | runbook.md | catalog-info.yaml |
```

**F. `scripts/agent-docs-scope.txt`** — append a line with just `<app>`.

**G. `base-apps/<app>.yaml`** — add a `directory` block under `spec.source`, as a sibling of `path` (none of the 12 have one yet):
```yaml
    path: base-apps/<app>
    directory:
      # catalog-info.yaml is a Backstage entity, not a Kubernetes manifest.
      # Exclude it so Argo CD does not try to apply it and fail sync.
      exclude: catalog-info.yaml
```

**H. Per-app self-check** (the subagent runs before reporting done):
```bash
# every sources: path exists
python3 - <<'PY'
import yaml
for f in ('docs','runbook'):
    fm=yaml.safe_load(open(f'base-apps/<app>/{f}.md').read().split('---')[1])
    for s in fm.get('sources',[]):
        import os; assert os.path.exists(s), f"missing source: {s}"
print('sources ok')
PY
```

**Reviewer criteria** (per app): entity kind correct; `dependsOn` real and qualified; `sources:` all exist and are the right files; runbook failure modes are app-specific (not boilerplate) with runnable checks; index row + scope entry + `directory.exclude` present; no invented resource names.

---

## Task 1: Batch 1 — nginx-ingress, postgresql, chores-tracker, chores-tracker-frontend

**Files:** per the per-app procedure, for each of the 4 apps, plus the shared `_INDEX.md` and `scripts/agent-docs-scope.txt`.

**Interfaces:**
- Produces: 4 onboarded apps (scope grows from 4 → 8) — the pattern Batches 2–3 repeat.

- [ ] **Step 1: Branch**

```bash
cd ~/git/kubernetes && git checkout main && git pull
git checkout -b docs/onboard-base-apps-batch1
```

- [ ] **Step 2: Onboard each of the 4 apps via the per-app procedure**

For `nginx-ingress` (Resource; ingress controller), `postgresql` (Resource; CloudNativePG cluster), `chores-tracker` (the chores-tracker namespace/DB layer), `chores-tracker-frontend` (Component; HTMX/static frontend): apply procedure steps A–H. Note real dependencies (e.g. `chores-tracker-frontend` → the backend/ingress; apps using the DB → `resource:postgresql/<name>`).

- [ ] **Step 3: Validate the whole batch**

```bash
python3 scripts/validate-agent-docs.py
YL=/Users/arisela/.claude/jobs/3654b4c1/tmp/yl/bin/yamllint
$YL -c .yamllint.yaml base-apps/nginx-ingress/*.yaml base-apps/postgresql/*.yaml base-apps/chores-tracker/*.yaml base-apps/chores-tracker-frontend/*.yaml base-apps/{nginx-ingress,postgresql,chores-tracker,chores-tracker-frontend}.yaml
kubectl apply --dry-run=server -f base-apps/nginx-ingress.yaml -f base-apps/postgresql.yaml -f base-apps/chores-tracker.yaml -f base-apps/chores-tracker-frontend.yaml 2>&1 | grep -v last-applied
```
Expected: `agent-docs validation passed (8 apps in scope, ...)`; yamllint clean; each Application `configured (server dry run)`. (Markdown files aren't yamllint/kubeconform targets; the validator covers their frontmatter.)

- [ ] **Step 4: Commit, PR, merge**

```bash
git add base-apps/nginx-ingress base-apps/postgresql base-apps/chores-tracker base-apps/chores-tracker-frontend \
        base-apps/nginx-ingress.yaml base-apps/postgresql.yaml base-apps/chores-tracker.yaml base-apps/chores-tracker-frontend.yaml \
        base-apps/_INDEX.md scripts/agent-docs-scope.txt
git commit -m "docs(agent-docs): onboard nginx-ingress, postgresql, chores-tracker(+frontend)"
git push -u origin docs/onboard-base-apps-batch1
gh pr create --fill
```
Merge after CI green (`agent-docs-validate`, `yaml-lint`, `kubernetes-validate`) + review.

---

## Task 2: Batch 2 — weather-kitchen-backend, weather-kitchen-frontend, n8n, ollama

**Files:** per-app procedure for the 4 apps + shared files.

**Interfaces:**
- Consumes: Task 1's pattern. Produces: scope 8 → 12.

- [ ] **Step 1: Branch from updated main**

```bash
cd ~/git/kubernetes && git checkout main && git pull
git checkout -b docs/onboard-base-apps-batch2
```

- [ ] **Step 2: Onboard the 4 apps via the per-app procedure**

`weather-kitchen-backend` (Component; likely `dependsOn` Vault/DB), `weather-kitchen-frontend` (Component; → the backend), `n8n` (Component; automation, likely a DB dependency), `ollama` (Component; LLM serving, used by kagent embeddings). Use `system: default/weather-kitchen` for the two weather-kitchen apps; pick sensible systems for `n8n`/`ollama` (e.g. `default/platform-automation`, `default/platform-ai`).

- [ ] **Step 3: Validate**

```bash
python3 scripts/validate-agent-docs.py
YL=/Users/arisela/.claude/jobs/3654b4c1/tmp/yl/bin/yamllint
$YL -c .yamllint.yaml base-apps/weather-kitchen-backend/*.yaml base-apps/weather-kitchen-frontend/*.yaml base-apps/n8n/*.yaml base-apps/ollama/*.yaml base-apps/{weather-kitchen-backend,weather-kitchen-frontend,n8n,ollama}.yaml
kubectl apply --dry-run=server -f base-apps/weather-kitchen-backend.yaml -f base-apps/weather-kitchen-frontend.yaml -f base-apps/n8n.yaml -f base-apps/ollama.yaml 2>&1 | grep -v last-applied
```
Expected: `agent-docs validation passed (12 apps in scope, ...)`; yamllint clean; dry-run OK.

- [ ] **Step 4: Commit, PR, merge**

```bash
git add base-apps/weather-kitchen-backend base-apps/weather-kitchen-frontend base-apps/n8n base-apps/ollama \
        base-apps/weather-kitchen-backend.yaml base-apps/weather-kitchen-frontend.yaml base-apps/n8n.yaml base-apps/ollama.yaml \
        base-apps/_INDEX.md scripts/agent-docs-scope.txt
git commit -m "docs(agent-docs): onboard weather-kitchen(+frontend), n8n, ollama"
git push -u origin docs/onboard-base-apps-batch2
gh pr create --fill
```
Merge after CI + review.

---

## Task 3: Batch 3 — backstage, atlantis, coroot, logging

**Files:** per-app procedure for the 4 apps + shared files.

**Interfaces:**
- Consumes: Task 2's pattern. Produces: scope 12 → 16 (final).

- [ ] **Step 1: Branch from updated main**

```bash
cd ~/git/kubernetes && git checkout main && git pull
git checkout -b docs/onboard-base-apps-batch3
```

- [ ] **Step 2: Onboard the 4 apps via the per-app procedure**

`backstage` (Component; the IDP — depends on postgresql + the github token + kubernetes-ingestor; cite the real files under `base-apps/backstage/`), `atlantis` (Component; Terraform automation, OpenTofu — Helm/config), `coroot` (Component; observability), `logging` (Component; the alloy/loki logging stack — has many files). For Helm-only apps (`atlantis`, `coroot`), `sources:` cite the in-repo files that exist (the HelmRelease/values/config) and the narrative summarizes chart behavior — no invented upstream paths.

- [ ] **Step 3: Validate**

```bash
python3 scripts/validate-agent-docs.py
YL=/Users/arisela/.claude/jobs/3654b4c1/tmp/yl/bin/yamllint
$YL -c .yamllint.yaml base-apps/backstage/*.yaml base-apps/atlantis/*.yaml base-apps/coroot/*.yaml base-apps/logging/*.yaml base-apps/{backstage,atlantis,coroot,logging}.yaml
kubectl apply --dry-run=server -f base-apps/backstage.yaml -f base-apps/atlantis.yaml -f base-apps/coroot.yaml -f base-apps/logging.yaml 2>&1 | grep -v last-applied
```
Expected: `agent-docs validation passed (16 apps in scope, ...)`; yamllint clean; dry-run OK.

- [ ] **Step 4: Commit, PR, merge**

```bash
git add base-apps/backstage base-apps/atlantis base-apps/coroot base-apps/logging \
        base-apps/backstage.yaml base-apps/atlantis.yaml base-apps/coroot.yaml base-apps/logging.yaml \
        base-apps/_INDEX.md scripts/agent-docs-scope.txt
git commit -m "docs(agent-docs): onboard backstage, atlantis, coroot, logging"
git push -u origin docs/onboard-base-apps-batch3
gh pr create --fill
```
Merge after CI + review.

- [ ] **Step 5: Post-merge verification (optional)**

After Backstage's next provider scan (~30 min), the 12 new `catalog-info.yaml` appear in the catalog:
```bash
POD=$(kubectl -n backstage get pods -o jsonpath='{.items[0].metadata.name}')
kubectl -n backstage exec "$POD" -- node -e 'require("http").get("http://localhost:7007/api/catalog/entities?limit=2000",r=>{let b="";r.on("data",d=>b+=d);r.on("end",()=>{const d=JSON.parse(b);console.log("catalog entities:",d.length)})})'
```

---

## Done criteria

- All 12 apps have `catalog-info.yaml` + `docs.md` + `runbook.md`, an `_INDEX.md` row, a `scope.txt` entry, and their Argo Application excludes `catalog-info.yaml`.
- `validate-agent-docs.py` passes with **16 apps in scope**, 0 errors; all 3 batch PRs merged with green CI.
- Docs cite only real `sources:`; runbooks are app-specific; no invented resource names.
- No Argo sync errors on the onboarded app directories.
