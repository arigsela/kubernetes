# Backstage MCP Tool for the homelab-knowledge Agent (v2) — Design

- **Date:** 2026-07-10
- **Status:** Approved (design); implementation plan to follow
- **Depends on:** the agent-ready docs framework, the kagent agent-docs retrieval agent (v1), and the Backstage catalog discovery work (Backstage now ingests `base-apps/*/catalog-info.yaml`). This is "v2" from the kagent retrieval spec — the Backstage MCP.

## Problem

The `homelab-knowledge` agent reads narrative docs and raw `catalog-info.yaml` files from git via the agent-docs GitHub MCP. It can read a single file's declared facts, but it cannot answer **graph** questions — "what depends on vault?", "who owns cert-manager?", "what's in the platform-secrets system?" — because those require the **resolved** catalog relations (including computed reverse relations like `dependencyOf`) that exist only in Backstage's catalog, not in any single file.

Backstage now ingests the per-app entities (v1). Backstage 1.48 also ships an official MCP backend (`@backstage/plugin-mcp-actions-backend`) that can expose catalog actions as MCP tools. Wiring that to the agent turns the resolved catalog graph into a queryable tool.

**Goal:** give `homelab-knowledge` a read-only Backstage catalog MCP tool so it answers ownership/dependency/system questions from the live, resolved catalog graph, and make those relations actually resolve by defining the missing `Group`/`System` entities.

## Goals

- Expose a **read-only** Backstage catalog MCP (`catalog:entity` only) and wire it as a tool on `homelab-knowledge`.
- Define the `Group`/`System` entities the pilot files reference, so `ownedBy`/`partOf` relations resolve (today only `dependsOn`/`dependencyOf` resolve).
- The agent uses the catalog tool for relation/ownership/dependency questions and the agent-docs MCP for narrative docs — complementary, not redundant.

## Non-goals

- No write/mutation actions. Only `catalog:entity` is exposed (not `catalog:*`, which includes `register`/`unregister`/`validate`).
- No free-form catalog graph search. Backstage 1.48's read action (`catalog:entity`) looks up a single entity by name and returns its full record (including `relations`). Multi-hop traversal is the agent chaining lookups, not one call.
- No change to the agent-docs MCP, `k8s-agent`/`helm-agent`, or the v1 catalog provider.
- No OAuth/Dynamic Client Registration — static token auth is used (server-to-server).

## Chosen approach

Add the official Backstage MCP Actions backend to `arigsela/backstage`, exposing a single read-only `catalog` MCP server, protected by a static token. Define the missing `Group`/`System` entities in this repo and register them via a `url` catalog Location. On the kagent side, add a `RemoteMCPServer` (with an `Authorization` header from Vault) and wire it as a third tool on `homelab-knowledge`. Everything declarative; the Backstage change ships as image `v1.3.0`.

Rejected alternatives:
- **Expose `catalog:*`** — includes mutation actions (register/unregister); violates the agent's read-only/advisory posture.
- **Unauthenticated MCP endpoint** (relying on `dangerouslyDisableDefaultAuthPolicy` + cluster network) — the backstage backend is reachable via the public ingress, so an unauthenticated MCP endpoint would expose catalog reads publicly. Static token is defense-in-depth.
- **Dynamic Client Registration (OAuth)** — needs the auth frontend and a token-exchange flow; overkill for a single in-cluster server-to-server caller.
- **Custom catalog query MCP** — building a bespoke server duplicates what the official plugin provides.

## Design

### Part A — Backstage (`arigsela/backstage`, image `v1.3.0`)

**1. MCP Actions backend + a read-only catalog server**
- Add dependency `@backstage/plugin-mcp-actions-backend@^0.1.14` and `backend.add(import('@backstage/plugin-mcp-actions-backend'))` in `packages/backend/src/index.ts`.
- app-config:
  ```yaml
  mcpActions:
    servers:
      catalog:
        include:
          - id: 'catalog:entity'
  ```
  Serves `/api/mcp-actions/v1/catalog` exposing one tool: get-entity-with-relations (`catalog:entity` takes `{kind?, namespace?, name}`, returns the full entity incl. `relations`).

**2. Static-token auth**
- Configure `backend.auth.externalAccess` with a `static` subject whose token comes from the `MCP_TOKEN` env var. External MCP clients must send `Authorization: Bearer <MCP_TOKEN>`.
- `MCP_TOKEN` is injected into the backstage pod from Vault via an ExternalSecret (Part B).

**3. `Group`/`System` entities**
- New file in **this repo** at a path Argo CD does not watch (Argo watches only `base-apps/`): `catalog/platform-entities.yaml`. Contains:
  - `Group:platform` (the `owner` all pilots reference).
  - `System:platform-networking`, `System:platform-secrets`, `System:platform-gitops`, `System:chores-tracker` (the `system` refs).
- Registered by adding a `url` catalog `Location` in the backstage app-config pointing at that file's GitHub URL (`https://github.com/arigsela/kubernetes/blob/main/catalog/platform-entities.yaml`). The global `catalog.rules` already allow `System` but **not** `Group` (org kinds aren't globally allowed), so this `url` Location carries its own `rules: [{allow: [Group, System]}]` clause to admit both kinds it contains.

All three changes ship in one image rebuild (`v1.3.0`) via `./scripts/build-and-push.sh --version v1.3.0`, then a tag bump here.

### Part B — kagent (this repo)

**1. The MCP token (Vault) + two ExternalSecrets**
- One secret value stored in Vault under `k8s-secrets` (user-stored, like the GitHub PAT).
- **backstage namespace** — an ExternalSecret surfaces it as `MCP_TOKEN`, wired into the backstage deployment env (consumed by `externalAccess`).
- **kagent namespace** — an ExternalSecret `backstage-mcp-token` whose value under key `authorization` is the full header string `Bearer <token>`, so kagent injects it verbatim.

**2. `RemoteMCPServer` (kagent ns)**
```yaml
apiVersion: kagent.dev/v1alpha2
kind: RemoteMCPServer
metadata: { name: backstage-catalog, namespace: kagent }
spec:
  protocol: STREAMABLE_HTTP
  url: http://backstage.backstage.svc.cluster.local/api/mcp-actions/v1/catalog
  timeout: 30s
  sseReadTimeout: 5m0s
  terminateOnClose: true
  headersFrom:
    - name: Authorization
      valueFrom: { type: Secret, name: backstage-mcp-token, key: authorization }
```
(Service `backstage.backstage.svc.cluster.local` is port 80 → targetPort 7007.) kagent discovers the `catalog:entity` tool; the exact registered tool name is read post-deploy and pinned in the agent's `toolNames` (same method used for agent-docs).

**3. `homelab-knowledge` agent**
- Add a third tool: `type: McpServer` → `mcpServer: {apiGroup: kagent.dev, kind: RemoteMCPServer, name: backstage-catalog, toolNames: [<pinned>]}`.
- `systemMessage` update: for **ownership / dependency / "what depends on X" / system-membership** questions, use the Backstage catalog tool (returns resolved relations, including reverse `dependencyOf`). Keep the agent-docs MCP for narrative docs (`docs.md`/`runbook.md`) and as a fallback for `catalog-info.yaml` when the catalog is unreachable.

**4. Deploy** — bump `base-apps/backstage/deployments.yaml` → `:v1.3.0`.

## Data flow (worked example)

Query: *"What depends on vault?"*
1. Agent calls the Backstage catalog tool `catalog:entity` with `name=vault`.
2. Backstage returns the `vault` entity including `relations: [{type: dependencyOf, targetRef: resource:cert-manager/cert-manager}, {type: dependencyOf, targetRef: component:chores-tracker/chores-tracker-backend}, {type: ownedBy, targetRef: group:default/platform}, {type: partOf, targetRef: system:default/platform-secrets}]`.
3. Agent answers: "cert-manager and chores-tracker-backend depend on vault," citing the live catalog — a fact not present in vault's own `catalog-info.yaml`.

## Success criteria

After `v1.3.0` is deployed:
1. The kagent `RemoteMCPServer/backstage-catalog` is `Accepted=True` with the `catalog:entity` tool discovered.
2. `homelab-knowledge` lists the Backstage catalog tool (not "Unknown Tool").
3. Gold questions answered from the catalog graph:
   - "What depends on vault?" → cert-manager + chores-tracker-backend (reverse relation).
   - "Who owns cert-manager?" → `platform` (resolved Group, not a dangling ref).
   - "What system is chores-tracker-backend part of?" → `chores-tracker`.
4. The four pilot entities show resolved `ownedBy`/`partOf` relations in the Backstage UI (Group/System entities present).

## Testing

- **Backstage build gate:** `yarn tsc` + `config:check --lax` clean; image `v1.3.0` in ECR.
- **Post-deploy (cluster):** `RemoteMCPServer` accepted with the tool; the platform entities resolve in the catalog API; drive the gold relation questions in the kagent UI.
- Config/behavioral change — no unit-test harness; validation is behavioral, matching prior kagent work.

## Safety, blast radius & rollback

- **Read-only + advisory.** Only `catalog:entity` is exposed; the agent reads and recommends. The MCP endpoint requires the static token.
- **Auth exposure.** The static token limits the MCP endpoint even though the backstage backend is publicly ingress-exposed. The token lives only in Vault and the two ExternalSecret-derived K8s Secrets; never committed.
- **No Argo interaction for the entities.** `catalog/platform-entities.yaml` sits outside `base-apps/`, so Argo never syncs it; Backstage reads it via the `url` Location.
- **Rollback:** revert the `v1.3.0` tag bump (Argo redeploys `v1.2.0`); the agent tool addition and the RemoteMCPServer are additive and can be reverted independently. The Backstage code/config change remains in `arigsela/backstage` history, inert until an image ships it.

## Open questions

- **Exact registered tool name** for `catalog:entity` under the `catalog` MCP server (the plugin prefixes tool names by plugin id). Resolved during implementation by reading the `RemoteMCPServer` `discoveredTools` post-registration and pinning `toolNames` — same method used for agent-docs.
- **`externalAccess` interaction with `dangerouslyDisableDefaultAuthPolicy: true`.** The plan verifies the static token is actually enforced on `/api/mcp-actions/v1/catalog` (a request without the token is rejected) before wiring the agent.
