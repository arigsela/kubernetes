# Design: Backstage "New Application" golden-path template

**Date:** 2026-07-20
**Status:** design — pending user review
**Topic:** A Backstage scaffolder template that generates a complete `base-apps/<app>/`
GitOps application (matching every repo convention) and opens a PR to
`arigsela/kubernetes`.

## Goal

Turn "create a new base-apps service" from a manual, convention-remembering chore
into a form → PR. The generated PR is **correct-by-construction**: it passes every
CI gate in the kubernetes repo (agent-docs, techdocs, catalog-refs, ingress-policy,
yamllint, kubeconform) and follows the base-apps + agent-docs + TechDocs + secret
conventions without the author re-deriving them.

## Decisions (settled during brainstorming)

| Decision | Choice |
|---|---|
| Mechanism | `kind: Template` + `skeleton/`, `publish:github:pull-request` to `arigsela/kubernetes` |
| Template location | **kubernetes repo** `templates/new-app/`, registered via a url `catalog.location` (future edits are GitOps, no image) |
| Workload | **Stateless HTTP service** (Deployment + Service) |
| Optional pieces (form toggles) | **Ingress, Vault secrets, Config (ConfigMap+envFrom), Resource requests/limits** |
| Publish | PR only (Backstage's built-in editor dry-run covers testing) |
| No cluster mutation | The template only opens a PR; Argo deploys after merge |

## Grounding (verified)

- Scaffolder actions available: `fetch:template`, `publish:github:pull-request`
  (`@backstage/plugin-scaffolder-backend-module-github`), GitHub integration token
  with repo scope. `EntityPicker` field is available (used in the prior kagent template).
- Existing skeletons to mirror: `templates/agent-docs/{catalog-info.yaml,docs.md,runbook.md}`
  (the agent-docs contract, `REPLACE_ME` style) and `templates/agent-identity/`.
- The CI gates the output must pass (`.github/workflows/validate.yaml`):
  `yaml-lint`, `kubernetes-validate` (kubeconform, skips `mkdocs.yml`), `ingress-policy`
  (whitelist-source-range required on Ingress), `agent-docs-validate`,
  `techdocs-validate` (`gen-techdocs.py --check`), `catalog-refs-validate`.
- Base-apps convention (from real apps like `dex`, `whoami-test`): each app is
  `base-apps/<app>.yaml` (Argo `Application`, namespace `argo-cd`, source path
  `base-apps/<app>`, `syncPolicy.automated` prune+selfHeal, `CreateNamespace=true`,
  and `directory.exclude` when it has catalog-info/mkdocs) + `base-apps/<app>/` manifests.

## Form (parameters)

Multi-step, mirroring the kagent template's style.

- **Identity:** `name` (string, `pattern: '^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'`),
  `description` (string, one line), `system` (`ui:field: EntityPicker`,
  `catalogFilter.kind: System`), `owner` (`EntityPicker`, kind `[Group, User]`,
  default `group:default/platform`), `tags` (array of strings).
- **Workload:** `image` (string), `containerPort` (integer, default 8080),
  `replicas` (integer, default 1), `namespace` (string, default `${{ parameters.name }}`).
- **Networking & Config:**
  - `exposeIngress` (boolean) → `host` (string, ingress at `<host>.arigsela.com`).
  - `needsConfig` (boolean) → `configData` (map of string→string).
  - `needsSecrets` (boolean).
- **Resources:** `cpuRequest` / `cpuLimit` / `memRequest` / `memLimit` (strings,
  prefilled defaults e.g. `100m`/`500m`/`128Mi`/`256Mi`).

## Steps

1. `fetch:template` — render the **core** `skeleton/` into the workspace with all
   parameter values. Templated file/dir names produce `base-apps/${{ name }}.yaml`
   and `base-apps/${{ name }}/...`.
2. `fetch:template` (`if: ${{ parameters.exposeIngress }}`) — render `skeleton-ingress/`
   (`nginx-ingress.yaml` with the whitelist annotation).
3. `fetch:template` (`if: ${{ parameters.needsSecrets }}`) — render `skeleton-secrets/`
   (`secret-store.yaml` + `external-secret.yaml`).
4. `fetch:template` (`if: ${{ parameters.needsConfig }}`) — render `skeleton-config/`
   (`configmap.yaml`; the Deployment already wires `envFrom` guarded by `needsConfig`).
5. `publish:github:pull-request` — open a PR against `arigsela/kubernetes`
   (`repoUrl: github.com?owner=arigsela&repo=kubernetes`), `branchName: new-app/${{ name }}`,
   title `feat(<name>): onboard new application`, body summarizing inputs.

Output: link to the PR. (No `catalog:register` — the discovery provider ingests the
entity after the PR merges; registering from an unmerged branch is undesirable.)

## Generated files

**Core (always):**

- `base-apps/<name>.yaml` — Argo `Application` (namespace `argo-cd`, source path
  `base-apps/<name>`, destination namespace `<namespace>`, `syncPolicy.automated`
  prune+selfHeal + `CreateNamespace=true`, `directory.exclude: '{catalog-info.yaml,mkdocs.yml}'`).
- `base-apps/<name>/deployments.yaml` — Deployment (`app=<name>` labels, `image`,
  `containerPort`, `replicas`, resource requests/limits, `envFrom` configmap only if `needsConfig`).
- `base-apps/<name>/services.yaml` — Service (`port: 80` → `targetPort: containerPort`, ClusterIP).
- `base-apps/<name>/catalog-info.yaml` — `Component` (annotations `agent-docs/path: docs.md`,
  `backstage.io/techdocs-ref: dir:.`, `kubernetes-label-selector: app=<name>`,
  `kubernetes-namespace: <namespace>`; spec `type: service`, `lifecycle: experimental`,
  `owner`, `system`, `tags`, `dependsOn` incl. `resource:vault/vault` if `needsSecrets`).
- `base-apps/<name>/docs.md` + `runbook.md` — agent-docs frontmatter contract
  (type/title/description/app/catalog_entity/kind/namespace/last_reviewed/status/tags/sources)
  + starter body sections.
- `base-apps/<name>/mkdocs.yml` — TechDocs (`docs_dir: docs`, nav Overview/Runbook, `techdocs-core`).
- `base-apps/<name>/docs/index.md` + `docs/runbook.md` — copies rendered **identical**
  to `docs.md`/`runbook.md` (so `gen-techdocs.py --check` passes immediately).

**Conditional:**

- `nginx-ingress.yaml` — Ingress at `<host>.arigsela.com`, **with** `nginx.ingress.kubernetes.io/whitelist-source-range` (ingress-policy gate).
- `secret-store.yaml` + `external-secret.yaml` — per-namespace `SecretStore` (Vault
  `k8s-secrets` KV, role `<namespace>`) + `ExternalSecret`.
- `configmap.yaml` — ConfigMap from `configData`.

## Correct-by-construction (gate → how satisfied)

| CI gate | How the skeleton satisfies it |
|---|---|
| `agent-docs-validate` | docs.md/runbook.md carry the full frontmatter; catalog-info kind ∈ {Component}; `agent-docs/path: docs.md` present; names consistent |
| `techdocs-validate` (`gen-techdocs.py --check`) | mkdocs.yml + `docs/index.md` (=docs.md) + `docs/runbook.md` (=runbook.md) generated in-sync |
| `catalog-refs-validate` | `owner`/`system` from EntityPickers resolve; `dependsOn` uses existing refs (vault) |
| `ingress-policy` | Ingress includes `whitelist-source-range` |
| `yaml-lint` | block-style YAML, 2-space indent |
| `kubernetes-validate` | valid k8s manifests; `mkdocs.yml` skipped by the job's filter |
| Argo sync | `directory.exclude: '{catalog-info.yaml,mkdocs.yml}'` so Argo never applies them |

## OKF index auto-sync (added after Task-2 review)

A scaffolded PR adds a `base-apps/<app>/` dir, which trips two aggregate gates the
template cannot satisfy from its workspace: `okf-validate` (`gen-okf.py --check` —
`base-apps/index.md` needs a new row) and agent-docs index-coverage. Resolution: a
new **`.github/workflows/okf-autosync.yaml`** runs on `pull_request` touching
`base-apps/**` (same-repo PRs only), regenerates `base-apps/index.md` via
`gen-okf.py`, appends contract-complete apps to `scripts/agent-docs-scope.txt`, and
commits+pushes back to the PR branch (idempotent — no loop). This keeps the
scaffolded PR "green with no manual fixes" and benefits hand-added apps too.

Known v1 limitation: `last_reviewed` in the generated docs is a baked date (the
scaffolder cannot compute "today"); the PR body reminds the author to update it.

## Delivery

1. **kubernetes repo:** add `templates/new-app/` (`template.yaml` + `skeleton*/` dirs).
2. **backstage repo (one-time):** add a url `catalog.location` for
   `https://github.com/arigsela/kubernetes/blob/main/templates/new-app/template.yaml`
   with `rules: [allow: [Template]]` to `app-config.yaml` + `app-config.production.yaml`.
   → build **v1.4.11** (linux/amd64) + a kubernetes deploy bump.
3. After deploy, the template appears in Create. Future template/skeleton edits are
   pure GitOps (catalog refresh on the 30-min discovery / location refresh).

## Verification

- Backstage template editor **dry-run** renders the skeleton for each toggle combination
  without errors.
- A real run opens a PR; that PR's CI (all gates above) goes green without manual fixes.
- Merging the PR → Argo creates the app; Backstage ingests the Component (catalog-info +
  Docs tab).

## Risks / out of scope

- Only stateless HTTP `Deployment`; StatefulSet/CronJob/Job deferred.
- `system` must be an existing System (EntityPicker); onboarding a brand-new system is
  a separate `platform-entities.yaml` edit.
- No database provisioning (candidate B) or secret seeding (`vault:setup`) in this
  template; `needsSecrets` only scaffolds the SecretStore/ExternalSecret wiring.
- The skeleton's starter docs are stubs the author fills in; the frontmatter/structure
  is correct, the prose is a starting point.
