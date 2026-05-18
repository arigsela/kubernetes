# Kagent Agent IDP — Design

**Date:** 2026-05-18
**Status:** Draft
**Owner:** group:platform-engineering

## Goal

Add self-service "create" and "decommission" flows for kagent.dev declarative
Agents through the existing Backstage IDP, so that defining a new orchestrator-style
agent does not require a hand-written CRD + manual PR.

## Non-goals

- Edit/update flow. Users edit existing agents by hand-PRing the YAML.
  (Matches the existing `application-template` / `decommission-application` pattern.)
- MCP-server tool support. v1 only supports the "delegate to another kagent Agent"
  tool type — the orchestrator-style pattern that `build-orchestrator.yaml` uses.
- Crossplane XR-based abstraction. We render one Agent CRD per file directly.
- Code-based agents (CrewAI / FastAPI). Those have their own
  `crewai-agent-template` and are out of scope.
- LLM response-quality testing. Agent answers depend on the system message
  and delegated agents; outside the IDP's scope.

## Architecture

### Two scaffolder templates

Both live under `docs/reference/backstage/examples/templates/` and are
registered in `app-config.yaml` alongside the existing templates.

```
docs/reference/backstage/examples/templates/
├── kagent-agent/                   # NEW — create flow
│   ├── template.yaml
│   └── content/
│       └── base-apps/kagent/agents/${{ values.name }}.yaml
└── kagent-agent-decommission/      # NEW — remove flow
    └── template.yaml
```

The `content/` directory mirrors the target repo's path structure, matching
the existing `application-template` pattern. `fetch:template` processes both
filenames and file contents with Nunjucks, so `${{ values.name }}.yaml`
resolves to e.g. `release-coordinator.yaml` at render time.

### Where agents live in this repo

```
base-apps/kagent/
├── build-orchestrator.yaml          # existing hand-crafted, untouched
├── secret-store.yaml                 # existing
├── external-secrets.yaml             # existing
├── embedding-model-config.yaml       # existing
└── agents/                           # NEW — IDP-managed agents only
    ├── chores-knowledge-bot.yaml
    └── release-coordinator.yaml
```

The `agents/` subdirectory hard-isolates IDP-managed manifests from
hand-crafted ones. The decommission action globs only `base-apps/kagent/agents/`
to prevent accidental deletion of `build-orchestrator.yaml` or the secret-store
manifests.

### Why no new ArgoCD app is needed

`base-apps/kagent-secrets.yaml` is an existing ArgoCD Application that syncs
`base-apps/kagent/` recursively. Dropping a new manifest under
`base-apps/kagent/agents/` is auto-synced with `prune: true` and `selfHeal: true`
already enabled.

### Catalog ingestion

Each generated Agent CRD is annotated:

```yaml
metadata:
  annotations:
    terasky.backstage.io/add-to-catalog: "true"
    terasky.backstage.io/component-type: kagent-agent
    backstage.io/managed-by-location: url:https://github.com/arigsela/kubernetes/blob/main/base-apps/kagent/agents/<name>.yaml
    backstage.io/owner: group:platform-engineering   # from wizard
```

TeraSky's `kubernetesIngestor` (already configured in `app-config.yaml` with
`onlyIngestAnnotatedResources: true`) auto-imports these as Backstage Component
entities within ~30–120s. No catalog:register step in the scaffolder is needed.

### Why Option A (per-file YAML) over Crossplane XR

A Crossplane XR with `XKagentAgent` would integrate more uniformly with the
existing `XApplication` flow and TeraSky's auto-template-generation for XRDs.
Rejected for v1 because:

- The work is "render one in-cluster CRD" — no cloud-resource composition,
  no cross-resource lifecycle. Crossplane's value proposition isn't present.
- Adds a runtime dependency (Crossplane Composition machinery) for a job that
  a Nunjucks template handles in a few dozen lines.
- The existing `decommission-application` action is intentionally Crossplane-shaped
  (deletes `base-apps/<name>.yaml` + `base-apps/<name>/`); a separate sibling
  action is cleaner than parameterizing the existing one.

## Create flow

### Wizard parameters

**Page 1 — Identity** (all required)

| Field | Type | Notes |
|---|---|---|
| `name` | string, pattern `^[a-z][a-z0-9-]*$` | Becomes CRD `metadata.name` and filename. Custom validation rejects existing names. |
| `description` | string (textarea, 2 rows) | Shown in catalog and on the CRD. |
| `owner` | `EntityPicker` (Group/User) | Backstage owner annotation on the catalog entity. |

**Page 2 — Behavior**

| Field | Type | Default | Notes |
|---|---|---|---|
| `systemMessage` | textarea (12 rows) | (required) | Can use `{{include "builtin/..."}}` directives if `includeBuiltinPrompts` is true. |
| `includeBuiltinPrompts` | checkbox | `true` | When true, emits `promptTemplate.dataSources` referencing the `kagent-builtin-prompts` ConfigMap. Lets the system message reuse `builtin/kubernetes-context`, `builtin/safety-guardrails`, `builtin/tool-usage-best-practices`. |
| `delegateAgents` | multi-select, at least one | (none) | Enum of agent names: `k8s-agent`, `helm-agent`, `istio-agent`, `kgateway-agent`, `argo-rollouts-conversion-agent`, `observability-agent`. The list is hardcoded in v1 to match the currently-enabled chart agents — adding a new built-in agent later requires editing the template. |

**Page 3 — A2A Skills** (optional, repeating)

For each skill: `id` (kebab, required), `name` (display, required),
`description` (1 sentence, required), `examples` (string array, 3–5 lines),
`tags` (comma-separated string).

Empty list is allowed (skills are not required for the Agent to run, only
for A2A discoverability).

**Page 4 — Resources** (optional, all defaulted)

| Field | Default |
|---|---|
| `cpuRequest` / `cpuLimit` | `100m` / `1000m` |
| `memoryRequest` / `memoryLimit` | `256Mi` / `1Gi` |
| `compactionInterval` | `5` |
| `overlapSize` | `2` |

**Page 5 — Publish**

| Field | Default | Notes |
|---|---|---|
| `dryRun` | `false` | When `true`, writes to `/tmp/backstage-scaffolder/<name>/` instead of opening a PR. Matches the existing `crewai-agent-template` testing convention. |

### Hardcoded (not exposed)

- `spec.type: Declarative`
- `spec.declarative.modelConfig: default-model-config`
- `spec.declarative.memory.modelConfig: embedding-model-config`
- `spec.declarative.runtime: python`
- `spec.declarative.stream: true`
- `metadata.namespace: kagent`
- `metadata.labels["app.kubernetes.io/part-of"]: kagent`
- `metadata.labels["app.kubernetes.io/managed-by"]: backstage-scaffolder`
  (this label is the discriminator the decommission action uses to refuse
  deletion of hand-crafted agents)

### Rendered output example

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: release-coordinator
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
    app.kubernetes.io/managed-by: backstage-scaffolder
  annotations:
    terasky.backstage.io/add-to-catalog: "true"
    terasky.backstage.io/component-type: kagent-agent
    backstage.io/managed-by-location: url:https://github.com/arigsela/kubernetes/blob/main/base-apps/kagent/agents/release-coordinator.yaml
    backstage.io/owner: group:platform-engineering
spec:
  description: Coordinates release activities across helm, argo-rollouts, and k8s domains
  type: Declarative
  declarative:
    modelConfig: default-model-config
    memory:
      modelConfig: embedding-model-config
    runtime: python
    stream: true
    promptTemplate:                       # only if includeBuiltinPrompts
      dataSources:
      - alias: builtin
        kind: ConfigMap
        name: kagent-builtin-prompts
    context:
      compaction:
        compactionInterval: 5
        overlapSize: 2
    systemMessage: |
      You are a release coordinator...
      {{include "builtin/kubernetes-context"}}
      ...
    a2aConfig:                            # only if skills array is non-empty
      skills:
      - id: release-coordination
        name: Release Coordination
        description: Orchestrate helm upgrades, rollouts, and post-deploy verification
        examples:
        - Coordinate the v2.3 release of chores-tracker
        - Verify rollout health after a helm upgrade
        tags:
        - release
        - coordination
    tools:
    - type: Agent
      agent:
        name: helm-agent
    - type: Agent
      agent:
        name: argo-rollouts-conversion-agent
    - type: Agent
      agent:
        name: k8s-agent
    deployment:
      resources:
        requests:
          cpu: 100m
          memory: 256Mi
        limits:
          cpu: 1000m
          memory: 1Gi
```

The Nunjucks template uses `{% if includeBuiltinPrompts %}` and
`{% if skills | length > 0 %}` to avoid emitting empty `promptTemplate:` or
`a2aConfig:` stubs.

### Scaffolder steps (create)

1. **Validate name uniqueness** — custom action `kagent:agent:validate-name`.
   Does GitHub API `GET /repos/.../contents/base-apps/kagent/<name>.yaml`
   AND `GET /repos/.../contents/base-apps/kagent/agents/<name>.yaml`. Fails
   if either returns 200 (file exists). This catches both the hand-crafted
   `build-orchestrator.yaml` collision and any prior IDP-created agent.
2. **Render manifest** — `fetch:template` from `./content` with all wizard
   values. Renders into the workspace at
   `base-apps/kagent/agents/<name>.yaml`.
3. **Open PR** (`if: not parameters.dryRun`) — `publish:github:pull-request`
   to `arigsela/kubernetes`, branch `scaffolder/add-kagent-<name>`, title
   `feat(kagent): add <name> agent`. No `sourcePath` or `targetPath` —
   workspace root maps to repo root (same as `application-template`).
4. **Write to /tmp** (`if: parameters.dryRun`) — `publish:file` to
   `/tmp/backstage-scaffolder/<name>/`.

### Custom scaffolder actions (summary)

Two custom actions, both in `packages/backend/src/modules/scaffolder/`:

| Action ID | Used by | Responsibility |
|---|---|---|
| `kagent:agent:validate-name` | create | Fails the wizard with a clear error if a file with the chosen name already exists in either `base-apps/kagent/` or `base-apps/kagent/agents/`. |
| `kagent:agent:open-decommission-pr` | decommission | Existence check + management-label check + branch + delete + PR (detail below). |

Both actions share a small Octokit helper (`getFileContent(owner, repo, path)`)
to keep the GitHub API access pattern consistent and testable.

### Output links

| Title | URL |
|---|---|
| Pull request | `${{ steps.publish.output.remoteUrl }}` |
| Dry run output | `file:///tmp/backstage-scaffolder/${{ parameters.name }}` |

## Decommission flow

### Wizard parameters

Single page, single field:

| Field | Type | Notes |
|---|---|---|
| `name` | string, pattern `^[a-z][a-z0-9-]*$` | Must match a file under `base-apps/kagent/agents/`. |

### Custom scaffolder action

New file `packages/backend/src/modules/scaffolder/kagentDecommissionAction.ts`,
modeled on the existing `decommissionPullRequestAction.ts` and sharing the
Octokit helper with `kagent:agent:validate-name`. Registered as
`kagent:agent:open-decommission-pr`.

Steps performed via Octokit REST:

1. **Existence check** — `GET .../contents/base-apps/kagent/agents/${name}.yaml`.
   If 404, fail with: *"Agent not found. Either it was already decommissioned
   or it was hand-crafted (only files under `base-apps/kagent/agents/` can be
   torn down via the IDP)."*
2. **Management check** — parse the YAML body and verify
   `metadata.labels["app.kubernetes.io/managed-by"] === "backstage-scaffolder"`.
   If missing or different, fail with: *"Agent is not IDP-managed; tear down
   by hand to avoid removing unrelated files."*
3. **Branch create / reuse** — `scaffolder/decommission-kagent-${name}`,
   based on `main`. Idempotent: if the branch exists, fetch its head SHA.
4. **File delete** — `DELETE .../contents/base-apps/kagent/agents/${name}.yaml`
   on that branch.
5. **PR create / reuse** — title `chore(kagent): decommission agent ${name}`,
   against `main`. If an open PR for the branch exists, return its URL.

### Why not reuse `crossplane:teardown:open-decommission-pr`

The existing action deletes `base-apps/<name>.yaml` *and* `base-apps/<name>/`
(an ArgoCD-app file + a manifests directory). Our shape is different: a single
file in a *shared* directory. Reusing would either over-delete (wiping
`base-apps/kagent/` is a disaster) or require risky parameterization. A
sibling action is safer.

### Post-merge cleanup

Once merged, ArgoCD's `kagent-secrets` app (with `prune: true`) deletes the
Agent CRD from the cluster. The kagent controller tears down the Deployment
and Service automatically. No manual `kubectl delete` step is required.

## Testing

### Create — dry-run scenarios

| Scenario | Inputs | Expected |
|---|---|---|
| Minimal valid agent | name, description, owner, systemMessage, one delegate | YAML with no `promptTemplate`, no `a2aConfig`, default resources, single tool entry |
| Full agent | + builtin prompts on, 2 skills, custom resources, all 6 delegates | YAML with `promptTemplate.dataSources`, `a2aConfig.skills` array, custom CPU/mem, 6 tool entries |
| Invalid name | `Foo-bar` or `1agent` | Wizard validation rejects before submit (pattern regex) |
| Duplicate name | `name: build-orchestrator` | Custom name-validation step fails: *"agent already exists at base-apps/kagent/build-orchestrator.yaml"* |

### Decommission — fault paths

| Scenario | Inputs | Expected |
|---|---|---|
| Non-existent agent | `name: does-not-exist` | Action fails: *"Agent not found."* |
| Hand-crafted agent | `name: build-orchestrator` | Action fails: *"Agent is not IDP-managed."* |
| IDP-managed agent | known good name | Branch + PR created, file removed in the PR diff |
| Re-run after PR is open | same name | Returns the same PR URL (idempotent) |

### End-to-end smoke test (create)

After first IDP-created agent merges to `main`:

1. **ArgoCD sync** — `kagent-secrets` app syncs the new file within ~3 min.
2. **CRD acceptance** — `kubectl get agent -n kagent <name>` shows
   `Accepted=True` and `Ready=True`.
3. **Pod running** — kagent controller spawns a Deployment + Service; pod
   reaches `Running` within ~60s.
4. **Catalog ingestion** — within ~120s, the agent appears in Backstage at
   `/catalog` as a Component.
5. **Functional check** — open `kagent.arigsela.com`, send the new agent a
   test query, verify it delegates to the listed agents.

### End-to-end smoke test (decommission)

1. **PR merge** — manifest removed from `main`.
2. **ArgoCD prune** — within ~3 min, the Agent CRD disappears.
3. **Resource cleanup** — kagent controller removes the Deployment/Service.
4. **Catalog removal** — within ~120s, the Component entity disappears.

### Optional v1.1 improvement

Add `kubectl apply --dry-run=server` validation as a scaffolder step before
the PR is opened. Catches CRD-schema typos at template-creation time instead
of at ArgoCD-sync time. Requires the scaffolder pod to have read access to
the cluster API — a small RBAC addition. Listed as v1.1 because ArgoCD will
catch malformed manifests at sync time anyway, just with worse UX.

## Known limitations

1. **`ignoreDifferences` block in `base-apps/kagent.yaml`** ignores
   `/spec/declarative/memory` for *all* Agent CRDs cluster-wide. IDP-rendered
   agents include the correct `memory.modelConfig` at creation time, but
   ArgoCD will not drift-detect manual edits to it later. Matches current
   behavior for the chart-installed agents; flagged here so future maintainers
   understand the gap.
2. **Pre-existing broken dependency in `observability-agent`** — its
   `tools[].agent.name: promql-agent` references an agent that is disabled
   in the Helm values, producing `Accepted=False` with reason
   `ReconcileFailed`. Unrelated to this work but discovered during the live
   agent survey. Worth raising separately.
3. **Naming mismatch in argo-rollouts agent** — the chart key is
   `argo-rollouts-agent`, but the CRD `metadata.name` is
   `argo-rollouts-conversion-agent`. The wizard's hardcoded `delegateAgents`
   enum must use the CRD name (`argo-rollouts-conversion-agent`), not the
   chart key. The existing `build-orchestrator.yaml` already gets this right.

## Open follow-ups (not v1 scope)

- **Edit flow** — currently users edit by hand-PR. A future template could
  pre-populate the wizard from an existing Agent CRD, but Backstage's
  scaffolder doesn't natively support "edit" UX. Likely needs a custom
  frontend plugin.
- **Dynamic delegate-agents list** — replace the hardcoded enum with a
  custom field extension that calls the K8s API to list `Agent` CRDs.
  Avoids template edits when new built-in agents are enabled.
- **Auto-update build-orchestrator's tools list** — when a new IDP-managed
  agent is created, the user may want it added to `build-orchestrator.yaml`'s
  `tools:` list. v1 leaves this manual.
- **MCP-server tool support** — for agents that need direct tool access
  rather than delegation (i.e., not orchestrator-style). Adds significant
  wizard complexity (`requireApproval` lists, MCP server URLs, tool names).
