# Crossplane v2 + Backstage IDP — Design

**Date:** 2026-04-27
**Author:** Ari Sela (with Claude)
**Status:** Approved for plan
**Scope:** GitOps repo `arigsela/kubernetes` (paths under `base-apps/`) and the user's separate Backstage portal source repo (which builds the `backstage-portal` ECR image).

## 1. Goal

Build a self-service Internal Developer Platform (IDP) that lets a homelab user provision a complete new application — namespace, ArgoCD Application, ECR repository (with auto-injected pull secret), optional S3 bucket + IAM bundle, and optional Postgres database — by filling a form in Backstage. The form opens a PR; ArgoCD applies it; Crossplane v2 materializes the resources via a Composition pipeline; the result is visualized back in the Backstage catalog.

This is also a learning project. The user explicitly chose the deepest scope to learn XRDs, Compositions, function-python composition functions, Backstage Software Templates, and the IDP loop end-to-end.

## 2. Current State (leveraged unchanged)

| Component | Version | Path / Notes |
|---|---|---|
| Crossplane core | `v2.2.1` | `base-apps/crossplane-system/` |
| Upbound AWS providers (s3, iam, family) | `v2.5.3` | `base-apps/crossplane-aws-provider/` |
| Provider SQL (Postgres) | `v0.9.0` | already installed |
| AWS DeploymentRuntimeConfig | `aws-provider-runtime` | recent hardening for resource limits |
| Backstage portal | `v1.0.1` (ECR image) | `base-apps/backstage/`, source repo separate |
| Existing Backstage scaffolder actions | `aws:ecr:create`, `aws:ecr:build-push`, `vault:setup` | configured in portal source |
| ArgoCD master-app pattern | watches `base-apps/*.yaml` | each `.yaml` becomes an Application |
| Kyverno ECR-auth policy | generates `imagePullSecret` on namespaces with a label | already in cluster |
| Vault + ESO | wired for cross-namespace secret materialization | already in cluster |
| In-cluster PostgreSQL | `base-apps/postgresql/` | shared instance |

## 3. Target State

After all four PRs ship, the platform exposes:

- A new XRD: `HomelabApp` in API group `platform.asela.io`, scope `Namespaced`, all of `v1alpha1`, `v1alpha2`, `v1alpha3` served simultaneously (additive evolution; no conversion webhook).
- A Composition `homelab-app` running a `function-python` pipeline that emits 4–11 composed resources depending on claim flags.
- A Backstage Software Template `new-homelab-app` that produces a one-file PR adding a `HomelabApp` claim to `base-apps/`.
- TeraSky Backstage Crossplane plugins (resources frontend + permissions backend) installed in the portal — every claim and its composed resources visible in the catalog with a graph view, YAML inspector, and event timeline.

## 4. Architecture

End-to-end loop, one cycle:

1. User opens Backstage → **Create** → **New Homelab App** template.
2. Form prompts for `name`, `manifestPath`, `ecrRepo`, `wantsS3`, `wantsPostgres`.
3. `publish:github` action opens a PR against `arigsela/kubernetes` adding `base-apps/<app>-claim.yaml`.
4. User reviews and merges.
5. ArgoCD master-app picks up the new file → applies the `HomelabApp` claim.
6. Crossplane Composition pipeline runs `function-python` → emits desired-state resources.
7. Each MR controller reconciles its slice of the desired state to the real world.
8. TeraSky plugin reads the resource graph and renders it in the Backstage catalog entity page for the new app.
9. Lifecycle: edit the claim file in git → Crossplane reconciles. Delete the file → ArgoCD prunes → Crossplane cascades deletion.

### Invariants

- **XRD scope is `Namespaced`** (Crossplane v2 default). The `HomelabApp` claim lives in `platform-system`; the Composition produces resources spanning cluster-scope (Provider MRs), the new per-app namespace, and `crossplane-system` (provider-managed connection secrets).
- **The Backstage template emits a claim, not raw managed resources.** This is the abstraction boundary. Replacing the Composition implementation later does not change the user-facing contract.
- **No write-side RBAC for the Backstage ServiceAccount.** TeraSky plugin needs read-only on Crossplane CRDs and the relevant `*.aws.upbound.io` groups; nothing more.

### Component map

| Layer | New (this design) | Existing (leveraged) |
|---|---|---|
| Backstage portal | TeraSky plugins, `new-homelab-app` template | Portal v1.0.1, existing scaffolder actions |
| Cluster control plane | XRD, Composition, function-python package | Crossplane v2.2.1, Upbound providers v2.5.3, provider-sql |
| Provider additions | `provider-aws-ecr:v2.5.3` (PR-C) | `provider-aws-s3`, `provider-aws-iam`, `upbound-provider-family-aws` |
| Cluster RBAC | ClusterRole/Binding for Backstage SA | — |
| ArgoCD | New `homelab-app-platform` Application | master-app pattern unchanged |
| Secrets | Per-app ExternalSecrets emitted by Composition (PR-D) | Vault, ESO, Kyverno ECR-auth policy |

## 5. Components — file-level changes per PR

### PR-A — TeraSky Crossplane plugins (visualization-only)

**This repo (`arigsela/kubernetes`):**
- `base-apps/backstage/configmaps.yaml` — add `KUBERNETES_API_URL` env, configuration for the kubernetes plugin (TeraSky depends on it).
- `base-apps/backstage/rbac.yaml` — new `ClusterRole` + `ClusterRoleBinding` granting the Backstage ServiceAccount `get/list/watch` on:
  - API groups: `pkg.crossplane.io`, `apiextensions.crossplane.io`, `platform.asela.io`, `s3.aws.upbound.io`, `iam.aws.upbound.io`, `ecr.aws.upbound.io`, `aws.upbound.io`.
  - **No Secrets, no write verbs.**
- `base-apps/backstage/deployments.yaml` — bump image tag once the new image is built (`v1.0.1 → v1.1.0`).

**Backstage source repo (separate, user applies + builds):**
- `package.json`: `yarn add @terasky/backstage-plugin-crossplane-resources-frontend @terasky/backstage-plugin-crossplane-permissions-backend @terasky/backstage-plugin-crossplane-common`
- `packages/app/src/App.tsx`: register the frontend plugin route + EntityPage Crossplane tab.
- `packages/backend/src/index.ts`: register the permissions backend.
- Build → push to ECR `backstage-portal:v1.1.0` (per CLAUDE.md, user-handled).

### PR-B — HomelabApp v1.0 Slim

**This repo — new directory `base-apps/homelab-app-platform/`:**
- `xrd.yaml` — `CompositeResourceDefinition` in group `platform.asela.io`, kind `HomelabApp`, plural `homelabapps`, scope `Namespaced`, version `v1alpha1` (served, referenceable). Schema fields: required `manifestPath` (string).
- `composition.yaml` — `Composition` referencing the function package via a single pipeline step.
- `function.yaml` — `Function` resource pointing at the function-python OCI image (initial `v0.1.0`).
- `function-source/` — Python source for the composition function:
  - `main.py` — `compose(req, rsp)` Slim version: emits Namespace + ArgoCD Application.
  - `requirements.txt` — `crossplane-function-sdk-python`, etc.
  - `Dockerfile` — multi-stage Python image.
  - `crossplane.yaml` — function package metadata.

**This repo — new ArgoCD Application:**
- `base-apps/homelab-app-platform.yaml` — wires the new directory into the master-app pattern.

**Backstage source repo:**
- `templates/new-homelab-app/template.yaml` — Software Template with parameters (`name`, `manifestPath`) and steps: `fetch:template` (skeleton produces the claim YAML) → `publish:github` (opens a PR adding `base-apps/<app>-claim.yaml`).
- `templates/new-homelab-app/skeleton/{{ values.name }}-claim.yaml` — Nunjucks-templated claim.
- `app-config.yaml`: register the new template under `catalog.locations`.

**Image build (user handles):** `function-homelab-app:v0.1.0` pushed to ECR.

### PR-C — HomelabApp v1.0 Useful (adds ECR + Kyverno auto-auth)

**This repo:**
- `base-apps/crossplane-aws-provider/provider.yaml` — add `provider-aws-ecr:v2.5.3` Provider entry at sync-wave `"1"` (matches the existing s3/iam pattern).
- `base-apps/homelab-app-platform/xrd.yaml` — add served version `v1alpha2` adding required `ecrRepo` field. v1alpha1 stays served.
- `base-apps/homelab-app-platform/function-source/main.py` — extends `compose()`:
  - Adds ECR `Repository` resource.
  - Adds the Kyverno-trigger label on the Namespace (e.g., `ecr-pull-secret: enabled`) so the existing Kyverno policy generates the imagePullSecret.

**Backstage source repo:**
- `templates/new-homelab-app/template.yaml` — add `ecrRepo` form field.
- Skeleton — include `ecrRepo` in the rendered claim.

**Image build:** `function-homelab-app:v0.2.0`.

### PR-D — HomelabApp v1.0 Full-fat (conditional S3 + Postgres)

**This repo:**
- `base-apps/homelab-app-platform/xrd.yaml` — add served version `v1alpha3` adding optional `wantsS3` and `wantsPostgres` boolean fields (default `false`).
- `base-apps/homelab-app-platform/function-source/main.py`:
  - Add `make_s3_bundle(spec)` returning Bucket + IAM User + AccessKey + Policy + UserPolicyAttachment.
  - Add `make_postgres_bundle(spec)` returning Database + Role + ExternalSecret (Vault path `kv/data/<app>/postgres`).
  - Dispatch in `compose()`: `if spec.get("wantsS3")`, `if spec.get("wantsPostgres")`.
- `base-apps/homelab-app-platform/function-source/tests/test_compose.py` — pytest covering Slim, Useful, Full-fat S3-only, Full-fat Postgres-only, Full-fat both branches. (Test discipline starts in PR-B but is decisive here.)

**Backstage source repo:**
- `templates/new-homelab-app/template.yaml` — add `wantsS3`, `wantsPostgres` checkbox fields.
- Skeleton — include the new fields in the claim.

**Image build:** `function-homelab-app:v0.3.0`.

## 6. Demo flow (post-PR-D)

Illustrative example: user wants `recipe-sharer` with S3 and Postgres.

1. Backstage → Create → **New Homelab App**.
2. Form: `name=recipe-sharer`, `manifestPath=base-apps/recipe-sharer`, `ecrRepo=recipe-sharer`, `wantsS3=true`, `wantsPostgres=true`.
3. PR opened: `base-apps/recipe-sharer-claim.yaml` with `apiVersion: platform.asela.io/v1alpha3`, `kind: HomelabApp`, the four spec fields, and `metadata.namespace: platform-system`.
4. Merge → ArgoCD applies (~30s) → Crossplane runs Composition (~10s) → resources reconcile (~60–120s).
5. **11 composed resources** at `READY=True`: Namespace, ArgoCD Application, ECR Repository, S3 Bucket, IAM User, AccessKey, IAM Policy, UserPolicyAttachment, Postgres Database, Postgres Role, ExternalSecret.
6. Backstage catalog entity for `recipe-sharer` shows the resource graph via the TeraSky plugin tab.
7. Lifecycle: edit `wantsPostgres: false` in the claim → next sync prunes Postgres resources, keeps the rest. `git rm` the claim file → ArgoCD prunes the claim → Crossplane cascades deletion.

## 7. Learning checkpoints

After **PR-A**: can navigate the Backstage Crossplane resource graph for existing managed resources. Understands the catalog → entity → annotation → cluster-discovery model and the Backstage permissions framework.

After **PR-B**: has authored a working XRD, a Composition with a function-pipeline step, a function-python composition function, and a Backstage Software Template. Has produced a complete IDP self-service loop on minimal payload. **If the project stalled here, this would still be a defensible IDP foundation.**

After **PR-C**: understands compose-anything (mixing AWS MRs, native K8s objects, ArgoCD CRDs in one Composition), XRD schema versioning (additive `v1alpha2`), and the IDP-delegates-to-platform-services pattern (Composition emits a labeled Namespace; Kyverno does the rest).

After **PR-D**: can write conditional Composition logic in Python with bundle factoring (`make_s3_bundle`, `make_postgres_bundle`), connection-secret propagation across namespaces, and cross-controller dependencies (Postgres database needs cluster). Has a pytest harness that validates Composition output before it ships.

## 8. Risks & mitigations

1. **function-python OCI image lifecycle.** Three image builds across the rollout. Each PR-B/C/D references a different image tag. *Mitigation:* spec calls out the build step explicitly at each PR boundary; user handles the `docker build && docker push` step per CLAUDE.md, then bumps `function.yaml` to reference the new tag.

2. **Backstage source repo coordination.** PR-A, PR-B, PR-C, and PR-D each require parallel work in the separate Backstage portal source repo. *Mitigation:* each PR's spec produces an explicit "Backstage source diff" artifact (file paths + line-level changes). The kubernetes-repo PR is gated on the Backstage image being built and pushed.

3. **Conditional composition function correctness (PR-D).** The `if wantsS3` / `if wantsPostgres` branches are where Composition projects most often stall. *Mitigation:* pytest harness from PR-B onward (not just PR-D). Each PR adds tests for its branches before they ship.

4. **Connection secret propagation across namespaces.** S3 AccessKey writes a Secret in `crossplane-system`; the consuming app needs it in its own namespace. Postgres uses a different path (Vault → ESO). *Mitigation:* the Composition emits an `ExternalSecret` in the per-app namespace pointing at the Crossplane connection secret (or at Vault for Postgres). Explicitly tested in PR-D.

5. **RBAC scope creep on the Backstage ServiceAccount.** TeraSky plugin reads many CRD groups. *Mitigation:* ClusterRole limited to `get/list/watch` on the specific groups listed in §5 PR-A. **No Secrets verb. No write verbs.**

6. **XRD schema evolution across PR-B/C/D (`v1alpha1 → v1alpha2 → v1alpha3`).** Older claims must keep reconciling. *Mitigation:* additive-only schema changes (new optional fields with defaults). Older versions remain `served: true`. No conversion webhook in v1.x of this design. If a breaking schema change is ever needed, that's a separate v2.0 design conversation.

7. **TeraSky plugin / Backstage upstream version compatibility.** TeraSky's published plugins are built and tested against Backstage `1.40.x`. The user's portal is at custom image `v1.0.1` — its underlying upstream Backstage version is set by `package.json` in the portal source repo and may differ from `1.40.x`. *Mitigation:* PR-A starts with a compatibility check — read the portal's `package.json`, confirm Backstage upstream version is `>=1.30.0` (TeraSky's stated floor) before installing plugins. If the portal is older, decide whether to upgrade Backstage upstream first (out-of-scope side quest) or pin a TeraSky plugin version that matches.

## 9. Out of scope (deferred deliberately)

- Migrating existing infra (`base-apps/loki-aws-infrastructure/`, `base-apps/argo-workflows-aws-infrastructure/`) to claim against `HomelabApp`. Future v1.x candidate.
- Multiple XRDs (e.g., a standalone `JustABucket`, an `AppEnvironment` for environments, etc.). Good v2.0 candidates.
- Composition Revisions / progressive rollout strategies. Default `Automatic` activation only.
- Backstage user-level permissions / multi-tenant guardrails (quotas, ownership filters). Single-user homelab.
- Cost guardrails (budget alerts, resource caps in the XRD schema).
- Crossplane v2 Operations / function-pipeline workflow primitive.
- Non-AWS providers (Cloudflare, GCP, GitHub-via-Crossplane).
- Multi-cluster fleet patterns / hub-spoke.
- Backstage TechDocs / API docs entity types.

## 10. Acceptance

This design is "done" when:
1. All four PRs (A → D) are merged with post-merge verification per PR.
2. Backstage portal shows the **New Homelab App** template under Create, with the form fields appropriate to the latest XRD version.
3. End-to-end smoke test: claim a fresh app (e.g., `scratch-app` with `wantsS3=true wantsPostgres=true`) — all 11 composed resources reach `READY=True`. Delete the claim file — clean cascade.
4. Backstage catalog entity for the smoke-test app shows the full resource graph correctly via the TeraSky plugin.
5. pytest suite passes for `function-source/`, with at least one test per spec.* branch.

---

## Appendix A: Decisions made during brainstorming

| Decision | Picked | Rationale |
|---|---|---|
| Depth | Full IDP (templates + XRDs + Compositions) | Learning goal is depth; user accepted the larger scope |
| Anchor XRD | `HomelabApp` (full new-app scaffold) | Highest learning surface; mixes AWS + K8s + ArgoCD CRDs |
| v1.0 scope | Full-fat (Slim + Useful + conditional S3 + Postgres) | User explicitly chose conditional logic as the learning peak |
| Composition function language | `function-python` | Leverages existing FastAPI/Python skills; conditional logic is just `if`; testable with pytest |
| Implementation phasing | 4 PRs (Layered phasing) | Each PR ships working IDP; conditional logic lands in smallest possible scope at PR-D |

## Appendix B: Glossary (concepts new in this design)

- **XRD** — `CompositeResourceDefinition`, the Crossplane CRD that defines a custom API (e.g., `HomelabApp`).
- **Composition** — the recipe that turns a claim into managed resources. In v2 these are pipelines of functions.
- **Composition function** — an OCI-packaged program that takes the claim spec and emits desired-state resources. We use `function-python`.
- **Claim** — a user-created instance of an XRD-defined kind. In v2 these are namespaced by default.
- **MR (Managed Resource)** — Crossplane's term for a Kubernetes object that represents an external resource (e.g., an AWS Bucket).
- **TeraSky plugin** — third-party Backstage plugin set that visualizes Crossplane resource graphs in the catalog.
