# Onboard Prioritized base-apps into the Agent-Docs Framework — Design

- **Date:** 2026-07-10
- **Status:** Approved (design); implementation plan to follow
- **Depends on:** the agent-ready docs framework (`templates/agent-docs/README.md`, `scripts/validate-agent-docs.py`, `base-apps/_INDEX.md`, `scripts/agent-docs-scope.txt`) and the 4 onboarded pilots (`cert-manager`, `vault`, `argo-cd`, `chores-tracker-backend`).

## Problem

Only 4 of ~38 `base-apps/` are onboarded into the agent-docs framework. The `homelab-knowledge` agent (and Backstage catalog) can only answer richly about those 4. Onboarding more apps extends the agent's grounded coverage and grows the Backstage catalog.

Onboarding all ~34 remaining at once is large; a handful are thin CRD-installer/config dirs where a runbook would be mostly empty. We onboard a **prioritized subset of 12 high-value apps** now and defer the long tail.

## Goal

Fully onboard 12 prioritized `base-apps` into the framework — each with `catalog-info.yaml` + `docs.md` + `runbook.md`, an `_INDEX.md` row, a `scope.txt` entry, and its Argo Application's `directory.exclude` — grounded in the app's real manifests, in 3 reviewable batches (one PR each).

## Scope

**The 12 apps (each has a `base-apps/<app>/` dir), in 3 batches:**
- **Batch 1:** `nginx-ingress`, `postgresql`, `chores-tracker`, `chores-tracker-frontend`
- **Batch 2:** `weather-kitchen-backend`, `weather-kitchen-frontend`, `n8n`, `ollama`
- **Batch 3:** `backstage`, `atlantis`, `coroot`, `logging`

## Non-goals

- The other ~22 dirs (CRD installers, config-only, and top-level Helm apps without a `base-apps/<app>/` dir such as `istio-istiod`/`external-secrets`) — deferred. Their `_INDEX.md` stub rows stay as-is.
- Defining the referenced `System`/`Group` catalog entities so relations resolve in the Backstage UI — that remains the separate, previously-identified follow-up. This work uses qualified refs so it benefits automatically once those entities exist.
- No changes to the framework itself (templates, validator, atlas) or to the 4 existing pilots.
- No runtime/deployment changes — docs are additive; `catalog-info.yaml` is excluded from Argo sync.

## The per-app contract (what "onboarded" means)

For each app, matching the pilots exactly:

1. **`base-apps/<app>/catalog-info.yaml`** — a Backstage entity:
   - `kind: Component` for a deployed application/service; `kind: Resource` for a platform capability others depend on (e.g. `postgresql`, `nginx-ingress`).
   - `metadata`: `name: <app>`, `namespace: <k8s-namespace>`, `annotations` including `agent-docs/path: docs.md` and `backstage.io/managed-by-location: url:https://github.com/arigsela/kubernetes/blob/main/base-apps/<app>/catalog-info.yaml`, and descriptive `tags`.
   - `spec`: `type`, `lifecycle: production`, `owner: group:default/platform`, `system: default/<system>`, and `dependsOn` only where a real dependency exists (e.g. an app that reads Vault → `resource:vault/vault`; a DB-backed app → the DB resource).
2. **`base-apps/<app>/docs.md`** — architecture/config narrative with frontmatter:
   ```
   ---
   app: <app>
   catalog_entity: <app>
   kind: docs
   namespace: <namespace>
   last_reviewed: <today>
   status: current
   tags: [...]
   sources:
     - base-apps/<app>/<real-manifest>.yaml   # every file cited must exist
   ---
   ```
   Body: what it is, how it's deployed (Helm/manifests), key config, how it wires to other apps — all from the real manifests.
3. **`base-apps/<app>/runbook.md`** — same frontmatter shape with `kind: runbook`; body is the app's top 2–3 **real** failure modes as symptom → check (exact `kubectl`) → fix (a PR, not a live mutation).
4. **`base-apps/_INDEX.md`** — fill the app's existing stub row: `| <app> | <purpose> | <namespace> | docs.md | runbook.md | catalog-info.yaml |`.
5. **`scripts/agent-docs-scope.txt`** — append `<app>`.
6. **`base-apps/<app>.yaml`** (Argo Application) — ensure `spec.source.directory.exclude` covers `catalog-info.yaml` (add the `directory` block if absent). The validator (`check_app_directory_exclude`) enforces this; without it Argo CD would try to sync the Backstage entity and fail.

## Quality bar

- **Grounded, not invented.** Every fact (image, namespace, ports, dependencies, config) comes from the app's manifests (and, where a manifest is a `HelmRelease`, its `values`). No invented file paths or resource names — the validator checks `sources:` exist, and the reviewer checks accuracy.
- **Match pilot depth.** Compare against `base-apps/vault/{docs,runbook}.md`: concise but complete; a runbook with genuine, app-specific failure modes (not boilerplate).
- **Correct entity kind & relations.** `Resource` vs `Component` chosen deliberately; `dependsOn` only for real dependencies, fully-qualified (`resource:<ns>/<name>`).

## Execution

Subagent-per-app, batched:

1. For each app in a batch, dispatch an implementer subagent that reads `base-apps/<app>/` (+ `base-apps/<app>.yaml` and any upstream Helm values) and produces the 6 deliverables above.
2. A reviewer subagent checks spec-compliance and factual accuracy against the manifests.
3. After the batch's apps pass review, validate the whole batch and open one PR.

## Validation

Per batch, before the PR and in CI:
- `python3 scripts/validate-agent-docs.py` — contract-file presence for in-scope apps, frontmatter schema, `sources:` existence, `_INDEX.md` coverage, catalog `apiVersion`/`kind`/name match, and the `directory.exclude` guard. Must pass with 0 errors.
- `yamllint -c .yamllint.yaml` on the changed YAML; `kubeconform` (CI) on the catalog-info + app manifests.
- The same three CI checks that gate every PR (`agent-docs-validate`, `yaml-lint`, `kubernetes-validate`).

## Success criteria

1. All 12 apps have valid `catalog-info.yaml` + `docs.md` + `runbook.md`, appear in `_INDEX.md` and `scope.txt`, and their Argo Applications exclude `catalog-info.yaml`.
2. `validate-agent-docs.py` passes (now 16 apps in scope) with 0 errors.
3. Each app's docs cite only real `sources:` and the runbook's failure modes are app-specific and accurate.
4. Post-merge: the new `catalog-info.yaml` files ingest into Backstage via the `arigsela-kubernetes` provider (catalog grows); no Argo sync errors on the onboarded app directories.

## Safety, blast radius & rollback

- **Additive, advisory, read-only.** New docs + catalog entities + index/scope edits; the only manifest touch is adding `directory.exclude` to each Argo Application, which is a sync-safety guard, not a workload change.
- **Argo:** `catalog-info.yaml` is excluded from each app's sync, so Argo never tries to apply a Backstage entity.
- **Rollback:** revert the batch PR. No runtime impact to undo.

## Open questions

- **Entity kind & system naming per app** — resolved per app during implementation from the manifests (e.g. `nginx-ingress`/`postgresql` → `Resource`; user apps → `Component`; systems like `weather-kitchen`, `platform-observability`, `platform-automation` chosen to group logically). The reviewer adjudicates.
- **Apps whose only manifest is a `HelmRelease`** (e.g. `atlantis`, `coroot`, `argo-workflows`) — `sources:` cite the in-repo files that exist (the `HelmRelease`/values), and the narrative summarizes the chart's behavior; no invented upstream paths.
