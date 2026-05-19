# Kagent IDP v1.7 — Backstage Entity Page Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "About this agent" Markdown card to the Backstage entity page for IDP-created kagent agents, sourced from a pre-rendered annotation baked into the agent CRD at scaffold time.

**Architecture:** One annotation (`arigsela.com/kagent-about`) carrying pre-rendered Markdown gets added by the scaffolder content template. The Backstage `EntityPage.tsx` gets one new `EntitySwitch.Case` that reads the annotation and renders it via `MarkdownContent` inside an `InfoCard`. The existing `homelab-knowledge.yaml` is hand-backfilled with the same annotation so the working agent gets the new view immediately.

**Tech Stack:** TypeScript / React (Backstage), Nunjucks (scaffolder templates), YAML, Octokit (PR creation).

**Companion spec:** `docs/superpowers/specs/2026-05-18-kagent-idp-v1.7-design.md`

**Working directories:**
- Tasks 1 + 2 work in `/Users/arisela/git/kubernetes/docs/reference/backstage/` (the `arigsela/backstage` repo clone).
- Task 3 works in `/Users/arisela/git/kubernetes/` (this repo).
- Task 4 is operational (kubectl + browser).

---

## File Structure

### Files to MODIFY in `arigsela/backstage` (`docs/reference/backstage/`)

| File | Change |
|---|---|
| `packages/app/src/components/catalog/EntityPage.tsx` | + 2 imports, + helper + card definition, + one Grid item in `overviewContent`. ~20 lines added. |
| `examples/templates/kagent-agent/content/base-apps/kagent/agents/${{ values.name }}.yaml` | + 1 annotation block under `metadata.annotations`. Holds the rendered Markdown body. ~60 lines added. |

### Files to MODIFY in `arigsela/kubernetes` (this repo)

| File | Change |
|---|---|
| `base-apps/kagent/agents/homelab-knowledge.yaml` | + 1 annotation block (one-time hand backfill). |

### What this plan does NOT touch

- Any kagent Helm values (`base-apps/kagent.yaml`)
- The kagent CRD schema (the new annotation is operator metadata, transparent to the kagent controller)
- Any other Backstage scaffolder template or custom scaffolder action
- TeraSky `kubernetes-ingestor` config — annotations are automatically carried over from the K8s object to the Backstage entity

### Out of scope (per spec)

- Building a Backstage frontend plugin
- TechDocs scaffold generation
- Live K8s data fetching from the card
- Cross-agent delegation graph
- Embedded kagent UI iframe

### What the user handles outside this plan

- Building + deploying the updated `backstage-portal` container image after Task 1 merges
- Merging the three PRs in order (Task 1 → Task 2 → Task 3)

---

## Phase 1 — Backstage EntityPage card

### Task 1: Add the "About this agent" card to `EntityPage.tsx`

**Why:** Without the UI change, the new annotation has no rendering. Ships first because it's safe even when no entity has the annotation yet (the card returns `null`).

**Files:**
- Modify: `packages/app/src/components/catalog/EntityPage.tsx`

- [ ] **Step 1: Extend the `@backstage/core-components` import**

Open `packages/app/src/components/catalog/EntityPage.tsx`. Find line 37:

```typescript
import { EmptyState } from '@backstage/core-components';
```

Replace with:

```typescript
import { EmptyState, InfoCard, MarkdownContent } from '@backstage/core-components';
```

Both `InfoCard` and `MarkdownContent` are already exported by `@backstage/core-components` (existing dependency). No new packages needed.

- [ ] **Step 2: Add the `Entity` type import**

Find the `@backstage/catalog-model` import that ends on line 51:

```typescript
import {
  RELATION_API_CONSUMED_BY,
  RELATION_API_PROVIDED_BY,
  RELATION_CONSUMES_API,
  RELATION_DEPENDENCY_OF,
  RELATION_DEPENDS_ON,
  RELATION_HAS_PART,
  RELATION_PART_OF,
  RELATION_PROVIDES_API,
} from '@backstage/catalog-model';
```

Add `Entity` to the import list (alphabetical ordering before the `RELATION_*` constants):

```typescript
import {
  Entity,
  RELATION_API_CONSUMED_BY,
  RELATION_API_PROVIDED_BY,
  RELATION_CONSUMES_API,
  RELATION_DEPENDENCY_OF,
  RELATION_DEPENDS_ON,
  RELATION_HAS_PART,
  RELATION_PART_OF,
  RELATION_PROVIDES_API,
} from '@backstage/catalog-model';
```

- [ ] **Step 3: Add the helper + card block**

Find the `cicdContent` constant (starts at line 77). Insert this block **immediately before** `cicdContent`:

```typescript
// =============================================================================
// Kagent IDP v1.7 — "About this agent" card
// =============================================================================
// Renders the pre-baked arigsela.com/kagent-about annotation on the Overview
// tab for entities with spec.type = 'kagent-agent'. Returns null for any other
// entity type or when the annotation is absent (e.g. older agents not yet
// backfilled).
//
// Pattern: EntitySwitch outside, Grid item INSIDE the matching case. This is
// the convention `entityWarningContent` uses below — keeps the Grid container
// flat for non-matching entities (no empty Grid item leaks).
//
// Companion spec: arigsela/kubernetes:docs/superpowers/specs/2026-05-18-kagent-idp-v1.7-design.md

const KAGENT_ABOUT_ANNOTATION = 'arigsela.com/kagent-about';

const isKagentAgent = (entity: Entity): boolean =>
  entity?.spec?.type === 'kagent-agent';

const kagentAboutCard = (
  <EntitySwitch>
    <EntitySwitch.Case if={isKagentAgent}>
      {({ entity }: { entity: Entity }) => {
        const about = entity.metadata.annotations?.[KAGENT_ABOUT_ANNOTATION];
        if (!about) return null;
        return (
          <Grid item xs={12}>
            <InfoCard title="About this agent">
              <MarkdownContent content={about} />
            </InfoCard>
          </Grid>
        );
      }}
    </EntitySwitch.Case>
  </EntitySwitch>
);

```

- [ ] **Step 4: Inject the card into `overviewContent`**

Find the `overviewContent` block (around line 135 in the current file; line numbers shift slightly after Step 3 inserts above it). It currently looks like:

```typescript
const overviewContent = (
  <Grid container spacing={3} alignItems="stretch">
    {entityWarningContent}
    <Grid item md={6}>
      <EntityAboutCard variant="gridItem" />
    </Grid>
    <Grid item md={6} xs={12}>
      <EntityCatalogGraphCard variant="gridItem" height={400} />
    </Grid>

    <Grid item md={4} xs={12}>
      <EntityLinksCard />
    </Grid>
    <Grid item md={8} xs={12}>
      <EntityHasSubcomponentsCard variant="gridItem" />
    </Grid>
  </Grid>
);
```

Embed `{kagentAboutCard}` as a bare JSX expression directly inside the `<Grid container>`, after the `EntityCatalogGraphCard` block. Don't wrap it in another `<Grid item>` — the Grid item is already inside the matching `EntitySwitch.Case` from Step 3, mirroring how `entityWarningContent` is embedded on the line above:

```typescript
const overviewContent = (
  <Grid container spacing={3} alignItems="stretch">
    {entityWarningContent}
    <Grid item md={6}>
      <EntityAboutCard variant="gridItem" />
    </Grid>
    <Grid item md={6} xs={12}>
      <EntityCatalogGraphCard variant="gridItem" height={400} />
    </Grid>

    {kagentAboutCard}

    <Grid item md={4} xs={12}>
      <EntityLinksCard />
    </Grid>
    <Grid item md={8} xs={12}>
      <EntityHasSubcomponentsCard variant="gridItem" />
    </Grid>
  </Grid>
);
```

For non-kagent entities the EntitySwitch returns nothing (no Grid item leaks). For kagent-agent entities with the annotation, the inner `<Grid item xs={12}>` produces a full-width row.

- [ ] **Step 5: Type-check**

From the backstage repo root:

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
yarn tsc 2>&1 | tail -10
```

Expected: no errors. If there are errors, common issues:
- `Cannot find name 'Entity'` → Step 2 was skipped or applied to the wrong import block
- `Cannot find name 'InfoCard'` or `'MarkdownContent'` → Step 1 was skipped
- `Property 'spec' does not exist on type 'undefined'` → `entity?.spec?.type` uses optional chaining; check it wasn't accidentally typed as `entity.spec.type`

- [ ] **Step 6: Run the existing app tests**

The Backstage `app` package has an integration test (`App.test.tsx`) that exercises EntityPage rendering. Run it to catch any rendering crashes:

```bash
yarn workspace app test 2>&1 | tail -20
```

Expected: tests pass. (Same Node 25 startup slowness as v1.6 — may take 5-8 min for the first run to finish bootstrapping the test environment.)

- [ ] **Step 7: Commit**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
git add packages/app/src/components/catalog/EntityPage.tsx
git commit -m "feat(app): add 'About this agent' card for kagent IDP entities

Adds an EntitySwitch.Case in overviewContent that reads the
arigsela.com/kagent-about annotation and renders it via MarkdownContent
inside an InfoCard. Visible only for entities with spec.type='kagent-agent'
that carry the annotation; non-kagent and unbackfilled entities render
nothing (no broken UI).

Companion spec: arigsela/kubernetes:docs/superpowers/specs/2026-05-18-kagent-idp-v1.7-design.md"
```

---

## Phase 2 — Scaffolder content template

### Task 2: Add `arigsela.com/kagent-about` to the rendered Agent CRD

**Why:** New agents created via the IDP need the annotation auto-rendered. Ships after Task 1 is deployed so the card has somewhere to render to.

**Files:**
- Modify: `examples/templates/kagent-agent/content/base-apps/kagent/agents/${{ values.name }}.yaml`

- [ ] **Step 1: Open the content template**

Open `examples/templates/kagent-agent/content/base-apps/kagent/agents/${{ values.name }}.yaml`. The current top of the file looks like:

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: ${{ values.name }}
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
    app.kubernetes.io/managed-by: backstage-scaffolder
  annotations:
    terasky.backstage.io/add-to-catalog: "true"
    terasky.backstage.io/component-type: kagent-agent
    backstage.io/managed-by-location: url:https://github.com/arigsela/kubernetes/blob/main/base-apps/kagent/agents/${{ values.name }}.yaml
    backstage.io/owner: ${{ values.owner }}
spec:
  ...
```

- [ ] **Step 2: Add the Nunjucks helper for delegate descriptions**

At the very top of the file (before `apiVersion:`), add the lookup table as a Nunjucks block. Nunjucks `{% set %}` at the top of the file establishes the variable for all later substitutions:

```yaml
{%- set delegate_descriptions = {
    "k8s-agent": "Kubernetes cluster operations (pods, deployments, RBAC, troubleshooting)",
    "helm-agent": "Helm release lifecycle (install/upgrade/rollback, chart inspection)",
    "istio-agent": "Istio service-mesh configuration and traffic management",
    "kgateway-agent": "Kubernetes Gateway API (kgateway/Envoy)",
    "argo-rollouts-conversion-agent": "Convert Deployments to Argo Rollouts for progressive delivery",
    "observability-agent": "Prometheus + Grafana metrics and dashboard management"
} -%}
apiVersion: kagent.dev/v1alpha2
kind: Agent
...
```

The `{%- ... -%}` whitespace-control hyphens strip the surrounding newlines so the `apiVersion:` line stays at the top of the rendered YAML.

- [ ] **Step 3: Add the `arigsela.com/kagent-about` annotation block**

In the `metadata.annotations` block, add the new annotation **after** `backstage.io/owner`:

```yaml
  annotations:
    terasky.backstage.io/add-to-catalog: "true"
    terasky.backstage.io/component-type: kagent-agent
    backstage.io/managed-by-location: url:https://github.com/arigsela/kubernetes/blob/main/base-apps/kagent/agents/${{ values.name }}.yaml
    backstage.io/owner: ${{ values.owner }}
    arigsela.com/kagent-about: |
      # ${{ values.name }}

      ${{ values.description }}

      ## Purpose

      ${{ values.systemMessage | replace('\n', '\n      ') }}
{%- if values.skills | length > 0 %}

      ## Skills
{%- for skill in values.skills %}

      ### ${{ skill.name }} (`${{ skill.id }}`)

      ${{ skill.description }}
{%- if skill.examples and skill.examples | length > 0 %}

      **Examples:**
{%- for example in skill.examples %}
      - ${{ example }}
{%- endfor %}
{%- endif %}
{%- if skill.tags and skill.tags | length > 0 %}

      **Tags:** {% for tag in skill.tags %}`${{ tag }}`{% if not loop.last %}, {% endif %}{% endfor %}
{%- endif %}
{%- endfor %}
{%- endif %}

      ## Delegates to

      This agent can delegate tasks to the following agents:
{%- for agentName in values.delegateAgents %}
      - **${{ agentName }}** — {{ delegate_descriptions[agentName] or "(see kagent docs)" }}
{%- endfor %}

      ## Configuration

      | Setting | Value |
      |---|---|
      | Model | `default-model-config` |
      | Memory model | `embedding-model-config` |
      | Compaction interval | ${{ values.compactionInterval }} turns |
      | Compaction overlap | ${{ values.overlapSize }} turns |
      | CPU | ${{ values.cpuRequest }} / ${{ values.cpuLimit }} (req/lim) |
      | Memory | ${{ values.memoryRequest }} / ${{ values.memoryLimit }} (req/lim) |
      | Built-in prompts | {% if values.includeBuiltinPrompts %}included{% else %}not included{% endif %} |

      ## Manage

      - **Edit:** hand-edit `base-apps/kagent/agents/${{ values.name }}.yaml` and open a PR
      - **Decommission:** use the **Decommission Kagent Agent** template in Backstage
spec:
  ...
```

**Indentation notes:**
- The YAML literal block `|` after `arigsela.com/kagent-about:` sets the indent level to **6 spaces** (one level deeper than the annotation key, which is at 4 spaces inside `metadata.annotations`).
- All literal Markdown lines must start with at least 6 spaces.
- `${{ values.systemMessage | replace('\n', '\n      ') }}` injects 6 spaces after each newline in the multi-line system message. This is the **fix for the v1.6 `| indent(6)` pitfall** — that filter added extra spaces; `replace` produces exactly 6 every time.
- Nunjucks control structures (`{%- if %}`, `{%- for %}`) use whitespace-control hyphens so they don't leak blank lines into the rendered YAML.

- [ ] **Step 4: Validate the template parses with a quick mock render**

Backstage's `fetch:template` action processes both filenames and contents via Nunjucks. There's no standalone CLI to test a single content file, so the cleanest pre-flight check is a YAML parse of a hand-substituted version. Run this Node snippet from the backstage repo root:

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
node -e '
const nunjucks = require("nunjucks");
const fs = require("fs");
const tpl = fs.readFileSync("examples/templates/kagent-agent/content/base-apps/kagent/agents/${{ values.name }}.yaml", "utf-8")
  .replace(/\${{/g, "{{").replace(/}}/g, "}}");
const env = new nunjucks.Environment(null, { autoescape: false });
const out = env.renderString(tpl, {
  values: {
    name: "test-agent",
    description: "A test agent",
    owner: "group:default/platform-engineering",
    systemMessage: "You are a test agent.\nLine two.\nLine three.",
    includeBuiltinPrompts: true,
    delegateAgents: ["k8s-agent", "helm-agent"],
    skills: [{ id: "smoke", name: "Smoke", description: "Test skill", examples: ["ping"], tags: ["test"] }],
    cpuRequest: "100m", cpuLimit: "1000m",
    memoryRequest: "256Mi", memoryLimit: "1Gi",
    compactionInterval: 5, overlapSize: 2,
  },
});
require("js-yaml").load(out);
console.log("OK — template renders and parses as YAML");
'
```

Expected: prints `OK — template renders and parses as YAML`. If it fails:
- `YAMLException: bad indentation of a mapping entry` → an indent level is off; re-check the 6-space base for the literal block
- `unknown filter: replace` → Nunjucks `replace` filter is built-in; if missing, install via `nunjucks` package (already a Backstage dep)
- `Cannot read property 'length' of undefined` → a wizard input wasn't passed; the mock values block needs all the keys the template references

- [ ] **Step 5: Dry-run smoke test (full Backstage path)**

After the Backstage image with Task 1 is deployed, open the wizard at `/create`, pick **Kagent Declarative Agent**, fill it in with a throwaway name like `v17-smoke-test`, ensure `dryRun: true`, and submit. Then from the pod:

```bash
kubectl exec -n backstage <pod-name> -- cat \
  /tmp/backstage-scaffolder/v17-smoke-test/base-apps/kagent/agents/v17-smoke-test.yaml \
  | grep -A 80 "kagent-about:"
```

Expected output starts with:
```
    arigsela.com/kagent-about: |
      # v17-smoke-test

      <description you entered>

      ## Purpose

      <systemMessage you entered, with consistent 6-space indent on continuation lines>

      ## Skills
      ...
```

Verify:
- All sections present (Purpose, Skills, Delegates to, Configuration, Manage)
- No raw `{{ }}` or `{%- %}` syntax leaks
- Configuration table values match what you entered
- Delegate descriptions resolved correctly (not the fallback `(see kagent docs)` for known agents)

If anything's off, the template needs another pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
git add 'examples/templates/kagent-agent/content/base-apps/kagent/agents/${{ values.name }}.yaml'
git commit -m "feat(scaffolder): render kagent-about annotation in agent CRD

Adds the arigsela.com/kagent-about annotation containing pre-rendered
Markdown that summarizes the agent's purpose (full systemMessage), skills
with examples and tags, delegate graph with descriptions, configuration
table, and management actions. The annotation is consumed by the Backstage
EntityPage 'About this agent' card added in the previous commit.

Uses replace('\\n', '\\n      ') to handle systemMessage line breaks
correctly — sidesteps the | indent(6) pitfall from v1.6 that added extra
spaces on continuation lines.

Companion spec: arigsela/kubernetes:docs/superpowers/specs/2026-05-18-kagent-idp-v1.7-design.md"
```

---

## Phase 3 — Backfill existing agent

### Task 3: Add the annotation to `homelab-knowledge.yaml`

**Why:** The existing agent (created via v1.6 IDP) doesn't have the new annotation. Without backfill, its entity page renders the card as empty. Hand-edit is cheaper than decommission + re-scaffold when the population is 1.

**Files:**
- Modify (in `arigsela/kubernetes`): `base-apps/kagent/agents/homelab-knowledge.yaml`

- [ ] **Step 1: From a branch off latest main, open the file**

```bash
cd /Users/arisela/git/kubernetes
git fetch origin main
git checkout -B feat/kagent-v1.7-backfill-homelab-knowledge origin/main
```

Open `base-apps/kagent/agents/homelab-knowledge.yaml`.

- [ ] **Step 2: Add the annotation block**

In the `metadata.annotations` block, after the `backstage.io/owner` line, insert:

```yaml
    arigsela.com/kagent-about: |
      # homelab-knowledge

      Answers questions about the homelab GitOps repo, base-apps deployments, and live cluster state by delegating to k8s and helm specialists

      ## Purpose

      You are HomelabAssist, an expert on this homelab Kubernetes cluster and its
      GitOps repository at github.com/arigsela/kubernetes. Your job is to answer
      questions about what's deployed, why, how things are wired together, and to
      help diagnose issues — by delegating to specialist agents for live cluster
      queries and to your built-in knowledge for architectural context.

      ## What you know about

      - **GitOps model**: ArgoCD watches arigsela/kubernetes/base-apps. The master-app
        pattern creates one ArgoCD Application per .yaml file in that directory.
        All apps have prune: true and selfHeal: true.
      - **Application layout**: each base-apps/<name>/ subdirectory holds the
        manifests for one app; the matching base-apps/<name>.yaml is its ArgoCD
        Application definition.
      - **Secret management**: HashiCorp Vault (in-cluster, k8s auth method, KV v2
        at path k8s-secrets) feeds External Secrets via per-namespace SecretStores
        that reference vault role = <namespace-name>.
      - **Cloud resources**: Crossplane v2 with the XApplication composition
        provisions optional Postgres (CloudNativePG) and S3 buckets. TeraSky's
        kubernetes-ingestor auto-registers XRs in Backstage.
      - **TLS + ingress**: nginx-ingress + cert-manager with letsencrypt-prod
        (Route 53 DNS-01).
      - **IDP**: Backstage with scaffolder templates (Application, CrewAI agent,
        Kagent agent, decommission flows). Custom actions in
        packages/backend/src/modules/scaffolder/ for non-Crossplane work.
      - **AI agents**: kagent.dev controller manages declarative agents in the
        kagent namespace. Custom orchestrators (build-orchestrator, this one) live
        alongside chart-installed agents (k8s-agent, helm-agent, etc.).

      ## Delegation rules

      - For LIVE cluster state (pod status, events, logs, resource usage, RBAC,
        CRDs, namespaces) → delegate to k8s-agent. Always prefer this over guessing.
      - For Helm release questions (what charts are installed, versions, values
        diffs, release history) → delegate to helm-agent.
      - For repo/manifest questions (what's in base-apps/<x>/, ArgoCD sync status,
        Vault role names, ingress hostnames) → answer from your own knowledge if
        possible, then cross-check live state via k8s-agent.

      ## Skills

      ### Repo & Architecture Knowledge (`repo-knowledge`)

      Explain what's deployed, where it lives in the GitOps repo, and how components are wired together.

      **Examples:**
      - What apps run in the kagent namespace?
      - How does the Vault SecretStore for chores-tracker work?
      - Where is the cert-manager Route 53 config?

      **Tags:** `gitops`, `documentation`, `architecture`

      ### Cluster State Troubleshooting (`cluster-troubleshooting`)

      Diagnose issues by checking live pod/deployment/event state and correlating with the GitOps manifests.

      **Examples:**
      - Why is the backstage pod restarting?
      - Is the kagent helm-agent ready?
      - What's the ArgoCD sync status for external-secrets?

      **Tags:** `troubleshooting`, `kubernetes`, `argocd`

      ### Deployment & Onboarding Guidance (`deployment-guidance`)

      Recommend how to onboard a new app following the established base-apps patterns (Crossplane composition, SecretStore, ingress, ECR auth).

      **Examples:**
      - I want to deploy a new service called billing-api. What's the right pattern?
      - How do I add Vault secrets for a new namespace?

      **Tags:** `onboarding`, `crossplane`, `idp`

      ## Delegates to

      This agent can delegate tasks to the following agents:
      - **k8s-agent** — Kubernetes cluster operations (pods, deployments, RBAC, troubleshooting)
      - **helm-agent** — Helm release lifecycle (install/upgrade/rollback, chart inspection)

      ## Configuration

      | Setting | Value |
      |---|---|
      | Model | `default-model-config` |
      | Memory model | `embedding-model-config` |
      | Compaction interval | 5 turns |
      | Compaction overlap | 2 turns |
      | CPU | 100m / 1000m (req/lim) |
      | Memory | 256Mi / 1Gi (req/lim) |
      | Built-in prompts | included |

      ## Manage

      - **Edit:** hand-edit `base-apps/kagent/agents/homelab-knowledge.yaml` and open a PR
      - **Decommission:** use the **Decommission Kagent Agent** template in Backstage
```

The content mirrors what the scaffolder would now generate for this agent, with one difference: the systemMessage's `{{include "builtin/..."}}` directives stay in the raw text — they're for the kagent prompt processor, not for Markdown.

- [ ] **Step 3: Validate the YAML still parses**

```bash
node -e "require('js-yaml').load(require('fs').readFileSync('base-apps/kagent/agents/homelab-knowledge.yaml','utf-8'))" && echo OK
```

Expected: prints `OK`. If the YAML is malformed, common causes:
- Indent inside the literal block isn't uniform 6 spaces
- A line in the Markdown content starts with `<` followed by something that triggers a YAML tag interpretation (unlikely with our content but possible)

- [ ] **Step 4: Commit + push + open PR**

```bash
git add base-apps/kagent/agents/homelab-knowledge.yaml
git commit -m "feat(kagent): backfill kagent-about annotation on homelab-knowledge

One-time backfill of the arigsela.com/kagent-about annotation introduced
in IDP v1.7. Adds the same Markdown summary the scaffolder now renders for
new agents, so the existing homelab-knowledge agent gets the rich entity
page card without needing to be decommissioned + re-scaffolded.

Companion spec: docs/superpowers/specs/2026-05-18-kagent-idp-v1.7-design.md"

git push -u origin feat/kagent-v1.7-backfill-homelab-knowledge
```

Then open a PR against `main` via `gh pr create` or the GitHub MCP server.

---

## Phase 4 — End-to-end smoke tests

### Task 4: Verify the card renders correctly on `homelab-knowledge`

**Prerequisites:**
- Task 1 PR merged + Backstage image rebuilt + redeployed
- Task 2 PR merged + Backstage image rebuilt + redeployed (or merged together with Task 1's PR)
- Task 3 PR merged + ArgoCD reconciled

- [ ] **Step 1: Confirm the annotation reached the live Agent CRD**

```bash
kubectl get agent -n kagent homelab-knowledge -o jsonpath='{.metadata.annotations.arigsela\.com/kagent-about}' | head -20
```

Expected: prints the first 20 lines of the Markdown body (heading, description, "## Purpose", systemMessage content).

If nothing prints:
- `kubectl get agent -n kagent homelab-knowledge -o yaml | grep -A 5 "kagent-about"` to verify the annotation exists in the YAML
- If the annotation isn't there, check ArgoCD synced the backfill PR

- [ ] **Step 2: Confirm the annotation reached the Backstage catalog**

From inside the running Backstage pod:

```bash
kubectl exec -n backstage <pod-name> -- node -e \
  "fetch('http://localhost:7007/api/catalog/entities/by-name/component/default/homelab-knowledge').then(r=>r.json()).then(e=>console.log('annotation present:', 'arigsela.com/kagent-about' in (e.metadata.annotations||{})))"
```

Expected: `annotation present: true`.

If `false`:
- TeraSky kubernetes-ingestor takes ~30-120s after the K8s annotation appears. Wait + re-try.
- If still false after 5 min, verify the entity ingestion: `kubectl exec ... -- node -e "fetch('http://localhost:7007/api/catalog/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entityRef:'component:default/homelab-knowledge'})}).then(r=>console.log(r.status))"`

- [ ] **Step 3: Visual check in the browser**

Open `https://backstage.arigsela.com/catalog/default/component/homelab-knowledge` in a browser.

Expected on the **Overview** tab:
- The existing "About" card (name, description, owner) at the top
- A new "**About this agent**" card below, full width
- Renders headings (`#`, `##`, `###`) as styled headings
- Renders the configuration table with proper borders
- Renders code blocks (the `default-model-config` etc. in backticks) as monospace
- Renders skill examples as bulleted lists
- The "Manage" section's bullets are visible

If the card is missing or the layout is broken:
- Browser dev tools → check for React errors in console
- If `MarkdownContent` is showing raw `#` characters, the package version might not support GFM tables; the table still renders but with pipe characters visible. Consider noting as a minor cosmetic issue.

- [ ] **Step 4: Negative check — non-kagent entity has no card**

Open any other Component entity, e.g. `https://backstage.arigsela.com/catalog/default/component/example-website` or any `service` type component.

Expected: NO "About this agent" card. The page renders unchanged from before.

- [ ] **Step 5: Negative check — empty-annotation behavior**

(Skip this if Task 3 backfill already merged.)

Browse to `homelab-knowledge` **before** the backfill PR is merged.

Expected: NO "About this agent" card (annotation is missing → `EntitySwitch.Case` returns `null`). The page renders without errors.

- [ ] **Step 6: Document any issues found**

If everything passes, the rollout is complete. If anything's off, capture screenshots + console errors and open a follow-up issue against the Backstage repo.

---

## Done criteria

- All 3 PRs merged: Task 1 (Backstage EntityPage), Task 2 (scaffolder template), Task 3 (backfill).
- Backstage image rebuilt and redeployed at least once after Task 1 + Task 2 merge.
- `homelab-knowledge` entity page shows the "About this agent" card with all sections rendering correctly.
- A new agent created via the IDP wizard (after this rollout) automatically gets the card without manual backfill.

## Known limitations carried from the spec

1. **Hardcoded `DELEGATE_DESCRIPTIONS` lookup.** When a new built-in agent is enabled in the kagent Helm values AND added to the wizard's `delegateAgents` enum, the TypeScript lookup in `EntityPage.tsx` needs a matching entry. The fallback rendered text is `(see kagent docs)`.
2. **No Docs tab content.** The card lives on Overview; the entity's `/docs` tab stays empty until a future iteration adds TechDocs scaffolding.
3. **Network call per page load.** The card makes one fetch through the K8s plugin proxy each time a kagent-agent entity page is opened. Backstage caches at the plugin layer; in practice the latency is invisible.
4. **Cluster name hardcoded.** The card calls `kubernetesApi.proxy({clusterName: 'homelab', ...})`. A multi-cluster deployment would need to read the cluster name from the entity (e.g. from `backstage.io/kubernetes-cluster` annotation or `tags: ['cluster:...']`).

## Findings from production deployment

> ⚠️ **Tasks 1–4 above describe the ORIGINAL annotation-baked design
> that DID NOT WORK in production.** They are kept verbatim as a
> historical record of what we tried first. If you're re-running this
> plan today, read this Findings section first, then **execute against
> the shipped code in commit `d1f194b` of `arigsela/backstage`** plus
> the `backstage-kagent-read` ClusterRole shipped in
> `arigsela/kubernetes` PR #285 — both are referenced below.

During execution we discovered three blockers that forced a complete
pivot from the spec's original "annotation baked at scaffold time"
approach to a "live K8s fetch from the card" approach. For the
historical record + so future template work avoids the same trap,
here's what went wrong:

### Finding 1: kagent controller filters multi-line annotations

We added `arigsela.com/kagent-about` (originally) then
`terasky.backstage.io/kagent-about` (after a rename) to the Agent CRD
via the scaffolder template. Both annotations carried ~100 lines of
Markdown as YAML literal block (`|`).

- The annotation **was** on the Agent CRD (confirmed via
  `kubectl get agent -n kagent <name> -o yaml`).
- The annotation **was NOT** on the Deployment that kagent's controller
  spawned (confirmed via `kubectl get deploy -n kagent <name> -o yaml`).
- TeraSky's `kubernetes-ingestor` reads annotations from the
  **Deployment** (not the Agent CRD), so the Backstage entity ended up
  with no `kagent-about` annotation, and the card had nothing to render.

Short `terasky.backstage.io/*` annotations DID propagate
(`add-to-catalog`, `component-type` made it through). The filter is
most likely length/content-based, not namespace-based. Fixing it would
require patching kagent's reconciler — out of scope.

**Lesson:** GitOps-set annotations on a kagent Agent CRD that contain
multi-line values WILL NOT reach the Backstage entity. Use short
single-line annotations only.

### Finding 2: TeraSky `kubernetes-resource-*` annotations point at the workload

When the card's pivot to live-fetch design landed, the first version
checked `terasky.backstage.io/kubernetes-resource-api-version === 'kagent.dev/v1alpha2'`
to decide whether to fetch. The check always failed because TeraSky
sets these annotations from the **workload** (the Deployment), not the
source Agent CRD:

```
terasky.backstage.io/kubernetes-resource-api-version = apps/v1
terasky.backstage.io/kubernetes-resource-kind        = Deployment
```

The `name` and `namespace` are correct (they match by IDP convention),
but `api-version` and `kind` describe the Deployment.

**Lesson:** Code that wants to fetch the Agent CRD from Backstage must
**hardcode** the kagent API path. Use the annotation's `name` and
`namespace` for those values; ignore its `api-version` and `kind`.

### Finding 3: Custom CRD fetches via Backstage proxy need explicit RBAC

The Backstage ServiceAccount has RBAC for standard k8s resources
(`backstage-read-only`) and Crossplane (`backstage-crossplane-read`),
but NO access to `kagent.dev/*`. The K8s plugin proxy returns 403,
which Backstage's UI surfaces as a misleading **502 toast** ("bad
gateway from upstream").

**Lesson:** A new ClusterRole + ClusterRoleBinding granting the
Backstage SA `get/list/watch` on the relevant CRDs is REQUIRED for any
custom-CRD entity-page card. The fix is captured in Task 3 (RBAC)
above; if you re-run this plan, ship Tasks 1–3 together.

### Finding 4: The pivot is the durable pattern

For any future Backstage entity-page card backed by data that lives in
a custom K8s CRD, **use the live-fetch pattern** (Task 1's
`KagentAboutCardContent` is the template). Do not try to bake the
content into an annotation at scaffold time. The pivot has two bonus
properties beyond just working:

- Hand-edits to the source CRD (`spec.declarative.systemMessage`)
  reflect in the entity card immediately after ArgoCD syncs — no
  re-scaffold cycle needed.
- The entity YAML stays clean (no ~100-line annotation blocks).

**Lesson:** "Bake content into an annotation" is a tempting shortcut
when you don't want a network call. For workloads spawned by a
controller (kagent, Crossplane Composition Functions, etc.), the
annotation chain is fragile. Live fetch is more code (~80 LOC) but
much more reliable.

### Cycle PRs (for archaeology)

The execution cycle produced more PRs than the spec anticipated. For
future debugging, here's the timeline:

| PR | Repo | Purpose | Outcome |
|---|---|---|---|
| arigsela/backstage#20 | backstage | Card + scaffolder (4 iterations) | Merged — final commit `d1f194b` |
| arigsela/kubernetes#282 | kubernetes | Original annotation backfill | Merged, later proved dead |
| arigsela/kubernetes#283 | kubernetes | Rename annotation namespace | Merged, also dead |
| arigsela/kubernetes#284 | kubernetes | Remove dead annotation | Merged (cleanup) |
| arigsela/kubernetes#285 | kubernetes | `backstage-kagent-read` RBAC | Merged — the unblocker |
