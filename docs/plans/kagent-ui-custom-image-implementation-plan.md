# Custom kagent UI Image Implementation Plan

**Status:** Approved — ready to execute
**Date:** 2026-05-09
**Owner:** Ari Sela
**Spec:** [docs/superpowers/specs/2026-05-09-kagent-ui-custom-image-design.md](../superpowers/specs/2026-05-09-kagent-ui-custom-image-design.md)
**Upstream issue:** [kagent-dev/kagent#1505](https://github.com/kagent-dev/kagent/issues/1505)

## Overview

Build a Node-20-on-Debian replacement for `cr.kagent.dev/kagent-dev/kagent/ui:0.8.6` to fix the SIGILL crash on the HP server's older CPU. One-shot patch, amd64-only, hosted in ECR.

## Success Criteria

- [ ] `kagent-ui:0.8.6-node20` image lives in `852893458518.dkr.ecr.us-east-2.amazonaws.com/kagent-ui`
- [ ] kagent-ui pod runs **without SIGILL crashes** on the HP server (no "Illegal instruction" in logs, RESTARTS stable)
- [ ] kagent UI is reachable via port-forward and renders pages without errors
- [ ] Repo contains build artifacts under `base-apps/kagent/build/` so the image is reproducible
- [ ] `base-apps/kagent.yaml` overrides the UI image; ArgoCD shows the kagent app as Synced/Healthy

## Research Findings

### Relevant Files

| File | Relevance |
|---|---|
| `base-apps/kagent.yaml` | kagent ArgoCD Application; helm values get the `ui:` override |
| `base-apps/kagent/` | currently holds Agent CRDs and Vault wiring; we add `build/` subdir |
| `base-apps/kyverno-policies/inject-ecr-pull-secret.yaml` | auto-injects `imagePullSecrets: [{name: ecr-registry}]` for any ECR image |
| `base-apps/ecr-auth/cronjobs.yaml` | refreshes ECR tokens cluster-wide (the `ecr-registry` Secret) |
| `base-apps/chores-tracker-backend/deployments.yaml` (and ~15 others) | reference ECR pattern: bare image reference, no explicit pull secret in manifest |

### Existing Patterns

- ECR registry/region: `852893458518.dkr.ecr.us-east-2.amazonaws.com`, `us-east-2`
- ECR image references are bare in manifests (e.g., `852893458518.dkr.ecr.us-east-2.amazonaws.com/chores-tracker:7.1.0`); Kyverno handles pull secrets transparently
- ArgoCD GitOps: changes deploy on commit-to-main with `prune: true, selfHeal: true`

### Dependencies

- `aws` CLI configured with ECR push perms (account `852893458518`, region `us-east-2`)
- `docker buildx` (for amd64 builds, especially on Mac/M-series)
- The `ecr-registry` Secret must already exist (or get created) in the `kagent` namespace — verified in Phase 1

## Architecture Decisions (from spec)

| # | Decision | Rationale |
|---|---|---|
| 1 | One-shot patch (no CI) | Minimal overhead; delete when upstream fixes #1505 |
| 2 | `node:20-bookworm-slim` | Distro Node 20 binary, no `x86-64-v2` baseline; satisfies Next.js 16's Node ≥20.9 requirement |
| 3 | AWS ECR | Matches existing repo conventions |
| 4 | `linux/amd64` only | HP server is the only target |
| 5 | Vendored Dockerfile + manual local build | Two files in repo; lowest ceremony |

## Implementation

### Phase 1: Pre-flight (cluster-side checks, no commits)

#### Task 1.1: Verify ECR repo exists

**Files:** none (cluster check)

**Steps:**
1. `aws ecr describe-repositories --repository-names kagent-ui --region us-east-2`
2. If it errors `RepositoryNotFoundException`, create it:
   ```
   aws ecr create-repository --repository-name kagent-ui --region us-east-2 \
     --image-scanning-configuration scanOnPush=true
   ```
3. Confirm registry URI is `852893458518.dkr.ecr.us-east-2.amazonaws.com/kagent-ui`

**Testing:**
- [ ] `describe-repositories` returns 200 with the expected `repositoryUri`

#### Task 1.2: Verify `ecr-registry` Secret exists in `kagent` namespace

**Files:** none (cluster check)

**Steps:**
1. `kubectl -n kagent get secret ecr-registry`
2. If missing, look at `base-apps/ecr-auth/cronjobs.yaml` to understand how it's seeded; the cronjob may target only certain namespaces
3. If the cronjob doesn't include `kagent`, add it (separate small change, follow the pattern in `base-apps/ecr-auth/cronjobs.yaml`)

**Testing:**
- [ ] `kubectl -n kagent get secret ecr-registry` returns a Secret of type `kubernetes.io/dockerconfigjson`
- [ ] `kubectl -n kagent get secret ecr-registry -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d | jq` shows valid auth for `852893458518.dkr.ecr.us-east-2.amazonaws.com`

---

### Phase 2: Author build artifacts

#### Task 2.1: Create `base-apps/kagent/build/` with the patched Dockerfile

**Files:** `base-apps/kagent/build/Dockerfile`

**Steps:**
1. `mkdir -p base-apps/kagent/build`
2. Author `Dockerfile` per the spec's "Full patched Dockerfile" section verbatim

**Testing:**
- [ ] `docker buildx build --platform linux/amd64 -t kagent-ui:test .` succeeds when run in a manually-cloned `kagent-dev/kagent@v0.8.6/ui` directory with this Dockerfile dropped in (manual smoke before `build.sh` exists)

#### Task 2.2: Author `build.sh`

**Files:** `base-apps/kagent/build/build.sh`

**Steps:**
1. Author per spec's "Component 2" section
2. Hardcode the resolved values (no longer placeholders):
   - `ECR_REGISTRY="852893458518.dkr.ecr.us-east-2.amazonaws.com"`
   - `ECR_REGION="us-east-2"`
3. Make executable: `chmod +x base-apps/kagent/build/build.sh`

**Testing:**
- [ ] `bash -n build.sh` (syntax check) passes
- [ ] `shellcheck build.sh` (if available) returns no errors

#### Task 2.3: Author `README.md`

**Files:** `base-apps/kagent/build/README.md`

**Steps:**
1. Document prereqs: `aws configure` w/ ECR perms, `docker buildx create --use`
2. Document usage: `./build.sh`
3. Document how to bump `KAGENT_VERSION` if upstream releases a new tag while we still need the patch
4. Cross-link to the design spec
5. Cross-link to upstream issue [#1505](https://github.com/kagent-dev/kagent/issues/1505)

**Testing:**
- [ ] Markdown renders cleanly (visual check)

---

### Phase 3: Build & push the image

#### Task 3.1: Run `build.sh`

**Files:** none committed (image goes to ECR)

**Steps:**
1. From the repo root: `./base-apps/kagent/build/build.sh`
2. Watch for: clone success, `npm ci` success, `next build` success, ECR login success, push success

**Testing:**
- [ ] Script exits 0
- [ ] `aws ecr describe-images --repository-name kagent-ui --region us-east-2 --image-ids imageTag=0.8.6-node20` shows the image
- [ ] Image manifest reports `architecture: amd64`, `os: linux`

#### Task 3.2: (contingency) Local smoke test before cluster deploy

**Files:** none

**Steps:**
1. `docker pull 852893458518.dkr.ecr.us-east-2.amazonaws.com/kagent-ui:0.8.6-node20`
2. `docker run --rm -p 8080:8080 852893458518.dkr.ecr.us-east-2.amazonaws.com/kagent-ui:0.8.6-node20`
3. In another shell: `curl -sS http://localhost:8080/ | head -20` — expect Next.js HTML

**Testing:**
- [ ] Container stays running for 30+ seconds (no startup crash)
- [ ] supervisord starts both `nginx` and `nextjs` (visible in container logs)
- [ ] Curl returns HTML (not connection-refused)
- [ ] If this fails: jump to Risk A/B mitigation in Phase 5 *before* cluster deploy

---

### Phase 4: Wire into the cluster (GitOps)

#### Task 4.1: Override `ui.image` in `base-apps/kagent.yaml`

**Files:** `base-apps/kagent.yaml`

**Steps:**
1. Add the `ui:` block from the spec's "Component 3" between `providers:` and `agents:` in `helm.valuesObject`
2. Use the resolved values:
   ```yaml
   ui:
     image:
       registry: "852893458518.dkr.ecr.us-east-2.amazonaws.com"
       repository: "kagent-ui"
       tag: "0.8.6-node20"
       pullPolicy: IfNotPresent
   ```

**Testing:**
- [ ] `yamllint base-apps/kagent.yaml` passes
- [ ] `helm template` (locally with the chart) renders the kagent-ui Deployment with the expected image

#### Task 4.2: Commit & push

**Files:** all artifacts from Phase 2 + the `kagent.yaml` edit

**Steps:**
1. Pre-commit credential scan: `git diff --cached | grep -iE "(api[_-]?key|token|password|aws_access)"` returns nothing
2. Commit covering build artifacts + helm override (or split into two commits — one per concern — depending on user preference)
3. `git push` to feature branch, open PR per repo convention

**Testing:**
- [ ] Commit has no credentials
- [ ] Commit message follows repo conventional-commits style (e.g., `feat(kagent): custom UI image for older x86-64 CPUs`)

#### Task 4.3: ArgoCD sync

**Files:** none

**Steps:**
1. After PR merge to `main`: watch ArgoCD UI / `argocd app get kagent`
2. Confirm the kagent Application detects diff → syncs → kagent-ui Deployment updated
3. New pod rolls out; old pod terminates

**Testing:**
- [ ] `argocd app get kagent` reports `Sync Status: Synced`, `Health: Healthy`
- [ ] `kubectl -n kagent get pods -l app.kubernetes.io/name=kagent-ui` shows new pod (different age than the rest)

---

### Phase 5: Verify & contingency

#### Task 5.1: Run all 5 acceptance tests from spec

**Files:** none

**Steps:** Execute each test from the spec's "Verification" section in order, capture outputs.

**Testing:**
- [ ] Test 1: image pulled (no `ImagePullBackOff`)
- [ ] Test 2: **no SIGILL** — pod stays Running, RESTARTS stable, "Ready in <Xms>" log line present
- [ ] Test 3: no nginx EROFS / permission errors
- [ ] Test 4: `supervisorctl status` shows nginx & nextjs RUNNING
- [ ] Test 5: UI loads in browser via port-forward, Agents and Model Configs pages render

#### Task 5.2: (contingency) Risk A/B mitigation

**Files:** `base-apps/kagent/build/nginx.conf.patch` and/or `supervisord.conf.patch` (only if needed)

**Steps:** *(Only if Test 3 or Test 4 fails.)*
1. Diagnose from logs which paths/binaries are wrong
2. Author the minimal `.patch` file
3. Uncomment the corresponding block in `build.sh`
4. Re-run `build.sh`
5. ArgoCD will not detect the change automatically (same tag) — either bump tag to `0.8.6-node20-r1` and update `base-apps/kagent.yaml`, OR `kubectl rollout restart deploy/kagent-ui -n kagent` to force a re-pull

**Testing:**
- [ ] Re-run Phase 5.1 tests after the new image deploys

#### Task 5.3: Capture run notes

**Files:** Optionally `docs/kagent-ui-custom-image-run-notes.md`

**Steps:**
1. Document image SHA, build timestamp, any Risk A/B patches actually applied
2. Document upstream-fix-watch: when to remove the patch (when #1505 is fixed in v0.8.7+ or v0.9.x)

**Testing:**
- [ ] (Optional) Run notes committed

## End-to-End Testing

The whole feature is verified by: **the kagent UI pod is `Running` with no restarts, and a user can hit the UI in their browser and click around without errors.** That's the headline. Everything else (image existence, supervisord, nginx logs) is supporting evidence.

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Debian nginx default log paths conflict with `readOnlyRootFilesystem: true` | Medium — depends on what upstream's `conf/nginx.conf` overrides | Phase 5.2: ship `nginx.conf.patch` redirecting `error_log`/`access_log` to `/tmp/nginx/` |
| `supervisord.conf` references absolute paths (e.g., `/usr/sbin/nginx`) that differ between Debian and wolfi | Low–Medium — most modern supervisord configs use bare binary names | Phase 5.2: ship `supervisord.conf.patch` updating paths |
| Next.js 16 has a runtime issue with Node 20 we don't catch in Phase 3.2 local smoke | Low — Next.js 16 docs explicitly support Node ≥20.9 | Phase 5.1 Test 5 (browser smoke) catches it; rollback by removing `ui:` block from `kagent.yaml` |
| ECR push permissions misconfigured on local AWS profile | Low — but easy to forget | Task 1.1 surfaces this immediately; fix `aws configure` or `AWS_PROFILE` and rerun |
| `ecr-registry` Secret missing in `kagent` namespace → ImagePullBackOff | Medium — depends on whether the existing ecr-auth cronjob already targets `kagent` | Task 1.2 surfaces this before deploy; fix by extending the cronjob's namespace list |
| Bumping `KAGENT_VERSION` later breaks because upstream `ui/` directory layout changed | Low — only relevant if we end up needing this for >1 kagent version | Documented in `README.md` Task 2.3; if it bites, re-merge upstream's Dockerfile changes manually |
