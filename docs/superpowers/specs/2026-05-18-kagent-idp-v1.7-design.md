# Kagent IDP v1.7 — Backstage Entity Page Enhancement

**Date:** 2026-05-18
**Status:** Draft
**Owner:** group:platform-engineering

## Goal

Enrich the Backstage entity page for IDP-created kagent agents so that
operators can see the agent's purpose, skills, delegate graph, and
configuration without inspecting the YAML file or leaving the Backstage UI.
Currently kagent agents appear as bare Component entities with no
kagent-specific UI surface beyond the existing Kubernetes tab.

## Non-goals

- Building a first-class Backstage frontend plugin
  (`@arigsela/backstage-plugin-kagent-frontend`) for kagent. Out of scope for
  v1.7; revisit if the simpler card-based approach proves insufficient.
- Real multi-page TechDocs (mkdocs.yml + per-agent docs/ directory). The
  systemMessage + skills are the documentation; TechDocs would require
  scaffolder-generated boilerplate that adds maintenance burden.
- Live data fetching from the K8s API by the card. The card reads a
  pre-rendered annotation; if the systemMessage is hand-edited later, the
  annotation goes stale until the next scaffolder PR. Acceptable trade-off
  for v1.7.
- Adding a dedicated `kagentAgentPage` layout (parallel to `serviceEntityPage`
  / `websiteEntityPage`). We instead extend `defaultEntityPage` with a
  conditional card so we don't duplicate layout JSX.
- Embedded kagent UI iframe / chat widget on the entity page. Out of scope.
- A relationship graph showing which agents delegate to which. Out of scope.

## Architecture

Two repos touched, both lightly. No new ArgoCD apps, no new plugins, no
Helm changes.

### `arigsela/backstage` (the IDP repo)

| File | Change |
|---|---|
| `examples/templates/kagent-agent/content/base-apps/kagent/agents/${{ values.name }}.yaml` | Add one new annotation `arigsela.com/kagent-about` containing pre-rendered Markdown that summarizes the agent. Nunjucks renders inline using the wizard inputs already collected. |
| `packages/app/src/components/catalog/EntityPage.tsx` | Add an `EntitySwitch.Case` for `entity.spec.type === 'kagent-agent'` to the existing `overviewContent` JSX block. When matched, render an `InfoCard` titled "About this agent" containing `MarkdownContent` from the annotation. |

### `arigsela/kubernetes` (this repo)

| File | Change |
|---|---|
| `base-apps/kagent/agents/homelab-knowledge.yaml` | One-time backfill — hand-add the `arigsela.com/kagent-about` annotation with content matching the existing agent's fields. |

### Why the surgical EntityPage edit (vs. a dedicated layout)

Current `componentPage` only branches into `service` and `website` types and
defaults everything else to `defaultEntityPage`. Our kagent agents already
fall through to that default page and we want to keep that (Kubernetes tab,
Docs tab, dependencies tab all stay useful). Adding the card via an
`EntitySwitch.Case` inside `overviewContent` means the card appears ONLY for
kagent agents, but every other Component renders unchanged. ~15 lines of
TSX vs. ~30+ lines for a full duplicate layout.

### Annotation namespace

`arigsela.com/kagent-about` — `arigsela.com` namespace mirrors the homelab's
owned domain, `kagent-about` describes content. Stays clear of the
`kagent.dev/*`, `terasky.backstage.io/*`, and `backstage.io/*` namespaces
which are owned by their respective controllers / plugins.

## The rendered Markdown content

This is what the Nunjucks template renders into the
`arigsela.com/kagent-about` annotation. Designed to read well as a single
Markdown card on the entity page.

```markdown
# <Agent name>

<one-line description from the wizard>

## Purpose

<full systemMessage, properly indented>

## Skills        (only if skills array is non-empty)

### <Skill name> (`<skill-id>`)

<description>

**Examples:**
- <example 1>
- <example 2>

**Tags:** `<tag1>`, `<tag2>`

(repeated per skill)

## Delegates to

This agent can delegate tasks to the following agents:

- **k8s-agent** — Kubernetes cluster operations (pods, deployments, RBAC, troubleshooting)
- **helm-agent** — Helm release lifecycle (install/upgrade/rollback, chart inspection)
- ... (one bullet per entry in `delegateAgents`, with hardcoded description)

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

- **Edit:** hand-edit `base-apps/kagent/agents/<name>.yaml` and open a PR
- **Decommission:** use the **Decommission Kagent Agent** template in Backstage
```

### Section design rationale

- **Purpose = full systemMessage verbatim.** This is the most useful thing
  for someone wanting to understand the agent — it's the same context the
  LLM gets. Can run 50+ lines; that's acceptable as a single InfoCard.
- **Skills** = A2A capabilities — same content the kagent UI uses for
  skill discovery, surfaced in human-readable form.
- **Delegates to** uses a static description lookup so non-experts see what
  `k8s-agent` actually does without leaving the page.
- **Configuration** = quick reference table. Hides the boilerplate
  (`default-model-config` is always the same value); shows the values that
  vary per agent.
- **Manage** links the operator action paths so the page is self-serve.

### Excluded from the card (intentional)

- The full YAML — already viewable via the existing Kubernetes tab →
  Manifest pane.
- ArgoCD info — already on the entity (the `argocd/app-name` annotation
  links to ArgoCD).
- Live pod status — already on the Kubernetes tab.

### Implementation note: Nunjucks indentation pitfall

The v1.6 deployment surfaced a quirk with the `| indent(N)` filter:
combined with a YAML literal block (`|`), it adds extra spaces on
continuation lines (see v1.6 findings doc). The annotation rendered here
faces the same pattern — multi-line `systemMessage` injected into a YAML
literal block via Nunjucks. The implementation plan should use the
cleanest workable approach (likely
`${{ values.systemMessage | replace('\n', '\n      ') }}` rather than
`indent(6)`) so that the Markdown rendered in the card doesn't carry
spurious indentation that the renderer interprets as code blocks.

### Hardcoded delegate-description lookup

A small Nunjucks dict at the top of the content template:

```jinja
{% set delegate_descriptions = {
    "k8s-agent": "Kubernetes cluster operations (pods, deployments, RBAC, troubleshooting)",
    "helm-agent": "Helm release lifecycle (install/upgrade/rollback, chart inspection)",
    "istio-agent": "Istio service-mesh configuration and traffic management",
    "kgateway-agent": "Kubernetes Gateway API (kgateway/Envoy)",
    "argo-rollouts-conversion-agent": "Convert Deployments to Argo Rollouts for progressive delivery",
    "observability-agent": "Prometheus + Grafana metrics and dashboard management"
} %}
```

If a new built-in agent gets added to the wizard's `delegateAgents` enum
later, this lookup needs a matching entry — same file, ~1 line. Acceptable.

## The Backstage EntityPage change

A surgical edit to `packages/app/src/components/catalog/EntityPage.tsx`:

### New import (with existing imports)

```typescript
import { MarkdownContent, InfoCard } from '@backstage/core-components';
```

Both already in `@backstage/core-components` (existing dep). No new
packages needed.

### New helper + content block (near `cicdContent`)

```typescript
const ABOUT_ANNOTATION = 'arigsela.com/kagent-about';

const isKagentAgent = (entity: Entity) =>
  entity?.spec?.type === 'kagent-agent';

const kagentAboutCard = (
  <EntitySwitch>
    <EntitySwitch.Case if={isKagentAgent}>
      {({ entity }: { entity: Entity }) => {
        const about = entity.metadata.annotations?.[ABOUT_ANNOTATION];
        if (!about) return null;
        return (
          <InfoCard title="About this agent">
            <MarkdownContent content={about} />
          </InfoCard>
        );
      }}
    </EntitySwitch.Case>
  </EntitySwitch>
);
```

### Inject the card into the default Component overview

Inside the existing `overviewContent` JSX (the Grid that `defaultEntityPage`
renders for the Overview tab), add one Grid item:

```diff
   <Grid container spacing={3} alignItems="stretch">
     {entityWarningContent}
     <Grid item md={6}>
       <EntityAboutCard variant="gridItem" />
     </Grid>
     <Grid item md={6} xs={12}>
       <EntityCatalogGraphCard variant="gridItem" height={400} />
     </Grid>
+    <Grid item xs={12}>
+      {kagentAboutCard}
+    </Grid>
     ...
   </Grid>
```

### Behavior matrix

| Entity type | Overview | Kubernetes tab | Docs tab |
|---|---|---|---|
| `spec.type=service` | unchanged | unchanged | unchanged |
| `spec.type=kagent-agent` | + "About this agent" card | works (label selector already present) | empty (no techdocs-ref) |
| Anything else | unchanged | unchanged | unchanged |

### Failure modes

- **Annotation missing** (e.g. old agent without backfill): card renders nothing — empty state is silent.
- **Annotation present but malformed Markdown**: `MarkdownContent` renders the raw text as text — graceful degrade.
- **Entity is not kagent-agent**: `EntitySwitch.Case` predicate fails, card never mounts.

## Backfill + coordination

### Backfill strategy

- **New agents** created via the wizard after this change: annotation
  rendered automatically by the scaffolder.
- **Existing agents** created before this change (today, only `homelab-knowledge`):
  one hand-backfill PR per agent. Acceptable because the population is 1.
- **Agents whose systemMessage gets hand-edited later**: the annotation
  goes stale (Option A trade-off documented in the data-flow decision).
  Operator can re-render by editing the annotation in the same PR.

### Coordination across the two repos

1. **Backstage EntityPage change ships first.** Safe on its own — if no
   entity has the annotation, the card renders nothing.
2. **Scaffolder template change ships second.** Once Backstage is deployed
   with the EntityPage edit, new agents from the wizard will render
   correctly.
3. **Backfill PR for `homelab-knowledge` ships any time.** Independent.

Three small PRs, none blocking each other.

## Testing

### 1. Scaffolder dry-run (manual)

Re-run the create wizard from Backstage with a throwaway name and
`dryRun: true`. Inspect the rendered file:

```bash
kubectl exec -n backstage <pod> -- cat \
  /tmp/backstage-scaffolder/<name>/base-apps/kagent/agents/<name>.yaml \
  | grep -A 60 "kagent-about:"
```

Expected:
- The `arigsela.com/kagent-about` annotation is present
- All expected sections render (Purpose, Skills, Delegates to, Configuration, Manage)
- Sections conditional on `skills` array correctly omitted when empty
- Delegate-description lookup populated correctly for each chosen agent
- No raw Nunjucks `{{ }}` or `{%- %}` syntax leaks into the output

### 2. Backstage EntityPage visual smoke test (manual, after deploy)

Once the Backstage image is rebuilt with the EntityPage.tsx change:

| Check | URL | Expected |
|---|---|---|
| kagent-agent entity shows the card | `https://backstage.arigsela.com/catalog/default/component/homelab-knowledge` (after backfill PR merges) | "About this agent" card appears on Overview tab with rendered Markdown |
| Card absent when annotation missing | Same URL **before** backfill PR | No card, no error, no broken layout |
| Card absent on non-kagent entities | Any `service` or `website` component | No card |
| Markdown renders correctly | The kagent agent page | Headings, lists, tables, inline code all visible — no raw `#` or `|` showing |

If all four pass, the EntityPage change is correct.

### 3. End-to-end annotation round-trip (manual, after both deploys)

Confirm the annotation survives the git → ArgoCD → Agent CRD → TeraSky
kubernetes-ingestor → catalog database round-trip:

```bash
kubectl exec -n backstage <pod> -- node -e \
  "fetch('http://localhost:7007/api/catalog/entities?filter=spec.type=kagent-agent').then(r=>r.json()).then(d=>d.forEach(e=>console.log(e.metadata.name, Object.keys(e.metadata.annotations||{}).filter(k=>k.includes('about')))))"
```

Expected: each kagent-agent entity prints its name and at least one
`about`-keyed annotation.

### 4. What we explicitly don't test

- **Unit tests for the EntityPage change.** The change is ~15 lines of TSX
  with no logic beyond reading an annotation. Backstage's existing
  `App.test.tsx` integration test would catch import errors or typos.
- **Cross-agent visual regression.** Would need Playwright/Chromatic
  infrastructure that doesn't exist here today.
- **The annotation Nunjucks template under unit test.** Dry-run covers it.

## Known limitations

1. **Stale annotation on hand-edited YAML** (Option A trade-off). If
   someone changes the `systemMessage` in `base-apps/kagent/agents/<name>.yaml`
   directly without also updating `arigsela.com/kagent-about`, the entity
   card will show the old content. Mitigation: a follow-up could add a
   small CI check that flags PRs touching the systemMessage when the
   annotation hasn't been updated.
2. **Hardcoded `delegate_descriptions` lookup.** When a new built-in agent
   is enabled in the kagent Helm values AND added to the wizard's
   `delegateAgents` enum, the lookup table needs a matching entry. Easy to
   forget; falls back to "(see kagent docs)" if missing.
3. **No Docs tab content.** The existing `/docs` tab on the entity page
   remains empty for kagent agents (no `backstage.io/techdocs-ref` annotation).
   The card is on the Overview tab instead. v1.8 could add real TechDocs
   if needed.

## Open follow-ups (not v1.7 scope)

- **TechDocs scaffold** — wizard checkbox to generate a per-agent `mkdocs.yml`
  + `docs/index.md` for proper multi-page documentation.
- **Delegate graph** — a small SVG showing the A2A delegation tree for the
  current agent and the agents it points to. Would need a custom React
  component but no plugin.
- **Embedded kagent UI iframe** — clickable conversation widget on the
  entity page. Requires auth coordination between Backstage and kagent UI.

## Findings from production deployment

This spec's original design (Option A — bake the Markdown into an
annotation at scaffold time) **did not work**. We had to pivot to a
live-K8s-fetch design (originally called Option B and rejected in
brainstorming as "too much code"). The shipped implementation differs
significantly from what's documented above — these findings explain why
and what changed.

### 1. kagent controller filters annotations when spawning the Deployment

We knew TeraSky's `kubernetes-ingestor` reads from the Deployment that
kagent's controller spawns, not directly from the Agent CRD. We
incorrectly assumed kagent would propagate arbitrary annotations from
the Agent CRD to the spawned Deployment. It doesn't:

- **Short** `terasky.backstage.io/*` and `backstage.io/*` annotations
  DO get propagated (verified: `add-to-catalog`, `component-type`,
  `managed-by-location`, `owner` all appear on both the Agent CRD AND
  the Deployment).
- **Multi-line annotations** (anything with a `|` literal block YAML
  value) are silently dropped. Our `terasky.backstage.io/kagent-about`
  carrying ~100 lines of Markdown never made it onto the Deployment, so
  TeraSky never saw it, so the Backstage entity never carried it.

This is not a namespace filter (we tried `arigsela.com/*` and
`terasky.backstage.io/*` — same result). It's most likely a
length/content filter inside kagent's reconciler. Fixing this would
require forking kagent — out of scope for an IDP enhancement.

### 2. TeraSky's `kubernetes-resource-*` annotations point at the workload, not the source

We naively assumed TeraSky's `terasky.backstage.io/kubernetes-resource-{api-version, kind, name, namespace}`
annotations would point at the Agent CRD because the entity's
`spec.type` is `kagent-agent`. They actually point at the
**workload** (the Deployment that kagent spawned):

```
terasky.backstage.io/kubernetes-resource-api-version = apps/v1
terasky.backstage.io/kubernetes-resource-kind        = Deployment
terasky.backstage.io/kubernetes-resource-name        = homelab-knowledge
terasky.backstage.io/kubernetes-resource-namespace   = kagent
```

The `name` and `namespace` happen to match the Agent CRD by IDP
convention (the Deployment is named after the Agent), but `kind` and
`api-version` describe the Deployment. Code that wants to fetch the
Agent CRD must hardcode the kagent API path (`kagent.dev/v1alpha2/agents`)
rather than reading it from these annotations.

### 3. Custom CRD fetches via Backstage's K8s proxy need explicit RBAC

The Backstage ServiceAccount comes with RBAC for standard k8s resources
(pods, deployments, services via `backstage-read-only`) and Crossplane
resources (via `backstage-crossplane-read`). It has **no** access to
`kagent.dev` types by default. Without a matching ClusterRole +
ClusterRoleBinding, the K8s plugin proxy returns 403, which Backstage's
proxy wrapper surfaces as a 502 toast in the UI.

This wasn't an obvious requirement until we hit it live. The fix is a
new `backstage-kagent-read` ClusterRole granting `get/list/watch` on
`agents`, `modelconfigs`, and `remotemcpservers` in the `kagent.dev`
API group, bound to the `backstage` ServiceAccount in the `backstage`
namespace. Lives in `base-apps/backstage/rbac.yaml` alongside the
existing Crossplane RBAC.

### 4. The card pivot — what actually shipped

After the annotation approach proved impossible, we pivoted to:

- **No annotation** on the Agent CRD (originally added in arigsela/kubernetes
  PR #282, renamed in PR #283, removed in PR #284 once the pivot
  shipped — net change to the Agent CRD shape: zero).
- **No Nunjucks block** in the scaffolder content template (the
  delegate-description lookup, the Markdown body, the indent
  workaround — all removed).
- **EntityPage card now fetches the Agent CRD live** via the K8s
  plugin proxy at render time. The card reads
  `terasky.backstage.io/kubernetes-resource-name` and
  `kubernetes-resource-namespace` from the entity (these survive
  ingestion), hardcodes the kagent API path, and proxies
  `/apis/kagent.dev/v1alpha2/namespaces/<ns>/agents/<name>`.
- **Markdown builder lives in TypeScript** (`buildKagentMarkdown` in
  EntityPage.tsx) — same Markdown structure as the original spec, just
  generated client-side from the fetched `spec.declarative.*` fields.
- **Delegate descriptions** live in a TypeScript `DELEGATE_DESCRIPTIONS`
  constant in the same file. Same maintenance pattern as the original
  Nunjucks lookup, different language.
- **Required RBAC**: `backstage-kagent-read` ClusterRole + Binding.
- **Bonus property** of this approach (not available with annotation):
  hand-edits to `spec.declarative.systemMessage` in the GitOps repo
  reflect in the entity card **immediately** after ArgoCD syncs. No
  re-scaffold cycle needed for refresh.

### What this means for re-running this plan from scratch

If you re-execute the v1.7 work today, **do not** add an annotation to
the scaffolder template. **Do** implement the EntityPage card as a
fetch-based component reading `terasky.backstage.io/kubernetes-resource-*`
annotations (for name + namespace), and **do** ship the
`backstage-kagent-read` ClusterRole as part of the same rollout.

The companion plan file (`docs/superpowers/plans/2026-05-18-kagent-idp-v1.7.md`)
still has its original task structure — those tasks describe the
**failed** annotation-baked approach as a historical record, with a
prominent warning at the top of the Findings section pointing readers
to the shipped code. The shipped code is in:

- `arigsela/backstage` commit `d1f194b` (EntityPage.tsx +
  examples/templates/kagent-agent/content/...)
- `arigsela/kubernetes` PR #285 (`base-apps/backstage/rbac.yaml`)

### Coordination order for the corrected design

1. Open PR with: EntityPage.tsx card + scaffolder template (no annotation block)
2. Open PR with: `base-apps/backstage/rbac.yaml` ClusterRole + Binding
3. Build + deploy the Backstage image from PR #1
4. Merge PR #2 → ArgoCD syncs the RBAC
5. Visit a kagent-agent entity page → card renders live data

No backfill of existing agents needed (data is read from the live CRD).
