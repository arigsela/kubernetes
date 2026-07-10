# Backstage Catalog Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Backstage discover and ingest the agent-docs framework's per-app `base-apps/*/catalog-info.yaml` files by adding a dedicated GitHub entity provider to the `arigsela/backstage` app-config, rebuilding the image, and bumping the pinned tag in this repo.

**Architecture:** Config-only change. `arigsela/backstage` already runs an org-wide GitHub entity provider (root `catalog-info.yaml` only) and the provider backend module is already installed. Add a second, dedicated provider scoped to the kubernetes repo and the `base-apps/*` path, rebuild the image (`v1.2.0`), then bump `base-apps/backstage/deployments.yaml` here — the GitOps tag bump is the real deploy (Argo CD owns the deployment with `selfHeal`).

**Tech Stack:** Backstage `@backstage/plugin-catalog-backend-module-github` (GithubEntityProvider) · `arigsela/backstage` (Node/Yarn, `scripts/build-and-push.sh` → buildx → ECR) · Argo CD GitOps · yamllint/kubeconform (this repo's CI).

## Global Constraints

- **Two repos.** Tasks 1–2 operate in `arigsela/backstage` (source). Task 3 operates in `arigsela/kubernetes` (this repo). Task 4 verifies against the live cluster. Never mix a change into the wrong repo.
- **Config-only.** No backend/TypeScript code changes. The `github` catalog module is already registered (`packages/backend/src/index.ts:222`); `catalog.rules` already allow `Component` and `Resource`. Do not add dependencies or backend wiring.
- **Existing provider is untouched.** The `arigsela` provider (`catalogPath: '/catalog-info.yaml'`, `repository: '.*'`) stays exactly as-is. The new provider is additive.
- **Exact provider values (verbatim):** id `arigsela-kubernetes`; `organization: 'arigsela'`; `catalogPath: '/base-apps/*/catalog-info.yaml'`; `filters.repository: '^kubernetes$'`; `schedule.frequency: { minutes: 30 }`; `schedule.timeout: { minutes: 3 }`.
- **Image version:** `v1.2.0` (current is `v1.1.0`). ECR repo `852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal`.
- **The image build (Task 2) is user-run.** It needs local Docker+buildx, AWS ECR push creds, and Node/Yarn — it cannot run in the agent environment. It is a human gate between Task 1 and Task 3.
- **Ordering is load-bearing.** Task 1 merges → Task 2 builds from that merged config → Task 3's PR merges only after `v1.2.0` exists in ECR (else ImagePullBackOff) → Task 4 verifies after Argo syncs.

---

## File Structure

- `arigsela/backstage` → `app-config.yaml` — add the `arigsela-kubernetes` provider stanza under the existing `catalog.providers.github` block (lines 289–300). Single-file edit; `app-config.production.yaml` does not redefine `catalog.providers`, so no overlay change.
- `arigsela/kubernetes` → `base-apps/backstage/deployments.yaml:27` — bump the pinned image tag `v1.1.0` → `v1.2.0`.

No new files. No test files (config change; validation is `config:check`, build success, and a post-deploy catalog-API assertion).

---

## Task 1: Add the `arigsela-kubernetes` GitHub entity provider (repo: `arigsela/backstage`)

**Files:**
- Modify: `app-config.yaml` (in `arigsela/backstage`) — insert after the existing `arigsela` provider's `timeout` line (currently line 300).

**Interfaces:**
- Consumes: the existing `catalog.providers.github` block and the already-installed github catalog module.
- Produces: a running GithubEntityProvider `arigsela-kubernetes` (active once the image built in Task 2 is deployed in Task 3) that discovers `base-apps/*/catalog-info.yaml` in the `kubernetes` repo.

- [ ] **Step 1: Get a working checkout of `arigsela/backstage`**

If you do not already have one:
```bash
git clone https://github.com/arigsela/backstage.git ~/git/backstage
cd ~/git/backstage
git checkout main && git pull
git checkout -b feat/base-apps-catalog-discovery
```
If you already have a checkout, `cd` into it, ensure `main` is current, and branch from it.

- [ ] **Step 2: Confirm the current provider block (the anchor)**

Run:
```bash
awk 'NR>=289 && NR<=300 {printf "%d: %s\n", NR, $0}' app-config.yaml
```
Expected: the `providers:` → `github:` → `arigsela:` block ending at the `timeout: { minutes: 3 }` line (line 300). If line numbers differ, locate the block by content — the new stanza goes as a **sibling of `arigsela:`** under `github:` (6-space indent for the key, 8-space for its children).

- [ ] **Step 3: Add the new provider stanza**

Insert this block immediately after the existing `arigsela:` provider (after its `timeout:` line), keeping it under the same `github:` parent:
```yaml
      # Agent-docs framework: the framework places a catalog-info.yaml in each
      # base-apps/<app>/ directory of the kubernetes repo. The 'arigsela' provider
      # above only scans repo-root /catalog-info.yaml, so those per-app files were
      # invisible. This dedicated provider discovers them. Scoped to the one repo
      # that has base-apps/ so it never scans that path in other repos.
      arigsela-kubernetes:
        organization: 'arigsela'
        catalogPath: '/base-apps/*/catalog-info.yaml'
        filters:
          repository: '^kubernetes$'
        schedule:
          frequency: { minutes: 30 }
          timeout: { minutes: 3 }
```

- [ ] **Step 4: Verify the YAML structure and exact values**

Run (uses the `yaml` package that ships as a Backstage dependency; run `yarn install` first if `node_modules` is absent):
```bash
node -e '
const fs=require("fs"), YAML=require("yaml");
const d=YAML.parse(fs.readFileSync("app-config.yaml","utf8"));
const p=d.catalog.providers.github;
if(!p.arigsela) throw new Error("existing arigsela provider missing — do not remove it");
const k=p["arigsela-kubernetes"];
if(!k) throw new Error("arigsela-kubernetes provider missing");
const a=(c,m)=>{if(!c)throw new Error("FAIL: "+m)};
a(k.organization==="arigsela","organization");
a(k.catalogPath==="/base-apps/*/catalog-info.yaml","catalogPath");
a(k.filters&&k.filters.repository==="^kubernetes$","filters.repository");
a(k.schedule&&k.schedule.frequency&&k.schedule.frequency.minutes===30,"schedule.frequency");
a(k.schedule&&k.schedule.timeout&&k.schedule.timeout.minutes===3,"schedule.timeout");
a(p.arigsela.catalogPath==="/catalog-info.yaml","existing arigsela provider unchanged");
console.log("OK: arigsela-kubernetes provider valid; existing provider intact");
'
```
Expected: `OK: arigsela-kubernetes provider valid; existing provider intact`

- [ ] **Step 5: Validate against the Backstage config schema**

Run (requires `yarn install` to have populated `node_modules`):
```bash
yarn install --immutable
yarn backstage-cli config:check --lax
```
Expected: exits 0 with no schema errors. (`--lax` skips env-var substitution for secrets so it runs without production env set. If `config:check` reports an unrelated missing-env warning, confirm it is not about `catalog.providers.github` and proceed.)

- [ ] **Step 6: Type-check**

Run:
```bash
yarn tsc
```
Expected: completes with no errors (config edits should not affect types; this catches an accidental code touch).

- [ ] **Step 7: Commit and open a PR in `arigsela/backstage`**

```bash
git add app-config.yaml
git commit -m "feat(catalog): discover base-apps/*/catalog-info.yaml from kubernetes repo

Add a dedicated GithubEntityProvider (arigsela-kubernetes) scoped to the
kubernetes repo and the base-apps/* path so the agent-docs framework's per-app
catalog-info.yaml files are ingested. The existing org-wide root provider is
unchanged. Config-only; the github catalog module is already installed."
git push -u origin feat/base-apps-catalog-discovery
gh pr create --fill
```
Expected: PR created. Merge it to `main` after review (this is the source the Task 2 build compiles).

---

## Task 2: Build and push image `v1.2.0` (repo: `arigsela/backstage`) — USER-RUN GATE

**This task cannot run in the agent environment.** It requires local Docker + buildx, AWS credentials with ECR push access, and Node/Yarn. The user runs it. An agent executing this plan must STOP here and hand off to the user, then resume at Task 3 once the image exists.

**Files:** none (produces an image artifact in ECR).

**Interfaces:**
- Consumes: `arigsela/backstage` `main` with Task 1 merged.
- Produces: `852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.2.0` (and `:latest`) in ECR.

- [ ] **Step 1: Build from the merged config**

From the `arigsela/backstage` checkout on up-to-date `main`:
```bash
git checkout main && git pull
./scripts/build-and-push.sh --version v1.2.0
```
Expected: the script runs `yarn install --immutable`, `yarn tsc`, `yarn build:all`, logs in to ECR, buildx-builds `linux/amd64`, and pushes both `:v1.2.0` and `:latest`. Final lines:
```
Successfully pushed and deployed:
  852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.2.0
  852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:latest
```
Note: the script's final `kubectl rollout restart` restarts the **currently pinned** `v1.1.0` (a no-op for this upgrade) because Argo CD owns the image tag. The real deploy is Task 3. This is expected — do not treat the restart as the upgrade.

- [ ] **Step 2: Confirm the tag exists in ECR**

```bash
aws ecr describe-images --repository-name backstage-portal --region us-east-2 \
  --image-ids imageTag=v1.2.0 --query 'imageDetails[0].imageTags' --output json
```
Expected: a JSON array containing `"v1.2.0"` (and likely `"latest"`). If this fails, the push did not complete — do not proceed to Task 3.

---

## Task 3: Bump the pinned image tag to `v1.2.0` (repo: `arigsela/kubernetes` — this repo)

**Files:**
- Modify: `base-apps/backstage/deployments.yaml:27` (the `image:` line).

**Interfaces:**
- Consumes: the `v1.2.0` image in ECR (Task 2).
- Produces: the deployed config change — Argo CD rolls the `backstage` app onto `v1.2.0`, activating the `arigsela-kubernetes` provider.

- [ ] **Step 1: Branch from current `main`**

```bash
cd ~/git/kubernetes
git checkout main && git pull
git checkout -b chore/backstage-v1.2.0
```

- [ ] **Step 2: Confirm the current image line**

Run:
```bash
grep -nE 'image:.*backstage-portal' base-apps/backstage/deployments.yaml
```
Expected: `27:        image: 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.1.0`

- [ ] **Step 3: Bump the tag**

Change the tag on that line from `:v1.1.0` to `:v1.2.0`. The line becomes:
```yaml
        image: 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.2.0
```

- [ ] **Step 4: Validate the manifest (mirrors this repo's CI)**

Run:
```bash
grep -q 'backstage-portal:v1.2.0' base-apps/backstage/deployments.yaml && echo "tag bumped"
yamllint -c .yamllint.yaml base-apps/backstage/deployments.yaml
kubeconform -strict -ignore-missing-schemas -summary base-apps/backstage/deployments.yaml
kubectl apply --dry-run=server -f base-apps/backstage/deployments.yaml
```
Expected: `tag bumped`; yamllint clean; kubeconform reports no failures; server dry-run prints `deployment.apps/backstage configured (server dry run)`. (If `kubeconform` isn't installed locally, CI runs it on the PR; the server dry-run is the authoritative schema check.)

- [ ] **Step 5: Commit and open the PR**

```bash
git add base-apps/backstage/deployments.yaml
git commit -m "chore(backstage): bump image to v1.2.0 for base-apps catalog discovery

Deploys the arigsela-kubernetes GitHub entity provider (built in
arigsela/backstage) so Backstage ingests base-apps/*/catalog-info.yaml."
git push -u origin chore/backstage-v1.2.0
gh pr create --fill
```
Expected: PR created, CI (yaml-lint, kubernetes-validate) green. Merge after review; Argo CD syncs the `backstage` app to the new tag.

---

## Task 4: Verify ingestion post-deploy (live cluster) — VERIFICATION GATE

**Files:** none (read-only verification).

**Interfaces:**
- Consumes: the deployed `v1.2.0` pod running the new provider.
- Produces: confirmation the four pilot entities are in the catalog (success criteria from the spec).

- [ ] **Step 1: Confirm Argo synced and the pod is on `v1.2.0`**

```bash
kubectl -n argo-cd get application backstage -o jsonpath='{.status.sync.status}{"\n"}'
kubectl -n backstage get deploy backstage -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl -n backstage rollout status deploy/backstage --timeout=120s
```
Expected: sync `Synced`, image ends `:v1.2.0`, `successfully rolled out`.

- [ ] **Step 2: Wait for a provider scan, then query the catalog API for the four pilot entities**

The provider scans on a 30-minute schedule and also runs shortly after startup. Give it a few minutes after rollout, then run:
```bash
POD=$(kubectl -n backstage get pods -l app.kubernetes.io/name=backstage -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
[ -z "$POD" ] && POD=$(kubectl -n backstage get pods -o jsonpath='{.items[0].metadata.name}')
kubectl -n backstage exec "$POD" -- node -e '
const http=require("http");
http.get("http://localhost:7007/api/catalog/entities?limit=2000",res=>{
  let b="";res.on("data",d=>b+=d);res.on("end",()=>{
    const d=JSON.parse(b); const byRef={};
    for(const e of d) byRef[`${e.kind}:${e.metadata.namespace}/${e.metadata.name}`]=e;
    const want=[
      "Resource:cert-manager/cert-manager",
      "Resource:vault/vault",
      "Resource:argo-cd/argo-cd",
      "Component:chores-tracker/chores-tracker-backend"];
    let ok=true;
    for(const r of want){ const e=byRef[r];
      if(e){ const loc=(e.metadata.annotations||{})["backstage.io/managed-by-location"]||"";
        console.log("FOUND",r,"loc:",loc.slice(0,70)); }
      else { console.log("MISSING",r); ok=false; } }
    console.log(ok?"ALL PILOT ENTITIES PRESENT":"SOME MISSING — see above");
  });
}).on("error",e=>console.log("ERR",e.message));
'
```
Expected: four `FOUND` lines (each `loc:` a `github.com/arigsela/kubernetes/blob/...catalog-info.yaml` URL) and `ALL PILOT ENTITIES PRESENT`. If some are `MISSING`, continue to Step 3 before assuming failure — the scan may not have run yet.

- [ ] **Step 3: Check provider health / ingest errors in the logs**

```bash
kubectl -n backstage logs "$POD" --tail=500 | grep -iE 'arigsela-kubernetes|GithubEntityProvider|conflicting entityRef|catalog.*error' | tail -30
```
Expected: lines showing the `arigsela-kubernetes` provider committing entities, and **no** `conflicting entityRef` errors. If you see a conflict, note the clashing entity name — a `base-apps/<app>/catalog-info.yaml` entity collides with an existing catalog entity and its name/namespace must be disambiguated (out of scope for this plan; record it for follow-up).

- [ ] **Step 4: Spot-check in the UI (optional)**

Open `https://backstage.arigsela.com` → Catalog → filter Kind = `Resource` and Kind = `Component`; confirm `cert-manager`, `vault`, `argo-cd`, and `chores-tracker-backend` appear and their About cards render. Their `system:` relations (`platform-networking`, etc.) will show as external until those System entities exist — expected per the spec.

---

## Done criteria

- Task 1 merged in `arigsela/backstage`; Task 3 merged here; image `v1.2.0` deployed.
- Task 4 Step 2 prints `ALL PILOT ENTITIES PRESENT`; Step 3 shows no `conflicting entityRef`.
- Existing catalog entities (agents via kubernetes-ingestor, self-registration, examples) unchanged.
