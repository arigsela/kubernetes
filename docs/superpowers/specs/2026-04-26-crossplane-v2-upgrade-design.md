# Crossplane v1.15 → v2 Upgrade — Design

**Date:** 2026-04-26
**Author:** Ari Sela (with Claude)
**Status:** Approved for plan
**Scope:** GitOps repo `arigsela/kubernetes`, paths `base-apps/crossplane-system/` and `base-apps/crossplane-aws-provider/`

## 1. Goal

Upgrade the in-cluster Crossplane control plane from v1.15.0 to the latest stable v2 release line, and the Upbound AWS providers from v1.12.0 to v2.5.3, with no functional disruption to the AWS resources currently managed by Crossplane (Loki S3/IAM, argo-workflows S3/IAM).

## 2. Current State

| Component | Version | Path |
|---|---|---|
| Crossplane core (Helm subchart) | `1.15.0` | `base-apps/crossplane-system/Chart.yaml` |
| `provider-aws-s3` (Upbound) | `v1.12.0` | `base-apps/crossplane-aws-provider/provider.yaml` |
| `provider-aws-iam` (Upbound) | `v1.12.0` | `base-apps/crossplane-aws-provider/provider.yaml` |
| ProviderConfig | `aws.upbound.io/v1beta1` | `base-apps/crossplane-aws-provider/provider-config.yaml` |
| Managed resources in use | `s3.aws.upbound.io/v1beta1` Bucket; `iam.aws.upbound.io/v1beta1` User, AccessKey, Policy, PolicyAttachment | `loki-aws-infrastructure/`, `argo-workflows-aws-infrastructure/` |
| Compositions / XRDs | none | — |

## 3. Target State

| Component | Version |
|---|---|
| Crossplane core | `v2.2.1` (or latest v2.2.x available at PR-prep time) via the corresponding chart version on `https://charts.crossplane.io/stable` |
| `provider-aws-s3` | `v2.5.3` |
| `provider-aws-iam` | `v2.5.3` |
| ProviderConfig API | unchanged (`aws.upbound.io/v1beta1`) |
| MR APIs | unchanged (`v1beta1`, cluster-scoped) — namespaced `v1beta2` migration is **out of scope** for this work |

## 4. Why this upgrade is low-risk for our setup

Of the five major Crossplane v2 breaking changes, four do not apply to this repo:

| v2 breaking change | Impact here |
|---|---|
| Native patch & transform compositions removed | None — no Compositions in repo |
| `ControllerConfig` removed (→ `DeploymentRuntimeConfig`) | None — not used in `values.yaml` |
| External Secret Stores (Crossplane's own) removed | None — secrets flow via ESO + Vault |
| XR connection-detail support removed | None — no XRs |
| Default registry flag removed; FQ image URLs required | Already compliant — packages use `xpkg.upbound.io/...` |

The fifth shift — namespaced MRs (`v1beta2`) — is additive in Upbound v2 providers; the existing cluster-scoped `v1beta1` API continues to be served, so existing manifests reconcile without edits.

## 5. Architecture

Two-stage GitOps version bump executed via the existing master-app pattern.

```
                    ┌──────────────────┐
                    │   PR #1 merged   │ providers v1.12.0 → v2.5.3
                    └────────┬─────────┘
                             │ argocd auto-sync
                             ▼
              ┌────────────────────────────┐
              │  Crossplane v1.15.0 core   │  (unchanged)
              │  ↓ pulls new pkg revisions │
              │  provider-aws-s3   v2.5.3  │
              │  provider-aws-iam  v2.5.3  │
              └────────────┬───────────────┘
                           │  v1beta1 MR APIs unchanged
                           ▼
                    ┌──────────────────┐
                    │   PR #2 merged   │ chart 1.15.0 → 2.x
                    └────────┬─────────┘
                             │ argocd auto-sync
                             ▼
              ┌────────────────────────────┐
              │  Crossplane v2.2.x core    │
              │  + same v2.5.3 providers   │
              │  + same v1beta1 MRs        │
              └────────────────────────────┘
```

Invariants preserved across both PRs:
- `crossplane-system` namespace, `aws-secret`, and `crossplane-provider-aws` ServiceAccount untouched.
- ArgoCD master-app pattern, sync waves (`"1"` providers, `"2"` ProviderConfig), `prune: true`, `selfHeal: true` unchanged.
- Vault-backed `aws-secret` (managed by Terraform / ESO) untouched.
- All consuming MRs in `loki-aws-infrastructure/` and `argo-workflows-aws-infrastructure/` untouched.

## 6. Components / File-level changes

### PR #1 — Provider bump

**File:** `base-apps/crossplane-aws-provider/provider.yaml`

- `provider-aws-s3` package tag: `v1.12.0` → `v2.5.3`
- `provider-aws-iam` package tag: `v1.12.0` → `v2.5.3`
- Header comment on line 2 updated to reflect new versions.

No other edits in this PR.

### PR #2 — Core chart bump

**File:** `base-apps/crossplane-system/Chart.yaml`

- `appVersion`: `"1.15.0"` → exact target (e.g., `"2.2.1"`), confirmed during PR prep.
- `dependencies[0].version`: `1.15.0` → exact chart version that ships the target appVersion, resolved during PR prep via `helm search repo crossplane/crossplane --versions`.

**File:** `base-apps/crossplane-system/values.yaml`

Likely no changes. Pre-merge `helm template` diff confirms the v2 chart still honors:
- `crossplane.resourcesCrossplane.{requests,limits}`
- `crossplane.resourcesRBACManager.{requests,limits}`
- `crossplane.metrics.enabled`

Any rename surfaced by the diff is fixed in the same PR.

### Files explicitly **not** modified

- `base-apps/crossplane-aws-provider/provider-config.yaml`
- `base-apps/crossplane-aws-provider.yaml`, `base-apps/crossplane-system.yaml` (ArgoCD Applications)
- `base-apps/crossplane-system/secret-store.yaml`
- All MRs under `base-apps/loki-aws-infrastructure/` and `base-apps/argo-workflows-aws-infrastructure/`
- `terraform/`

## 7. Pre-merge verification (per PR)

Both PRs must include a verification block in the PR body produced from these commands.

### PR #1 (providers)

```bash
# From a checkout of the PR branch
kubectl diff -f base-apps/crossplane-aws-provider/provider.yaml
```

Expected: only `spec.package` field changes on the two Provider resources. Pasted into PR body.

### PR #2 (core)

Render both versions side by side from the PR branch:

```bash
# Render v2 (current branch state, with bumped Chart.yaml)
cd base-apps/crossplane-system
helm dependency update
helm template . -f values.yaml > /tmp/v2.yaml

# Render v1 from main without leaving the branch
git worktree add /tmp/crossplane-v1-baseline main
cd /tmp/crossplane-v1-baseline/base-apps/crossplane-system
helm dependency update
helm template . -f values.yaml > /tmp/v1.yaml
cd -
git worktree remove /tmp/crossplane-v1-baseline

diff -u /tmp/v1.yaml /tmp/v2.yaml > /tmp/crossplane-chart.diff
```

A summarized form of the diff (CRDs added/removed, RBAC changes, deployment changes, removed/renamed values) is pasted into the PR body. If any unexpected change appears (e.g., a removed values key currently set in `values.yaml`), the PR is updated to address it before merge.

## 8. Post-merge verification (per PR)

### PR #1

```bash
# All providers Healthy and INSTALLED=True
kubectl get providers.pkg.crossplane.io
# Expected: provider-aws-s3 and provider-aws-iam at v2.5.3, INSTALLED=True, HEALTHY=True

# Provider revisions present and active
kubectl get providerrevisions.pkg.crossplane.io
# Expected: latest revision per provider has STATE=Active

# All managed resources still SYNCED and READY
kubectl get buckets.s3.aws.upbound.io,users.iam.aws.upbound.io,accesskeys.iam.aws.upbound.io,policies.iam.aws.upbound.io,userpolicyattachments.iam.aws.upbound.io -A
# Expected: SYNCED=True, READY=True for all rows
```

Sign-off criterion: all of the above true within 5 minutes of ArgoCD sync; no events of type `Warning` on the existing MRs.

### PR #2

```bash
# Crossplane core deployment image and Ready
kubectl -n crossplane-system get deploy crossplane -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
# Expected: image tag matches the v2 appVersion

kubectl -n crossplane-system get pods
# Expected: crossplane and crossplane-rbac-manager pods Running, Ready

# Re-run the PR #1 post-merge MR checks; expected unchanged
```

Sign-off criterion: same as PR #1 plus the core deployment image reflects the new version.

## 9. Rollback

### Trigger conditions (any of)

- A managed resource flips from `READY=True` to `READY=False` and stays there >5 min after sync.
- A Provider does not reach `HEALTHY=True` within 10 min of sync.
- The `crossplane` deployment in `crossplane-system` enters `CrashLoopBackOff` (PR #2 only).
- A `helm template` rendering error appears in the ArgoCD UI for the `crossplane-system` Application (PR #2 only).

### Procedure

```bash
# Identify the offending merge commit
git log --oneline base-apps/crossplane-aws-provider base-apps/crossplane-system

# Revert it
git revert <sha>
git push origin main
```

ArgoCD reverses the change on next sync. Because v2 providers retain the legacy `v1beta1` MR APIs and ProviderConfig is untouched, downgrading the provider package or chart does not require any MR mutation.

If only the providers are problematic and core is fine: revert PR #1 only. The reverse (revert PR #2 only, leaving providers at v2.5.3) is also supported — Upbound v2 providers run on Crossplane v1.x core.

### Out-of-band escape hatch

If `git revert` is for any reason not enough (e.g., a stuck reconcile), Provider revisions are kept at `revisionHistoryLimit: 1`, but a new revision can be installed by editing `provider.yaml` to a known-good tag and merging — same path, same loop.

## 10. Out of scope

- Migrating MRs to namespaced `v1beta2` API. Tracked as a follow-up — captured as a TODO in `base-apps/crossplane-aws-provider/provider.yaml` header comment after PR #2 merges, or scheduled as a separate brainstorm.
- Adopting Crossplane v2 Operations / function-pipeline workflows.
- Replacing direct MRs with XRDs / Compositions.
- Any change to Vault auth, ESO wiring, or the `aws-secret` Terraform module.

## 11. Acceptance

This design is approved when both PRs:
1. Merge cleanly with the verification block in the PR body.
2. Reach the post-merge sign-off criteria in §8 within the stated time bounds.
3. Do not require any edit to MRs, ProviderConfig, ESO, or Terraform.

---

## Appendix A: Migration findings (executed 2026-04-26 to 2026-04-27)

The upgrade was executed in **four PRs**, not the planned two, plus three operational hotfixes that this spec did not anticipate. Recording them here so a future upgrader knows the real shape.

### PRs that actually shipped

| # | Branch | Purpose | Why it was needed |
|---|---|---|---|
| #191 | `docs-crossplane-v2-upgrade-design-and-plan` | Add this spec + the implementation plan | Branch protection blocks direct push to `main`; docs go through their own PR |
| #192 | `crossplane-providers-v2.5.3` | Bump `provider-aws-s3` and `provider-aws-iam` to `v2.5.3` | Per the original plan |
| **#193** | `crossplane-family-aws-v2.5.3` | **Explicitly declare `upbound-provider-family-aws v2.5.3`** | Spec gap (see Finding 1) |
| #194 | `crossplane-core-v2` | Bump core Helm subchart to `2.2.1` | Per the original plan |

### Finding 1 — Implicit family-provider dependency

**What the spec missed.** The original `provider.yaml` declared only the two sub-providers (`provider-aws-s3`, `provider-aws-iam`). The spec assumed Crossplane's package manager would auto-resolve the `upbound-provider-family-aws` dependency to a compatible version when the sub-providers were bumped. It does not — it stuck at the previously auto-installed `v2.3.0` (compatible with `v1.12.0` sub-providers, **incompatible** with `v2.5.x`).

**Symptom.** After PR #192 merged, both sub-provider ProviderRevisions stayed `HEALTHY=Unknown` with repeated `ResolveDependencies` warning events. Data plane (managed AWS resources) stayed `SYNCED=True, READY=True` throughout.

**Fix.** PR #193 added the family provider explicitly to `provider.yaml` at sync-wave `"0"`:
```yaml
apiVersion: pkg.crossplane.io/v1
kind: Provider
metadata:
  name: upbound-provider-family-aws
  annotations:
    argocd.argoproj.io/sync-wave: "0"
spec:
  package: xpkg.upbound.io/upbound/provider-family-aws:v2.5.3
```
Crossplane adopted the existing in-cluster Provider resource (matched by name) and rolled it `v2.3.0 → v2.5.3`.

**Lesson.** When upgrading Upbound family-prefixed providers, pin the family explicitly. The implicit auto-resolved family is fine on initial install but drifts during version churn.

### Finding 2 — `BucketLifecycleConfiguration.status.atProvider` schema break

**What the spec missed.** The spec assumed Upbound providers would convert v1beta1↔v1beta2 cleanly. They cannot when stored data uses incompatible types.

**Symptom.** After PR #194 merged, ArgoCD's cluster cache failed to load with:
```
conversion webhook for s3.aws.upbound.io/v1beta1, Kind=BucketLifecycleConfiguration failed:
json: cannot unmarshal string into Go struct field
RuleFilterObservation.status.atProvider.rule.filter.objectSizeGreaterThan
of type float64
```
The `loki-logs-lifecycle` resource had `objectSizeGreaterThan: ""` (empty string) in `.status.atProvider`, written by the v1.12.0 controller. The v2.5.3 schema typed this field as `float64`. The conversion webhook couldn't parse the empty string. ArgoCD couldn't list the resource → couldn't compute sync state → couldn't apply PR #194's chart bump. **Circular block.**

**Fix.** Surgical operational hotfix:
1. Backup the CRD's conversion config: `kubectl get crd bucketlifecycleconfigurations.s3.aws.upbound.io -o jsonpath='{.spec.conversion}' > /tmp/blc-conversion-backup.json`
2. Disable the webhook: `kubectl patch crd bucketlifecycleconfigurations.s3.aws.upbound.io --type=merge -p '{"spec":{"conversion":{"strategy":"None","webhook":null}}}'`
3. Clear the offending status field: `kubectl patch bucketlifecycleconfiguration.s3.aws.upbound.io loki-logs-lifecycle --subresource=status --type=merge -p '{"status":{"atProvider":null}}'`
4. Restore the webhook: `kubectl patch crd bucketlifecycleconfigurations.s3.aws.upbound.io --type=merge -p "{\"spec\":{\"conversion\":$(cat /tmp/blc-conversion-backup.json)}}"`
5. Trigger ArgoCD refresh — cluster state now loadable, PR #194 applies.

**Lesson.** Before merging the core chart bump, dry-run probe the CRD conversion paths for any resource type whose schema may have shifted between v1beta1 (the legacy stored version) and v1beta2 (the new served version). For Upbound `s3.aws.upbound.io` and `iam.aws.upbound.io` v2.5.x, watch specifically for fields that changed from `string` to `float64` or other type-narrowing changes.

### Finding 3 — `environmentconfigs.apiextensions.crossplane.io` storedVersion conflict

**What the spec missed.** Crossplane v2 promoted `EnvironmentConfig` past `v1alpha1`, but the in-cluster CRD still listed `v1alpha1` in `status.storedVersions`. The v2.2.1 init container's CRD spec doesn't include `v1alpha1`, and Kubernetes refuses to update a CRD that drops a stored version while data may exist.

**Symptom.** After ArgoCD applied PR #194, the new `crossplane:v2.2.1` Pod entered `Init:CrashLoopBackOff` with:
```
crossplane: error: cannot initialize core: cannot apply crd:
CustomResourceDefinition.apiextensions.k8s.io "environmentconfigs.apiextensions.crossplane.io"
is invalid: status.storedVersions[0]: Invalid value: "v1alpha1":
missing from spec.versions
```

**Fix.**
1. Confirm zero `EnvironmentConfig` resources exist (we had none): `kubectl get environmentconfigs.apiextensions.crossplane.io -A`
2. Delete the CRD entirely: `kubectl delete crd environmentconfigs.apiextensions.crossplane.io`
3. The v2.2.1 init container recreates it cleanly with the new spec on its next retry.

**Lesson.** Before the core chart bump, audit `status.storedVersions` on Crossplane-owned CRDs for `v1alpha1` versions that v2 has graduated past. Common candidates: `EnvironmentConfig`, `Usage`, `Lock`. If no resources of those kinds exist, the CRD can be deleted to let v2 init recreate it. If resources exist, a real storage migration is needed before the upgrade.

### Finding 4 — Old vs new ReplicaSet fight during rolling update

**What the spec missed.** While the new v2.2.1 Pod was looping in init due to Finding 3, the old v1.15.0 ReplicaSet remained at `replicas=1` (rolling-update preservation logic). The old v1.15.0 init kept re-creating the `environmentconfigs` CRD with `v1alpha1` in spec.versions, perpetuating Finding 3.

**Fix.**
```bash
kubectl -n crossplane-system scale rs <old-replicaset-name> --replicas=0
```
Then re-do the CRD delete from Finding 3. The v2.2.1 init then runs unobstructed and succeeds.

**Lesson.** During Crossplane core upgrades that involve CRD schema graduations, expect the old ReplicaSet to actively undo the new init's work. Be ready to manually scale the old RS to 0 if the rolling update stalls.

### What the data plane did throughout

All 10 managed AWS resources (`Bucket`, `User`, `AccessKey`, `Policy`, `UserPolicyAttachment`, `BucketLifecycleConfiguration` across Loki and argo-workflows) **stayed `SYNCED=True, READY=True` throughout the entire upgrade window**, including all four findings above. The control-plane troubles were entirely package-manager / CRD layer; the existing AWS resources reconciled fine via cached desired state on whichever provider revision was Active at the moment.

### Total elapsed

- Plan creation: ~30 minutes
- PRs #191 + #192 + #193 + verification: ~1 hour spread across two sessions
- PR #194 + Findings 2/3/4 + verification: ~1.5 hours across two sessions, including the operational hotfixes
