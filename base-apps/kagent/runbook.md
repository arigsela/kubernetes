---
app: kagent
catalog_entity: kagent
kind: runbook
namespace: kagent
last_reviewed: 2026-07-10
status: current
tags: [ai-agent, kagent, mcp, anthropic]
sources:
  - base-apps/kagent.yaml
  - base-apps/kagent-secrets.yaml
  - base-apps/kagent/embedding-model-config.yaml
  - base-apps/kagent/agents/homelab-knowledge.yaml
  - base-apps/kagent/agent-docs-mcp-remote.yaml
  - base-apps/kagent/plex-stack-mcp-remote.yaml
  - base-apps/kagent/external-secrets.yaml
---

# kagent — Runbook

## Failure modes

### Symptom: an `Agent` is not `Ready` (chat/A2A calls fail)
- **Check:** `kubectl -n kagent get agents` for the `Ready` column, then `kubectl -n kagent describe agent <name>` for the condition/reason. Also confirm the `ModelConfig` it references exists: `kubectl -n kagent get modelconfigs` — every declarative agent sets `spec.declarative.modelConfig` (e.g. `default-model-config`, `anthropic-claude-sonnet-4-6`) and `spec.declarative.memory.modelConfig: embedding-model-config` (`agents/homelab-knowledge.yaml`); a missing or misnamed `ModelConfig` leaves the agent unable to reconcile. Check the controller logs for the underlying error: `kubectl -n kagent logs deploy/kagent-controller --tail=100`.
- **Fix:** if a `ModelConfig` reference is wrong or the Anthropic API key rotated, open a PR fixing the `modelConfig` name in the agent's manifest (or, for `default-model-config`, the `providers` block in `base-apps/kagent.yaml`) — Argo CD self-heals on merge. If the Anthropic API key itself needs rotating, update the `kagent-anthropic` Vault secret (referenced by `apiKeySecretRef: kagent-anthropic` in `kagent.yaml`) rather than the manifest.

### Symptom: an agent reports "Unknown Tool", or a `RemoteMCPServer` shows `toolCount: 0`
- **Check:** `kubectl -n kagent get remotemcpservers` for `toolCount`/status, and `kubectl -n kagent describe remotemcpserver <name>` (e.g. `agent-docs`, `backstage-catalog`, `plex-stack-mcp`) for connection errors. If it's a container-backed server (`agent-docs-mcp`, `plex-stack-mcp`), also check the backing pod: `kubectl -n kagent get pods -l app.kubernetes.io/name=agent-docs-mcp` / `plex-stack-mcp` and its logs. This is a common gap: a container `MCPServer` only *deploys* the server — the matching `RemoteMCPServer` is what *registers* its tools with kagent (see `agent-docs-mcp-remote.yaml`), and an agent only gets the specific `toolNames` it lists in `spec.declarative.tools[].mcpServer.toolNames` — anything not listed there resolves to nothing at agent-invocation time.
- **Fix:** if the `RemoteMCPServer` itself can't reach its target (`url:` in `agent-docs-mcp-remote.yaml`/`plex-stack-mcp-remote.yaml`), check the target Service/pod is healthy first. If tools are missing because an agent's `toolNames` list is stale (a new tool was added upstream, or a typo), open a PR updating the agent's `spec.declarative.tools[].mcpServer.toolNames`.

### Symptom: agents fail on memory/embedding calls (RAG lookups error or time out)
- **Check:** every declarative agent's `memory.modelConfig: embedding-model-config` (`embedding-model-config.yaml`) points at `http://ollama.ollama.svc.cluster.local:11434` (model `nomic-embed-text`). Confirm Ollama is up: `kubectl -n ollama get pods` and `kubectl -n ollama logs deploy/ollama --tail=50`; confirm the `ModelConfig` resolved correctly in kagent: `kubectl -n kagent describe modelconfig embedding-model-config`.
- **Fix:** this is a dependency on the `ollama` component, not a kagent config problem — see `base-apps/ollama/runbook.md` for Ollama-specific recovery. If Ollama is healthy but kagent still can't reach it, restart the controller so it re-resolves the endpoint: `kubectl -n kagent rollout restart deploy/kagent-controller`.

## How-to

### Deploy / update
Edit the Helm `valuesObject` in `base-apps/kagent.yaml` (controller/UI/bundled-agent config) or the declarative CRDs under `base-apps/kagent/` (custom agents, MCP servers, model configs, secrets) and open a PR; Argo CD (`kagent` and `kagent-secrets` Applications, both `prune`/`selfHeal`) syncs on merge into `main`.

### Rotate a Vault-backed secret
All Secrets here (`kagent-db-credentials`, `agent-docs-github-mcp-token`, `backstage-mcp-token`, `plex-stack-mcp-creds`, `kagent-mcp-basic-auth`) are `ExternalSecret`s resolving from the `vault-backend` `SecretStore` (`secret-store.yaml`, Vault key `kagent` or `plex-stack-mcp`, `refreshInterval: 1h`). Update the value at the corresponding Vault path directly (never in Git) — External Secrets Operator picks it up within the refresh interval, or force it immediately: `kubectl -n kagent annotate externalsecret <name> force-sync=$(date +%s) --overwrite`.

### Restart the controller
`kubectl -n kagent rollout restart deploy/kagent-controller` — safe; agents reconcile again once the controller is back. Verify with `kubectl -n kagent get pods -l app.kubernetes.io/name=kagent` and `kubectl -n kagent get agents`.
