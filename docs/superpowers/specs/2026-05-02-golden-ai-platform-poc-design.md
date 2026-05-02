# Golden AI Platform POC — Design

**Date:** 2026-05-02
**Status:** Draft, pending user review
**Audience for the demo:** Mixed exec + engineering leadership at Golden
**Source presentation:** `/Users/arisela/Designing AI platform/golden-ai-platform-presentation-source.md` (28 slides + 4 appendix)

## 1. Overview

This document specifies a Proof of Concept (POC) demonstrating the AI platform pitched in the Golden source presentation. The POC is designed to be a credible, demo-able artifact running on the existing arigsela homelab k3s cluster, exercising the engineer-facing journey: from Backstage Software Template through Crossplane Composition through kagent runtime, with skills served from an in-cluster solo.io agentregistry, LLM traffic flowing through agentgateway, and traces captured in Langfuse.

The POC is not the full 18-month roadmap from the deck. It is a tight 30-minute demo narrative built from the deck's slide 12 ("Backstage as front door"), slide 14 (kagent), slide 16 (skills distribution), slide 17 (Runbook Agent as the day-1 agent), and slide 25 (the platform flywheel).

### Goals

- Land the **engineer journey** end to end: form fill in Backstage → PR → ArgoCD → Crossplane → kagent runtime → Slack response and inline GitHub PR review.
- Demonstrate the **complete solo.io agentic stack** (kagent + agentregistry + agentgateway) in one cluster.
- Ship two visible agents: **Cluster Health Agent** (pre-baked, mirrors slide 17 Runbook Agent) and **PR Review Agent** (live-built during the demo to prove the platform leverage from slide 25).
- Use Crossplane v2 namespaced XRs as the abstraction layer, mirroring the existing `XApplication` pattern.

### Non-goals

- Customer-facing AI surfaces (deck Phase 4+).
- Full Bedrock integration with HIPAA path (deck slide 13). Anthropic API direct is used for the demo; production routing is a design-doc note.
- Multi-tenant isolation patterns at full deck-fidelity (deck slide 13). Single-namespace POC is sufficient.
- Per-agent cost dashboards, eval harnesses, governance routing in Backstage (deck Level 3 polish).

## 2. Demo narrative (30 minutes)

The demo walks through one storyline: an engineer ships a new agent through the platform, while another agent is already running.

1. **Open Backstage.** Show the Catalog with the Agents tab. Cluster Health Agent already lives there — running, with recent invocations visible, a "Try it" button, and a link to its Langfuse traces.
2. **Click "Try it" on Cluster Health Agent.** Live response in a chat sidecar. Open Langfuse, show the trace.
3. **Build agent #2 live.** Backstage → "Create New Agent" template → fill the form (~7 fields) for the PR Review Agent. Click Create.
4. **Show the PR.** Backstage opened a PR against `arigsela/kubernetes` adding `base-apps/agents/pr-review/agent.yaml` and `catalog-info.yaml`. Walk through the agent.yaml: ~25 lines, no Kubernetes machinery exposed.
5. **Merge the PR.** ArgoCD picks up within ~30s. Agent appears in the Backstage catalog. Show Crossplane reconciling the Composition (kagent Agent CR, ToolServer CRs, ExternalSecret, Service, Ingress).
6. **Open a real GitHub PR** on `arigsela/kubernetes` (queued ahead of time, contains an intentionally risky change). Webhook fires. PR Review Agent posts inline review comments. Pull the trace up in Langfuse.
7. **Close with the "Browse Skills" tab in Backstage** — embedded agentregistry catalog. "This is where the next agent's skills come from."

### Demo-risk fallbacks

- If GitHub webhook breaks: invoke PR Review Agent via curl against its HTTP endpoint, show the same output.
- If Slack breaks for Cluster Health: fall through to the "Try it" button or curl.
- If agentregistry's UI breaks: fall through to `arctl list` in a terminal.
- The "Try it" button is the load-bearing demo moment; it does not depend on Slack or GitHub, only on the cluster being up.

## 3. Reference architecture

```
Engineer
  |
  v
Backstage (existing)
  +-- Software Template "Create New Agent"  --> PR to arigsela/kubernetes
  +-- Catalog: Agents (auto-discovered via TeraSky annotations)
  +-- Per-agent page: status, Try-it card, Langfuse link
  +-- Skills tab (embedded agentregistry plugin)
                            |
                            v
                        ArgoCD (existing)
                            | syncs base-apps/agents/<name>/*.yaml
                            v
                        XAgent CR (Crossplane v2 namespaced XR, new)
                            |
                            v
                Crossplane Composition (function-python, new)
                    renders:
                        +-- kagent Agent CR
                        +-- kagent ToolServer CR(s)
                        +-- ExternalSecret (surface-specific creds, when needed)
                        +-- Service + Ingress (always)
                        +-- TeraSky/Backstage labels and annotations
                            |
                            v
                kagent (existing) runs the Agent
                            |
              +-------------+--------------+
              v                            v
        agentgateway (new)            Langfuse (new)
        all LLM traffic               OTLP trace ingest
              |
              v
        Anthropic API
        (production: Bedrock)


agentregistry (new) <-- kagent pulls skill artifacts (OCI)
   | (in-cluster Helm release; UI on 12121)
   v
   Skill artifacts (kubernetes-mcp, prometheus-mcp, github-mcp, k8s-yaml-lint)


slack-adapter (new shared service)            github-webhook-adapter (new shared service)
   watches XAgents with surface: slack            watches XAgents with surface: github-webhook
   --> agent HTTP endpoints                       --> agent HTTP endpoints
```

### What's new vs. reused

**New:**
- `XAgent` XRD + Composition + function-python script
- `base-apps/agentregistry/`, `base-apps/agentgateway/`, `base-apps/langfuse/` Helm releases
- `base-apps/slack-adapter/` and `base-apps/github-webhook-adapter/` shared services
- Backstage Software Template and per-agent page custom plugin (Try-it card)
- Backstage embedded skill catalog plugin
- Two demo agents (`cluster-health`, `pr-review`)
- One custom skill (`k8s-yaml-lint`)

**Reused:**
- Backstage (existing deployment)
- ArgoCD (existing master-app pattern)
- Crossplane v2 with function-python (existing setup)
- Vault + External Secrets (existing per-namespace SecretStore pattern)
- kagent (existing install)
- TeraSky annotation pattern (`backstage.io/kubernetes-id`, `terasky.backstage.io/*`)
- nginx-ingress, cert-manager, Coroot

## 4. XAgent XR and Composition

### XAgent — engineer-facing API

The XAgent XR follows the same intent-only philosophy as the existing `XApplication`: the engineer specifies *what* the agent should do, not *how*. The Composition picks the model, gateway, observability, secrets, and RBAC.

```yaml
apiVersion: platform.arigsela.com/v1alpha1
kind: XAgent
metadata:
  name: pr-review
  namespace: agents
  annotations:
    platform.arigsela.com/github-repo: arigsela/kubernetes
spec:
  description: "Posts inline review comments on opened PRs with risk callouts"
  systemPrompt: |
    You are a PR Review Agent for arigsela/kubernetes...
  skills:
    - ref: oci://agentregistry.agentregistry.svc/skills/github-mcp:v1
      alias: github
    - ref: oci://agentregistry.agentregistry.svc/skills/k8s-yaml-lint:v1
      alias: lint
  surface: github-webhook
  # model: claude-sonnet  # platform default; override only when needed
```

### XRD definition

Lives at `base-apps/crossplane-compositions/xrd-agent.yaml`. Mirrors `xrd-application.yaml`:

- `apiVersion: apiextensions.crossplane.io/v2`
- `scope: Namespaced`
- `group: platform.arigsela.com`
- `kind: XAgent`, `plural: xagents`
- `defaultCompositionRef: { name: agent }`
- `versions: [{ name: v1alpha1, served: true, referenceable: true }]`

Required fields: `description`, `systemPrompt`, `skills` (array, minItems: 1), `surface` (enum: `slack | http | mcp | github-webhook`).

Optional fields: `model` (default `claude-sonnet`).

`skills[].ref` validation pattern: must start with `oci://`.

### Composition

Single-step pipeline at `base-apps/crossplane-compositions/composition-agent.yaml`, using `function-python` (same function used by the XApplication Composition).

Per `XAgent`, the Composition renders:

1. **`kagent.dev/v1alpha1` Agent CR** — name, system prompt, model endpoint pointing at `https://gateway.agentgateway.svc/v1/messages` (NOT a direct Anthropic key), and skill ToolServer references.
2. **`kagent.dev/v1alpha1` ToolServer CR(s)** — one per unique skill `ref`. Each points at an agentregistry OCI URI. (See open question 1 in Section 11 about whether kagent's ToolServer CRD natively accepts OCI refs or needs a shim.)
3. **`ExternalSecret`** — only when the surface requires surface-specific credentials:
   - `surface: slack` → Slack bot token (read from `<namespace>/agents/<name>` in Vault)
   - `surface: github-webhook` → GitHub App private key + app ID
   - `surface: http` or `mcp` → no per-agent secret rendered
4. **`Service` + `Ingress`** — always rendered. Provides the HTTP endpoint for the "Try it" button and the curl fallback for any surface.
5. **TeraSky/Backstage labels and annotations** — same pattern as XApplication: `app.kubernetes.io/name`, `app.kubernetes.io/managed-by: crossplane`, `backstage.io/kubernetes-id`, plus carried-through `terasky.backstage.io/*` annotations.

### What the Composition does NOT render

- The Slack adapter or GitHub webhook adapter. These are shared cluster services that watch XAgent resources via the Kubernetes API and route accordingly.
- LLM credentials. agentgateway holds the Anthropic API key.
- Langfuse trace configuration. kagent emits OTLP at install-time configuration; per-agent setup is not required.
- Skill ServiceAccount RBAC. RBAC is pre-created at install time per skill; the Composition wires the agent's ServiceAccount to the appropriate existing RoleBinding by name.

## 5. Backstage UX (Level 2 polish)

### Software Template

`base-apps/backstage/templates/create-agent/`. A standard `scaffolder.backstage.io/v1beta3` Template, three-step form:

- **Identity:** `name` (kebab-case validated), `namespace` (dropdown of agent namespaces), `owner` (`OwnerPicker` widget).
- **Behavior:** `description` (single line), `systemPrompt` (textarea).
- **Skills + Surface:** `skills` (array, `SkillPicker` custom widget hitting agentregistry's HTTP API; falls back to plain text inputs), `surface` (radio: `slack | http | mcp | github-webhook`).

Two scaffolder steps:
1. `fetch:template` against a Jinja skeleton that renders **two files**: `base-apps/agents/<name>/agent.yaml` (the XAgent) and `base-apps/agents/<name>/catalog-info.yaml` (the Backstage Component definition).
2. `publish:github:pull-request` → opens a PR against `arigsela/kubernetes` on branch `agent/<name>`.

Output gives the engineer a link to the PR. End-to-end "Create" click to PR-open ~5 seconds.

### Catalog auto-discovery

The `catalog-info.yaml` rendered by the template registers the agent as a Backstage Component of type `agent`. The Kubernetes plugin (already configured) auto-discovers the rendered child resources via the TeraSky `backstage.io/kubernetes-id` label and links them to the Component.

The agent appears in the catalog approximately one minute after PR merge (ArgoCD sync + Crossplane reconcile + Backstage Kubernetes plugin refresh).

### Per-agent page (Level 2 polish)

`catalog-info.yaml` template renders these annotations:

```yaml
annotations:
  kagent.dev/agent-name: <name>
  langfuse.platform.arigsela.com/project: golden-poc
  agent.platform.arigsela.com/try-url: https://<name>.agents.<base-domain>/v1/messages
```

A small custom Backstage frontend plugin reads these annotations and renders a card on the agent's page with:

- **Status** — from the Kubernetes plugin (running / failed / progressing).
- **Recent Invocations** — queries Langfuse REST API filtered by `tags: [<name>]`. Last 10 invocations: timestamp, duration, token count, click-through to full trace.
- **"Try it" chat sidecar** — small React component that POSTs to the `try-url`, streams the response. The demo's most reliable visual moment.
- **Quick links** — kagent UI for this agent, ArgoCD app, Langfuse project filter.

### Authentication on the agent's HTTP endpoint

For the POC, the agent Ingress restricts source-IP to the cluster network and the Backstage pod IP. The "Try it" button proxies through Backstage's backend, which holds a shared bearer token. Production needs proper auth (per-user OIDC, agent-scoped tokens) — this is explicitly POC-deferred.

### Embedded skill catalog (`/skills` route)

A second small Backstage frontend plugin: a top-level page at `/skills` that:

- Fetches `https://agentregistry.<base-domain>/api/v1/artifacts` (exact endpoint pending agentregistry code-read, see open question 2 in Section 11).
- Filters by type (skill, MCP server, agent, prompt — agentregistry's four artifact types) and tags.
- Per-skill detail view: description, latest version, dependencies, copy-pasteable `oci://` ref.
- Linked from the Software Template's SkillPicker so engineers can browse → pick → fill.

## 6. Platform infrastructure

### `base-apps/agentregistry/`

- Helm chart from solo.io's `agentregistry-dev/agentregistry`. Pin v0.3.x exact version (pre-1.0).
- Namespace: `agentregistry`.
- Components: server (REST API + web UI on port 12121), OCI proxy, persistent volume for artifact storage.
- Auth: internal-only for POC. Admin token in Vault → ExternalSecret.
- Seed via one-shot Job that runs `arctl push` for the four POC skills.
- UI exposed via Ingress at `agentregistry.<base-domain>` so the embedded Backstage plugin can reach it through Backstage's backend, and so the demo fallback ("open agentregistry's UI in a browser tab") works.

### `base-apps/agentgateway/`

- Helm chart from solo.io's [agentgateway](https://agentgateway.dev) project.
- Namespace: `agentgateway`.
- Configuration:
  - Upstream: `api.anthropic.com`. (Production routes to Bedrock for HIPAA paths — design-doc note, not POC scope.)
  - Auth: `ANTHROPIC_API_KEY` in Vault → ExternalSecret. Agents never see the key.
  - Internal endpoint: `https://gateway.agentgateway.svc/v1/messages`.
  - Rate limit policy: 10 req/s/agent (POC; demonstrates the cost-control story).
  - Request/response logging to stdout.

### `base-apps/langfuse/`

- Langfuse self-host Helm chart.
- Namespace: `langfuse`.
- Backend: CNPG-managed Postgres (reuse existing CNPG pattern from XApplication's `dbNeeded: true`). Single Cluster instance.
- Single Langfuse project: `golden-poc`. Public + secret keys generated at install, stored in Vault.
- Web UI exposed via Ingress at `langfuse.<base-domain>`.

### kagent reconfiguration

Update existing `base-apps/kagent` configmap/values:

- Default LLM endpoint → `https://gateway.agentgateway.svc/v1/messages`.
- OTLP trace export → Langfuse OTLP ingest endpoint, with project keys via ExternalSecret.

Single PR, then every kagent agent inherits both behaviors.

### `base-apps/slack-adapter/`

Small Go service (~250 lines):

- Watches XAgent resources with `surface: slack` via Kubernetes informer.
- Builds `<slash-command, agent-namespace/name>` map on startup and on XAgent changes.
- Subscribes to Slack Events API for `app_mention` events.
- On message: parses the command, POSTs message to that agent's HTTP endpoint, posts the response back to the Slack thread.
- Needs `SLACK_BOT_TOKEN` from Vault.
- Single Deployment, 1 replica.

### `base-apps/github-webhook-adapter/`

Small Go service (~300 lines):

- Watches XAgent resources with `surface: github-webhook` via Kubernetes informer.
- Each XAgent has annotation `platform.arigsela.com/github-repo: arigsela/<repo>` indicating which repo it serves.
- HTTP endpoint receives GitHub webhook deliveries (PR opened/synchronize). Verifies signature, looks up the matching agent, POSTs PR diff + context to it, parses response, posts inline review comments via GitHub API.
- Auth: GitHub App credentials (`APP_ID`, `PRIVATE_KEY`) from Vault.
- GitHub App setup is the most admin-heavy single piece of the POC (~30 minutes of GitHub UI work). Done once during Phase 0.

## 7. Demo agents

### Agent 1: Cluster Health Agent (pre-baked)

```yaml
apiVersion: platform.arigsela.com/v1alpha1
kind: XAgent
metadata:
  name: cluster-health
  namespace: agents
spec:
  description: "Reports cluster/namespace status: pod readiness, events, recent deploys, top noisy pods. Read-only."
  systemPrompt: |
    You are the Cluster Health Agent. On request, gather for the named namespace
    (or cluster-wide if none specified):
      - Pod readiness counts
      - Events in the last hour, grouped by reason
      - Deployments in the last 24h
      - Top 5 pods by CPU and by memory
    Respond with brief, skimmable Markdown. Lead with anything actionable
    (CrashLoopBackOff, OOMKilled, ImagePullBackOff). If everything is normal, say so plainly.
    You are read-only — do not take any action.
  skills:
    - ref: oci://agentregistry.agentregistry.svc/skills/kubernetes-mcp:v1
      alias: k8s
    - ref: oci://agentregistry.agentregistry.svc/skills/prometheus-mcp:v1
      alias: prom
  surface: slack
```

**Invocation flow:**
1. `@goldenai cluster-health agents` in Slack
2. slack-adapter routes to the agent's HTTP endpoint
3. Agent calls `kubernetes-mcp` (kubectl-style reads) and `prometheus-mcp` (or Coroot equivalent)
4. LLM synthesizes Markdown, slack-adapter posts to thread

**Sample output (demo target):**

```
**Namespace `agents`** — 5 pods running

- Ready: 5/5  - Events (1h): none  - Recent deploys (24h): pr-review-agent (just now!)
Top by CPU: cluster-health-agent (8m), pr-review-agent (5m), ...
Cluster looks healthy.
```

The "pr-review-agent (just now!)" line is a designed-in callback to the live-build moment from the demo narrative.

**RBAC:** ServiceAccount with cluster-wide read-only Role (get/list on pods, events, deployments, namespaces). Pre-created at install time. Composition wires the agent's SA to the existing RoleBinding by name.

### Agent 2: PR Review Agent (live-built during the demo)

```yaml
apiVersion: platform.arigsela.com/v1alpha1
kind: XAgent
metadata:
  name: pr-review
  namespace: agents
  annotations:
    platform.arigsela.com/github-repo: arigsela/kubernetes
spec:
  description: "Posts inline review comments on opened PRs with risk callouts."
  systemPrompt: |
    You are the PR Review Agent for arigsela/kubernetes. For each PR diff:

    Categorize risks as:
      CRITICAL — secret exposure, mass deletion, prod-affecting RBAC widening
      HIGH     — CRD removals, broad RBAC changes, removed health checks,
                 image:latest, forceDestroy on persistent storage
      MEDIUM   — replicas reduced below 2, removed resource limits, namespace changes
      LOW      — style, comments, cosmetic

    For each risk, post an inline review comment on the affected lines with severity
    prefix, one-line explanation, and one-line suggested fix. If no risks, post a
    single approving overall comment. Do NOT approve or merge — you are advisory only.
  skills:
    - ref: oci://agentregistry.agentregistry.svc/skills/github-mcp:v1
      alias: github
    - ref: oci://agentregistry.agentregistry.svc/skills/k8s-yaml-lint:v1
      alias: lint
  surface: github-webhook
```

**Invocation flow:**

1. Engineer opens a PR on `arigsela/kubernetes` (the demo has a queued PR with intentional risks: `replicas: 1`, removed `livenessProbe`)
2. GitHub fires webhook → github-webhook-adapter
3. Adapter looks up `pr-review` agent (annotation matches repo), POSTs PR context
4. Agent calls `github-mcp` to fetch the full diff, then `k8s-yaml-lint` for structural checks
5. LLM returns line-anchored severity-tagged comments
6. Adapter posts inline review comments via GitHub API

**Demo target output (visible on the PR in GitHub):**

- Line `replicas: 1`: `[MEDIUM] Replicas reduced below 2 — risks downtime during pod replacement. Consider keeping >=2.`
- Removed `livenessProbe` block: `[HIGH] livenessProbe removed — silently broken pods won't be restarted. Restore or justify in PR description.`

**RBAC:** GitHub App permissions (read PRs, write PR comments — no merge, no approve). `k8s-yaml-lint` runs offline.

### Skills seeded into agentregistry for the POC

| Skill | Source | Notes |
|---|---|---|
| `kubernetes-mcp` | upstream (`mcp-server-kubernetes` or similar) | Pulled into agentregistry once at install |
| `prometheus-mcp` | upstream (`mcp-prometheus`) | Coroot equivalent acceptable |
| `github-mcp` | Anthropic's official github MCP server | Standard distribution |
| `k8s-yaml-lint` | **Custom** — ~50 lines Python wrapping `kube-linter` as MCP | The "we built this skill ourselves" callout |

## 8. Phasing

POC build is approximately 7–8 weeks of focused work for one engineer.

| Phase | Weeks | Scope |
|---|---|---|
| **0 — Foundation** | 1 | Install agentregistry + agentgateway + Langfuse. Reconfigure kagent to route through gateway and emit OTLP. Smoke test each in isolation. Set up GitHub App. Resolve the two preflight checks (Section 11). |
| **1 — XAgent + Composition** | 2–3 | XRD + Composition + function-python script. Hand-write a `cluster-health` XAgent YAML, watch it reconcile, hit its HTTP endpoint with curl. End-to-end working *without* Backstage. |
| **2 — Surface adapters** | 4–5 | Slack adapter and GitHub webhook adapter. Cluster Health works in Slack. PR Review works via webhook on a test repo. |
| **3 — Backstage** | 6–7 | Software Template + auto-discovery + per-agent Try-it card. Embedded skill catalog if time permits. By end of Phase 3, the Cluster Health agent must be running continuously so Langfuse accumulates a week of historical traces in time for the demo. |
| **4 — Demo polish** | 8 | Cluster Health agent has now been running for ~1 week and Langfuse shows real history. Rehearse the live build of PR Review. Record a backup video covering the entire 30-minute narrative. |

The first end-to-end agent runs by week 3, *without* Backstage. This derisks the spine of the platform — every later phase layers on a working core.

## 9. Cuttable scope

Drop in this order if timeline tightens:

1. **Embedded skill catalog plugin** (Section 5) — fall back to "open agentregistry's UI in a separate browser tab" during the demo.
2. **Custom `k8s-yaml-lint` skill** (Section 7) — PR Review Agent works with `github-mcp` alone; you lose only the "we built this skill ourselves" callout.
3. **PR Review Agent live-build** (steps 3–6 of the demo narrative) — if webhook plumbing won't cooperate, demo only Cluster Health. Platform story still lands.
4. **Per-agent invocations card** in Backstage — replace with a single "Recent traces in Langfuse" link.

Not cuttable: the XAgent + Composition + "Try it" button. Without those three, this isn't a platform demo.

## 10. Risks

| Risk | Mitigation |
|---|---|
| agentregistry pre-1.0 instability | Pin a specific version; fallback path is hand-written kagent ToolServer CRs that inline MCP server image refs (skipping the registry for that agent). |
| kagent OTLP → Langfuse format mismatch | Verify during Phase 0; if mismatch, drop a small OTel collector in between. |
| GitHub webhook delivery to homelab cluster | Cloudflare Tunnel or smee.io for the demo. Test in Phase 2. |
| Slack Events API setup | Standard pattern but requires public-facing endpoint; reuse existing nginx-ingress + cert-manager. |
| Composition function-python protobuf edge cases (the float-vs-int issue your existing XApplication script flagged) | Copy the casting pattern from your existing compose function. |
| Demo flakiness | Cluster Health pre-baked for >=1 week; curl fallback for every surface; backup recorded video. |
| agentregistry HTTP API surface for the Backstage skill catalog | Start with a hard-coded skill list; only hit the API once it's been verified stable. |
| Live demo connectivity to homelab from the Golden meeting room | Pre-establish VPN; backup is the recorded video. |

## 11. Open questions and preflight checks

These are unknowns that should be resolved during implementation-plan writing, not during build:

1. **kagent's ToolServer CRD shape** — does it natively accept agentregistry OCI refs, or do we need a shim controller that pulls the artifact and feeds kagent a local path? Resolution: ~half-day code-read of the kagent project.
2. **agentregistry's HTTP API** — what endpoints exist for the Skill catalog plugin to consume? Resolution: ~half-day code-read of the agentregistry project.

Both are flagged as Phase 0 prerequisite tasks in the implementation plan.

## 12. Production extensions (deliberately deferred)

These are explicitly documented as POC-deferred, with the framing "in production this would be...":

- **Bedrock for the HIPAA path** (deck slide 13). agentgateway upstream switches from `api.anthropic.com` to a Bedrock endpoint; per-tenant data flows route through this path while non-PHI metadata flows can route to Anthropic.
- **Multi-tenant isolation** (deck slide 13). Namespace-per-tenant patterns; tenant-scoped tool servers; logical scoping enforced in agentgateway middleware.
- **Per-user OIDC auth** on agent HTTP endpoints (Section 5).
- **Per-agent cost dashboards and eval harnesses in Backstage** (deck slide 12 Level 3 polish).
- **Customer-facing AI surfaces** (deck Phase 4+) — the Volunteer Matching Assistant (slide 19) is the canonical example.
- **Production scale-out of agentregistry** beyond the in-cluster Helm release.

## 13. Glossary

- **Agent**: a software component that reasons and takes actions using an LLM and a set of tools.
- **MCP (Model Context Protocol)**: the emerging standard for how agents access tools. Skills in this design are MCP servers.
- **kagent**: Kubernetes-native open-source framework (CNCF) for declaring and running agents as custom resources. Existing in the cluster.
- **agentregistry**: solo.io's sister project to kagent — a registry for the four artifact types (skills, MCP servers, agents, prompts). Pre-1.0 as of this design.
- **agentgateway**: solo.io's HTTP/gRPC proxy for A2A and MCP traffic. Donated to Linux Foundation.
- **Langfuse**: open-source LLM observability with trace UI, eval support, and OTLP ingest.
- **XAgent**: the new Crossplane v2 namespaced XR introduced by this POC.
- **Surface**: the channel by which humans or systems reach an agent (Slack, HTTP, MCP, GitHub webhook).
- **TeraSky annotations**: the existing pattern (`backstage.io/kubernetes-id` and `terasky.backstage.io/*`) for Backstage Kubernetes plugin discovery.

## 14. References

- Source presentation: `/Users/arisela/Designing AI platform/golden-ai-platform-presentation-source.md`
- kagent: https://github.com/kagent-dev/kagent
- agentregistry: https://github.com/agentregistry-dev/agentregistry
- agentgateway: https://agentgateway.dev
- Solo.io press release on Agent Skills: https://www.solo.io/press-releases/agent-skills-kubernetes-ecosystem
- Langfuse: https://langfuse.com
- Backstage Software Templates: https://backstage.io/docs/features/software-templates
- Existing XApplication design: `docs/superpowers/specs/2026-04-28-backstage-crossplane-idp-design.md`
- Existing v1.3 AWS resources design (paused, architectural blocker): `docs/superpowers/specs/2026-05-01-idp-v1.3-aws-resources-design.md`
