# Crossplane v1.15 → v2 Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade in-cluster Crossplane from v1.15.0 to v2.2.x and Upbound AWS providers from v1.12.0 to v2.5.3 via two sequential PRs, with no edits to consuming managed resources.

**Architecture:** Two-stage GitOps version bump. PR #1 bumps the two Upbound provider packages while core stays on v1.15.0 (Upbound v2 providers run on v1.x core). PR #2 bumps the Crossplane Helm subchart to v2 once the providers are verified healthy. Each PR carries a pre-merge render-and-diff and a post-merge verification gate. No edits to ProviderConfig, ESO, Vault, Terraform, or any consuming MR.

**Tech Stack:** Crossplane (Helm), Upbound provider packages, ArgoCD (GitOps auto-sync), kubectl, helm, GitHub MCP for PR ops.

**Source spec:** `docs/superpowers/specs/2026-04-26-crossplane-v2-upgrade-design.md`

---

## File Map

| Path | Action | Responsibility |
|---|---|---|
| `base-apps/crossplane-aws-provider/provider.yaml` | Modify (PR #1) | Pin Upbound `provider-aws-s3` and `provider-aws-iam` versions |
| `base-apps/crossplane-system/Chart.yaml` | Modify (PR #2) | Pin Crossplane core Helm subchart version |
| `base-apps/crossplane-system/values.yaml` | Modify (PR #2, only if rendering surfaces a key rename) | Helm values for the core subchart |

No new files. No deletions.

---

## Pre-flight (one-time, before Task 1)

**You have access to a live cluster?** Yes — assumed. The plan calls `kubectl` and `helm` against the in-cluster Crossplane. Confirm with:

```bash
kubectl config current-context
# Expected: the homelab cluster context (single cluster at https://192.168.0.100:6443)
kubectl -n crossplane-system get deploy crossplane
# Expected: a Deployment named "crossplane" exists
```

If either fails, halt and resolve cluster access before continuing.

**You have helm installed?**

```bash
helm version --short
# Expected: v3.x.x
helm repo list | grep crossplane
# Expected: a repo entry for https://charts.crossplane.io/stable
# If missing:
#   helm repo add crossplane https://charts.crossplane.io/stable
#   helm repo update
```

---

## Task 1: Resolve exact target chart version

**Files:** none (research-only task)

The spec pins providers at `v2.5.3` (concrete) but leaves the core Helm chart version as "latest v2.2.x at PR-prep time." Lock the exact chart version before touching `Chart.yaml`.

- [ ] **Step 1.1: Refresh the Crossplane Helm repo cache**

```bash
helm repo update crossplane
# Expected: "Successfully got an update from the \"crossplane\" chart repository"
```

- [ ] **Step 1.2: List available chart versions and identify the latest v2.x**

```bash
helm search repo crossplane/crossplane --versions | head -20
```

Expected: a table with columns `NAME`, `CHART VERSION`, `APP VERSION`, `DESCRIPTION`. Look for the highest `CHART VERSION` whose `APP VERSION` starts with `2.` and is the most recent (typically `2.2.x`).

- [ ] **Step 1.3: Record the resolved versions in your scratch notes**

Write down (you will paste these into PR bodies later):
- **Target chart version:** `<resolved>` (e.g., `1.21.0` — chart version)
- **Target app version:** `<resolved>` (e.g., `2.2.1` — app version)
- **Provider versions:** `v2.5.3` (both `provider-aws-s3` and `provider-aws-iam`)

- [ ] **Step 1.4: Sanity check the chart's values schema for keys we set**

```bash
helm show values crossplane/crossplane --version <resolved-chart-version> | grep -E '^(resourcesCrossplane|resourcesRBACManager|metrics):' -A 4
```

Expected: each of `resourcesCrossplane`, `resourcesRBACManager`, and `metrics` is present in the v2 chart's default values. If any is **missing or renamed**, note the rename — `values.yaml` will need a corresponding edit in PR #2 (Task 6, Step 6.4).

This task is research only — no commits, no PR.

---

## Task 2: PR #1 — Bump Upbound provider versions

**Files:**
- Modify: `base-apps/crossplane-aws-provider/provider.yaml` (lines 2, 16, 32)

- [ ] **Step 2.1: Create the branch from main**

```bash
git fetch origin main
git checkout main
git pull --ff-only origin main
git checkout -b crossplane-providers-v2.5.3
```

Expected: `On branch crossplane-providers-v2.5.3` after the last command.

- [ ] **Step 2.2: Edit `base-apps/crossplane-aws-provider/provider.yaml`**

Apply three edits (use the `Edit` tool, not raw shell):

1. Header comment on line 2: change
   ```
   # Version: v1.12.0 (from provider-family-aws v2.1.1)
   ```
   to
   ```
   # Version: v2.5.3
   ```

2. Line 16 — `provider-aws-s3` package tag:
   ```
   package: xpkg.upbound.io/upbound/provider-aws-s3:v1.12.0
   ```
   →
   ```
   package: xpkg.upbound.io/upbound/provider-aws-s3:v2.5.3
   ```

3. Line 32 — `provider-aws-iam` package tag:
   ```
   package: xpkg.upbound.io/upbound/provider-aws-iam:v1.12.0
   ```
   →
   ```
   package: xpkg.upbound.io/upbound/provider-aws-iam:v2.5.3
   ```

- [ ] **Step 2.3: Verify the diff is exactly three lines**

```bash
git diff base-apps/crossplane-aws-provider/provider.yaml
```

Expected: three changed lines (one comment, two package tags), no other modifications. If anything else changed, halt and re-read the file.

- [ ] **Step 2.4: Render against the live cluster and capture the diff**

```bash
kubectl diff -f base-apps/crossplane-aws-provider/provider.yaml > /tmp/provider-bump.diff
echo "exit=$?"
cat /tmp/provider-bump.diff
```

Expected:
- `exit=1` (kubectl diff returns 1 when there is a diff — this is success).
- The output shows `spec.package` field changing from `:v1.12.0` to `:v2.5.3` on both Provider resources, and nothing else.

If `exit=0` (no diff): halt — the file edits did not land.
If any non-`spec.package` field appears in the diff: halt and investigate before merging.

- [ ] **Step 2.5: Commit**

```bash
git add base-apps/crossplane-aws-provider/provider.yaml
git commit -m "$(cat <<'EOF'
feat(crossplane): bump Upbound AWS providers to v2.5.3

Aligns with Crossplane v2 upgrade plan; v2 providers retain v1beta1
MR APIs so existing managed resources reconcile unchanged. Core
remains on v1.15.0 in this PR (v2 providers run on v1.x core).

Spec: docs/superpowers/specs/2026-04-26-crossplane-v2-upgrade-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin crossplane-providers-v2.5.3
```

Expected: commit succeeds, push succeeds, branch `crossplane-providers-v2.5.3` is on the remote.

- [ ] **Step 2.6: Read the project PR template**

```bash
cat docs/pr_template.md 2>/dev/null || echo "PR template not found at docs/pr_template.md"
```

If the template exists, follow its structure. If not, use the body below.

- [ ] **Step 2.7: Open the PR via the GitHub MCP server**

Use `github:create_pull_request` with:
- **base:** `main`
- **head:** `crossplane-providers-v2.5.3`
- **title:** `feat(crossplane): bump Upbound AWS providers to v2.5.3`
- **body:**

````markdown
## Summary
- Upbound `provider-aws-s3`: `v1.12.0` → `v2.5.3`
- Upbound `provider-aws-iam`: `v1.12.0` → `v2.5.3`
- Crossplane core stays on `v1.15.0` in this PR — v2 providers run on v1.x core.

This is PR 1 of 2 in the Crossplane v2 upgrade. PR 2 (core chart bump) is gated on this PR's post-merge verification.

Spec: `docs/superpowers/specs/2026-04-26-crossplane-v2-upgrade-design.md`

## Pre-merge dry-run

`kubectl diff -f base-apps/crossplane-aws-provider/provider.yaml`:

```diff
<paste contents of /tmp/provider-bump.diff>
```

Only `spec.package` changes on the two Provider resources.

## Post-merge verification (executor will run)

- [ ] `kubectl get providers.pkg.crossplane.io` — both providers `INSTALLED=True`, `HEALTHY=True` at v2.5.3 within 5 minutes of ArgoCD sync.
- [ ] `kubectl get providerrevisions.pkg.crossplane.io` — latest revision per provider has `STATE=Active`.
- [ ] `kubectl get buckets.s3.aws.upbound.io,users.iam.aws.upbound.io,accesskeys.iam.aws.upbound.io,policies.iam.aws.upbound.io,userpolicyattachments.iam.aws.upbound.io -A` — all rows `SYNCED=True, READY=True`.
- [ ] No `Warning` events on existing MRs.

## Rollback

`git revert <merge-sha> && git push origin main` — ArgoCD reverses on next sync. v2 providers retain v1beta1 APIs so MRs do not need mutation.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
````

Expected: PR URL returned. Record it for the verification task.

- [ ] **Step 2.8: Wait for human review and merge**

This is a hand-off step. Do not merge automatically. After the human merges, proceed to Task 3.

---

## Task 3: PR #1 — Post-merge verification

**Files:** none (cluster verification)

Run **after** PR #1 is merged to `main` and you have observed the `crossplane-aws-provider` Application sync in the ArgoCD UI (or after `git pull` on `main` shows the merge commit).

- [ ] **Step 3.1: Confirm ArgoCD picked up the change**

```bash
kubectl -n argo-cd get application crossplane-aws-provider \
  -o jsonpath='{"sync="}{.status.sync.status}{" health="}{.status.health.status}{" rev="}{.status.sync.revision}{"\n"}'
```

Expected within ~3 minutes of merge: `sync=Synced health=Healthy rev=<merge-sha>`. If `sync=OutOfSync` past 5 minutes, trigger a manual refresh in the ArgoCD UI before continuing.

- [ ] **Step 3.2: Verify both providers are at v2.5.3 and Healthy**

```bash
kubectl get providers.pkg.crossplane.io -o custom-columns=NAME:.metadata.name,PACKAGE:.spec.package,INSTALLED:.status.conditions[?(@.type==\"Installed\")].status,HEALTHY:.status.conditions[?(@.type==\"Healthy\")].status
```

Expected (within 5 minutes of sync):
```
NAME                  PACKAGE                                                  INSTALLED   HEALTHY
provider-aws-iam      xpkg.upbound.io/upbound/provider-aws-iam:v2.5.3          True        True
provider-aws-s3       xpkg.upbound.io/upbound/provider-aws-s3:v2.5.3           True        True
```

If `HEALTHY=False` after 10 minutes: invoke rollback (Step 3.6).

- [ ] **Step 3.3: Verify ProviderRevisions are active**

```bash
kubectl get providerrevisions.pkg.crossplane.io -o custom-columns=NAME:.metadata.name,STATE:.spec.desiredState,HEALTHY:.status.conditions[?(@.type==\"Healthy\")].status
```

Expected: each provider has at least one row with `STATE=Active` and `HEALTHY=True`. Older `Inactive` revisions may also be present — that's normal.

- [ ] **Step 3.4: Verify all consuming managed resources stay SYNCED and READY**

```bash
kubectl get buckets.s3.aws.upbound.io \
        users.iam.aws.upbound.io \
        accesskeys.iam.aws.upbound.io \
        policies.iam.aws.upbound.io \
        userpolicyattachments.iam.aws.upbound.io -A
```

Expected: every row shows `SYNCED=True` and `READY=True`. Specifically, the following resources must be present:
- Buckets: `asela-chores-loki-logs-20251017` (Loki), the argo-workflows S3 bucket
- Users: `loki-s3-user`, the argo-workflows IAM user
- AccessKeys: `argo-workflows-s3-key`
- Plus the Loki and argo-workflows IAM Policies and PolicyAttachments

If any row is `READY=False` more than 5 minutes after sync: invoke rollback (Step 3.6).

- [ ] **Step 3.5: Check for Warning events on managed resources**

```bash
kubectl get events -A --field-selector type=Warning \
  --sort-by='.lastTimestamp' | \
  grep -E 'Bucket|User|AccessKey|Policy|UserPolicyAttachment' | tail -20
```

Expected: no recent (last 10 minutes) Warning events on the Crossplane MRs. Pre-existing warnings unrelated to providers are acceptable.

- [ ] **Step 3.6: (Conditional) Rollback if any verification step failed**

Only execute if Step 3.2, 3.4, or 3.5 failed past their stated timeouts.

```bash
git checkout main
git pull --ff-only origin main
# Find the merge commit
git log --oneline -- base-apps/crossplane-aws-provider/provider.yaml | head -3
# Revert it
git revert <merge-sha> --no-edit
git push origin main
```

Then re-run Steps 3.1–3.4 expecting providers back at `v1.12.0` and MRs `READY=True`. Halt the plan and surface findings — do not proceed to Task 4.

- [ ] **Step 3.7: Post sign-off comment on the merged PR**

Use `github:add_issue_comment` to record verification on the PR (PRs and Issues share comment endpoints in the GitHub API):

```
Post-merge verification ✅
- Providers: provider-aws-s3@v2.5.3 / provider-aws-iam@v2.5.3 — INSTALLED=True, HEALTHY=True
- ProviderRevisions: latest revision Active for both
- All MRs SYNCED=True, READY=True (Loki + argo-workflows)
- No Warning events on MRs

Proceeding to PR #2 (core chart bump).
```

---

## Task 4: PR #2 — Bump Crossplane core Helm subchart

**Files:**
- Modify: `base-apps/crossplane-system/Chart.yaml` (lines 6, 10)
- Modify: `base-apps/crossplane-system/values.yaml` (only if Task 1 Step 1.4 surfaced a values key rename)

**Precondition:** Task 3 fully completed (PR #1 merged + verified + sign-off).

- [ ] **Step 4.1: Create the branch from current main**

```bash
git fetch origin main
git checkout main
git pull --ff-only origin main
git checkout -b crossplane-core-v2
```

Expected: `On branch crossplane-core-v2`. The latest commit should include the merged PR #1.

- [ ] **Step 4.2: Edit `base-apps/crossplane-system/Chart.yaml`**

Substitute the values you locked in Task 1 Step 1.3.

1. Line 6 — `appVersion`:
   ```
   appVersion: "1.15.0"
   ```
   →
   ```
   appVersion: "<resolved-app-version>"   # e.g., "2.2.1"
   ```

2. Line 10 — chart dependency version:
   ```
       version: 1.15.0
   ```
   →
   ```
       version: <resolved-chart-version>   # e.g., 1.21.0
   ```

- [ ] **Step 4.3: Verify the diff**

```bash
git diff base-apps/crossplane-system/Chart.yaml
```

Expected: exactly two changed lines (`appVersion` and dependency `version`), no other modifications.

- [ ] **Step 4.4: Update the local chart dependency cache**

```bash
cd base-apps/crossplane-system
helm dependency update
ls charts/
cd -
```

Expected: `charts/` directory now contains `crossplane-<resolved-chart-version>.tgz`. If the helm command errors with "chart not found", re-check the version string from Task 1 Step 1.2.

> Note: by convention this repo does not commit the `charts/` directory. Confirm with `cat base-apps/crossplane-system/.gitignore 2>/dev/null` — if `charts/` is listed (or `**/charts/` is in the repo root `.gitignore`), do not stage it. If not gitignored, follow the existing pattern (check what was committed last time).

- [ ] **Step 4.5: Render v2 from the PR branch**

```bash
helm template base-apps/crossplane-system \
  -f base-apps/crossplane-system/values.yaml \
  > /tmp/crossplane-v2.yaml
echo "v2 lines: $(wc -l < /tmp/crossplane-v2.yaml)"
```

Expected: a non-empty manifest, typically several thousand lines (Crossplane ships many CRDs). If `helm template` errors out about a deprecated/unknown values key, halt — your `values.yaml` references something the v2 chart no longer accepts. Address per Step 4.6 then re-run.

- [ ] **Step 4.6: Render v1 baseline from main via worktree**

```bash
git worktree add /tmp/crossplane-v1-baseline main
cd /tmp/crossplane-v1-baseline/base-apps/crossplane-system
helm dependency update
helm template . -f values.yaml > /tmp/crossplane-v1.yaml
cd /Users/arisela/git/kubernetes
git worktree remove /tmp/crossplane-v1-baseline --force
echo "v1 lines: $(wc -l < /tmp/crossplane-v1.yaml)"
```

Expected: a non-empty v1 baseline rendering. The worktree is removed cleanly.

- [ ] **Step 4.7: Generate and inspect the diff**

```bash
diff -u /tmp/crossplane-v1.yaml /tmp/crossplane-v2.yaml > /tmp/crossplane-chart.diff || true
wc -l /tmp/crossplane-chart.diff
```

Expected: a non-empty diff. Now skim it for these categories — write a short summary table for the PR body:

| Change category | What to look for |
|---|---|
| **CRDs added/removed** | `kind: CustomResourceDefinition` blocks appearing or disappearing |
| **API version changes** | `apiVersion: apiextensions.crossplane.io/v1` ↔ `v2` lines in the rendered output |
| **RBAC changes** | New / removed `ClusterRole`, `ClusterRoleBinding`, `Role` |
| **Deployment image** | `image:` lines under the `crossplane` Deployment — confirm the new image tag matches `<resolved-app-version>` |
| **Removed values keys** | Any `--set <key>` arg or env var present in v1 but absent in v2 — these are signals that `values.yaml` uses a removed key |
| **`ControllerConfig` references** | Confirm zero — v2 removed this CRD |

- [ ] **Step 4.8: (Conditional) Edit `values.yaml` if a values key was renamed**

Only if Task 1 Step 1.4 or Step 4.7 surfaced a rename. Apply the rename in `base-apps/crossplane-system/values.yaml` and re-run Steps 4.5–4.7. If no rename is needed, skip this step.

- [ ] **Step 4.9: Verify no `ControllerConfig` is referenced**

```bash
grep -RIn 'ControllerConfig' base-apps/ docs/ 2>/dev/null | grep -v specs/2026-04-26-crossplane-v2-upgrade-design.md | grep -v plans/2026-04-26-crossplane-v2-upgrade.md
```

Expected: no output (the only mentions are in the spec/plan docs themselves, which are excluded). If `ControllerConfig` references appear in `base-apps/`, halt — those need to be migrated to `DeploymentRuntimeConfig` before proceeding (this would be a scope expansion not anticipated by the spec).

- [ ] **Step 4.10: Commit**

```bash
git add base-apps/crossplane-system/Chart.yaml
# ONLY if Step 4.8 was needed:
# git add base-apps/crossplane-system/values.yaml
git status   # confirm staged set is exactly what you intend
git commit -m "$(cat <<'EOF'
feat(crossplane): bump core Helm subchart to v2

Bumps Crossplane core from v1.15.0 to v2 (chart <chart-ver>,
appVersion <app-ver>). PR #1 already moved Upbound AWS providers
to v2.5.3, which support both v1.x and v2.x core. Existing v1beta1
managed resources reconcile unchanged.

Spec: docs/superpowers/specs/2026-04-26-crossplane-v2-upgrade-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin crossplane-core-v2
```

(Edit the commit message to substitute the actual chart and app versions.)

- [ ] **Step 4.11: Open the PR via GitHub MCP**

Use `github:create_pull_request` with:
- **base:** `main`
- **head:** `crossplane-core-v2`
- **title:** `feat(crossplane): bump core Helm subchart to v2.x`
- **body:**

````markdown
## Summary
- Crossplane core Helm subchart: `1.15.0` → `<resolved-chart-version>`
- appVersion: `1.15.0` → `<resolved-app-version>`
- Providers stay on `v2.5.3` (already merged in #<prev-PR-number>).

This is PR 2 of 2 in the Crossplane v2 upgrade.

Spec: `docs/superpowers/specs/2026-04-26-crossplane-v2-upgrade-design.md`

## Pre-merge render-and-diff

Generated via `helm template` on this branch vs. `main` baseline. Summary:

| Category | Result |
|---|---|
| CRDs added | <count> (list significant ones) |
| CRDs removed | <count or 0> |
| RBAC changes | <summary> |
| Deployment image | `crossplane/crossplane:<app-version>` |
| `ControllerConfig` references | 0 in `base-apps/` |
| Values keys removed/renamed | <summary or "none"> |

Full diff at `/tmp/crossplane-chart.diff` (local artifact, not committed).

## Post-merge verification (executor will run)

- [ ] `kubectl -n crossplane-system get deploy crossplane -o jsonpath='{.spec.template.spec.containers[0].image}'` matches the new app version.
- [ ] `kubectl -n crossplane-system get pods` — `crossplane` and `crossplane-rbac-manager` pods Running and Ready.
- [ ] All providers and provider revisions still `HEALTHY=True`.
- [ ] All MRs still `SYNCED=True, READY=True`.

## Rollback

`git revert <merge-sha> && git push origin main`. v2 providers run on v1.x core, so reverting only the chart leaves providers operational.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
````

Expected: PR URL returned.

- [ ] **Step 4.12: Wait for human review and merge**

Hand-off step. Do not merge automatically. Proceed to Task 5 after merge.

---

## Task 5: PR #2 — Post-merge verification

**Files:** none (cluster verification)

Run after PR #2 is merged and ArgoCD has synced the `crossplane-system` Application.

- [ ] **Step 5.1: Confirm ArgoCD synced the new chart**

```bash
kubectl -n argo-cd get application crossplane-system \
  -o jsonpath='{"sync="}{.status.sync.status}{" health="}{.status.health.status}{" rev="}{.status.sync.revision}{"\n"}'
```

Expected within ~3 minutes of merge: `sync=Synced health=Healthy rev=<merge-sha>`.

- [ ] **Step 5.2: Confirm the Crossplane Deployment is on the new version**

```bash
kubectl -n crossplane-system get deploy crossplane \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
```

Expected: `crossplane/crossplane:<resolved-app-version>` (e.g., `crossplane/crossplane:v2.2.1`).

- [ ] **Step 5.3: Confirm core pods are Running and Ready**

```bash
kubectl -n crossplane-system get pods
```

Expected: both `crossplane-...` and `crossplane-rbac-manager-...` pods show `Running` with `READY=1/1`. No `CrashLoopBackOff`. No pending pods past 2 minutes after sync.

If `CrashLoopBackOff`: capture logs (`kubectl -n crossplane-system logs deploy/crossplane --tail=200`) and invoke rollback (Step 5.7).

- [ ] **Step 5.4: Re-run provider health check**

```bash
kubectl get providers.pkg.crossplane.io -o custom-columns=NAME:.metadata.name,PACKAGE:.spec.package,INSTALLED:.status.conditions[?(@.type==\"Installed\")].status,HEALTHY:.status.conditions[?(@.type==\"Healthy\")].status
```

Expected: same as Task 3 Step 3.2 — both providers `INSTALLED=True, HEALTHY=True` at v2.5.3.

- [ ] **Step 5.5: Re-run MR health check**

```bash
kubectl get buckets.s3.aws.upbound.io \
        users.iam.aws.upbound.io \
        accesskeys.iam.aws.upbound.io \
        policies.iam.aws.upbound.io \
        userpolicyattachments.iam.aws.upbound.io -A
```

Expected: every row `SYNCED=True, READY=True`. No flips since pre-merge state.

- [ ] **Step 5.6: Check for Warning events**

```bash
kubectl get events -A --field-selector type=Warning \
  --sort-by='.lastTimestamp' | tail -30
```

Expected: nothing new on Crossplane MRs or `crossplane-system` namespace in the last 10 minutes.

- [ ] **Step 5.7: (Conditional) Rollback if any verification step failed**

Only execute if Step 5.2, 5.3, 5.4, or 5.5 failed past their stated timeouts.

```bash
git checkout main
git pull --ff-only origin main
git log --oneline -- base-apps/crossplane-system/Chart.yaml | head -3
git revert <merge-sha> --no-edit
git push origin main
```

Re-run Steps 5.1–5.5 expecting the chart back at v1.15.0 and everything healthy. Halt the plan and surface findings.

- [ ] **Step 5.8: Post sign-off comment on the merged PR**

Via `github:add_issue_comment`:

```
Post-merge verification ✅
- crossplane Deployment image: crossplane/crossplane:<app-version>
- crossplane and crossplane-rbac-manager pods Running, Ready
- Providers HEALTHY=True at v2.5.3
- All MRs SYNCED=True, READY=True
- No Warning events

Crossplane v2 upgrade complete.
```

---

## Done

After Task 5 sign-off, the upgrade is complete. State:
- Crossplane core: `<resolved-app-version>` (v2.x)
- Upbound providers: `v2.5.3`
- All MRs unchanged, still on `v1beta1` cluster-scoped APIs
- ProviderConfig, ESO, Vault, Terraform: untouched

**Optional follow-up (not in scope here):** schedule a brainstorm for migrating MRs to `v1beta2` namespaced APIs. The spec calls this out as deliberately deferred.

---

## Self-review (writer's notes — do not execute)

- **Spec coverage:**
  - Spec §6 (PR #1 file changes) → Task 2 ✓
  - Spec §6 (PR #2 file changes) → Task 4 ✓
  - Spec §7 (pre-merge dry-run, both PRs) → Task 2 Step 2.4, Task 4 Steps 4.5–4.7 ✓
  - Spec §8 (post-merge verification, both PRs) → Task 3, Task 5 ✓
  - Spec §9 (rollback procedure) → Task 3 Step 3.6, Task 5 Step 5.7 ✓
  - Spec §10 (out of scope) → Done section reaffirms ✓
- **No placeholders:** version strings deliberately marked `<resolved-…>` with concrete examples and a Task 1 step that resolves them. PR template references are conditional on the file existing. Rollback merge-sha is a `<merge-sha>` parameter — unavoidable, but the surrounding `git log` step shows how to find it.
- **Type consistency:** the same set of MR kinds is checked in Task 3 Step 3.4 and Task 5 Step 5.5 — verified identical.
