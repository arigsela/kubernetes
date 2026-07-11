---
app: kagent
catalog_entity: kagent
kind: docs
namespace: kagent
last_reviewed: 2026-07-10
status: current
tags: [ai-agent, kagent, mcp, anthropic]
sources:
  - base-apps/kagent.yaml
  - base-apps/kagent-secrets.yaml
  - base-apps/kagent-crds.yaml
  - base-apps/kagent/embedding-model-config.yaml
  - base-apps/kagent/secret-store.yaml
  - base-apps/kagent/external-secrets.yaml
  - base-apps/kagent/eso-agent-docs-mcp-serviceaccount.yaml
  - base-apps/kagent/agent-docs-mcp-secret-store.yaml
  - base-apps/kagent/model-configs/anthropic-claude-sonnet-4-6.yaml
  - base-apps/kagent/mcp-basic-auth-external-secret.yaml
  - base-apps/kagent/agents/homelab-knowledge.yaml
  - base-apps/kagent/agent-docs-mcp.yaml
  - base-apps/kagent/agent-docs-mcp-remote.yaml
  - base-apps/kagent/backstage-catalog-mcp.yaml
  - base-apps/kagent/plex-stack-mcp.yaml
  - base-apps/kagent/plex-stack-mcp-remote.yaml
  - base-apps/kagent/mcp-ingress.yaml
  - base-apps/kagent/nginx-ingress.yaml
---

# kagent

## What it is
kagent is a Kubernetes-native AI agent platform: a controller (installed via Helm, `base-apps/kagent.yaml`, `chart: kagent`, `repoURL: ghcr.io/kagent-dev/kagent/helm`, `targetRevision: 0.9.4`) that reconciles declarative CRDs — `Agent`, `ModelConfig`, `MCPServer`, `RemoteMCPServer` — into running agent workloads. The CRDs themselves come from a sibling Helm install, `base-apps/kagent-crds.yaml` (`chart: kagent-crds`, same repo/version). Both are deployed into the `kagent` namespace.

## Two Argo CD apps, one namespace
- **`base-apps/kagent.yaml`** installs the controller/UI/bundled agents via Helm `valuesObject` (no `path: base-apps/kagent`, so it has no directory-exclude concern). Notable values: the LLM provider (`providers.default: anthropic`, model `claude-haiku-4-5-20251001`, key from `apiKeySecretRef: kagent-anthropic`), the database wiring (`database.postgres.bundled.enabled: false`, `urlFile: /etc/kagent/secrets/db-url`, `vectorEnabled: true`, sourced from a mounted `kagent-db-credentials` Secret), per-agent enable switches for the chart's bundled agents (only `observability-agent` is enabled — `k8s-agent` and `istio-agent` are disabled here and owned in Git under `agents/` so their memory + HITL `requireApproval` gates are declarative; the rest are off), OpenTelemetry export to Coroot, and a custom UI image (Node 20 build, ECR) to work around a SIGILL on older host CPUs. Note these agent keys are **top-level** chart keys (Helm subchart aliases), not nested under an `agents:` map — the chart has no such map, and nesting them silently disables the whole block.
- **`base-apps/kagent-secrets.yaml`** (`path: base-apps/kagent`, `directory: {recurse: true}`) syncs everything in this directory: the declarative `Agent`, `ModelConfig`, `MCPServer`/`RemoteMCPServer` manifests, and the `SecretStore`/`ExternalSecret`s that back them. This is the Application whose `directory.exclude` covers `catalog-info.yaml`.

## Model configs
- **`default-model-config`** — the chart's default `ModelConfig`, generated from the `providers` block in `kagent.yaml` (Anthropic, `claude-haiku-4-5-20251001`). Used by simple/low-context agents (e.g. `agents/dungeon-crawler-carl-agent.yaml`, `agents/skill-suggester.yaml`, `build-orchestrator.yaml`).
- **`anthropic-claude-sonnet-4-6`** — a sonnet-tier `ModelConfig` referenced by `agents/homelab-knowledge.yaml` and `agents/plex-stack-diagnostics.yaml` (both need larger context/more reliable tool-calling for multi-step delegation); its manifest is git-tracked at `model-configs/anthropic-claude-sonnet-4-6.yaml` (adopted into GitOps per the agent-identity contract) and uses its own dedicated `apiKeySecret` (`anthropic-claude-sonnet-4-6`), not the shared `kagent-anthropic` key.
- **`embedding-model-config`** (`embedding-model-config.yaml`) — points at **Ollama** (`http://ollama.ollama.svc.cluster.local:11434`, model `nomic-embed-text`), used as every declarative agent's `memory.modelConfig` for RAG/embedding recall. This is why the `kagent` component depends on `ollama`.

## Tools via MCP servers
Agents get tools by referencing `MCPServer`/`RemoteMCPServer` objects in their `spec.declarative.tools`. Two patterns exist side by side:
- **stdio proxy**: `agent-docs-mcp.yaml` deploys the read-only GitHub MCP server (`ghcr.io/github/github-mcp-server`, `--read-only --toolsets repos`) as a container; `agent-docs-mcp-remote.yaml` is the `RemoteMCPServer` that registers its tools (`get_file_contents`, `search_code`) with kagent — a container-only `MCPServer` does not register tools in this kagent version, only a `RemoteMCPServer` does.
- **HTTP server**: `plex-stack-mcp.yaml` runs a FastMCP HTTP process directly (streamable-HTTP on `:3000`, path `/mcp`); `plex-stack-mcp-remote.yaml` registers its tools (`plex_status`, `plex_sessions`, `qbit_status`, `plex_scan_library`, `qbit_resume`, `qbit_recheck`) for `agents/plex-stack-diagnostics.yaml`.
- **In-cluster remote**: `backstage-catalog-mcp.yaml` points at Backstage's own MCP endpoint (`http://backstage.backstage.svc.cluster.local/api/mcp-actions/v1/catalog`) for resolved-entity/dependency lookups (`get-catalog-entity`), with the `Authorization` header injected from the `backstage-mcp-token` Secret.

Agents only get the `toolNames` they explicitly list (e.g. `agents/homelab-knowledge.yaml` binds `get_file_contents`/`search_code` from `agent-docs` plus `get-catalog-entity` from `backstage-catalog`, and delegates to `k8s-agent` via a `type: Agent` tool entry) — an unlisted tool resolves to nothing and the agent reports "Unknown Tool". A `type: Agent` entry naming an agent that is not deployed resolves to nothing in the same way, so trim delegations when disabling an agent.

## Secrets & database
All Secrets in `base-apps/kagent/` flow through Vault: `secret-store.yaml` defines the `vault-backend` `SecretStore` (`http://vault.vault.svc.cluster.local:8200`, KV v2 path `k8s-secrets`, Kubernetes-auth role `kagent`). `ExternalSecret`s pull from Vault key `kagent` (DB creds, Backstage MCP token) and key `plex-stack-mcp` (Plex/qBittorrent creds); `mcp-basic-auth-external-secret.yaml` pulls the `/mcp` ingress basic-auth htpasswd blob.

The `agent-docs-mcp` GitHub token is credential-scoped per the agent-identity contract (`templates/agent-identity/README.md`): it resolves through a dedicated `SecretStore` (`vault-agent-docs-mcp`) whose ESO ServiceAccount (`eso-agent-docs-mcp`) assumes a Vault role scoped to only the `kagent-agent-docs-mcp` path, not the broad `kagent` role.

kagent uses the **shared PostgreSQL** instance (`base-apps/postgresql/`) rather than the chart's bundled DB (`database.postgres.bundled.enabled: false` in `kagent.yaml`): `external-secrets.yaml` here syncs `kagent-db-credentials` (`db-url`, `db-user`, `db-password`, `db-name`) from Vault key `kagent`, matching the `kagent` role/database that `postgresql`'s `init-kagent-db` Job provisions with the `vector` extension enabled (see `base-apps/postgresql/docs.md`). The controller mounts that Secret's `db-url` at `/etc/kagent/secrets/db-url` (`kagent.yaml`'s `controller.volumes`/`volumeMounts`).

## Exposure
The UI is at `kagent.arigsela.com` (`nginx-ingress.yaml`, IP-whitelisted). Agents are also invocable over **A2A**: each agent's Backstage annotations (e.g. `agents.platform.ai/a2a-endpoint` in `agents/dungeon-crawler-carl-agent.yaml`) point at `http://<agent>.kagent.svc.cluster.local:8080`. External MCP clients (e.g. Claude Code CLI) reach the controller's `/mcp` endpoint (`invoke_agent`/`list_agents`) via `mcp-ingress.yaml`, which fronts `kagent-controller:8083` at `kagent.arigsela.com/mcp`, gated by the same IP whitelist plus HTTP basic auth (`kagent-mcp-basic-auth`, since `/mcp` has no auth of its own).
