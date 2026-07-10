# Backstage Catalog Discovery for Agent-Docs `catalog-info.yaml` — Design

- **Date:** 2026-07-09
- **Status:** Approved (design); implementation plan to follow
- **Depends on:** the agent-ready docs framework (per-app `base-apps/<app>/catalog-info.yaml`) and the kagent agent-docs retrieval work. This is "Option B" — wiring the framework's structured `catalog-info.yaml` files into the Backstage catalog.

## Problem

The agent-ready docs framework emits a Backstage-compatible `catalog-info.yaml`
for each app under `base-apps/<app>/`. These files are valid entities, but
**Backstage does not ingest them** — none of the pilot apps (`cert-manager`,
`vault`, `argo-cd`, `chores-tracker-backend`) appear in the live catalog.

Root cause (verified against the running `backstage-portal:v1.1.0` app-config):
Backstage already runs a GitHub entity provider (`catalog.providers.github.arigsela`)
that scans the whole `arigsela` org, but with `catalogPath: '/catalog-info.yaml'`
— **repo root only**. The kubernetes repo has **no** root `catalog-info.yaml`, so
the provider finds nothing there. The per-app files live at
`base-apps/<app>/catalog-info.yaml` and are never scanned.

**Goal:** make Backstage discover and ingest `base-apps/*/catalog-info.yaml`
from the kubernetes repo, so the framework's structured facts (owner, system,
`dependsOn`) show up in the catalog and become queryable — the foundation the
later Backstage-MCP work (v2 of the retrieval agent) builds on.

## Goals

- Backstage auto-discovers every `base-apps/<app>/catalog-info.yaml` in the
  kubernetes repo and ingests each as its declared `Component`/`Resource`.
- New apps that add a `catalog-info.yaml` appear automatically on the next scan
  (no per-app registration).
- No regression to the existing org-wide root provider or any current entity.
- Ship via the established pipeline (edit `arigsela/backstage` app-config →
  rebuild image → bump the pinned tag in this repo via GitOps).

## Non-goals

- No backend code changes — the GitHub entity provider module is already
  installed (`backend.add(import('@backstage/plugin-catalog-backend-module-github'))`).
- No Backstage MCP in this work (that is the separate v2 of the retrieval agent).
- No creation of the referenced `System` entities (`platform-networking`,
  `platform-secrets`, `platform-gitops`, `chores-tracker`). Ingestion succeeds
  without them; they show as unresolved relations. Defining them is optional
  follow-up (see Future work).
- No change to the kubernetes-ingestor, the agent-docs contract, the CI
  validator, or the Argo CD `catalog-info.yaml` exclusion (all already correct).

## Chosen approach

Add a **second, dedicated GitHub entity provider** to the Backstage app-config,
scoped to the kubernetes repo and the `base-apps/*` path. The existing org-wide
root provider is left untouched (additive, no regression). Everything is
declarative; the config lives with the app in `arigsela/backstage` and reaches
the cluster as a rebuilt image plus a GitOps tag bump in this repo.

Rejected alternatives:
- **Broaden the existing provider** to `catalogPath: '/**/catalog-info.yaml'` —
  fewer stanzas, but discovers `catalog-info.yaml` at any depth in **every** org
  repo, which can pull in unexpected entities later. The dedicated provider is
  explicitly scoped.
- **GitOps-only layer in this repo** (a ConfigMap + `--config` + args override,
  no image rebuild) — fights Backstage's documented "config layering merges
  objects but REPLACES arrays" behavior and requires replicating the image's
  entrypoint; fragile, can break startup. Rejected in favor of editing the
  config at its source.
- **Static `url` Locations / manual `catalog-import`** — one entry per app, no
  auto-discovery of new apps. Fine for a one-off validation, not for the
  framework's steady state.

## Design

### Components

Three declarative pieces across two repos:

```
arigsela/backstage (source repo)
  app-config.yaml
    catalog.providers.github:
      arigsela:            # UNCHANGED — org-wide root provider
        catalogPath: '/catalog-info.yaml'
      arigsela-kubernetes: # NEW — dedicated base-apps provider
        organization: 'arigsela'
        catalogPath: '/base-apps/*/catalog-info.yaml'
        filters:
          repository: '^kubernetes$'
        schedule:
          frequency: { minutes: 30 }
          timeout: { minutes: 3 }
  -> ./scripts/build-and-push.sh --version v1.2.0
  -> ECR: 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.2.0 (+ :latest)

kubernetes (this repo)
  base-apps/backstage/deployments.yaml
    image: ...backstage-portal:v1.2.0   # bumped from v1.1.0
  -> Argo CD `backstage` app syncs -> new pod runs the new config
```

### 1. `arigsela/backstage` app-config change

Add the `arigsela-kubernetes` provider entry under the existing
`catalog.providers.github` block in `app-config.yaml` (the production overlay does
not redefine `catalog.providers`, so a single-file edit is sufficient). The
`catalogPath` glob `/base-apps/*/catalog-info.yaml` matches each app directory one
level under `base-apps/`, which is where the framework places every file. The
`repository: '^kubernetes$'` filter scopes the scan to the one repo that has
these files. The `schedule` mirrors the existing provider (30-minute frequency,
3-minute timeout).

No backend code, no new dependency, no `catalog.rules` change — the rules already
allow `Component` and `Resource`, which are the only kinds the framework emits.

### 2. Image build (user-run)

Run `./scripts/build-and-push.sh --version v1.2.0` from a machine with AWS ECR
push access, Docker + buildx, and Node/Yarn. The script type-checks
(`yarn tsc`), builds all packages, and pushes `backstage-portal:v1.2.0` and
`:latest` to ECR. Its final `kubectl rollout restart` is a no-op for the upgrade
(the deployment stays pinned to `v1.1.0` until this repo's tag bump), but is
harmless. **This step cannot be run from the agent environment** — it needs the
user's local toolchain and credentials.

### 3. This repo — pin the new image

Bump the image tag in `base-apps/backstage/deployments.yaml` from
`backstage-portal:v1.1.0` to `:v1.2.0`. Because Argo CD owns this deployment with
`selfHeal: true`, this GitOps change — not the build script's rollout-restart —
is what deploys the new config. PR → merge → Argo sync → new pod.

## Data flow (worked example)

1. On its 30-minute schedule, the `arigsela-kubernetes` provider calls the GitHub
   API for the `kubernetes` repo and lists `base-apps/*/catalog-info.yaml`.
2. For each match it fetches the file, validates it against `catalog.rules`
   (`Component`/`Resource` allowed), and ingests the entity.
3. `base-apps/cert-manager/catalog-info.yaml` becomes `Resource:cert-manager/cert-manager`
   (namespace `cert-manager`, system `platform-networking`); `chores-tracker-backend`
   becomes `Component:chores-tracker/chores-tracker-backend`; etc.
4. The entities appear in the catalog UI under their namespaces; `system:`/`owner`
   relations resolve where the referenced entities exist and show as external
   otherwise.
5. Adding a new `base-apps/<newapp>/catalog-info.yaml` surfaces it automatically on
   the next scan.

## Success criteria

After the `v1.2.0` image is deployed:

1. The catalog API returns all four pilot entities with correct kind, namespace,
   and owner: `Resource:cert-manager`, `Resource:vault`, `Resource:argo-cd`,
   `Component:chores-tracker-backend`.
2. They render in the Backstage catalog UI (About card, annotations, the
   `backstage.io/managed-by-location` pointing at the GitHub file URL).
3. No `conflicting entityRef` or provider errors in the backstage pod logs.
4. The existing entities (agents via kubernetes-ingestor, self-registration,
   examples) are unchanged.

## Testing

- **Build gate:** `yarn tsc` and `yarn build:all` succeed (run by the build
  script; a bad app-config YAML fails the backend at startup).
- **Post-deploy behavioral check:** query the catalog API
  (`GET /api/catalog/entities`) from inside the backstage pod via `node` and
  assert the four pilot entities are present and well-formed; scan pod logs for
  provider/ingest errors.
- **Optional independent pre-check:** in the Backstage UI, *Create → Register
  Existing Component* on one file's GitHub URL to confirm a single entity
  previews and validates, independent of the provider change.

This is a config change with no unit-test harness; validation is behavioral.

## Safety, blast radius & rollback

- **Additive and read-only.** A second discovery provider that only reads and
  imports catalog entities. No workload, IAM, or secret change. A malformed
  `catalog-info.yaml` produces a logged ingest error for that one entity, not a
  crash — and the framework's CI validator (`scripts/validate-agent-docs.py`)
  already checks these files.
- **No Argo interaction.** Backstage reads `catalog-info.yaml` from GitHub via
  API; Argo already excludes `catalog-info.yaml` from app sync (per-app
  `directory.exclude`). The two paths do not touch.
- **Rollback:** revert the one-line image bump in this repo → Argo redeploys
  `v1.1.0`. The app-config change remains in `arigsela/backstage` history, inert
  until an image ships it.

## Future work

- **Define the referenced `System` entities** (`platform-networking`,
  `platform-secrets`, `platform-gitops`, `chores-tracker`) so `system:` relations
  resolve into a clean dependency graph.
- **Backstage MCP (v2 of the retrieval agent):** once the catalog holds these
  structured entities, add the Backstage MCP as a second tool on
  `homelab-knowledge` so it can query catalog relations (ownership,
  `dependsOn`/`dependencyOf`) instead of reconstructing them from individual
  files.

## Open questions

- None blocking. The `catalogPath` glob form (`/base-apps/*/catalog-info.yaml`)
  is the documented GithubEntityProvider pattern; if a Backstage version nuance
  requires `/base-apps/**/catalog-info.yaml`, the plan's build-time type-check and
  the post-deploy catalog check will surface it before merge of the tag bump.
