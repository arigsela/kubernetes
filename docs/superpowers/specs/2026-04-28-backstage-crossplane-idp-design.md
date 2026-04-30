# Backstage + Crossplane IDP — Application Onboarding Template — Design

**Date:** 2026-04-28
**Author:** Ari Sela (with Claude)
**Status:** Approved for plan
**Scope:** New Backstage Software Template (`application-template`) backed by a Crossplane v2 Composition that provisions a Deployment + Service + Ingress and an optional CloudNativePG database, end-to-end via the existing GitOps pipeline.
**Repos touched:** `arigsela/kubernetes` (manifests), `arigsela/backstage` (template + image rebuild)

## 1. Goal

Give a developer a self-service path from "I have a container image" to "my app is running on `<host>.arigsela.com` with optional Postgres" by filling out one Backstage form. Use the build as a learning vehicle for Crossplane v2 Compositions, namespaced XRs, pipeline functions, and the Backstage→Crossplane→Argo CD wire — without dragging AWS providers, Vault round-trips, or source-code scaffolding into the v1 surface area.

## 2. Current state

| Component | Version / shape | Path |
|---|---|---|
| Crossplane core | v2.2.1 (Helm subchart) | `base-apps/crossplane-system/` |
| Upbound providers (S3, IAM) | v2.5.3 | `base-apps/crossplane-aws-provider/` |
| XRDs / Compositions | none | — |
| Composition functions | none installed | — |
| Backstage portal | image `backstage-portal:v1.0.1` | `base-apps/backstage/` |
| Backstage scaffolder actions (custom, baked into image) | `aws:ecr:create`, `aws:ecr:build-push`, `vault:setup`, K8s helpers | `arigsela/backstage:plugins/`, registered in `packages/backend/` |
| Existing Software Template | `crewai-agent-template` (dual-repo, full-stack, 9 phases) | `arigsela/backstage:examples/templates/crewai-agent/` |
| Template registration mechanism | `catalog.locations` with `type: file`; updates ship via image rebuild | `arigsela/backstage:app-config.yaml` |
| GitHub auto-discovery | scans every `arigsela` repo for `/catalog-info.yaml` every 30 min | `app-config.yaml: catalog.providers.github.arigsela` |
| CloudNativePG | deployed; chores-tracker uses cluster `postgresql-cluster` in `postgresql` namespace | `base-apps/postgresql/` |
| Master-app pattern | every `base-apps/*.yaml` becomes an Argo CD Application | terraform `application-sets` module |

## 3. Target state — the wire

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Backstage (existing image, +1 new template)                                 │
│  examples/templates/application/template.yaml  (NEW)                         │
│    Form: name, owner, image, host, port, replicas, dbNeeded, dbStorage       │
│    Steps: fetch:template (content-k8s/) → publish:github:pull-request        │
│           → catalog:register                                                 │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 │ PR → arigsela/kubernetes
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  arigsela/kubernetes  (NEW dirs only; existing apps untouched)               │
│  base-apps/<name>/       (new — XR + catalog-info)                           │
│  base-apps/<name>.yaml   (new — Argo CD Application)                         │
│  base-apps/crossplane-functions/    (new — function-python)                  │
│  base-apps/crossplane-compositions/ (new — XRD + Composition)                │
│  Reviewer merges PR; master-app picks up new dir                             │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 │ Argo CD auto-syncs
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  Cluster                                                                     │
│  Argo CD applies XApplication (namespaced, v1alpha1)                         │
│  Crossplane Composition (function-python) renders:                           │
│      Deployment + Service + Ingress + (optional) CNPG Cluster                │
│  CNPG creates <name>-db-app Secret with Postgres URI                         │
│  Deployment envFrom that Secret + DATABASE_URL projected as env              │
│  cert-manager + nginx-ingress finish public hostname wiring                  │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Invariants:** every existing Argo CD app, secret, namespace, and the existing CrewAI template are untouched. The master-app pattern, the per-namespace SecretStore + ESO + Vault flow, and the chores-tracker-frontend/backend deployments are all out of scope.

## 4. Components

| # | Artifact | Repo | Path | Purpose |
|---|----------|------|------|---------|
| 1 | `function-python` install | `arigsela/kubernetes` | `base-apps/crossplane-functions/function-python.yaml` + `base-apps/crossplane-functions.yaml` | Installs the upstream `function-python` OCI image as a Crossplane `Function` so Compositions can reference it. |
| 2 | `XApplication` XRD | `arigsela/kubernetes` | `base-apps/crossplane-compositions/xrd-application.yaml` | Defines the `XApplication` namespaced XR — what shape developers can request. |
| 3 | `Application` Composition | `arigsela/kubernetes` | `base-apps/crossplane-compositions/composition-application.yaml` | One Composition with a single `function-python` pipeline step running an inline script that emits the workload + optional CNPG `Cluster`. |
| 4 | Two new Argo CD parent apps | `arigsela/kubernetes` | `base-apps/crossplane-functions.yaml`, `base-apps/crossplane-compositions.yaml` | Picked up by master-app. Sync waves: function = `1`, composition = `2`. |
| 5 | Backstage `Template` | `arigsela/backstage` | `examples/templates/application/template.yaml` | Form definition + scaffolder steps. |
| 6 | Template content | `arigsela/backstage` | `examples/templates/application/content-k8s/` | Three Nunjucks-templated files: `catalog-info.yaml`, `application-xr.yaml`, `argocd-application.yaml`. |
| 7 | `app-config.yaml` AND `app-config.production.yaml` changes | `arigsela/backstage` | `app-config.yaml` + `app-config.production.yaml` | One new `catalog.locations` entry in **both** files. Production overlay replaces (not merges) the array; missing it in production silently drops the entry. |
| 8 | Image rebuild + tag bump | `arigsela/backstage` (build) → `arigsela/kubernetes` (deploy) | `base-apps/backstage/deployments.yaml` | Bump `backstage-portal` tag (e.g. `v1.0.1` → `v1.1.0`). |
| 9 | RBAC ClusterRole for the Crossplane SA | `arigsela/kubernetes` | `base-apps/crossplane-compositions/composition-rbac.yaml` | Aggregates `networking.k8s.io/Ingress` + `postgresql.cnpg.io/Cluster` permissions into the `crossplane` ClusterRole via the `rbac.crossplane.io/aggregate-to-crossplane: "true"` label. Without this, Crossplane fails to compose Ingress (and the with-DB CNPG Cluster) into target namespaces. |

**Sync ordering:** the per-app Argo CD Application (`base-apps/<name>.yaml`) carries `argocd.argoproj.io/sync-wave: "10"` so on a cold cluster the XRD/Composition/Function land before any XR. The RBAC ClusterRole carries sync-wave `"1"` to land alongside the Function.

## 5. The `XApplication` XRD (developer-facing API)

```yaml
apiVersion: apiextensions.crossplane.io/v2
kind: CompositeResourceDefinition
metadata:
  name: xapplications.platform.arigsela.com
spec:
  scope: Namespaced
  group: platform.arigsela.com
  names:
    kind: XApplication
    plural: xapplications
  defaultCompositionRef:
    name: application
  versions:
    - name: v1alpha1
      served: true
      referenceable: true
      schema:
        openAPIV3Schema:
          type: object
          required: [spec]
          properties:
            spec:
              type: object
              required: [image, host, port]
              properties:
                image:        { type: string, description: "Full container image ref including tag" }
                host:         { type: string, description: "FQDN for the public Ingress" }
                port:         { type: integer, default: 8080, minimum: 1, maximum: 65535 }
                replicas:     { type: integer, default: 2, minimum: 1, maximum: 10 }
                env:
                  type: array
                  description: "Plain-text env vars (no secrets here)"
                  items:
                    type: object
                    required: [name, value]
                    properties:
                      name:  { type: string }
                      value: { type: string }
                dbNeeded:     { type: boolean, default: false }
                dbStorage:    { type: string, default: "1Gi" }
```

**Design choices:**
- `scope: Namespaced` (v2 default) — the XR lives in the developer's namespace. RBAC is per-namespace. Claims are not used (v2 makes them optional).
- `apiVersion: v1alpha1` — explicit "we will iterate." Bumps to `v1` when the schema stabilizes.
- `imagePullSecrets`, health-probe paths (`/healthz`), resource requests/limits, Prometheus-scrape annotations, and the `backstage.io/kubernetes-id: <name>` label are platform defaults set by the Composition — not XR fields.

## 6. The `Application` Composition

**Skeleton:**

```yaml
apiVersion: apiextensions.crossplane.io/v1
kind: Composition
metadata:
  name: application
spec:
  compositeTypeRef:
    apiVersion: platform.arigsela.com/v1alpha1
    kind: XApplication
  mode: Pipeline
  pipeline:
    - step: render-resources
      functionRef:
        name: function-python
      input:
        apiVersion: python.fn.crossplane.io/v1beta1
        kind: Script
        script: |
          # see Section 6.1
```

### 6.1 The Python script (sketch)

```python
def compose(req, rsp):
    xr   = req.observed.composite.resource
    spec = xr["spec"]
    name      = xr["metadata"]["name"]
    namespace = xr["metadata"]["namespace"]

    db_cluster = f"{name}-db"
    db_secret  = f"{db_cluster}-app"   # CNPG auto-creates this

    add_resource(rsp, "deployment", make_deployment(
        name, namespace, spec["image"], spec["port"],
        spec.get("replicas", 2),
        env=spec.get("env", []),
        env_from_secret=db_secret if spec.get("dbNeeded") else None,
    ))
    add_resource(rsp, "service", make_service(name, namespace, spec["port"]))
    add_resource(rsp, "ingress", make_ingress(name, namespace, spec["host"]))

    if spec.get("dbNeeded"):
        add_resource(rsp, "dbcluster", make_cnpg_cluster(
            db_cluster, namespace,
            instances=1,
            storage=spec.get("dbStorage", "1Gi"),
        ))
```

### 6.2 Locked-in shape decisions

**(A) DB credential injection.** Deployment renders:
```yaml
envFrom:
  - secretRef:
      name: <name>-db-app          # CNPG-created
env:
  - name: DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: <name>-db-app
        key: uri
```
App authors get individual fields (`username`, `password`, `host`, `port`, `dbname`) AND a pre-formed `DATABASE_URL`.

**(B) CNPG cluster v1 defaults.** `instances: 1`, PostgreSQL major version `16`, default storage class, `bootstrap.initdb.database: app`, no backups configured.

**(C) Image pull secret.** Hardcoded `imagePullSecrets: [{ name: ecr-auth }]`. ECR images Just Work; non-auth public images Just Work; mixed-registry edge cases are a v2 concern.

**(D) Standard labels stamped on every rendered resource:**
- `app.kubernetes.io/name: <name>`
- `app.kubernetes.io/managed-by: crossplane`
- `backstage.io/kubernetes-id: <name>` (so the Backstage Kubernetes plugin auto-discovers the workload on the entity page)

**(E) Health probe path is hardcoded to `/healthz` — known v1 limitation.** Both `livenessProbe` and `readinessProbe` use `httpGet { path: /healthz, port: <spec.port> }`. Onboarded apps must serve HTTP 200 on `/healthz` or their Pods will CrashLoopBackOff (the platform pieces — XApplication, Composition, ArgoCD, cert-manager — still reconcile correctly; only the Pod readiness fails). v2 follow-up: expose `healthCheckPath` as an optional XR field with `/healthz` default.

## 7. The Backstage Template

**File:** `arigsela/backstage:examples/templates/application/template.yaml`

```yaml
apiVersion: scaffolder.backstage.io/v1beta3
kind: Template
metadata:
  name: application-template
  title: Application (Crossplane)
  description: Onboard an existing container image as a managed application
  tags: [crossplane, kubernetes, recommended]
spec:
  owner: group:platform-engineering
  type: service
  parameters:
    - title: Identity
      required: [name, owner]
      properties:
        name:        { type: string, pattern: "^[a-z][a-z0-9-]{2,38}[a-z0-9]$" }
        description: { type: string }
        owner:       { type: string, ui:field: OwnerPicker, ui:options: { catalogFilter: { kind: [Group, User] } } }
    - title: Workload
      required: [image, host, port]
      properties:
        image:    { type: string }
        host:     { type: string }
        port:     { type: integer, default: 8080 }
        replicas: { type: integer, default: 2, minimum: 1, maximum: 10 }
    - title: Database (optional)
      properties:
        dbNeeded:   { type: boolean, default: false }
        dbStorage:  { type: string,  default: "1Gi" }
  steps:
    - id: fetch
      name: Render manifests
      action: fetch:template
      input:
        url: ./content-k8s
        values:
          name: ${{ parameters.name }}
          namespace: ${{ parameters.name }}
          description: ${{ parameters.description }}
          owner: ${{ parameters.owner }}
          image: ${{ parameters.image }}
          host: ${{ parameters.host }}
          port: ${{ parameters.port }}
          replicas: ${{ parameters.replicas }}
          dbNeeded: ${{ parameters.dbNeeded }}
          dbStorage: ${{ parameters.dbStorage }}
    - id: publish
      name: Open PR to arigsela/kubernetes
      action: publish:github:pull-request
      input:
        repoUrl: github.com?repo=kubernetes&owner=arigsela
        branchName: scaffold/${{ parameters.name }}
        title: "feat(${{ parameters.name }}): onboard via Crossplane Application"
        targetPath: ""
  # NOTE: no catalog:register step. publish:github:pull-request does NOT emit a
  # repoContentsUrl output (only the direct-push publish:github does), and there
  # is no clean URL to register pre-merge anyway. After PR merge the operator
  # imports manually via /catalog-import. v2 follow-up: extend the Backstage
  # GitHub provider to scan base-apps/*/catalog-info.yaml directly.
  output:
    links:
      - title: Pull request
        url: ${{ steps.publish.output.remoteUrl }}
```

**Content directory:** `examples/templates/application/content-k8s/`. Source paths embed `${{ values.name }}` so `fetch:template` writes the rendered files to the correct destinations without an explicit move step:

```
content-k8s/
└── base-apps/
    ├── ${{ values.name }}.yaml                            → base-apps/<name>.yaml
    └── ${{ values.name }}/
        ├── catalog-info.yaml                              → base-apps/<name>/catalog-info.yaml
        └── application-xr.yaml                            → base-apps/<name>/application-xr.yaml
```

**`catalog-info.yaml`** — `kind: Component` with `metadata.name: <name>`, `spec.owner: <owner>`, `spec.type: service`, and annotations `github.com/project-slug: arigsela/kubernetes` + `backstage.io/kubernetes-id: <name>` (matches the Composition-stamped label). The `description` field is rendered via `${{ (values.description | default("...", true)) | dump }}` so user-supplied strings containing `:`, `"`, or `#` can't break YAML parsing.

**`application-xr.yaml`** — the `XApplication` CR with the form values substituted.

**`argocd-application.yaml`** — vanilla Argo CD `Application` with `spec.source.path: base-apps/<name>`, `argocd.argoproj.io/sync-wave: "10"`, and **`spec.source.directory.exclude: 'catalog-info.yaml'`** (Backstage Component entity is not a Kubernetes resource — without the exclude, ArgoCD's directory source tries to apply it and fails with "no matches for kind Component in version backstage.io/v1alpha1").

**`namespace == name`** (computed, not asked) for v1.

## 8. Failure modes

| Failure mode | Visibility | Recovery |
|---|---|---|
| Form validation fails | Wizard inline error | User fixes input |
| GitHub PR cannot be opened | Scaffolder log; task fails. No partial state. | Fix token/branch; re-run |
| PR opened but not merged | Backstage shows pending PR link | Reviewer merges (no automated nag in v1) |
| Master-app fails to pick up new dir | Argo CD UI on `master-app` | Inspect ArgoCD events; usually a YAML error |
| `XApplication` validation fails | App Degraded; XR rejected by API server | Fix XR YAML and push |
| Composition Python script raises | XR `Synced=False`; events on the XR; function-python pod logs | `kubectl describe xapplication`; fix script |
| Image doesn't exist / pull fails | Pod `ImagePullBackOff` | Visible on Backstage K8s tab; developer fixes |
| CNPG cluster fails to bootstrap | CNPG events on `Cluster` | Cluster-platform issue; not app concern |
| cert-manager challenge fails | `Certificate`/`Order` in app namespace | Same path as every other ingress today |

**Two principles:**
1. **No retries inside the Composition.** Crossplane reconciles again on the next loop; the Python script is deterministic given the XR.
2. **Failures upstream of Crossplane are someone else's concern.** Image, cert-manager, cluster-storage failures are not platform-team triage paths.

**Decommissioning in v1 — manual three-step (CORRECTED — original ordering caused selfHeal recreation):**

1. **PR-delete the manifests FIRST.** Open a PR removing `base-apps/<name>.yaml` + `base-apps/<name>/`. Merge it. Master-app reconciles within ~30s and prunes the per-app Argo CD Application that was managing the XR.
2. **Once the per-app Application is gone, the XR is orphaned** — no controller is reconciling it back. NOW run:
   ```bash
   kubectl -n <name> delete xapplication <name>
   ```
   Crossplane's owner-references then reap the composed Deployment / Service / Ingress / Cluster cleanly. cert-manager's Certificate and ACME solver objects (children of the Ingress) GC along with it.
3. **`kubectl delete ns <name>`** — the namespace was created by `CreateNamespace=true` on the per-app Application; ArgoCD does NOT auto-delete it on Application removal.

**Why this order matters** (the original v1 spec had it backwards): the per-app ArgoCD Application has `syncPolicy.automated.selfHeal: true`. If you `kubectl delete xapplication` before merging the teardown PR, ArgoCD detects the drift and re-applies the XR from the still-in-repo manifest within seconds — the delete is undone instantly. The PR-first order ensures that by the time you `kubectl delete xapplication`, no controller is reconciling it back.

**CNPG PVC retention:** `Cluster` deletion does NOT auto-delete its PVC (CNPG default). When decommissioning a `dbNeeded: true` app, also clean up the PVC manually if you don't want to retain the data.

**Backstage catalog Location cleanup (v1 only — superseded by v1.1):** if you registered the app via `/catalog-import` in v1, the Location entity needs to be unregistered manually after PR merge. v1.1's TeraSky `kubernetes-ingestor` makes this automatic — entities auto-deingest within ~30–120s of the XR being deleted.

## 9. Testing strategy

**Layer 1 — `crossplane render` (local, milliseconds).** For every Composition change, `crossplane render <example-xr> <composition> <functions>` and check the output. Two committed examples:
- `tests/composition/xr-minimal.yaml` (no DB) → 3 resources expected
- `tests/composition/xr-with-db.yaml` (`dbNeeded: true`) → 4 resources expected

Optional: capture expected YAML in `tests/composition/expected-*.yaml` for `diff`-based regression in CI.

**Layer 2 — Backstage scaffolder dry-run (local, seconds).** Run Backstage locally; use a sandbox repo (e.g. `arigsela/scaffolder-sandbox`) instead of the real kubernetes repo; walk the form; verify generated files match Layer 1 outputs.

**Layer 3 — Full e2e in cluster (~3 min wall clock).** Acceptance gate for v1:
1. `XApplication` for `nginxinc/nginx-unprivileged:1.25-alpine`, `dbNeeded: false` — Pod ready, Ingress responds.
2. Same image, `dbNeeded: true`, port 80 — CNPG cluster bootstraps, `<name>-db-app` Secret exists, Pod env contains `DATABASE_URL`.
3. Optionally: scaffold a real workload via the live Backstage UI; verify end-to-end.

Throwaway namespace `platform-smoke`; tear down by deleting the `XApplication`.

**Discipline:** Layer 1 tests are written **before** the Composition Python — TDD applied to Composition development.

## 10. Out of scope (named explicitly)

| Topic | Why deferred | Notes |
|---|---|---|
| AWS resources (S3, IAM) in the Composition | Adds provider-specific Composition logic + IAM credential rotation concerns; dilutes Crossplane learning focus | v2 — the providers are already installed |
| Vault round-trip for DB credentials | Requires CNPG↔Vault sync mechanism (vault-secrets-operator or custom Job) | v2 — symmetry with chores-tracker pattern |
| Source-code repo creation (Dockerfile + CI) | Doubles the template's surface area | v2 — your existing `aws:ecr:create` + `aws:ecr:build-push` actions are ready |
| TeraSky/Roadie Crossplane Backstage plugin | Pure Backstage change, does not affect XRD/Composition design | v2 — drop-in once the wire is proven |
| Decommission template | Manual PR is the v1 documented path | v2 if developer pain accrues |
| XRD `v1` cut | Schema will iterate during v1 use | After 2–3 onboarded apps |
| Multiple Compositions selectable per XRD | Single Composition is fine for one workload shape | When we have a second shape (e.g. worker-only) |

## 11. Acceptance criteria (v1 done)

- [ ] `XApplication` XRD applied to cluster
- [ ] `Application` Composition applied; references `function-python`
- [ ] `function-python` installed as Crossplane `Function`
- [ ] New ArgoCD apps `crossplane-functions` + `crossplane-compositions` healthy
- [ ] Backstage image rebuilt with new `application-template`; tag bumped in `base-apps/backstage/deployments.yaml`
- [ ] One smoke-test app scaffolded end-to-end via the Backstage UI:
  - PR opened against `arigsela/kubernetes`, merged, master-app picks it up
  - `XApplication` reconciled by Crossplane
  - Pod ready, Ingress reachable on `<host>.arigsela.com`
  - Backstage entity page shows the K8s tab with live pod status
- [ ] Smoke test repeated with `dbNeeded: true`; CNPG cluster bootstraps; `DATABASE_URL` env var present in Pod
- [ ] `crossplane render` regression suite in repo with two example XRs
- [ ] CrewAI template still works (regression check)

## 12. Open follow-ups (not blocking v1)

- Verify on the spec-author side: confirm the latest `function-python` version + image reference at plan-writing time.
- Confirm `app-config.yaml` `catalog.locations` add for the new template + the image rebuild loop instructions match the existing CrewAI shape.
- Decide on a CI workflow for `crossplane render` regression (likely a new GitHub Actions job in `arigsela/kubernetes`).
- Sketch v2 follow-up spec (AWS resources, Vault round-trip, source-code scaffolding) once v1 is in use.
