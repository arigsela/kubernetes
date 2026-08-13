# `managed-apps` ApplicationSet Pilot — Design

**Date:** 2026-08-13
**Author:** Ari Sela (with Claude)
**Status:** Approved for plan
**Scope:** Move 12 boilerplate Argo CD Applications from the `master-app` app-of-apps to a single ApplicationSet, using a git *files* generator
**Prerequisite:** Argo CD 3.5.x (cluster currently runs `3.5.0-rc2` binaries pinned via `global.image.tag`; 3.5.1 GA shipped 2026-08-12)

## 1. Goal

Evaluate the ApplicationSet features that shipped in Argo CD 3.5 — specifically the **Preview Apps tab** — against real applications in this cluster, and keep the result if it proves itself.

This is a hands-on evaluation, not a consolidation drive. Reducing duplicated YAML is a side effect, not the objective. Success is: the pilot is live, the Preview tab has been used to inspect a real change before it applied, and there is enough evidence to decide whether to extend the pattern.

Explicitly **not** a commitment to migrate the remaining 38 applications.

### 1.1 Which 3.5 features this actually exercises

| 3.5 feature | Exercised? | Why |
|---|---|---|
| ApplicationSet UI + Preview Apps tab | **Yes** | The generated set is non-obvious, so previewing it is real information |
| Concurrency controls | No | Designed for hundreds of apps against Git APIs; unobservable at 12 |
| AppSet-in-any-namespace | No | Requires `applicationsetcontroller.namespaces` in `argocd-cmd-params-cm` plus app-in-any-namespace config — a Terraform change disproportionate to the pilot |

The pilot delivers Preview. That is stated up front so the outcome is not mistaken for a broader verdict on 3.5.

## 2. Current state

`master-app` is a plain **Application**, not an ApplicationSet, defined in `terraform/modules/application-sets/application-sets.tf:6`:

```yaml
spec:
  source:
    path: base-apps
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
  syncPolicy:
    automated: { prune: true, selfHeal: true }
```

No `directory.recurse`, so it reads only top-level `base-apps/*.yaml` — 50 files, each a hand-written Application. Subdirectories are invisible to it. (`base-apps/README.md` describes this as "the `master-app` ApplicationSet"; that is wrong, and §7 corrects it.)

### 2.1 The fleet is mostly snowflakes

Of the 50 Applications, 38 carry at least one bespoke field:

| Bespoke field | Count |
|---|---|
| Remote Helm chart source (no in-repo directory) | 14 |
| Inline `helm.values` | 13 |
| `directory.exclude` for `catalog-info.yaml` / `mkdocs.yml` | 21 |
| `ignoreDifferences` | 8 |
| `sync-wave` annotation | 11 |
| Namespace ≠ application name | 24 |

All 50 use `project: default`, one cluster, `targetRevision: main`. There are no ApplicationSets in the repo today.

The 12 that carry **none** of those fields are this pilot's scope.

### 2.2 The 12 in-scope applications

| Application | `source.path` | Namespace | `syncOptions` |
|---|---|---|---|
| `agent-audit-aws-infrastructure` | `base-apps/agent-audit-aws-infrastructure` | `postgresql` | `CreateNamespace=true`, `ServerSideApply=true` |
| `argo-rollouts-config` | `base-apps/argo-rollouts` | `argo-rollouts` | — |
| `argo-workflow-tasks` | `base-apps/argo-workflow-tasks` | `argo-workflows` | — |
| `argo-workflows-aws-infrastructure` | `base-apps/argo-workflows-aws-infrastructure` | `argo-workflows` | — |
| `argo-workflows-config` | `base-apps/argo-workflows` | `argo-workflows` | — |
| `crossplane-aws-provider` | `base-apps/crossplane-aws-provider` | `crossplane-system` | — |
| `crossplane-compositions` | `base-apps/crossplane-compositions` | `crossplane-system` | `CreateNamespace=false` |
| `crossplane-functions` | `base-apps/crossplane-functions` | `crossplane-system` | `CreateNamespace=false` |
| `crossplane-system` | `base-apps/crossplane-system` | `crossplane-system` | `CreateNamespace=true` |
| `ecr-auth` | `base-apps/ecr-auth` | `kube-system` | — |
| `kyverno-policies` | `base-apps/kyverno-policies` | `kyverno` | — |
| `loki-aws-infrastructure` | `base-apps/loki-aws-infrastructure` | `logging` | `CreateNamespace=true`, `ServerSideApply=true` |

Four `syncOptions` variants, and two cases where `source.path` does not match the Application name (`argo-rollouts-config`, `argo-workflows-config`). Both irregularities are load-bearing: naming the generated Applications after their directories would produce `argo-rollouts` and `argo-workflows`, which collide with the existing Helm-chart Applications of those exact names.

Four files carry comments that must survive the move: `agent-audit-aws-infrastructure`'s rationale for targeting the `postgresql` namespace, `crossplane-functions`' note that it syncs before `crossplane-compositions`, `loki-aws-infrastructure`'s note on `ServerSideApply`, and the header comments on the two `crossplane-*` files.

None of the 12 directories contain `catalog-info.yaml`, `docs.md`, `runbook.md`, or `mkdocs.yml` — which is exactly why none needed `directory.exclude`.

`base-apps/crossplane-system/` is an umbrella Helm chart (`Chart.yaml` declaring a `crossplane` 2.2.1 dependency, plus `values.yaml`), not a plain manifest directory. Argo auto-detects this from `source.path`; the Application spec does not mention it and does not need to.

### 2.3 The hazard that shapes the whole design

All 12 Applications carry `resources-finalizer.argocd.argoproj.io`, and `master-app` runs `prune: true`. Deleting `base-apps/loki-aws-infrastructure.yaml` therefore prunes that Application, whose finalizer cascades to the resources it manages — which are Crossplane CRs.

None of the three AWS-infrastructure apps set `deletionPolicy: Orphan` or `managementPolicies`. Crossplane's default is `Delete`:

```
loki-aws-infrastructure/       → S3 bucket asela-chores-loki-logs-20251017 (all Loki chunks), IAM user, IAM policy
argo-workflows-aws-infrastructure/ → S3 bucket, IAM user, IAM policy
agent-audit-aws-infrastructure/    → S3 bucket, IAM user, IAM policy, access key
```

A naive "delete 12 files, add one ApplicationSet" commit destroys real S3 buckets. §5 sequences the cutover so this cannot happen.

## 3. Approach

A **git files generator** over per-application config files, with the config tree living outside `base-apps/`.

### 3.1 Alternatives considered

**Git directory generator.** Generates one Application per subdirectory. Rejected: directory generators expose only path information, so of the four fields the template needs, `name` and `source.path` are derivable but `destination.namespace` and `syncOptions` are not — nothing in a path says `ecr-auth` → `kube-system`. Making it work requires encoding both facts in the layout (`managed/<namespace>/<app>/` plus a parallel `managed-ssa/` tree), which means relocating all 12 directories. **70 files across the repo reference those paths** — 42 markdown docs, 22 other YAML, and 6 scripts and tests with hardcoded paths (`scripts/agent-audit.py`, `scripts/gen-agent-audit-cronjob.py`, `scripts/gen-agent-capability-policy.py`, `scripts/validate-agent-capability.py`, `tests/agent-capability/kyverno/run.sh`, `tests/composition/render.sh`). A ~70-file refactor to pilot a UI feature is disproportionate.

The variant that avoids the move — generating over `base-apps/*` with ~38 exclusions — was also rejected: every future snowflake application must be added to the exclusion list, and forgetting one produces a duplicate Application that fights `master-app`.

**List generator.** All 12 entries inline in the ApplicationSet. Safest, since the generator input is the manifest itself and the empty-generator failure mode cannot occur. Rejected because the Preview tab would show nothing that is not already legible in the file, which defeats the pilot's stated purpose, and because it is not a pattern that would scale.

### 3.2 Why git files wins here

Directories stay exactly where they are, so all 70 references remain valid and no script or test breaks. The config files are purely additive. Both `path` ≠ `name` cases and all four `syncOptions` variants are expressed as ordinary config data, so no `templatePatch` — and therefore no YAML-inside-a-Go-template string escaping — is needed anywhere.

## 4. Architecture

```
base-apps/managed-apps.yaml     ← the ApplicationSet; master-app applies it into argo-cd
appsets/managed-apps/*.yaml     ← 12 config files, outside base-apps entirely
base-apps/<app>/                ← unchanged
```

### 4.1 Why the config tree lives outside `base-apps/`

Two scripts enumerate `base-apps/` subdirectories with no filter for dot- or underscore-prefixed names:

- `scripts/validate-agent-docs.py:157` (`check_index_coverage`) requires a `base-apps/index.md` row for every subdirectory. A `base-apps/_managed/` directory would produce a hard CI failure: `base-apps/index.md has no row for app '_managed'`.
- `scripts/gen-okf.py:80` enumerates the same way and would treat it as an application in the OKF bundle.

Placing the configs at `appsets/managed-apps/` avoids patching both scripts, their tests, and `index.md`. It also removes a latent trap: if anyone ever sets `recurse: true` on `master-app`, config files under `base-apps/` would be applied as Kubernetes manifests and fail the whole app-of-apps sync. Outside `base-apps/`, `master-app` cannot reach them.

`appsets/<appset-name>/` leaves room for a second ApplicationSet later without reshuffling.

The ApplicationSet manifest itself must stay in `base-apps/` so `master-app` applies it. It is a file, not a directory, so the enumerators above ignore it.

### 4.2 Config schema

Four fields, all required:

```yaml
# appsets/managed-apps/loki-aws-infrastructure.yaml
name: loki-aws-infrastructure
sourcePath: base-apps/loki-aws-infrastructure
namespace: logging
syncOptions:
  - CreateNamespace=true
  # ServerSideApply for better handling of managed fields
  - ServerSideApply=true
```

The field is **`sourcePath`, not `path`** — the git files generator injects a built-in `path` object (`.path.filename`, `.path.segments`, and so on), so a config key named `path` would collide with it silently.

`syncOptions` is required even when empty (`[]`). That allows `goTemplateOptions: ["missingkey=error"]`, so a typo'd key fails the render loudly rather than producing a quietly-wrong Application.

Comments identified in §2.2 are carried into the corresponding config files.

### 4.3 The ApplicationSet

```yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: managed-apps
  namespace: argo-cd
spec:
  goTemplate: true
  goTemplateOptions: ["missingkey=error"]
  generators:
    - git:
        repoURL: https://github.com/arigsela/kubernetes
        revision: main
        files:
          - path: appsets/managed-apps/*.yaml
  syncPolicy:
    preserveResourcesOnDeletion: true
  template:
    metadata:
      name: '{{ .name }}'
    spec:
      project: default
      source:
        repoURL: https://github.com/arigsela/kubernetes
        targetRevision: main
        path: '{{ .sourcePath }}'
      destination:
        server: https://kubernetes.default.svc
        namespace: '{{ .namespace }}'
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
        {{- if .syncOptions }}
        syncOptions:
        {{- range .syncOptions }}
          - {{ . }}
        {{- end }}
        {{- end }}
```

`applicationsSync` is left at its default (`create-update-delete`), so the set stays fully declarative: removing a config file removes the Application.

### 4.4 Zero behaviour change

The seven applications with no `syncOptions` get `syncOptions: []`, the conditional renders nothing, and the resulting spec is identical to today's. The two `CreateNamespace=false` applications keep that value verbatim. Every generated Application is spec-equivalent to the file it replaces, apart from the finalizer discussed below and the ApplicationSet's ownership metadata. §6 makes this testable.

### 4.5 `preserveResourcesOnDeletion` and pruning

`preserveResourcesOnDeletion: true` governs only what happens when the **Application object** is deleted. Per-application `syncPolicy.automated.prune: true` still prunes resources removed from git, exactly as today. Day-to-day pruning is unaffected.

What is given up is cascade-cleanup of live resources when an application leaves the set. Given §2.3, that trade is strongly favourable.

A useful consequence: `preserveResourcesOnDeletion: true` means generated Applications carry **no** `resources-finalizer`. The finalizer-stripping required for a safe cutover is therefore not a temporary measure — it is the permanent end state.

## 5. Cutover

### 5.1 Phase 0 — strip the finalizers

One commit editing the 12 existing `base-apps/<app>.yaml` files to remove:

```yaml
  finalizers:
    - resources-finalizer.argocd.argoproj.io
```

Push, let `master-app` sync, then **verify read-only** that all 12 live Applications report an empty `metadata.finalizers`:

```bash
kubectl get application -n argo-cd -o \
  jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.finalizers}{"\n"}{end}'
```

This is a gate, not a step: if any of the 12 still shows `resources-finalizer.argocd.argoproj.io`, Phase 1 must not land. It is what protects the S3 buckets and IAM users in §2.3. Inspection only — no direct `kubectl` mutation, per `CLAUDE.md`.

### 5.2 Phase 1 — the swap, single commit

Delete the 12 `base-apps/<app>.yaml` files; add `base-apps/managed-apps.yaml` and the 12 `appsets/managed-apps/*.yaml`.

On sync, `master-app` prunes the 12 Applications — which now orphan their resources instead of cascading — and creates the ApplicationSet, whose controller recreates the 12 Applications over the still-running resources. They report Synced immediately, because nothing about the resources changed.

There is a brief window in which `master-app` and the ApplicationSet controller both touch the same Application names. It converges: `master-app` prunes only what carries its own tracking annotation, so once it has pruned the originals it leaves the ApplicationSet's copies alone. Worst case is one reconcile flap, and with no finalizers in play a flap cannot touch workloads.

A single commit is preferred over splitting Phase 1 in two, which would trade that flap for a multi-minute window in which nothing reconciles those 12 applications at all.

### 5.3 Rollback

One `git revert` of Phase 1. Deleting the ApplicationSet cascades to its generated Applications, but `preserveResourcesOnDeletion: true` means the resources survive; `master-app` then recreates the original 12 Applications over them. Same convergence, opposite direction.

Phase 0 does not need reverting — no-finalizer is the intended end state either way.

### 5.4 Failure modes

| Failure | Consequence | Mitigation |
|---|---|---|
| Generator matches nothing (path typo, branch rename, repo unreachable) | All 12 Applications deleted | `preserveResourcesOnDeletion: true` — workloads and Crossplane CRs keep running; restoring the glob restores the Applications. This is the failure behind [argo-cd#18780](https://github.com/argoproj/argo-cd/issues/18780) and [argo-cd#9227](https://github.com/argoproj/argo-cd/issues/9227) |
| Duplicate names — someone adds both `base-apps/foo.yaml` and `appsets/managed-apps/foo.yaml` | Two controllers fight over one object | Test asserting the two name sets are disjoint (§6) |
| Malformed config | `missingkey=error` stops the render for all 12; existing Applications keep running but stop reconciling from the ApplicationSet | Fail-safe but silent. The Preview Apps tab is the pre-flight check — see §8 |

## 6. Testing

New `tests/appset/` with a CI job matching the existing per-directory convention in `.github/workflows/validate.yaml` (`pip install pyyaml==6.0.2 pytest==8.3.3` → `python -m pytest tests/appset/ -q`).

1. Every config has all four keys, with correct types.
2. Every `sourcePath` resolves to a non-empty directory.
3. Config `name` values are unique and **disjoint** from the top-level `base-apps/*.yaml` Application names.
4. Golden equivalence — each config, expanded through a small Python restatement of the template, deep-equals a golden copy of the Application spec it replaced, captured during Phase 0 and committed under `tests/appset/golden/`.

Test 4's limitation, stated plainly: it locks the *configs* to the old specs, but the Python expansion restates the Go template and can drift from what Argo actually renders. The template is ~25 lines and is reviewed once. The real proof is the one-time check in §8.3, which exercises the actual controller. The `argocd` CLI is not installed locally, so `argocd appset generate` is not available for CI.

## 7. Documentation

None of the 12 applications appear in `scripts/agent-docs-scope.txt`, so removing their Application files triggers no `docs.md` / `runbook.md` contract. `check_index_coverage` keys off directories, which stay put, so `base-apps/index.md` needs no edit. `gen-techdocs.py` only collects directories containing both `docs.md` and `runbook.md`, so it ignores all of this.

Two documents are still in scope:

- **`docs/managed-apps-appset.md`** (new) — what the ApplicationSet owns, the config schema, how to add or remove an application, the rollback procedure from §5.3, and the `recurse: true` caveat from §4.1. This is the runbook.
- **`base-apps/README.md`** (fix) — it calls `master-app` "the `master-app` ApplicationSet", which is precisely the confusion this work would deepen if left uncorrected. It also lists `chores-tracker-backend`, `chores-tracker-frontend`, `mysql-rds-backup`, and `nginx-ingress`, none of which exist, and marks `n8n`, `postgresql`, and `oncall-agent` as `.yaml.disabled` when all three are live. Scoped to the pattern description and the application inventory, not a rewrite.

## 8. Acceptance criteria

1. All 12 Applications are generated by the ApplicationSet, Synced and Healthy, with no change to their managed resources.
2. `base-apps/` contains 38 top-level Application YAML files plus `managed-apps.yaml`.
3. `python -m pytest tests/appset/ -q` passes in CI, and the full `validate.yaml` workflow is green.
4. **Preview before merge** — the Phase 1 PR's generated set is inspected in the Preview Apps tab before merging, and shows exactly the 12 expected Applications with correct namespaces and `syncOptions`.
5. **Deliberate break** — after landing, a branch with one config missing its `namespace` key confirms whether Preview surfaces the render failure rather than letting it reach the cluster. This is the real test of whether the feature earns its keep.
6. **Cutover proof** — the 12 live Application specs differ from the goldens only in the absent finalizer and the ApplicationSet's ownership metadata.
7. The Phase 1 revert commit is prepared and reviewed before Phase 1 merges, so §5.3 is a known-good one-command rollback rather than an improvised one. Rollback is not executed against the live cluster as part of acceptance.

## 9. Out of scope

- `applicationsetcontroller.namespaces` and AppSet-in-any-namespace (Terraform change, disproportionate to the pilot).
- Concurrency controls (unobservable at 12 applications).
- Any Terraform change. `master-app` applies the ApplicationSet, so none is required.
- Backstage scaffolder changes. `templates/new-app/` continues to emit a full `base-apps/<name>.yaml` Application.
- Migrating any of the other 38 applications, and any commitment to do so.

## 10. Known follow-ups, deliberately not addressed here

- `base-apps/crossplane-system/secret-store.yaml` sits at the umbrella chart's root rather than under `templates/`, so Argo is almost certainly not applying it. Pre-existing and unchanged by this work.
- `CLAUDE.md` imports `@AGENTS.md`, which does not exist in the repository.
- The Argo CD version pin in `terraform/modules/argocd/variables.tf:13` carries a TODO to move off the `3.5.0-rc2` binaries once argo-helm publishes a 3.5 chart. That is now unblocked: 3.5.1 GA shipped 2026-08-12 and argo-cd chart 10.3.3 shipped 2026-08-13.
