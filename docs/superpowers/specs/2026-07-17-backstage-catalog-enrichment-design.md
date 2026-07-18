# Design: Backstage catalog enrichment roadmap (Phases 0–4)

**Date:** 2026-07-17
**Status:** design — pending user review
**Topic:** Enrich the Backstage developer-portal experience for the `base-apps/*`
applications: fix the platform taxonomy so ownership/system relations actually
resolve, then add API entities, richer relations + full catalog coverage,
operational links, and in-portal TechDocs. ArgoCD/Grafana entity cards are
deferred to a separate spec.

## Goal

Turn the current catalog — 18 hand-authored `Component`/`Resource` entities plus
TeraSky-ingested cluster/Crossplane resources — into a coherent, navigable graph
where every entity has a resolvable owner, system, and domain; exposes its API;
links to its live operational surfaces (ArgoCD, Grafana, coroot, ingress); and
renders its existing `docs.md`/`runbook.md` as searchable in-portal docs. Ship in
low-blast-radius phases: GitOps-auto-synced catalog YAML first, image changes
last.

Two repositories are in scope:

- **`arigsela/kubernetes`** (this repo) — catalog YAML under `base-apps/*/` and
  `catalog/`.
- **`arigsela/backstage`** — the portal image (`backstage-portal`), whose
  `app-config.yaml` / `app-config.production.yaml`, `Dockerfile`, and plugin set
  are baked in. Clone: `/Users/arisela/git/backstage`.

## Decisions (settled during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Output shape | **One phased roadmap spec** | Keep the 5 enrichment areas coherent and correctly ordered; each phase still spins into its own implementation plan. |
| Phase scope | **Phases 0–4 here; Phase 5 (ArgoCD/Grafana) split out** | Phase 5 is the only heavy frontend+backend-code phase; prove the lighter phases first. |
| API spec source | **Live-fetch via `$text`** | Point `spec.definition` at each app's live spec endpoint (FastAPI `/openapi.json`, dex OIDC discovery). Always fresh, zero duplication. Uses internal service URLs. |
| TechDocs build | **Local build, in-image** | `generator.runIn: 'local'` + `mkdocs` baked into the image. No CI/S3/new infra; fine for single-replica homelab. Documented migration path to external+S3 later. |
| Phase 2 coverage | **Full, batched** | Every app gets a `catalog-info.yaml`; CRD/policy bundles become lightweight `Resource`s. |
| Doc reuse | **Reuse `docs.md` + `runbook.md` verbatim** | Per-app `mkdocs.yml` with `docs_dir: '.'`; no content migration. |

## Grounding: current-state facts (verified against the clone)

Installed and working (do not rebuild):

- **TechDocs** backend + frontend + search collator; `api-docs` frontend;
  Kubernetes plugin (`multiTenant`, `serviceAccount`); TeraSky
  `kubernetes-ingestor` + `crossplane-resources`; Scaffolder (with 4 templates +
  custom actions); MCP read-only catalog server; PG search; catalog-graph.
- Catalog global rules already `allow: [Component, System, API, Resource, Location]`
  (`app-config.yaml:227`) — so `kind: API` from discovery works today.
- GitHub discovery provider `arigsela-kubernetes` scans
  `base-apps/*/catalog-info.yaml` every 30 min (`app-config.yaml:331`).

Not installed: any ArgoCD plugin, any Grafana plugin, GitHub-Actions plugin.
(These gate Phase 5, deferred.)

Two real gaps the clone surfaced (both block the platform taxonomy):

1. **Rules bug** — the `platform-entities.yaml` url location is registered
   `rules: allow: [Group, System]` (`app-config.yaml:244-245`). New `Domain` and
   `User` entities are silently rejected.
2. **Production drops the taxonomy** — that location exists only in
   `app-config.yaml` (dev). `app-config.production.yaml` redefines
   `catalog.locations`, and Backstage config merging **replaces arrays** (the
   repo's documented "v1.6 lesson"), so production never loads
   `catalog/platform-entities.yaml`. Neither discovery provider matches
   `catalog/*.yaml` either. Consequence: in production the `platform` Group and
   all 4 Systems are very likely already dangling references.

Concrete coordinates used below:

- `chores-tracker-backend` — svc `chores-tracker-backend.chores-tracker.svc.cluster.local` (port `80` → targetPort `8000`), FastAPI `/openapi.json`.
- `weather-kitchen-backend` — svc `weather-kitchen-backend.weather-kitchen.svc.cluster.local` (port `80` → targetPort `8000`), `/openapi.json`.
- `dex` — svc `dex.dex.svc.cluster.local:5556`, OIDC `/.well-known/openid-configuration`.
- Operational hosts: `argocd.arigsela.com`, `grafana.arigsela.com`, `coroot.arigsela.com`.

---

## Phase 0 — Taxonomy plumbing (backstage repo) · unblocks the fork

**Goal:** make the platform taxonomy (`Group`, `System`, and the fork's new
`Domain` + `User`) load in both dev and production, so every `owner:`/`system:`
reference resolves.

**Changes (`arigsela/backstage`):**

- `app-config.yaml` — widen the `platform-entities.yaml` location rule:
  `allow: [Group, System]` → `allow: [Group, System, Domain, User]`.
- `app-config.production.yaml` — **add** the `platform-entities.yaml` url
  location (currently absent) to `catalog.locations`, with the same widened
  rules, so production loads the taxonomy at all.

**Companion (`arigsela/kubernetes`, done by the fork):** the fork extends
`catalog/platform-entities.yaml` with a `platform` Domain, a `products` Domain,
the 6 missing Systems (`platform-ai`, `platform-automation`, `platform-data`,
`platform-observability`, `platform-tooling`, `weather-kitchen`), `domain:`
fields on all Systems, and an `arigsela` `User` in the `platform` Group.

**Coordination:** the config change and the fork's YAML must land together (or
config first). This spec owns the backstage-repo half; the fork owns the YAML.

**Verify:** after deploy, confirm the Group/Systems/Domains/User resolve — query
the live catalog via the read-only MCP catalog server (`ask hk`: "does the
`platform` Group and `platform-observability` System resolve, and who owns
`kagent`?"). No dangling placeholder entities should remain.

## Phase 1 — API entities (kubernetes repo + one backstage change)

**Goal:** catalog the platform's APIs and wire provider/consumer relations so the
API Explorer and dependency graph populate.

**Changes (`arigsela/kubernetes`)** — define `kind: API` entities in a new
`catalog/api-entities.yaml`, ingested via a dedicated url location (mirroring the
existing `platform-entities.yaml` pattern). They are **not** added as extra
documents inside `base-apps/*/catalog-info.yaml`: `scripts/validate-agent-docs.py`
reads those files with single-document `yaml.safe_load`, and the discovery
provider only scans `base-apps/*/catalog-info.yaml` (a sibling `api.yaml` would be
both un-ingested and Argo-CD-applied). API entities live in `namespace: default`
and are referenced fully-qualified as `default/<name>` (consistent with the
taxonomy). Only the `providesApis`/`consumesApis` fields are added to the existing
Component entities (single-document edits):

- `chores-tracker-backend-api` — `spec.type: openapi`, `spec.lifecycle: production`,
  `spec.owner: group:default/platform`, `spec.system: default/chores-tracker`,
  `spec.definition: { $text: http://chores-tracker-backend.chores-tracker.svc.cluster.local/openapi.json }`
  (service port `80`).
- `weather-kitchen-backend-api` — same pattern, `system: default/weather-kitchen`,
  `$text: http://weather-kitchen-backend.weather-kitchen.svc.cluster.local/openapi.json`.
- `dex` API — `spec.type: openid-connect`,
  `$text: http://dex.dex.svc.cluster.local:5556/.well-known/openid-configuration`.
- On the backend Components: `spec.providesApis: [<api-name>]`.
- On the frontends: `spec.consumesApis:` (chores-tracker-frontend → chores API;
  weather-kitchen-frontend → weather API).

**Changes (`arigsela/backstage`):**

- Register `catalog/api-entities.yaml` as a url location in `catalog.locations`
  (`rules: allow: [API]`), in **both** `app-config.yaml` and
  `app-config.production.yaml` (arrays replace across layers).
- Add `backend.reading.allow` host entries (the three internal service hosts) to
  `app-config.yaml` only — production inherits it (the `backend` object
  deep-merges and prod does not override `reading`; unlike `catalog.locations`,
  an array that must be restated). Backstage's URL reader rejects non-allow-listed
  hosts, so `$text` fails without this. Internal svc URLs avoid public-ingress
  auth gating.

**Verify:** API entities appear under `/api-docs`; each backend page shows a
"Provided APIs" section; `$text` resolves (no catalog processing error).

## Phase 2 — Relations + full coverage (kubernetes repo, pure YAML)

**Goal:** every app is in the catalog with meaningful relations.

**Changes (`arigsela/kubernetes`):**

- **Backfill `catalog-info.yaml`** for all uncovered apps. Operational services
  → `Component` (`type: service`/`website`) or `Resource` (`type: infrastructure`);
  CRD/policy bundles (`kagent-crds`, `agent-sandbox-crds`, `kyverno-policies`) →
  lightweight `Resource` entities under the correct System. Every entity carries
  the standard annotations (`agent-docs/path`, `backstage.io/kubernetes-label-selector`,
  `backstage.io/kubernetes-namespace`) and `owner`/`system`, matching existing
  conventions (see `base-apps/dex/catalog-info.yaml`).
- **Enrich `dependsOn`** where it adds signal: ESO-consumers →
  `resource:external-secrets/external-secrets`; backends → their database
  (`resource:postgresql/postgresql`); components that call an API also get
  `consumesApis`. Avoid noise (no blanket "everything → argo-cd").
- Keep the taxonomy in sync: any new `system:` referenced by a backfilled app
  must exist in `catalog/platform-entities.yaml` (coordinate with Phase 0 / the
  fork).

**Verify:** catalog entity count rises to full app coverage; the System/Domain
pages list their members; no new dangling `system:`/`dependsOn:` references (see
the dangling-ref check under Cross-cutting).

## Phase 3 — Operational links (kubernetes repo, pure YAML)

**Goal:** each entity page becomes an operational jump-off point.

**Changes (`arigsela/kubernetes`)** — per entity:

- `metadata.links:` — ArgoCD app (`https://argocd.arigsela.com/applications/<app>`),
  Grafana (`https://grafana.arigsela.com`), coroot (`https://coroot.arigsela.com`),
  the app's live ingress URL, and the runbook. Each with a `title` and `icon`.
- `backstage.io/source-location: url:https://github.com/arigsela/kubernetes/tree/main/base-apps/<app>/`
  → "View Source".
- Pre-seed `argocd/app-name: <app>` (inert without the plugin; consumed by the
  deferred Phase 5).

**Verify:** entity pages show a populated Links card and a working "View Source".

## Phase 4 — TechDocs (both repos) — local build, in-image

**Goal:** render each app's existing `docs.md` + `runbook.md` as an in-portal,
searchable Docs tab, with zero content migration.

**Changes (`arigsela/backstage`):**

- `Dockerfile` — install `mkdocs-techdocs-core` so the in-process generator can
  shell out to `mkdocs`. **Bootstrap pip via `get-pip.py` with `--no-compile`,
  not `apt-get install python3-pip`**: the image is built for linux/amd64 under
  QEMU emulation on an arm64 host, and Debian's `python3-pip` postinst
  byte-compilation segfaults there (dpkg exit 139). `--no-compile` skips
  byte-compilation at build time; the real amd64 runtime compiles `.pyc` lazily.
- `app-config.yaml` — `techdocs.generator.runIn: 'docker'` → `'local'`
  (builder/publisher stay `'local'`). `app-config.production.yaml` does **not**
  override `techdocs`, so it inherits this (object merge — no restatement needed,
  unlike `catalog.locations`).

**Changes (`arigsela/kubernetes`)** — generated by `scripts/gen-techdocs.py`
(+ `tests/techdocs/` + a `techdocs-validate` CI job running `--check`):

- Per app: `mkdocs.yml` (`docs_dir: docs`, `nav: [Overview: index.md, Runbook:
  runbook.md]`, `plugins: [techdocs-core]`) + `docs/index.md` + `docs/runbook.md`,
  the latter two **copies** of the root `docs.md`/`runbook.md`.
  - **Not `docs_dir: '.'`** — mkdocs 1.6 rejects docs_dir being the config file's
    own directory; the markdown must live in a child subdir.
  - **Not symlinks** — they build locally, but Backstage's GitHub TechDocs fetch
    extracts the tree with tar and drops `..` symlinks.
  - Root `docs.md`/`runbook.md` stay canonical (agent-docs contract untouched);
    the CI `--check` gate fails on drift between them and the `docs/` copies.
- Add `backstage.io/techdocs-ref: dir:.` to each `catalog-info.yaml` (resolves
  relative to the discovered location, `base-apps/<app>/`).
- **Widen each Argo Application `directory.exclude`** from `catalog-info.yaml` to
  `'{catalog-info.yaml,mkdocs.yml}'` — `mkdocs.yml` is a `.yaml` file Argo would
  otherwise try to apply and fail sync. The `docs/*.md` files aren't manifests, so
  Argo ignores them.
- CI `kubeconform` step **skips `*/mkdocs.yml`** (no `kind` → kubeconform fails
  it); yamllint still covers it.

The TechDocs search collator is already installed, so docs become searchable once
built. The EntityPage already renders a Docs tab (`EntityTechdocsContent` at
`packages/app/src/components/catalog/EntityPage.tsx`).

**Verify:** an app's Docs tab renders `docs.md`/`runbook.md`; search returns doc
hits; on-demand build succeeds in-pod (no Docker daemon dependency). All 18 apps
build cleanly under local mkdocs (validated pre-deploy).

## Phase 5 — ArgoCD + Grafana entity cards (deferred to its own spec)

Out of scope here. Requires installing `@roadiehq/backstage-plugin-argo-cd`
(FE+BE) and `@backstage-community/plugin-grafana` (FE), proxy/config to
`argocd.arigsela.com` / `grafana.arigsela.com`, EntityPage cards, two new Vault
tokens (ArgoCD + Grafana) → `backstage-secrets` ExternalSecret → deployment env,
and the `argocd/app-name` / `grafana/tag-selector` annotations. Phase 3 pre-seeds
`argocd/app-name` so this is drop-in later. Will be brainstormed as
`2026-XX-XX-backstage-argocd-grafana-cards-design.md` once Phases 0–4 are proven.

---

## Cross-cutting

**Validation:**

- `yamllint` on all new/edited catalog YAML (repo already uses it).
- A "dangling-ref" check — `scripts/validate-catalog-refs.py` + `tests/catalog-refs/`
  + a `catalog-refs-validate` CI job (following the repo's existing
  `validate-*.py` validator pattern). It collects every referenced `owner:`,
  `system:`, `dependsOn:`, `providesApis:`, `consumesApis:` and asserts each
  target is defined in git (in `catalog/*.yaml` or a `base-apps/*` entity). Built
  in this first plan; extended as coverage grows. (Refs to live-ingested
  kagent/Crossplane entities are out of git scope — allowlisted if they appear.)
- After each deploy, spot-check the live catalog for processing errors via the
  read-only MCP catalog server (`ask hk`).

**Rollout / blast radius:**

- Phases 0–3 are catalog YAML + backstage config; Phases 1–3 auto-sync via Argo
  CD with near-zero risk (read-only catalog data). Phase 0's backstage-config
  change ships with the next image or config reload.
- Phase 4 requires a Backstage **image rebuild** (python + mkdocs) and an image
  tag bump in `base-apps/backstage/deployments.yaml`.

**Risks:**

- **P1 `$text` reachability** — the backend pod must reach the internal service
  URLs at catalog-refresh time; mitigated by using cluster-internal svc DNS and
  `backend.reading.allow`.
- **P4 image size** — +~200MB for python/mkdocs; acceptable for the single-replica
  homelab. Migration to external+S3 build documented as the graduation path.
- **P0 coordination** — the backstage-config change and the fork's taxonomy YAML
  are interdependent; land config first (or together) to avoid a window of
  dangling refs.

## Out of scope

- Phase 5 (ArgoCD/Grafana plugins) — separate spec.
- External+S3 TechDocs pipeline — documented as a future migration, not built now.
- Replacing the allow-all permission policy, GitHub-Actions plugin, custom
  scaffolder templates beyond the existing set.
