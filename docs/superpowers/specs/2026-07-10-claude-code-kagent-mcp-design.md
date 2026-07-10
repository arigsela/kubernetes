# Interact with the kagent Agent from Claude Code CLI — Design

- **Date:** 2026-07-10
- **Status:** Approved (design); implementation plan to follow
- **Depends on:** the kagent deployment and the `homelab-knowledge` agent (with its agent-docs + Backstage catalog tools).

## Problem

The `homelab-knowledge` kagent agent is only reachable from the kagent web UI. We want to invoke it from the **Claude Code CLI** so questions like "what depends on vault?" can be asked from the terminal.

## Key finding

No custom bridge is needed. kagent's controller **already exposes an MCP server** at `/mcp` (internally `kagent-controller.kagent.svc.cluster.local:8083/mcp`, publicly `https://kagent.arigsela.com/mcp` via the `kagent-mcp-nginx` ingress). That MCP server (`serverInfo.name: kagent-agents`, v0.9.4) exposes two tools:

- **`invoke_agent`** — `{ agent: string (required), task: string (required), context_id?: string }` — invokes a kagent agent via A2A. Clean JSON schema (no `anyOf`/`$ref`).
- **`list_agents`** — enumerate available agents.

Claude Code is an MCP client, so it can consume this endpoint directly. The only gap is **security**: `/mcp` is currently reachable (from IP-allowlisted sources) with **no authentication**.

## Goal

Point the Claude Code CLI at kagent's existing `/mcp` endpoint so it can invoke `homelab-knowledge` (and other agents), and gate that endpoint with a shared credential so it is not open to invocation from any allowlisted client.

## Non-goals

- No custom MCP bridge, slash command, or A2A client — the existing `/mcp` endpoint suffices.
- No change to the agents themselves or the kagent controller.
- No new public exposure — the endpoint already exists; we are adding auth to it.
- No per-user identity/RBAC on the endpoint — a single shared credential is sufficient for a single-operator homelab.

## Chosen approach

Add nginx **basic-auth** to the `kagent-mcp-nginx` ingress (annotation-native; snippet annotations are disabled on this controller, so a literal `Bearer` check isn't available), keeping the existing IP allowlist as a second layer. Configure the Claude Code CLI at **user scope** to reach the public URL with the credential in an `Authorization: Basic` header.

Rejected alternatives:
- **Custom MCP bridge / slash command** wrapping A2A — redundant; kagent already exposes `invoke_agent` over MCP.
- **Bearer-token via nginx snippet** — requires enabling `allow-snippet-annotations` cluster-wide (a security-relevant controller change); basic auth achieves the same shared-secret gate natively.
- **In-cluster-only via port-forward** — no public exposure, but requires a `kubectl port-forward` every session; rejected for convenience (the IP allowlist + basic auth already bounds exposure).
- **Leave `/mcp` unauthenticated** — rejected: `invoke_agent` can drive agents that read the repo (GitHub MCP) and query the cluster (`k8s-agent`); IP allowlist alone is weak.

## Design

### 1. Ingress basic auth (`base-apps/kagent/mcp-ingress.yaml`)

Add to the existing annotations (keep the IP whitelist and SSE/timeout settings):
```yaml
nginx.ingress.kubernetes.io/auth-type: basic
nginx.ingress.kubernetes.io/auth-secret: kagent-mcp-basic-auth
nginx.ingress.kubernetes.io/auth-realm: "kagent MCP"
```
`auth-secret` references a K8s secret in the `kagent` namespace with an `auth` key in htpasswd format (`username:hashedpassword`). Basic auth applies to every request including the MCP streamable-HTTP POSTs and SSE, which Claude Code authenticates on each call.

### 2. Credential (Vault → ExternalSecret)

- You generate an htpasswd entry — `htpasswd -nbB claude '<password>'` → `claude:$2y$...` — and store that line in Vault (`k8s-secrets/kagent`, property `mcp-basic-auth`). You also keep the plaintext `claude:<password>` for the Claude Code header.
- A new ExternalSecret (`base-apps/kagent/mcp-basic-auth-external-secret.yaml`) renders the K8s secret `kagent-mcp-basic-auth` with key `auth` = the htpasswd line. Nothing is committed.

### 3. Claude Code CLI (user scope, run by you)

```bash
claude mcp add --transport http --scope user kagent \
  https://kagent.arigsela.com/mcp \
  --header "Authorization: Basic $(printf 'claude:<password>' | base64)"
```
The `invoke_agent`/`list_agents` tools then appear in every Claude Code session. Usage: *"use invoke_agent to ask the homelab-knowledge agent what depends on vault."* `context_id` can be reused for multi-turn threads.

## Data flow

Claude Code → `POST https://kagent.arigsela.com/mcp` (`Authorization: Basic …`) → nginx checks source IP (allowlist) **and** basic auth → `kagent-controller:8083/mcp` → `invoke_agent(agent="homelab-knowledge", task="What depends on vault?")` → agent runs (docs + catalog + k8s/helm delegates) → response streamed back to Claude Code.

## Success criteria

1. `curl https://kagent.arigsela.com/mcp` **without** the credential → `401` (auth enforced); **with** it → MCP `initialize` succeeds.
2. In-cluster agent operation is unaffected (agents use internal service URLs, not the public `/mcp`).
3. From Claude Code, `list_agents` returns the agents and `invoke_agent(agent="homelab-knowledge", task="What depends on vault?")` returns cert-manager + chores-tracker-backend.

## Testing

- Pre-deploy: yamllint + server-side dry-run of the ingress + ExternalSecret manifests.
- Post-deploy (after the Vault credential is stored): the two `curl` checks above (401 without, 200 with), then the Claude Code `list_agents`/`invoke_agent` calls. Behavioral — no unit-test harness.

## Safety, blast radius & rollback

- **Additive auth on one ingress path.** Only `kagent.arigsela.com/mcp` is affected. The UI ingress (`/` → kagent-ui) is separate and untouched.
- **Verify no in-cluster consumer uses the public `/mcp`** before enabling auth (the `10.0.0.0/8` allowlist entry suggests possible in-cluster access; agents actually use internal service DNS, so this is expected to be a no-op — confirm during the plan).
- **Rollback:** revert the ingress annotations (Argo redeploys the open ingress). The Claude Code user config is local and independently removable (`claude mcp remove kagent`).

## Open questions

- **Exact `agent` value for `invoke_agent`** — `homelab-knowledge` vs a namespaced form (`kagent/homelab-knowledge`). Resolved during implementation via `list_agents`.
- **Claude Code + basic-auth interaction** — confirm the CLI sends the static `Authorization: Basic` header on the streamable-HTTP transport without attempting an OAuth flow on the 401. Verified during the plan's Claude Code step.
