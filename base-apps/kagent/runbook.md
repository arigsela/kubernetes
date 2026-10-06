---
type: "Kubernetes App Runbook"
title: "kagent — Runbook"
description: "Operational runbook for kagent: failure modes, checks, and fixes."
app: kagent
catalog_entity: kagent
kind: runbook
namespace: kagent
last_reviewed: 2026-09-30
status: stable
tags: [ai-agent, kagent, mcp, anthropic]
sources:
  - base-apps/kagent.yaml
  - base-apps/kagent-secrets.yaml
  - base-apps/kagent/embedding-model-config.yaml
  - base-apps/kagent/model-configs/anthropic-claude-sonnet-4-6.yaml
  - base-apps/kagent/agents/homelab-knowledge.yaml
  - base-apps/kagent/agents/k8s-reader.yaml
  - base-apps/kagent/agents/homelab-agent.yaml
  - base-apps/kagent/agent-docs-mcp-remote.yaml
  - base-apps/kagent/external-secrets.yaml
  - base-apps/kagent/kagent-anthropic-external-secret.yaml
  - base-apps/kagent/homelab-agent-external-secret.yaml
  - base-apps/kagent/homelab-agent-db-external-secret.yaml
  - base-apps/kagent/httproute.yaml
  - base-apps/kagent/httproute-mcp.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - base-apps/postgresql/init-homelab-agent-db.yaml
  - scripts/provision-homelab-agent-vault.sh
---

# kagent — Runbook

## Failure modes

### Symptom: an `Agent` is not `Ready` (chat/A2A calls fail)
- **Check:** `kubectl -n kagent get agents` for the `Ready` column, then `kubectl -n kagent describe agent <name>` for the condition/reason. Also confirm the `ModelConfig` it references exists: `kubectl -n kagent get modelconfigs` — every Declarative agent sets `spec.declarative.modelConfig` (`default-model-config` or `anthropic-claude-sonnet-4-6`), and those with a `memory` block (all but `k8s-reader`) also reference `embedding-model-config`; a missing or misnamed `ModelConfig` leaves the agent unable to reconcile. Check the controller logs for the underlying error: `kubectl -n kagent logs deploy/kagent-controller --tail=100`.
- **Fix:** if a `ModelConfig` reference is wrong, open a PR fixing the `modelConfig` name in the agent's manifest (or, for `default-model-config`, the `providers` block in `base-apps/kagent.yaml`) — Argo CD self-heals on merge. If the Anthropic key itself needs rotating, see *Rotate a Vault-backed secret* (and the non-Vault exception there for `anthropic-claude-sonnet-4-6`).

### Symptom: every multi-step question fails after a model bump (single-turn questions still work)
- **Check:** the agent's pod logs for `NotImplementedError: Not supported yet: ... thought=True thought_signature=None`. It fires on the second request of a tool loop, so a one-shot "hello" passes and hides it.
- **Cause:** a Declarative agent (kagent python runtime 0.10.2, google-adk 1.38.0) was pointed at a model that thinks by default, such as `claude-sonnet-5`. ADK drops the thinking block's signature and can't serialize it back on the next turn, and the Anthropic `ModelConfig` has no field to turn thinking off. Tried and reverted 2026-09-25 (#603); the header of `model-configs/anthropic-claude-sonnet-4-6.yaml` records it.
- **Fix:** revert the `model:` (or the agent's `modelConfig`) to `claude-sonnet-4-6` by PR. Re-test on each kagent bump with a *multi-step tool* question before moving again. This does not apply to the BYO `homelab-agent`, which runs `claude-sonnet-5` fine (image v0.4.0+).

### Symptom: an agent reports "Unknown Tool", or a `RemoteMCPServer` shows `toolCount: 0`
- **Check:** `kubectl -n kagent get remotemcpservers` for `toolCount`/status, and `kubectl -n kagent describe remotemcpserver <name>` (e.g. `agent-docs`, `backstage-catalog`) for connection errors. If it's a container-backed server (`agent-docs-mcp`), also check the backing pod: `kubectl -n kagent get pods -l app.kubernetes.io/name=agent-docs-mcp` and its logs. This is a common gap: a container `MCPServer` only *deploys* the server — the matching `RemoteMCPServer` is what *registers* its tools with kagent (see `agent-docs-mcp-remote.yaml`), and an agent only gets the specific `toolNames` it lists in `spec.declarative.tools[].mcpServer.toolNames` — anything not listed there resolves to nothing at agent-invocation time.
- **Fix:** if the `RemoteMCPServer` itself can't reach its target (`url:` in `agent-docs-mcp-remote.yaml`), check the target Service/pod is healthy first. If tools are missing because an agent's `toolNames` list is stale (a new tool was added upstream, or a typo), open a PR updating the agent's `spec.declarative.tools[].mcpServer.toolNames`.

### Symptom: agents fail on memory/embedding calls (RAG lookups error or time out)
- **Check:** agents with a `memory` block use `memory.modelConfig: embedding-model-config` (`embedding-model-config.yaml`), which points at `http://ollama.ollama.svc.cluster.local:11434` (model `nomic-embed-text`); `homelab-agent` calls the same Ollama directly (`OLLAMA_BASE_URL`). Confirm Ollama is up: `kubectl -n ollama get pods` and `kubectl -n ollama logs deploy/ollama --tail=50`; confirm the `ModelConfig` resolved correctly in kagent: `kubectl -n kagent describe modelconfig embedding-model-config`.
- **Fix:** this is a dependency on the `ollama` component, not a kagent config problem — see `base-apps/ollama/runbook.md` for Ollama-specific recovery. If Ollama is healthy but kagent still can't reach it, restart the controller so it re-resolves the endpoint: `kubectl -n kagent rollout restart deploy/kagent-controller`.

### Symptom: `homelab-agent` pod won't start (`CreateContainerConfigError`) or has no memory
- **Check:** the three ExternalSecrets it depends on are `SecretSynced`: `kubectl -n kagent get externalsecret homelab-agent-secrets homelab-agent-db` and `kubectl -n postgresql get externalsecret homelab-agent-db-credentials`. A missing Secret or key blocks the pod; a Vault 403 means the Vault side isn't provisioned (see the how-to below). For memory, check the DB exists: `kubectl -n postgresql logs job/init-homelab-agent-db` (the hook Job self-deletes on success, so no Job usually means it ran) and the pod's logs for Postgres connection errors.
- **Fix:** provision or repair the Vault side, then force-sync the ExternalSecret. If the DB password was rotated, the init Job has to run again (it `ALTER ROLE`s idempotently) — a PR that changes only hook Jobs doesn't trigger a sync (`base-apps/postgresql/runbook.md`), so trigger one sync of the `postgresql` app, then restart the agent pod so it picks up the re-rendered `MEMORY_DB_URL`.

### Symptom: the kagent UI or `kagent-mcp.arigsela.com/mcp` returns 403 or times out from outside
- **Check:** a 403 `RBAC: access denied` comes from the Gateway allow-list (`base-apps/istio-ingress/authorizationpolicy.yaml`), not kagent; a timeout usually means the Route 53 A record points at a stale WAN address. `kubectl -n kagent get httproute kagent kagent-mcp -o yaml` shows whether the routes are `Accepted` on the `https-kagent`/`https-kagent-mcp` listeners, and `kubectl -n kagent get certificate` whether `kagent-tls`/`kagent-mcp-tls` are `Ready`.
- **Fix:** if the WAN IP rotated, `base-apps/wan-ip-monitor/` moves DNS and opens the allow-list PR; see its runbook. If the client's address is new, add it to the rule for the host by PR. A missing `ReferenceGrant` leaves the listener silently certless (TLS handshake fails rather than a 403).

## How-to

### Deploy / update
Edit the Helm `valuesObject` in `base-apps/kagent.yaml` (controller/UI/bundled-agent config) or the CRDs under `base-apps/kagent/` (custom agents, MCP servers, model configs, secrets, routes) and open a PR; Argo CD (`kagent` and `kagent-secrets` Applications, both `prune`/`selfHeal`) syncs on merge into `main`. A new `homelab-agent` release is a new image built in `arigsela/claude-agents` (`homelab-agent/deploy-to-ecr.sh <tag>`) plus a PR bumping `spec.byo.deployment.image`; its dependencies are unpinned, so every rebuild can pull major bumps — run its tests against the resolved set.

### Rotate a Vault-backed secret
Every ExternalSecret here is credential-scoped: each resolves through its **own** `SecretStore` / ESO ServiceAccount / Vault role, reading a **per-consumer Vault key**. Rotate a value at that consumer's own path — the monolithic `kagent` key is no longer read by anything in this namespace.

| Secret (ExternalSecret if different) | SecretStore | Vault key (rotate here) |
|---|---|---|
| `agent-docs-github-mcp-token` | `vault-agent-docs-mcp` | `k8s-secrets/kagent-agent-docs-mcp` (property `github-token`) |
| `backstage-mcp-token` | `vault-backstage-mcp` | `k8s-secrets/kagent-backstage-mcp` (property `token`) |
| `kagent-db-credentials` | `vault-kagent-db` | `k8s-secrets/kagent-db` (`db-url`, `db-user`, `db-password`, `db-name`) |
| `kagent-anthropic` (`kagent-anthropic-secrets`) | `vault-kagent-anthropic` | `k8s-secrets/kagent-anthropic` (property `anthropic-api-key`) |
| `homelab-agent-secrets` | `vault-homelab-agent` | `k8s-secrets/homelab-agent` (`anthropic-api-key`, `backstage-token`) |
| `homelab-agent-db` | `vault-homelab-agent-db` | `k8s-secrets/homelab-agent-db` (property `password`; also re-run the init Job, see above) |
| `kagent-mcp-basic-auth` — orphaned, no consumer since T52 | `vault-kagent-mcp-basic-auth` | `k8s-secrets/kagent-mcp-basic-auth` (property `auth`) — nothing to rotate |

Update the value in Vault directly (never in Git) — External Secrets Operator picks it up within the `refreshInterval` (1h), or force it immediately: `kubectl -n kagent annotate externalsecret <name> force-sync=$(date +%s) --overwrite`. Env-var consumers (`homelab-agent`) need a pod restart after the Secret changes.

**Not in Vault:** `anthropic-claude-sonnet-4-6` (the key behind `homelab-knowledge`'s ModelConfig) was created by the kagent UI and has no ExternalSecret. Rotate it by patching the Secret's `ANTHROPIC_API_KEY` in-cluster. Never delete or rename the ModelConfig to "reset" it: the Secret is owner-referenced to it and is garbage-collected with it. The durable fix is to move the key into Vault + ESO first.

If an `ExternalSecret` goes `SecretSyncedError` with a Vault **403 permission denied**, the Vault side of its scope is missing or wrong — check that the policy `<role>` grants `read` on that exact path, and that the kubernetes-auth role binds the right ESO ServiceAccount in the `kagent` namespace. (A 403 rather than a 404 is what you get when the *policy* doesn't cover the path, even if the key doesn't exist.)

### Provision homelab-agent's Vault side (one-time; also for rotation)
Vault policies and roles here are hand-provisioned, not Terraform. `scripts/provision-homelab-agent-vault.sh` creates everything `homelab-agent` needs, idempotently: the two KV paths (`k8s-secrets/homelab-agent-db` with a generated `password`, preserved on re-run; `k8s-secrets/homelab-agent` with `anthropic-api-key` + `backstage-token`), two read-one-path policies, and two kubernetes-auth roles (`homelab-agent` → `eso-homelab-agent` in `kagent`; `homelab-agent-db` → `eso-homelab-agent-db` in `kagent,postgresql`). Token values come from env vars so they stay out of shell history; both are required on the first run, and on a re-run only the ones supplied are changed. The `anthropic-api-key` is a dedicated key for this agent, not the shared kagent one; `backstage-token` is the same Backstage MCP bearer value `backstage-catalog-mcp` uses.

```sh
kubectl -n vault cp scripts/provision-homelab-agent-vault.sh vault-0:/tmp/prov.sh
kubectl -n vault exec -it vault-0 -- sh
export VAULT_TOKEN=<root-or-admin-token>
export ANTHROPIC_API_KEY=<key>          # first run only (or to rotate)
export BACKSTAGE_MCP_TOKEN=<token>       # first run only (or to rotate)
sh /tmp/prov.sh
unset ANTHROPIC_API_KEY BACKSTAGE_MCP_TOKEN VAULT_TOKEN; rm /tmp/prov.sh; exit
```

The script's body is the by-hand reference (`vault kv put`, `vault policy write`, `vault write auth/kubernetes/role/...`). Then verify:

```sh
kubectl -n postgresql get externalsecret homelab-agent-db-credentials   # SecretSynced
kubectl -n kagent get externalsecret homelab-agent-secrets homelab-agent-db
kubectl -n kagent port-forward svc/homelab-agent 18080:8080 &
curl -s localhost:18080/health                                     # {"status":"healthy",...}
curl -s localhost:18080/.well-known/agent.json | jq '.skills[].id'  # the three skill ids
```

Memory check: ask a question, then a related one in a new turn; recall should surface the earlier exchange, and still do so after a pod restart.

### A tool call fails with `secrets is forbidden` or `403` from the API server
Expected for Secrets: the tool server's ClusterRole (`base-apps/cluster-rbac/kagent-tools.yaml`) grants everything except `secrets`, for every agent. The agent should report the refusal; nothing to fix. For any other resource, the API group is probably new to the cluster: the role names non-core groups explicitly and denies the rest until a PR adds the group. Check with `kubectl auth can-i <verb> <resource> --as=system:serviceaccount:kagent:kagent-tools`, add the group to the role, and note it in the manifest header's dated list.

### Restart the controller
`kubectl -n kagent rollout restart deploy/kagent-controller` — safe; agents reconcile again once the controller is back. Verify with `kubectl -n kagent get pods -l app.kubernetes.io/name=kagent` and `kubectl -n kagent get agents`.
