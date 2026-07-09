# Phase 2 — kagent Agent-Docs Retrieval — Design

- **Date:** 2026-07-09
- **Status:** Approved (design); implementation plan to follow
- **Depends on:** the agent-ready docs framework (`docs/superpowers/specs/2026-07-08-agent-ready-docs-framework-design.md`) — the atlas, per-directory `_INDEX.md`, and the per-app `catalog-info.yaml`/`docs.md`/`runbook.md` contract.

## Problem

The framework put an always-fresh, agent-navigable knowledge layer in git. Nothing consumes it yet. Meanwhile the existing `homelab-knowledge` kagent agent answers "what/why/how" questions from a **large hardcoded `systemMessage`** that is already stale (it describes the master-app as creating one Application per `.yaml`, MySQL persistence, etc. — the exact drift the framework exists to kill). It has no connection to the `docs.md`/`runbook.md`/`catalog-info.yaml` files.

**Goal:** rework `homelab-knowledge` to answer from the **live agent-docs** (read from git at query time) instead of a staleable prompt — killing the staleness, exercising the framework's atlas → index → app navigation, and turning the agent into a drift detector.

## Goals

- Give the agent a **read-only** tool that reads the agent-docs from `arigsela/kubernetes@main` on demand.
- Replace the agent's hardcoded architectural facts with **retrieval** (atlas → index → per-app docs), keeping its stable behavioral guardrails and its live-state delegation.
- Validate on the 4 pilot apps: the agent answers with correct current facts, cites real file paths, and flags doc-vs-live drift.

## Non-goals

- No Backstage integration in v1 (the agent reads `catalog-info.yaml` structured facts from git directly). **v2: Backstage MCP** — see Future Work.
- No RAG/vector indexing of docs (read-fresh-from-git was chosen over eventual-consistency indexing).
- No changes to the docs framework itself, the other kagent agents, or `k8s-agent`/`helm-agent`.
- No write/mutation capability for the new MCP (read-only, single repo).

## Chosen approach

Read the agent-docs **from git at query time** via the **official GitHub MCP server**, deployed read-only and scoped to a single repo, and wire it as a tool on the reworked `homelab-knowledge` agent. Everything is declarative in the repo (GitOps).

Rejected alternatives (from brainstorming):
- **RAG into kagent memory** — scales to fuzzy questions but adds an ingestion/re-index pipeline and carries staleness between doc changes and re-index.
- **Docs embedded in the prompt via a synced ConfigMap** — simplest wiring, but re-introduces staleness and bloats context; undermines the freshness goal.
- **Filesystem MCP over a synced clone** — no token needed but you own the clone-sync infra and it lags `main`.
- **Purpose-built agent-docs MCP** — most ergonomic navigation but you build/maintain a custom server; the official GitHub MCP + a navigation prompt gets there with no custom code.

## Design

### Components

Three declarative pieces (in `base-apps/kagent/`) plus one Vault secret:

```
homelab-knowledge Agent (reworked)
  tools:
    - RemoteMCPServer/MCPServer: agent-docs-github-mcp  ──reads──▶ GitHub API (arigsela/kubernetes@main)
    - Agent: k8s-agent   (live cluster state)                       INFRASTRUCTURE_ATLAS.md
    - Agent: helm-agent  (live helm state)                          base-apps/_INDEX.md
                                                                    base-apps/<app>/docs.md · runbook.md · catalog-info.yaml
```

### 1. `agent-docs-github-mcp` (the repo MCP)

- **Image:** the official `github/github-mcp-server` container, deployed as a kagent MCP server manifest under `base-apps/kagent/` (following the existing agent/MCP layout).
- **Read-only + scoped:** started in read-only mode with only read toolsets enabled (repo contents + code search — no write, issue, or PR tools). The token is a **fine-grained PAT limited to `arigsela/kubernetes` with read-only Contents permission**, so worst-case exposure is read of this one repo.
- **Token flow:** the PAT is stored in Vault (`k8s-secrets`), pulled via an **ExternalSecret** in the `kagent` namespace into a K8s Secret, and injected as the MCP server's env (`GITHUB_PERSONAL_ACCESS_TOKEN`) — mirroring how Backstage already receives its `github-token`.
- **Registration:** exposed to kagent as an `MCPServer`/`RemoteMCPServer` the agent references as a tool, the same way `kagent-tool-server` is wired today.
- **Open implementation detail (for the plan):** confirm the exact kagent `MCPServer` CRD shape for a container-based MCP (transport, command/args, env) and whether it registers as `MCPServer` or `RemoteMCPServer`.

### 2. Reworked `homelab-knowledge` Agent

- **Tools:** add `agent-docs-github-mcp` alongside the existing `k8s-agent` and `helm-agent` delegates.
- **`systemMessage` rewrite (retrieval, not recall):**
  - **Remove** the large hardcoded architectural block (stale GitOps/master-app/MySQL/Crossplane facts).
  - **Add** the framework navigation: *"For any question about an app or the platform, first read `INFRASTRUCTURE_ATLAS.md`, then `base-apps/_INDEX.md`, then the app's `docs.md`/`runbook.md`/`catalog-info.yaml` via the GitHub MCP. The `sources:` files listed in a doc are authoritative; never invent file paths or resource names — read them."*
  - **Keep** the stable guardrails: delegate to `k8s-agent`/`helm-agent` for **live** state; GitOps-not-`kubectl` (recommend a PR, never a direct mutation); never quote secret values, only the Vault path/property; the "brief answer → what I checked → specifics" response format.
  - **New capability:** when docs and live state disagree, **flag the drift** ("runbook says X, cluster shows Y") — makes the agent a drift detector.
- **`a2aConfig` skills:** keep repo-knowledge / cluster-troubleshooting / deployment-guidance; refresh examples to reflect retrieval (e.g., "What breaks cert-manager and how do I fix it?" pulls `cert-manager/runbook.md`).

Net effect: the prompt gets **shorter and stops going stale** — the atlas is the agent's always-fresh architectural base.

### 3. The Vault secret

A read-only, single-repo fine-grained GitHub PAT stored at a Vault path under `k8s-secrets`, surfaced to the `kagent` namespace via ExternalSecret + SecretStore (the established per-namespace pattern).

## Data flow (worked example)

Query: *"What breaks cert-manager and how do I fix it?"*

1. Agent reads `INFRASTRUCTURE_ATLAS.md` (MCP) → orient.
2. Reads `base-apps/_INDEX.md` → finds the `cert-manager` row.
3. Reads `base-apps/cert-manager/runbook.md` (symptom → check → fix) and `catalog-info.yaml` (`dependsOn: vault`).
4. For live state, delegates to `k8s-agent` (e.g., "is the Route 53 `ExternalSecret` Ready?").
5. Composes: brief answer → "what I checked" (docs read + delegates used) → specifics (exact file paths, `kubectl` verify commands, the Vault path for the creds).

## Success criteria

For ~5 gold questions across the 4 pilot apps (`chores-tracker-backend`, `vault`, `argo-cd`, `cert-manager`), the reworked agent:
1. Reads the *right* doc files (atlas → index → app), visible in its tool calls.
2. Answers with correct **current** facts (PostgreSQL not MySQL; Argo `Rollout` not Deployment; cert-manager prod/staging HTTP-01 with a separate Route 53 DNS-01 issuer).
3. Cites real `file_path`s (no invented paths).
4. Flags an injected doc-vs-live drift when asked about it.

## Testing

Drive the agent via the kagent UI / A2A with the gold questions after deploy; eyeball that it navigates atlas → index → app and cites real paths. This is a prompt + tool change, so there's no unit-test harness; validation is behavioral.

## Safety, blast radius & rollback

- **Additive and advisory.** A read-only, single-repo MCP + a prompt rewrite on one agent. The agent only reads and recommends — no workload impact, no mutation path.
- **Rollback:** GitOps revert of the agent CRD + removal of the MCP manifest and ExternalSecret. The old hardcoded prompt remains in git history.

## Future work — v2: Backstage MCP

Once v1 proves out, add the **Backstage MCP** (Backstage's MCP Actions backend) as a second tool so the agent can query **structured catalog relations** — ownership, `dependsOn`/`dependencyOf` graphs, systems, and cross-entity questions — from the Backstage catalog rather than reconstructing them from individual `catalog-info.yaml` files. This is the natural evolution of the framework's two-layer design (structured facts → Backstage; narrative → git) and is intentionally deferred so v1 stays small.

## Open questions

- Exact kagent `MCPServer` CRD shape for a container-based GitHub MCP (transport + env) — pin during the plan by inspecting the live CRD and an existing MCP manifest.
- Whether to reuse Backstage's existing `github-token` (broader scope) or mint a dedicated read-only single-repo PAT — the plan defaults to a **dedicated** read-only PAT for least privilege.
