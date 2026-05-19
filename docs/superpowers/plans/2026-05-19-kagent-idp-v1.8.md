# Kagent IDP v1.8 — Backstage Kubernetes Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the live Kubernetes resources (Pod, Deployment, ReplicaSet, Service, ServiceAccount, plus the Agent CRD itself) for kagent agents in Backstage by adding a dedicated 3-tab entity-page layout and configuring the K8s plugin to fetch kagent CRDs as custom resources.

**Architecture:** Six file changes in `arigsela/backstage` (1 PR) and one backfill in `arigsela/kubernetes` (1 PR). New layout reuses existing `EntityKubernetesContent` and `overviewContent` constants — no new React components. Label migration on the Agent CRD (and decoupled decommission safety check) makes the existing TeraSky-generated label selector match the Agent CRD itself.

**Tech Stack:** TypeScript / React (Backstage frontend), Nunjucks (scaffolder template), YAML, Jest.

**Companion spec:** `docs/superpowers/specs/2026-05-19-kagent-idp-v1.8-design.md`

**Working directories:**
- Tasks 1-4 work in `/Users/arisela/git/kubernetes/docs/reference/backstage/` (the `arigsela/backstage` repo clone).
- Task 5 works in `/Users/arisela/git/kubernetes/` (this repo).
- Task 6 is operational smoke tests.

---

## File Structure

### Files to MODIFY in `arigsela/backstage`

| File | Change | Task |
|---|---|---|
| `packages/backend/src/modules/scaffolder/kagentDecommissionAction.ts` | Migrate IDP-managed check from `app.kubernetes.io/managed-by: backstage-scaffolder` to dedicated `arigsela.com/idp-managed: "true"` label. | 1 |
| `packages/backend/src/modules/scaffolder/kagentDecommissionAction.test.ts` | Update 6 fixtures to use the new label. | 1 |
| `examples/templates/kagent-agent/content/base-apps/kagent/agents/${{ values.name }}.yaml` | Add 3 new labels (`app: kagent`, `app.kubernetes.io/name`, `arigsela.com/idp-managed`) and change `app.kubernetes.io/managed-by` value from `backstage-scaffolder` to `kagent`. | 2 |
| `app-config.yaml` | Add `kubernetes.customResources` block listing `kagent.dev/v1alpha2/agents`. | 3 |
| `app-config.production.yaml` | Same block (production replaces arrays — v1.6 lesson). | 3 |
| `packages/app/src/components/catalog/EntityPage.tsx` | Add `kagentAgentPage` 3-route layout + new `EntitySwitch.Case` in `componentPage`. | 4 |

### Files to MODIFY in `arigsela/kubernetes`

| File | Change | Task |
|---|---|---|
| `base-apps/kagent/agents/homelab-knowledge.yaml` | Same label additions as the scaffolder template — one-time backfill so the existing agent picks up the K8s tab visibility. | 5 |

### Files this plan does NOT touch

- `base-apps/backstage/rbac.yaml` — the `backstage-kagent-read` ClusterRole shipped in v1.7 PR #285 already grants what we need.
- Any kagent Helm values or chart configuration.
- Any other scaffolder action or template.

### Coordination order

1. **Backstage PR ships first**, image rebuilt + deployed. The decommission action now expects `arigsela.com/idp-managed: "true"`. Until the kubernetes backfill PR merges, the existing `homelab-knowledge.yaml` (which has the OLD `managed-by: backstage-scaffolder` value but no `arigsela.com/idp-managed` label) would be considered "not IDP-managed" by the decommission action — i.e. decommission would refuse to delete it. That's a SAFE failure mode (no destructive action).
2. **Kubernetes backfill PR ships second**, ArgoCD syncs the relabeled Agent CRD. Decommission works again. K8s plugin's label selector now matches the Agent CRD, so the Custom Resources panel populates.
3. **Visual verification** in the browser.

---

## Phase 1 — Backstage changes (single PR, 4 commits)

### Task 1: Migrate decommission action's IDP-managed check

**Why:** Section 3 of the spec changes `app.kubernetes.io/managed-by` on the Agent CRD from `backstage-scaffolder` to `kagent` (so the K8s plugin's label selector matches). The decommission action currently uses that label value as the "is this IDP-managed?" safety check. If we change the value without migrating the check, decommission would either accidentally allow deletion of non-IDP agents (if it kept checking for `kagent`, which the kagent controller sets on its own non-IDP Deployments) or refuse to delete IDP agents (if it kept checking for `backstage-scaffolder`). Migrating to a dedicated label (`arigsela.com/idp-managed: "true"`) decouples runtime-manager from provenance.

**Files:**
- Modify: `packages/backend/src/modules/scaffolder/kagentDecommissionAction.ts`
- Modify: `packages/backend/src/modules/scaffolder/kagentDecommissionAction.test.ts`

- [ ] **Step 1: Branch off latest main in the backstage repo**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
git fetch origin main
git checkout -B feat/kagent-v1.8-k8s-tab origin/main
git log --oneline -3
```

Expected: latest main HEAD shows the v1.7 merge.

- [ ] **Step 2: Update the test fixtures + assertions FIRST (TDD)**

Open `packages/backend/src/modules/scaffolder/kagentDecommissionAction.test.ts`. Find the two YAML fixture constants:

```typescript
const IDP_MANAGED_YAML = `apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: release-coordinator
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
    app.kubernetes.io/managed-by: backstage-scaffolder
spec:
  description: test agent
`;

const HAND_CRAFTED_YAML = `apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: build-orchestrator
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
spec:
  description: hand-crafted agent
`;
```

Replace BOTH with:

```typescript
const IDP_MANAGED_YAML = `apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: release-coordinator
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
    app.kubernetes.io/managed-by: kagent
    app.kubernetes.io/name: release-coordinator
    app: kagent
    arigsela.com/idp-managed: "true"
spec:
  description: test agent
`;

const HAND_CRAFTED_YAML = `apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: build-orchestrator
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
spec:
  description: hand-crafted agent
`;
```

The `HAND_CRAFTED_YAML` fixture keeps its old shape (no `arigsela.com/idp-managed` label) — that's the negative test case.

Find the assertion in the `agent_not_idp_managed_throws` test:

```typescript
    await expect(action.handler(ctx)).rejects.toThrow(
      "Agent 'build-orchestrator' is not IDP-managed (missing label app.kubernetes.io/managed-by=backstage-scaffolder). Tear down by hand to avoid removing unrelated files.",
    );
```

Replace with:

```typescript
    await expect(action.handler(ctx)).rejects.toThrow(
      "Agent 'build-orchestrator' is not IDP-managed (missing label arigsela.com/idp-managed=true). Tear down by hand to avoid removing unrelated files.",
    );
```

No other assertions in the file reference the label name.

- [ ] **Step 3: Run tests to verify they FAIL with the old code**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
yarn workspace backend test --testPathPatterns=kagentDecommissionAction 2>&1 | tail -20
```

Expected: the `agent_not_idp_managed_throws` test fails because the action still throws the OLD error message. (Other tests might also fail because the fixture's missing-label structure no longer matches what the action expects.)

- [ ] **Step 4: Update the action's constants + helper**

Open `packages/backend/src/modules/scaffolder/kagentDecommissionAction.ts`. Find the constants:

```typescript
const MANAGED_BY_LABEL = 'app.kubernetes.io/managed-by';
const MANAGED_BY_VALUE = 'backstage-scaffolder';
```

Replace with:

```typescript
const IDP_MANAGED_LABEL = 'arigsela.com/idp-managed';
const IDP_MANAGED_VALUE = 'true';
```

Find the helper function:

```typescript
/**
 * Test whether a YAML body carries the IDP-management label.
 * Uses a regex rather than a YAML parser to avoid a runtime dep and to keep
 * the check tolerant of minor formatting variations (quoted/unquoted value).
 * Our scaffolder always renders the label in a predictable form.
 */
function hasManagedByLabel(yamlBody: string): boolean {
  const pattern = new RegExp(
    `${MANAGED_BY_LABEL.replace(/\./g, '\\.').replace(/\//g, '\\/')}:\\s*["']?${MANAGED_BY_VALUE}["']?`,
  );
  return pattern.test(yamlBody);
}
```

Replace with:

```typescript
/**
 * Test whether a YAML body carries the IDP-management label.
 * Uses a regex rather than a YAML parser to avoid a runtime dep and to keep
 * the check tolerant of minor formatting variations (quoted/unquoted value).
 * Our scaffolder always renders the label in a predictable form.
 */
function isIdpManaged(yamlBody: string): boolean {
  const pattern = new RegExp(
    `${IDP_MANAGED_LABEL.replace(/\./g, '\\.').replace(/\//g, '\\/')}:\\s*["']?${IDP_MANAGED_VALUE}["']?`,
  );
  return pattern.test(yamlBody);
}
```

Find the call site in the handler:

```typescript
      if (!hasManagedByLabel(yamlBody)) {
        throw new Error(
          `Agent '${name}' is not IDP-managed (missing label ${MANAGED_BY_LABEL}=${MANAGED_BY_VALUE}). Tear down by hand to avoid removing unrelated files.`,
        );
      }
```

Replace with:

```typescript
      if (!isIdpManaged(yamlBody)) {
        throw new Error(
          `Agent '${name}' is not IDP-managed (missing label ${IDP_MANAGED_LABEL}=${IDP_MANAGED_VALUE}). Tear down by hand to avoid removing unrelated files.`,
        );
      }
```

- [ ] **Step 5: Run tests to verify they PASS**

```bash
yarn workspace backend test --testPathPatterns=kagentDecommissionAction 2>&1 | tail -20
```

Expected: All 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add packages/backend/src/modules/scaffolder/kagentDecommissionAction.ts \
        packages/backend/src/modules/scaffolder/kagentDecommissionAction.test.ts
git commit -m "fix(scaffolder): migrate kagent decommission check to dedicated IDP label

v1.8 changes app.kubernetes.io/managed-by on the Agent CRD from
'backstage-scaffolder' to 'kagent' so the Backstage K8s plugin's label
selector (auto-generated by TeraSky from the Deployment's labels) matches
the Agent CRD itself, surfacing it in the Custom Resources panel.

That breaks the existing IDP-managed safety check in the decommission
action. Migrating the check to a dedicated arigsela.com/idp-managed='true'
label decouples 'runtime manager' from 'provenance' so each signal can
evolve independently.

All 6 unit tests updated with new fixtures; same logic flow, different
label name.

Companion spec: arigsela/kubernetes:docs/superpowers/specs/2026-05-19-kagent-idp-v1.8-design.md"
```

---

### Task 2: Update scaffolder content template with new labels

**Why:** The Agent CRD needs labels matching the TeraSky-generated selector (`app=kagent`, `app.kubernetes.io/managed-by=kagent`, `app.kubernetes.io/name=<name>`) so the K8s plugin discovers it. Plus the new IDP-managed label for the decommission check.

**Files:**
- Modify: `examples/templates/kagent-agent/content/base-apps/kagent/agents/${{ values.name }}.yaml`

- [ ] **Step 1: Open the file and locate the labels block**

The current labels block (around line 7-8 of the template):

```yaml
  labels:
    app.kubernetes.io/part-of: kagent
    app.kubernetes.io/managed-by: backstage-scaffolder
```

- [ ] **Step 2: Replace with new label set**

```yaml
  labels:
    app.kubernetes.io/part-of: kagent
    app.kubernetes.io/managed-by: kagent
    app.kubernetes.io/name: ${{ values.name }}
    app: kagent
    arigsela.com/idp-managed: "true"
```

Net change: `managed-by` value changes from `backstage-scaffolder` to `kagent`; 3 new labels added.

- [ ] **Step 3: Validate the template still renders + parses as YAML**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
node -e '
const nunjucks = require("nunjucks");
const fs = require("fs");
const tpl = fs.readFileSync("examples/templates/kagent-agent/content/base-apps/kagent/agents/${{ values.name }}.yaml", "utf-8")
  .replace(/\$\{\{/g, "{{").replace(/\}\}/g, "}}");
const out = new (require("nunjucks").Environment)(null, { autoescape: false }).renderString(tpl, {
  values: {
    name: "test-agent",
    description: "test",
    owner: "group:default/foo",
    systemMessage: "hi",
    includeBuiltinPrompts: true,
    delegateAgents: ["k8s-agent"],
    skills: [],
    cpuRequest: "100m", cpuLimit: "1000m",
    memoryRequest: "256Mi", memoryLimit: "1Gi",
    compactionInterval: 5, overlapSize: 2,
  },
});
const parsed = require("js-yaml").load(out);
console.log("YAML OK");
console.log("---labels---");
console.log(JSON.stringify(parsed.metadata.labels, null, 2));
'
```

Expected output:

```
YAML OK
---labels---
{
  "app.kubernetes.io/part-of": "kagent",
  "app.kubernetes.io/managed-by": "kagent",
  "app.kubernetes.io/name": "test-agent",
  "app": "kagent",
  "arigsela.com/idp-managed": "true"
}
```

If labels are missing or values are wrong, the template change wasn't saved correctly.

- [ ] **Step 4: Commit**

```bash
git add 'examples/templates/kagent-agent/content/base-apps/kagent/agents/${{ values.name }}.yaml'
git commit -m "feat(scaffolder): add K8s-discovery labels to rendered Agent CRD

Adds 3 new labels and changes managed-by value so newly-scaffolded
agents satisfy the Backstage K8s plugin's label selector for their
own Agent CRD (in addition to the spawned Pod/Deployment/Service that
already matched).

New labels:
- app: kagent
- app.kubernetes.io/name: <agent-name>
- arigsela.com/idp-managed: 'true'  (new IDP-managed safety check signal)

Changed:
- app.kubernetes.io/managed-by: backstage-scaffolder -> kagent

After this + the customResources config in app-config (next commit),
the K8s tab's Custom Resources panel will show the Agent CRD itself
alongside the workload."
```

---

### Task 3: Add `kubernetes.customResources` to both app-configs

**Why:** The K8s plugin only fetches resource kinds it knows about. By default that's pods, deployments, services, ingresses, configmaps, etc. — NOT custom resources. To get the kagent Agent CRD into the K8s tab's "Custom Resources" panel, we declare it via `kubernetes.customResources`. Both files need it because production replaces arrays (v1.6 finding).

**Files:**
- Modify: `app-config.yaml`
- Modify: `app-config.production.yaml`

- [ ] **Step 1: Locate the `kubernetes:` block in app-config.yaml**

The current block (around line 320-335):

```yaml
kubernetes:
  serviceLocatorMethod:
    type: 'multiTenant'
  clusterLocatorMethods:
    - type: 'config'
      clusters:
        - url: ${K8S_CLUSTER_URL}
          name: homelab
          authProvider: 'serviceAccount'
          skipTLSVerify: true
          serviceAccountToken: ${K8S_SERVICE_ACCOUNT_TOKEN}
```

- [ ] **Step 2: Add the customResources block AFTER `clusterLocatorMethods`**

Final state:

```yaml
kubernetes:
  serviceLocatorMethod:
    type: 'multiTenant'
  clusterLocatorMethods:
    - type: 'config'
      clusters:
        - url: ${K8S_CLUSTER_URL}
          name: homelab
          authProvider: 'serviceAccount'
          skipTLSVerify: true
          serviceAccountToken: ${K8S_SERVICE_ACCOUNT_TOKEN}
  customResources:
    - group: 'kagent.dev'
      apiVersion: 'v1alpha2'
      plural: 'agents'
```

- [ ] **Step 3: Check whether `app-config.production.yaml` has a `kubernetes:` block**

```bash
grep -n "^kubernetes:" /Users/arisela/git/kubernetes/docs/reference/backstage/app-config.production.yaml
```

Expected: no output (no top-level `kubernetes:` key today).

- [ ] **Step 4: Add a minimal `kubernetes:` block to app-config.production.yaml**

At the bottom of `app-config.production.yaml` (or any sensible top-level position), add:

```yaml
# --- KUBERNETES ---
# Mirror of the customResources entry from app-config.yaml. Backstage's
# config layering MERGES objects but REPLACES arrays — and we want the
# kagent Agent CRD to be fetchable for the entity-page Kubernetes tab.
# Putting customResources in both files keeps the entry present
# regardless of which config layer wins. (v1.6 lesson — same pattern
# as catalog.locations.)
kubernetes:
  customResources:
    - group: 'kagent.dev'
      apiVersion: 'v1alpha2'
      plural: 'agents'
```

- [ ] **Step 5: Validate both YAML files parse**

```bash
node -e "require('js-yaml').load(require('fs').readFileSync('app-config.yaml','utf-8'))" && echo "app-config.yaml OK"
node -e "require('js-yaml').load(require('fs').readFileSync('app-config.production.yaml','utf-8'))" && echo "app-config.production.yaml OK"
```

Expected: both print OK.

- [ ] **Step 6: Commit**

```bash
git add app-config.yaml app-config.production.yaml
git commit -m "feat(backstage): add kagent.dev Agent to kubernetes.customResources

Declares the kagent.dev/v1alpha2/agents CRD to Backstage's K8s plugin so
it gets fetched for entity-page Kubernetes tabs. Without this, the K8s
plugin only fetches standard k8s resources (pods, services, etc.) and
the Custom Resources panel stays empty for kagent agents.

Added to BOTH app-config.yaml and app-config.production.yaml because
production replaces arrays (v1.6 lesson) — putting it in both keeps the
entry present regardless of which layer is loaded.

The backstage-kagent-read ClusterRole shipped in v1.7 (PR #285) already
grants the Backstage SA get/list/watch on agents.kagent.dev — no new
RBAC needed."
```

---

### Task 4: Add `kagentAgentPage` layout + routing in EntityPage.tsx

**Why:** Today kagent-agent entities fall through to `defaultEntityPage` which has only Overview + Docs tabs (no Kubernetes tab). The 3-tab layout (Overview / Kubernetes / Docs) gives kagent agents the K8s visibility they need without inheriting unused tabs (CI/CD, API, Dependencies) that don't apply to in-cluster agents.

**Files:**
- Modify: `packages/app/src/components/catalog/EntityPage.tsx`

- [ ] **Step 1: Add the `kagentAgentPage` constant**

Open `packages/app/src/components/catalog/EntityPage.tsx`. Find the `serviceEntityPage` constant (starts around line 373). **Immediately before** `serviceEntityPage`, add:

```typescript
// kagent IDP v1.8: dedicated 3-tab layout (Overview / Kubernetes / Docs)
// for entities with spec.type='kagent-agent'. The Kubernetes tab shows
// the kagent-controller-spawned workload (Pod/Deployment/Service) plus
// the Agent CRD itself in the Custom Resources panel (configured via
// kubernetes.customResources in app-config).
const kagentAgentPage = (
  <EntityLayout>
    <EntityLayout.Route path="/" title="Overview">
      {overviewContent}
    </EntityLayout.Route>

    <EntityLayout.Route
      path="/kubernetes"
      title="Kubernetes"
      if={isKubernetesAvailable}
    >
      <EntityKubernetesContent />
    </EntityLayout.Route>

    <EntityLayout.Route path="/docs" title="Docs">
      {techdocsContent}
    </EntityLayout.Route>
  </EntityLayout>
);

```

- [ ] **Step 2: Add the routing case in `componentPage`**

Find the `componentPage` constant (around line 492):

```typescript
const componentPage = (
  <EntitySwitch>
    <EntitySwitch.Case if={isComponentType('service')}>
      {serviceEntityPage}
    </EntitySwitch.Case>

    <EntitySwitch.Case if={isComponentType('website')}>
      {websiteEntityPage}
    </EntitySwitch.Case>

    <EntitySwitch.Case>{defaultEntityPage}</EntitySwitch.Case>
  </EntitySwitch>
);
```

Insert a new case BEFORE the catch-all `defaultEntityPage` case:

```typescript
const componentPage = (
  <EntitySwitch>
    <EntitySwitch.Case if={isComponentType('service')}>
      {serviceEntityPage}
    </EntitySwitch.Case>

    <EntitySwitch.Case if={isComponentType('website')}>
      {websiteEntityPage}
    </EntitySwitch.Case>

    <EntitySwitch.Case if={isComponentType('kagent-agent')}>
      {kagentAgentPage}
    </EntitySwitch.Case>

    <EntitySwitch.Case>{defaultEntityPage}</EntitySwitch.Case>
  </EntitySwitch>
);
```

- [ ] **Step 3: Type-check**

```bash
yarn tsc 2>&1 | tail -5
```

Expected: no errors. (If errors complain about `kagentAgentPage` being unreachable or `EntityLayout` undefined, the constant was added in the wrong place — needs to be at module scope, not nested inside another const.)

- [ ] **Step 4: Run app tests**

```bash
yarn workspace app test 2>&1 | tail -10
```

Expected: tests pass. (Same Node 25 startup slowness as v1.6/v1.7 — first run may take 5-8 min.)

- [ ] **Step 5: Commit**

```bash
git add packages/app/src/components/catalog/EntityPage.tsx
git commit -m "feat(app): add Kubernetes tab to kagent-agent entity page

New kagentAgentPage layout with 3 tabs (Overview / Kubernetes / Docs)
routed via EntitySwitch.Case for spec.type='kagent-agent'. Replaces the
2-tab defaultEntityPage that kagent agents were falling through to.

Reuses existing overviewContent (includes the v1.7 'About this agent'
card), EntityKubernetesContent (the standard K8s plugin tab — now
shows kagent workload + Agent CRD in Custom Resources), and
techdocsContent.

Intentionally excludes CI/CD, API, Dependencies, and Crossplane tabs
that don't apply to in-cluster agents and would render misleading
empty states."
```

---

### Task 5 (within Backstage PR): Push branch + open PR

- [ ] **Step 1: Push the feature branch**

```bash
git push -u origin feat/kagent-v1.8-k8s-tab
```

- [ ] **Step 2: Open the PR**

Title: `feat(kagent-idp-v1.8): Kubernetes tab on kagent-agent entity page`

Body should include:
- Summary referencing the 4 commits
- Coordination note: this PR must merge + image rebuilt + deployed BEFORE the kubernetes backfill PR (otherwise the decommission action's new label check fails on the existing `homelab-knowledge.yaml` — safe but blocks teardown).
- Test plan: link to unit test pass output and the smoke-test checklist from the spec
- Companion: arigsela/kubernetes backfill PR will follow

---

## Phase 2 — Kubernetes backfill (single PR)

### Task 6: Backfill `homelab-knowledge.yaml` with new labels

**Why:** The existing IDP-created agent file (added in v1.6) uses the old label set. Without this backfill, the decommission action (after the Backstage PR ships) would refuse to delete homelab-knowledge because it's missing the new `arigsela.com/idp-managed` label. Also, the K8s plugin's Custom Resources panel won't show the Agent CRD until its labels match the selector.

**Files:**
- Modify (in `arigsela/kubernetes`): `base-apps/kagent/agents/homelab-knowledge.yaml`

**Prerequisite:** Backstage PR has been merged + new image deployed in the cluster. (Otherwise the kubernetes change syncs first and the still-running old Backstage image will be inconsistent — `homelab-knowledge` would be considered "not IDP-managed" by the OLD decommission action. Also safe but annoying.)

- [ ] **Step 1: Branch off latest main in the kubernetes repo**

```bash
cd /Users/arisela/git/kubernetes
git fetch origin main
git checkout -B feat/kagent-v1.8-backfill-homelab-knowledge origin/main
git log --oneline -3
```

- [ ] **Step 2: Open `base-apps/kagent/agents/homelab-knowledge.yaml`**

Locate the labels block (lines 6-9):

```yaml
  labels:
    app.kubernetes.io/part-of: kagent
    app.kubernetes.io/managed-by: backstage-scaffolder
```

- [ ] **Step 3: Replace with the new label set**

```yaml
  labels:
    app.kubernetes.io/part-of: kagent
    app.kubernetes.io/managed-by: kagent
    app.kubernetes.io/name: homelab-knowledge
    app: kagent
    arigsela.com/idp-managed: "true"
```

- [ ] **Step 4: Validate YAML parses**

```bash
python3 -c "import yaml; yaml.safe_load(open('base-apps/kagent/agents/homelab-knowledge.yaml'))" && echo OK
```

Expected: prints `OK`.

- [ ] **Step 5: Diff check — only the labels block should have changed**

```bash
git diff base-apps/kagent/agents/homelab-knowledge.yaml
```

Expected: 1 line removed (the old `managed-by`), 4 lines added (new `managed-by` value + 3 new labels). No other changes.

- [ ] **Step 6: Commit**

```bash
git add base-apps/kagent/agents/homelab-knowledge.yaml
git commit -m "feat(kagent): backfill v1.8 labels on homelab-knowledge

Matches the new label set the v1.8 scaffolder template renders for
new agents:
- app.kubernetes.io/managed-by: backstage-scaffolder -> kagent
- + app.kubernetes.io/name: homelab-knowledge
- + app: kagent
- + arigsela.com/idp-managed: 'true'

After this PR syncs through ArgoCD:
- The K8s plugin's label selector matches the Agent CRD, so it shows up
  in the entity-page Kubernetes tab's Custom Resources panel.
- The decommission action's new IDP-managed safety check (shipped in
  arigsela/backstage v1.8 PR) accepts homelab-knowledge as IDP-managed.

PRE-REQ: arigsela/backstage v1.8 PR merged + image deployed first.
Otherwise the OLD image still runs, decommission still expects the
old managed-by=backstage-scaffolder label, and homelab-knowledge
becomes briefly un-decommissionable (safe, just annoying)."
```

- [ ] **Step 7: Push + open PR**

```bash
git push -u origin feat/kagent-v1.8-backfill-homelab-knowledge
```

Open PR titled `feat(kagent): backfill v1.8 labels on homelab-knowledge`. Reference the backstage PR in the body. Note the prerequisite (deploy Backstage first).

---

## Phase 3 — End-to-end smoke tests

### Task 7: Verify after deploy + ArgoCD sync

**Prerequisites:**
- Backstage PR merged + image rebuilt + redeployed
- Kubernetes backfill PR merged + ArgoCD synced (~3 min)

- [ ] **Step 1: Verify the new labels are on the Agent CRD in the cluster**

```bash
kubectl get agent -n kagent homelab-knowledge -o jsonpath='{.metadata.labels}' | python3 -c "import json,sys; d=json.load(sys.stdin); print('\n'.join(sorted(d.keys())))"
```

Expected:

```
app
app.kubernetes.io/managed-by
app.kubernetes.io/name
app.kubernetes.io/part-of
arigsela.com/idp-managed
```

If `arigsela.com/idp-managed` is missing, ArgoCD hasn't synced yet — wait ~3 min and retry.

- [ ] **Step 2: Verify the decommission action's safety check works**

In Backstage `/create`, choose **Decommission Kagent Agent**, enter `build-orchestrator` (the hand-crafted agent).

Expected: action fails with the NEW error message:
```
Agent 'build-orchestrator' is not IDP-managed (missing label arigsela.com/idp-managed=true).
Tear down by hand to avoid removing unrelated files.
```

(Do NOT actually run this against `homelab-knowledge` — that would open a teardown PR.)

- [ ] **Step 3: Verify the Kubernetes tab appears on the entity page**

Visit `https://backstage.arigsela.com/catalog/default/component/homelab-knowledge`.

Expected tabs at top of page: **OVERVIEW**, **KUBERNETES**, **DOCS**. (Previously only OVERVIEW + DOCS.)

- [ ] **Step 4: Click the KUBERNETES tab — verify workload resources**

Expected sections:
- **Pods** — 1 pod (`homelab-knowledge-...`)
- **Deployments** — 1 deployment (`homelab-knowledge`)
- **ReplicaSets** — 1 replicaset
- **Services** — 1 service (`homelab-knowledge`)
- **ServiceAccounts** — 1 (`homelab-knowledge`)

- [ ] **Step 5: Scroll to the Custom Resources section**

Expected: a "Custom Resources" or similar panel showing:
- **Agent** (`homelab-knowledge`, group: `kagent.dev`, version: `v1alpha2`)

If empty, check:
- `app-config.production.yaml` has the customResources entry (deployment may have cached old config — verify with `kubectl exec -n backstage <pod> -- grep -A 4 "customResources" /app/app-config.production.yaml`)
- The agent's labels include `app=kagent`, `app.kubernetes.io/managed-by=kagent`, `app.kubernetes.io/name=homelab-knowledge` (all three needed for the selector to match — verify with kubectl from Step 1)

- [ ] **Step 6: Open the pod entry → verify logs are viewable**

Click the pod row → drawer/expandable should show container logs.

Expected: log lines from the agent container. (Confirms read-only RBAC works for pod logs too.)

- [ ] **Step 7: Negative check — non-kagent entities are unchanged**

Visit any other Component entity (e.g. a `service` or `website` type entity). Verify the tab set is what it was before (Overview + CI/CD + Kubernetes + Crossplane + API + Dependencies + Docs for `service`; whatever the default is for `website`).

Expected: NO new "Kubernetes" tab where there wasn't one before. The kagent-agent layout change must not bleed into other entity types.

- [ ] **Step 8: Document any deviations**

If something doesn't match, capture screenshot + the kubectl/curl output that diverges from expected. File a follow-up issue.

---

## Done criteria

- Backstage PR merged with all 4 commits (decommission migration, scaffolder labels, app-config customResources, EntityPage layout)
- Kubernetes backfill PR merged + ArgoCD synced
- `homelab-knowledge` entity page shows 3 tabs (Overview / Kubernetes / Docs)
- Kubernetes tab shows Pod + Deployment + ReplicaSet + Service + ServiceAccount AND the Agent CRD in Custom Resources
- Decommission action's safety check rejects `build-orchestrator` with the new error message
- Decommission action would accept `homelab-knowledge` (verified by inspection of the YAML's labels, NOT by actually running it)
- No regressions on non-kagent entity pages

## Known limitations carried from the spec

1. **Single cluster hardcoded.** The K8s plugin's cluster locator only knows about `homelab`. Multi-cluster deployments would need per-entity cluster annotations.
2. **ModelConfigs not shown.** They're shared cluster-wide and don't match per-agent label selectors. Future iteration could add a separate ModelConfig entity or a cluster-level "kagent platform" page.
3. **No live pod-log streaming on the tab.** Standard Backstage K8s plugin behavior — logs viewable on click, no continuous tail.

## Rollback plan

If something breaks:

1. Revert the Backstage PR (rollback image to prior tag).
2. Revert the kubernetes backfill PR (re-apply old `homelab-knowledge.yaml` labels). One git revert PR.

Worst case: 2 revert PRs + image rollback. Decommission action would refuse all agents until the labels match the action's check — annoying but safe (no destructive action).
