# Design: deploy `homelab-agent` (BYO) with conversation memory

**Date:** 2026-07-17
**Status:** design — approved, pending spec review
**Repo:** `arigsela/kubernetes` (GitOps, Argo CD). Companion to the container built in `arigsela/claude-agents` (`homelab-agent/`, image `…/homelab-agent:v0.2.0`).

## Goal

Deploy the containerized LangGraph `homelab-agent` as a kagent **BYO `Agent` CR** on the current 0.9.11 chart, with **conversation memory enabled from the first sync** — backed by a dedicated database in the existing in-cluster pgvector Postgres and Ollama `nomic-embed-text` embeddings. It coexists with the Declarative `homelab-knowledge` agent (retirement is a later, separate step after A2A parity is observed). All credentials are scoped per the repo's agent-identity contract (`templates/agent-identity/README.md`).

## Decisions (settled during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Rollout | **Memory-ON from the first CR** | User choice; full parity in one increment. |
| Memory DB | **Dedicated `homelab_agent` database + owner role** in the existing pgvector Postgres | DB-level isolation from kagent's data; no schema-scoped mechanism exists to copy, and this matches the established `init-kagent-db` pattern. |
| DB role provisioning | **Idempotent Argo Sync-hook Job** (`base-apps/postgresql/init-homelab-agent-db.yaml`) | GitOps-declared, mirrors `init-kagent-db.yaml`. |
| Credentials | **Own Vault paths + policies + k8s-auth roles + ESO SAs + SecretStores** | Agent-identity contract; the CI validator + Kyverno enforce it. No reuse of `kagent-anthropic` or `kagent-db`. |
| Vault writes | **Operator (user) runs the `vault` CLI**; I author the K8s manifests | The session's Vault MCP token 403s on `sys/mounts` (its preflight), so it cannot read secrets or write policies/roles. DB password generated inline via `openssl rand` — no secret enters chat or git. |
| DB password | **Generated random**, stored only in Vault | Sourced by both the init-Job and the agent via ESO. |
| NetworkPolicy | **None** | No NetworkPolicy exists in the namespace and egress is not default-deny; the pod already reaches Ollama + Postgres. Adding one would be net-new lockdown, out of scope. |

## Verified environment facts (from investigation)

- **Postgres:** in-cluster `pgvector/pgvector:0.8.2-pg18` Deployment, `postgresql.postgresql.svc.cluster.local:5432` (`base-apps/postgresql/`), `vectorEnabled: true`. DB roles created by idempotent Argo Sync-hook Jobs (`init-kagent-db.yaml`), which grant DB ownership (no schema-scoped least-privilege mechanism exists).
- **Vault:** manual provisioning (raw `vault` CLI), no Terraform for policies/roles. Pattern: `kv put k8s-secrets/<name>` + `policy write` + `auth/kubernetes/role/<name>` bound to a dedicated `eso-<name>` SA in the consuming namespace. Server `http://vault.vault.svc.cluster.local:8200`, KV v2, mount `k8s-secrets`.
- **ESO copy template:** `backstage-mcp` trio — `eso-backstage-mcp-serviceaccount.yaml` (SA) → `backstage-mcp-secret-store.yaml` (SecretStore: role `backstage-mcp`, SA `eso-backstage-mcp`, path `k8s-secrets`) → `backstage-mcp-external-secret.yaml` (ExternalSecret templating `Authorization: Bearer {{ .token }}` into Secret `backstage-mcp-token`).
- **Anthropic:** only the shared `kagent-anthropic` key exists; there is **no** per-agent scoped key pattern yet — `homelab-agent` is the first. BYO agents don't use `ModelConfig`; the key is a pod env via `secretKeyRef`.
- **Argo inclusion:** the `kagent-secrets` Argo `Application` recurses `base-apps/kagent/` (`directory.recurse: true`) — new files auto-included, no kustomization to edit. `base-apps/postgresql/` is synced by its own app.
- **Gates:** CI `scripts/validate-agent-identity.py` + Kyverno `ClusterPolicy` (`base-apps/kyverno-policies/agent-identity.yaml`, Enforce, namespace `kagent`) reject unscoped credentials. Both target `spec.declarative.*` — **whether they inspect a BYO agent is an open item to verify** (a BYO agent has no `spec.declarative`, so it may be exempt).
- **MCP client auth:** the `agent-docs` `RemoteMCPServer` has no `headersFrom` (in-cluster, unauthenticated to clients — the GitHub token lives inside the MCP server), so the container needs **no** GitHub token; `backstage-catalog` injects a Bearer token, so the container needs its own `BACKSTAGE_MCP_TOKEN`. *(Re-confirm agent-docs in the plan.)*

## Container credential requirements

The BYO container needs exactly three secret env vars; everything else is plain config:

- `ANTHROPIC_API_KEY` — the agent's own scoped Anthropic key.
- `BACKSTAGE_MCP_TOKEN` — the agent's own scoped copy of the Backstage MCP bearer token.
- `MEMORY_DB_URL` — `postgresql://homelab_agent:<pw>@postgresql.postgresql.svc.cluster.local:5432/homelab_agent`.

## Deliverables

### A. Memory database provisioning (`base-apps/postgresql/`)

1. `init-homelab-agent-db.yaml` — idempotent Argo **Sync-hook** Job (image `pgvector/pgvector`, admin login from the existing Postgres admin secret) running SQL: create role `homelab_agent` LOGIN with the ESO-provided password (idempotent `\gexec` guard), create database `homelab_agent` OWNER `homelab_agent`, `CREATE EXTENSION IF NOT EXISTS vector`. Modeled on `init-kagent-db.yaml`; `ttlSecondsAfterFinished` + hook delete policy.
2. ESO chain in the **`postgresql`** namespace to give the Job the password: `eso-homelab-agent-db-serviceaccount.yaml` + `homelab-agent-db-secret-store.yaml` (role `homelab-agent-db`) + `homelab-agent-db-external-secret.yaml` (reads `k8s-secrets/homelab-agent-db`, key `password` → Secret `homelab-agent-db`). Mirrors the cross-namespace `kagent-db` duplication.

### B. Scoped credentials for the agent (`base-apps/kagent/`)

For each of the two Vault paths, a dedicated ESO ServiceAccount + SecretStore, then ExternalSecrets:

1. **App secrets** — `eso-homelab-agent-serviceaccount.yaml`, `homelab-agent-secret-store.yaml` (role `homelab-agent`, SA `eso-homelab-agent`), `homelab-agent-external-secret.yaml` reading `k8s-secrets/homelab-agent` keys `anthropic-api-key`, `backstage-token` → Secret `homelab-agent-secrets`.
2. **DB DSN** — `eso-homelab-agent-db-serviceaccount.yaml`, `homelab-agent-db-secret-store.yaml` (role `homelab-agent-db`, SA `eso-homelab-agent-db`), `homelab-agent-db-external-secret.yaml` reading `k8s-secrets/homelab-agent-db` key `password`, **templating** `MEMORY_DB_URL: "postgresql://homelab_agent:{{ .password }}@postgresql.postgresql.svc.cluster.local:5432/homelab_agent"` into Secret `homelab-agent-db` (in the `kagent` namespace).

*(The DB Vault path `k8s-secrets/homelab-agent-db` is read by two SecretStores — one per namespace — each with its own SA + role, since ESO is namespace-scoped.)*

### C. The BYO Agent CR (`base-apps/kagent/agents/homelab-agent.yaml`)

- `kind: Agent`, `spec.type: BYO`.
- `metadata.labels`: `capability.homelab/class: read`, `arigsela.com/idp-managed: "true"`, plus the standard `app.kubernetes.io/*` set. `metadata.annotations`: the `terasky.backstage.io/*` + `backstage.io/*` IDP annotations carried from `homelab-knowledge`.
- `spec.description` + `spec.byo.deployment`: `image: 852893458518.dkr.ecr.us-east-2.amazonaws.com/homelab-agent:v0.2.0`, `ports: [8080]`, `serviceAccountName: homelab-agent` (dedicated pod SA), `resources` (requests 100m/256Mi, limits 1000m/1Gi, matching `homelab-knowledge`), `imagePullSecrets` per the cluster's ECR-auth pattern.
- `env`:
  - **secretKeyRef:** `ANTHROPIC_API_KEY` + `BACKSTAGE_MCP_TOKEN` (Secret `homelab-agent-secrets`), `MEMORY_DB_URL` (Secret `homelab-agent-db`).
  - **plain:** `AGENT_DOCS_MCP_URL=http://agent-docs-mcp.kagent:3000/mcp`, `BACKSTAGE_MCP_URL=http://backstage.backstage.svc.cluster.local/api/mcp-actions/v1/catalog`, `K8S_READER_A2A_URL=http://k8s-reader.kagent.svc.cluster.local:8080`, `OLLAMA_BASE_URL=http://ollama.ollama.svc.cluster.local:11434`, `EMBEDDING_MODEL=nomic-embed-text`, `MODEL_NAME=claude-sonnet-4-6`, `MEMORY_NAMESPACE=homelab-agent`, `AGENT_URL=http://homelab-agent.kagent.svc.cluster.local:8080`, `LOG_LEVEL=INFO`. (`KAGENT_URL`/config injected by kagent for the checkpointer.)
- `a2aConfig.skills`: the three skills (`repo-knowledge`, `cluster-troubleshooting`, `deployment-guidance`) with examples/tags, carried verbatim from `homelab-knowledge`.
- `spec.byo.deployment.serviceAccountName` → a `ServiceAccount homelab-agent` manifest (non-credential-bearing; for pod identity/future RBAC).

### D. Vault provisioning (operator-run — I provide the commands)

A copy-pasteable block the user runs once against Vault (root token):
- `vault kv put k8s-secrets/homelab-agent-db password="$(openssl rand -hex 32)"` (random, URL-safe hex so the DSN needs no escaping; never printed).
- `vault kv put k8s-secrets/homelab-agent anthropic-api-key=<value> backstage-token=<value>` (user supplies their real values).
- Two `vault policy write` (each reads only its one path).
- Three `vault write auth/kubernetes/role/…` — `homelab-agent` (bound to `eso-homelab-agent` in `kagent`), `homelab-agent-db` (bound to `eso-homelab-agent-db` in `kagent`), and `homelab-agent-db` again bound to `eso-homelab-agent-db` in `postgresql` — or one role with both SA/namespace bounds, whichever the Vault k8s-auth role schema allows (resolve in the plan).

### E. NetworkPolicy

None — see decisions.

## Rollout

1. Operator runs the Vault commands (D).
2. Merge the manifests → Argo syncs: the init-Job creates the `homelab_agent` DB/role; ESO materializes the Secrets; the BYO Deployment starts and serves A2A on :8080; `store.setup()` creates the memory tables in `homelab_agent`.
3. Validate: `homelab-agent` answers the three skill areas at parity with `homelab-knowledge` over A2A/`/mcp`; memory recall works; traces flow.
4. **Cutover (separate, later):** retire the Declarative `homelab-knowledge` CR once parity holds. Fully reversible.

## Testing / validation

- `kubectl -n postgresql logs job/init-homelab-agent-db` shows idempotent success; re-sync is a no-op.
- ExternalSecrets reach `SecretSynced`; the three env Secrets exist with expected keys.
- Pod healthy: `/health` 200, agent card served, three skills present.
- Memory: ask a question, then a related one in a new turn — recall surfaces the prior exchange (and survives a pod restart, proving durable persistence).
- Agent-identity gates: CI validator + Kyverno pass (or, if they don't inspect BYO, that's confirmed and noted).

## Open items (resolve in the plan)

1. Confirm `agent-docs` needs no client auth (container reaches `agent-docs-mcp.kagent:3000/mcp` unauthenticated).
2. Confirm whether the agent-identity **CI validator + Kyverno** inspect BYO agents; if they do, ensure the CR satisfies them; if not, note the exemption.
3. Resolve the Vault kubernetes-auth role shape for the DB secret consumed in **two** namespaces (one role with two SA/namespace bounds, or two roles).
4. Confirm the Postgres **admin** login the init-Job uses (the existing `init-kagent-db.yaml` admin secret/keys) and the `imagePullSecrets`/ECR-auth pattern for the BYO Deployment.
5. Note the watch-item: the DB-init manifest under `base-apps/postgresql/` is outside the agent-identity CI glob (`base-apps/kagent/`), so a scoping mistake there wouldn't be caught by CI — keep that credential minimal (DB password only).

## Success criteria

- `homelab-agent` (BYO, v0.2.0) runs in `kagent`, serves A2A on :8080, answers the three skill areas at parity, with **memory enabled**: recalls prior exchanges and persists across restarts, using a dedicated `homelab_agent` DB in the pgvector Postgres.
- All credentials scoped (own Vault paths/policies/roles/ESO/SecretStores); no shared-key reuse; both agent-identity gates green (or BYO-exemption confirmed).
- No chart bump; no NetworkPolicy; the Declarative agent still runs (coexistence). Nothing secret in git or chat.

## Deferred (not this increment)

- Retiring `homelab-knowledge` (cutover).
- Schema-scoped (vs database-scoped) least-privilege DB role.
- Per-agent Anthropic budget caps; NetworkPolicy egress lockdown; Istio AuthorizationPolicy.
