# Agent-Ready Docs Framework — Design

- **Date:** 2026-07-08
- **Status:** Approved (design); implementation plan to follow
- **Scope:** Phase 0–2 (docs framework + CI). Retrieval wiring (kagent/Backstage MCP) is a separate later spec.

## Problem

This repo is a live GitOps control plane (~90 apps under `base-apps/`, Terraform, Crossplane, Vault). Operational knowledge is spread across manifests, `docs/`, and people's heads. We already run agent tooling — kagent, Backstage, `oncall-agent`/`oncall-crewai`, ollama — but there is no git-native, structured knowledge layer designed for those agents to navigate. Without one, agents (and humans) cannot reliably answer "what is this app, who owns it, what breaks it, how do I operate it" from the repo alone.

The goal is a **docs-as-code framework**: a git-tracked, markdown-first knowledge layer whose *structure* is designed for agent traversal, feeding incident triage, Q&A/knowledge lookup, and operational how-to. This spec covers building the framework and validating it on a thin pilot; it deliberately does **not** design the agent retrieval loop (Phase 2, separate spec).

## Goals

- Define a per-app documentation **contract** that separates structured facts from narrative knowledge without duplication.
- Provide a top-level **navigation layer** (atlas + per-directory indexes) that an agent reads first and traverses down.
- Validate the framework on a **thin pilot** of 4 representative apps before scaling.
- Enforce the contract and freshness in **CI from the start** so docs cannot silently drift.
- Leave the framework **retrieval-ready** for a later kagent/Backstage MCP phase.

## Non-goals

- Designing or deploying the kagent agents, Backstage MCP Actions backend, or any retrieval loop (separate spec).
- Documenting all ~90 apps in this pass (only the pilot 4 are deep; the rest get stub index rows).
- Migrating existing `docs/` content or changing Backstage catalog internals beyond adding `catalog-info.yaml` files.
- Any change to Kubernetes, Terraform, Helm, Crossplane, Vault, ingress, IAM, or RBAC runtime behavior. This is docs + CI only.

## Chosen approach

**Co-located per-app docs (Approach A).** Documentation lives next to the manifests it describes, so a manifest change and its doc update land in the same PR (the strongest drift defense), it honors the retrieval principles of keeping related facts physically close and being explicit, and it matches the repo's existing per-app-directory convention (`base-apps/<app>/`).

Rejected alternatives:
- **Centralized `knowledge/` tree** — physically separates docs from code, so they drift more easily and manifest PRs don't naturally touch them; weaker CI enforcement.
- **Backstage TechDocs-native** — couples authoring to the TechDocs/MkDocs toolchain, is less friendly to git-grep agents, and front-loads the retrieval wiring we chose to defer.

## Design

### 1. The per-app doc contract (two layers, one contract)

Each in-scope `base-apps/<app>/` directory contains three files:

| File | Layer | Holds | Primary consumer |
|---|---|---|---|
| `catalog-info.yaml` | Structured | name, owner, system, `dependsOn`, lifecycle, tags, links, annotations | Backstage catalog (agents later) |
| `docs.md` | Narrative | what it is, architecture/data-flow, where config lives, gotchas/tribal knowledge | git-grep agents, humans |
| `runbook.md` | Operational | failure modes as symptom → check → fix; how-to (deploy/rotate/scale) | triage/oncall agents |

**The contract that prevents duplication and drift:**
- Structured fields (owner, dependencies, namespace, lifecycle) are authored **only** in `catalog-info.yaml`.
- Narrative and operational prose are authored **only** in `docs.md` / `runbook.md`.
- `docs.md` and `runbook.md` frontmatter carry `catalog_entity: <name>` pointing at the entity in `catalog-info.yaml`.
- `catalog-info.yaml` carries an annotation `agent-docs/path: docs.md` pointing back at the narrative.
- Markdown may *reference* catalog facts but must not restate them as an independent source of truth.

**Frontmatter schema** (required in `docs.md` and `runbook.md`):

```yaml
---
app: chores-tracker-backend            # matches the base-apps/<app> directory name
catalog_entity: chores-tracker-backend # must resolve to a metadata.name in a catalog-info.yaml
kind: docs                             # one of: docs | runbook
namespace: chores-tracker
last_reviewed: 2026-07-08              # ISO date; drives the staleness check
status: current                        # one of: current | wip | deprecated
tags: [fastapi, mysql, jwt]
sources:                               # authoritative files this doc summarizes; must exist
  - base-apps/chores-tracker-backend/deployments.yaml
  - base-apps/chores-tracker-backend/external-secrets.yaml
---
```

`catalog-info.yaml` follows the standard Backstage entity schema (`kind: Component` or `Resource`) plus the `agent-docs/path` annotation. Example:

```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: chores-tracker-backend
  namespace: chores-tracker
  annotations:
    agent-docs/path: docs.md
  tags: [fastapi, mysql]
spec:
  type: service
  lifecycle: production
  owner: platform
  system: chores-tracker
  dependsOn:
    - resource:default/vault
    - component:default/chores-tracker-frontend
```

### 2. Top-level navigation layer

- **`INFRASTRUCTURE_ATLAS.md`** (repo root) — the agent's front door. Sections:
  1. **System context** — cluster endpoint (`192.168.0.100:6443`), Vault (`vault.vault.svc:8200`), Terraform state (S3 `asela-terraform-states`).
  2. **Platform topology** — ArgoCD master-app → `base-apps/`, Kargo, Terraform roots, Crossplane.
  3. **GitOps data flow** — commit → ArgoCD sync → cluster; secrets via Vault/ESO.
  4. **Cross-cutting concerns** — secrets (Vault/ExternalSecrets), ingress (istio/nginx), observability (logging/loki/coroot).
  5. **Known gaps** — table of gaps + recommendation + source.
  6. **Source registry** — domain → authoritative files/dirs.
  7. **For agents** — the traversal path and the "sources are authoritative, atlas is a lens" rule.

  The atlas is a navigation/summary layer only. When a summary looks suspicious, the agent returns to the listed source files.

- **`base-apps/_INDEX.md`** — one row per app: `app | purpose | namespace | docs | runbook | catalog`. Deep rows for pilot apps; stub rows (purpose + namespace, doc columns marked TODO) for the rest.
- **`terraform/_INDEX.md`** and **`docs/_INDEX.md`** — the same index treatment for those trees.

**Agent traversal path:** `INFRASTRUCTURE_ATLAS.md` → directory `_INDEX.md` → per-app `docs.md`/`runbook.md` → `sources:` files.

### 3. Agent entry-point wiring

- Root **`CLAUDE.md`** points at `INFRASTRUCTURE_ATLAS.md` as the starting point and briefly describes the doc contract. Keep it **under 200 lines**; if it grows, move detail into the atlas.
- `CLAUDE.md` imports `@AGENTS.md` (or symlink) so Claude Code and AGENTS.md-based agents share one instruction file without duplication.
- The atlas's "For agents" section documents the traversal path so a fresh agent session self-orients.

### 4. Pilot slice (4 apps)

Chosen to exercise four distinct shapes so the contract is validated broadly, not just on one archetype:

| App | Shape | Why |
|---|---|---|
| `chores-tracker-backend` | Typical app workload (FastAPI/MySQL, Vault secrets) | Becomes the reusable app template |
| `vault` | Stateful, high-stakes platform infra | Exercises a serious operational runbook |
| `argo-cd` | The GitOps control plane itself | Central to triage; meta-important |
| `cert-manager` | Cross-cutting platform infra (TLS/DNS-01 + ExternalSecrets/Vault) | Exercises a non-app, platform-wide component with its own directory (`istio-istiod` is a bare app YAML with no directory, so it can't carry the co-located contract) |

All other apps get a stub `_INDEX.md` row now; deep docs are backfilled later under CI gating.

### 5. CI enforcement (freshness from the start)

A validator, `scripts/validate-agent-docs.py`, run in CI alongside the existing `yamllint`/`kubeconform` steps. It reads an explicit **in-scope list** (starts as the pilot 4; grows as apps are backfilled) and checks:

1. **Contract presence** — each in-scope `base-apps/<app>/` has `catalog-info.yaml`, `docs.md`, and `runbook.md`.
2. **Frontmatter validity** — required keys present; `last_reviewed` parses as a date; `kind` ∈ {docs, runbook}; `status` ∈ {current, wip, deprecated}; `catalog_entity` resolves to a `metadata.name` in some `catalog-info.yaml`.
3. **Link resolution** — every markdown link and every `sources:` path resolves to a file that exists.
4. **Staleness** — flag docs whose `last_reviewed` is older than 180 days. Configurable warn-vs-fail; default warn during rollout.
5. **Index coverage** — every directory in `base-apps/` appears as a row in `base-apps/_INDEX.md`.

A new app must satisfy the contract, or carry an explicit opt-out marker (a documented annotation), to pass CI. Wiring is added to the existing CI workflow as a new step; failures block merge (except staleness while in warn mode).

### 6. Retrieval readiness (Phase 2 preview — not designed here)

The framework is shaped so a later spec can wire agents with minimal rework: kagent triage/Q&A/how-to agents consume `docs.md`/`runbook.md` via git; structured facts surface through Backstage's MCP Actions backend from `catalog-info.yaml`. The `sources:` and `catalog_entity` links are exactly the join keys those agents need. No retrieval components are built in this spec.

## Phasing / deliverables

- **Phase 0 — framework definition:** `_TEMPLATE` files (`catalog-info.yaml`, `docs.md`, `runbook.md`), a short frontmatter schema reference, the `INFRASTRUCTURE_ATLAS.md` skeleton, `_INDEX.md` scaffolds for `base-apps/`, `terraform/`, `docs/`, and `CLAUDE.md`/`AGENTS.md` wiring.
- **Phase 1 — pilot population:** deep `catalog-info.yaml` + `docs.md` + `runbook.md` for the 4 pilot apps; their `_INDEX.md` rows filled in.
- **Phase 2 — CI validator:** `scripts/validate-agent-docs.py` + wiring into CI, scoped to the pilot in-scope list.
- **Later (separate spec):** kagent/Backstage MCP retrieval loop.

## Success criteria

- The 4 pilot apps each have a valid, cross-linked three-file contract.
- `INFRASTRUCTURE_ATLAS.md` lets a fresh agent session reach any pilot app's runbook via atlas → index → app in a small number of hops.
- The CI validator passes on the pilot and fails on a deliberately broken contract (missing file, bad frontmatter, dangling `sources:` path).
- `CLAUDE.md` is under 200 lines and imports `AGENTS.md`.
- No runtime (Kubernetes/Terraform/Vault/ingress/RBAC) behavior changes.

## Risks & mitigations

- **Drift** — docs go stale. *Mitigation:* co-location (same-PR updates), `last_reviewed` staleness check, "sources authoritative" rule.
- **Structure locks in before validation** — *Mitigation:* thin pilot across four shapes before scaling to 90 apps.
- **Duplication with Backstage catalog** — *Mitigation:* the two-layer contract; structured facts only in `catalog-info.yaml`.
- **CI friction blocks unrelated PRs** — *Mitigation:* explicit in-scope list (pilot only at first) and staleness in warn mode during rollout.

## Resolved during planning

- **`owner`/`system` taxonomy:** the repo currently has no `catalog-info.yaml` files, so there is no in-repo convention to match. The plan defines defaults: `owner: platform` for shared infra, `owner` set to the app's team where known; `system` groups related components (e.g. `chores-tracker`). Revisit if/when the live Backstage catalog's taxonomy is confirmed.
- **CI validator form:** the repo has no Makefile, so the validator is a standalone `scripts/validate-agent-docs.py` invoked directly from CI (a new job in `.github/workflows/validate.yaml`).
- **4th pilot app:** `istio-istiod` is a bare app YAML with no directory, so it was swapped for `cert-manager` (which has a directory and is cross-cutting).
