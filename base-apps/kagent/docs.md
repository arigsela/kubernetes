---
type: "Kubernetes App Guide"
title: "kagent"
description: "kagent 1.0.0-alpha11: Kubernetes-native AI agents (AgentTemplate + Harness + Agent, api.kagent.dev/v1alpha3) running as gVisor actors on Agent Substrate, with MCP tool servers"
app: kagent
catalog_entity: kagent
kind: docs
namespace: kagent
last_reviewed: 2026-10-11
status: stable
tags: [ai-agent, kagent, mcp, anthropic, substrate]
sources:
  - base-apps/kagent.yaml
  - base-apps/kagent-crds.yaml
  - base-apps/kagent-secrets.yaml
  - base-apps/kagent/agent-templates/homelab-knowledge.yaml
  - base-apps/kagent/agent-templates/k8s-reader.yaml
  - base-apps/kagent/agent-templates/k8s-agent.yaml
  - base-apps/kagent/agent-templates/istio-agent.yaml
  - id: build-orchestrator
    resource: base-apps/kagent/agent-templates/build-orchestrator.yaml
    title: build-orchestrator AgentTemplate (sub-agent hand-off)
  - base-apps/kagent/agent-templates/skill-suggester.yaml
  - base-apps/kagent/agent-templates/dungeon-crawler-carl-agent.yaml
  - base-apps/kagent/agents/homelab-knowledge.yaml
  - base-apps/kagent/agents/k8s-reader.yaml
  - base-apps/kagent/agents/k8s-agent.yaml
  - base-apps/kagent/agents/istio-agent.yaml
  - base-apps/kagent/agents/build-orchestrator.yaml
  - base-apps/kagent/agents/skill-suggester.yaml
  - base-apps/kagent/agents/dungeon-crawler-carl-agent.yaml
  - base-apps/kagent/harnesses/kagent-memory.yaml
  - base-apps/kagent/harnesses/kagent-stateless.yaml
  - base-apps/kagent/harnesses/kagent-chat.yaml
  - id: sonnet-config
    resource: base-apps/kagent/model-configs/anthropic-claude-sonnet-5-5.yaml
    title: Sonnet 5.5 ModelConfig (maxTokens, prompt caching)
  - base-apps/kagent/model-configs/anthropic-claude-haiku-5-5.yaml
  - base-apps/kagent/embedding-model-config.yaml
  - base-apps/kagent/agent-docs-mcp.yaml
  - base-apps/kagent/agent-docs-mcp-remote.yaml
  - base-apps/kagent/brave-search-mcp.yaml
  - base-apps/kagent/brave-search-mcp-remote.yaml
  - base-apps/kagent/backstage-catalog-mcp.yaml
  - base-apps/kagent/kagent-postgres-external-secret.yaml
  - base-apps/kagent/kagent-anthropic-external-secret.yaml
  - base-apps/kagent/kagent-anthropic-secret-store.yaml
  - base-apps/kagent/eso-kagent-anthropic-serviceaccount.yaml
  - base-apps/kagent/kagent-db-secret-store.yaml
  - base-apps/kagent/agent-docs-mcp-secret-store.yaml
  - base-apps/kagent/eso-agent-docs-mcp-serviceaccount.yaml
  - base-apps/kagent/brave-search-mcp-external-secret.yaml
  - base-apps/kagent/brave-search-mcp-secret-store.yaml
  - base-apps/kagent/eso-brave-search-mcp-serviceaccount.yaml
  - base-apps/kagent/httproute.yaml
  - id: httproute-mcp
    resource: base-apps/kagent/httproute-mcp.yaml
    title: kagent-mcp HTTPRoute (/mcp and /agents/kagent/)
  - base-apps/kagent/certificate.yaml
  - base-apps/kagent/certificate-mcp.yaml
  - base-apps/kagent/reference-grant.yaml
  - base-apps/kagent/reference-grant-mcp.yaml
  - base-apps/cluster-rbac/kagent-tools.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - base-apps/postgresql/init-kagent-v1-db.yaml
  - base-apps/substrate/docs.md
  - scripts/ask-hk
  - scripts/kagent_a2a.py
  - docs/plans/kagent-1-0-on-k8s-1-37-implementation-plan.md
---

# kagent

## What it is
kagent is a Kubernetes-native AI agent platform. This cluster runs **kagent 1.0.0-alpha11**
(since 2026-10-11, replacing 0.10.3 in place) on **Agent Substrate 0.5.0-alpha2**, the actor
runtime that kagent 1.0 requires (`base-apps/substrate/docs.md`). Both are alphas, pinned by
version and image digest, and move only together.

The API is `api.kagent.dev/v1alpha3`. An agent is three objects:

| Object | Says | Directory |
|---|---|---|
| `AgentTemplate` | **what**: model, system prompt, tools, sub-agents, and the `capability.homelab/class` label the guardrails check | `agent-templates/` |
| `Harness` | **how**: the runtime (`spec.kagent`, the Go ADK), its image by digest, memory and compaction, the Substrate worker pool and snapshot location | `harnesses/` |
| `Agent` | the object users chat with: `templateRef` + `harnessRef`, plus the Backstage annotations | `agents/` |

An `Agent` with an inline `spec.template` or `spec.harness` is denied at admission and in CI
(`agent-capability-refs`): the capability and runtime checks live on the referenced objects.

**There is no per-agent Deployment, Service or ServiceAccount.** Each conversation is a
**session**, and each session runs as a **gVisor actor** inside a shared worker pod of the
WorkerPool `kagent-default` (2 replicas, namespace `kagent`). An idle actor is snapshotted to
Substrate's bundled rustfs (`s3://ate-snapshots/kagent/`) and suspended, freeing its worker; the
next message restores it, possibly on the other node. The controller serves every agent's A2A
endpoint itself.

## Three Argo CD apps, one namespace
- **`kagent-crds`** (`base-apps/kagent-crds.yaml`, wave -1): chart `kagent-crds` 1.0.0-alpha11
  from `oci://ghcr.io/kagent-dev/kagent/helm`. It installs the `api.kagent.dev` CRDs (Agent,
  AgentTemplate, Harness, ModelConfig, ModelProviderConfig, RemoteMCPServer, SandboxTemplate) and,
  through its kmcp subchart, `mcpservers.kagent.dev` (v1alpha1).
- **`kagent`** (`base-apps/kagent.yaml`, wave 0): chart `kagent` 1.0.0-alpha11, Helm values only
  (no `path:`, so no directory-exclude concern). It renders the controller, UI, kmcp controller,
  the `kagent-tools` tool server, the `kagent-builtin-prompts` ConfigMap, the RemoteMCPServers
  `kagent-tool-server` and `kagent-api`, the WorkerPool `kagent-default` and the chart's
  `default-model-config`. It renders **no agents**: kagent 1.0 ships none bundled. Notable values,
  each explained in a comment beside it:
  - `database.postgres`: Secret `kagent-postgres` (key `connectionString`), role `kagent_owner`,
    schema `kagent`, pgvector in schema `extensions`. 1.0 removed `url`/`urlFile`.
  - `controller.substrate`: `ateApiEndpoint: dns:///api.ate-system.svc:443` and
    `atenetRouterURL: http://atenet-router.ate-system.svc:80`, the names the Substrate release
    (`substrate` in `ate-system`) renders.
  - `controller.a2aGatewayUrl: https://kagent-mcp.arigsela.com`: the base advertised in agent cards.
  - `controller.sessionIdleTTL: 720h`: idle sessions are deleted after 30 days (chart default 7).
    Running turns and turns waiting for approval never expire.
  - `substrateWorkerPool`: `kagent-default`, 2 replicas, `sandboxClass: gvisor`, worker image
    `ateom-gvisor` by digest, 250m/1Gi requested and 2 CPU/4Gi limit per worker. A worker's
    limits are the envelope all the sandboxes on it share.
  - `providers`: `default-model-config` is Anthropic `claude-haiku-5-5` on the Vault-backed
    `kagent-anthropic` key. The chart always renders it (Argo drops a `null`), so it points at a
    reviewed, working model. No agent in git uses it; it is the default for agents created in
    the UI.
  - `kagent-tools.rbac.create: false` (see *Tool server RBAC*), `grafana-mcp.enabled: false`, and
    OpenTelemetry traces and logs to Coroot (`otel.exporter.otlp`, gRPC 4317; metrics off).
  - Every image (controller, default agent image, UI, kmcp, tools, worker) is pinned by digest
    through its `tag`, because the chart's image helpers take no digest key. Renovate can't see
    these (Helm `valuesObject`).
- **`kagent-secrets`** (`base-apps/kagent-secrets.yaml`, wave -1, `path: base-apps/kagent`,
  `directory.recurse: true`): everything in this directory, meaning the AgentTemplates, Harnesses,
  Agents, ModelConfigs, MCP servers, routes, certificates and the `SecretStore`/`ExternalSecret`s.
  Its `directory.exclude` covers `catalog-info.yaml` and `mkdocs.yml`. Its `ignoreDifferences`
  cover only webhook-defaulted ExternalSecret and HTTPRoute fields.

Substrate's four apps (`substrate-crds`, `substrate-config`, `substrate-podcert`, `substrate`,
waves -4 to -1) must be healthy before kagent can run anything. Renovate's `kagent-substrate`
group moves all five charts in one PR, and only after approval on the Dependency Dashboard.

## Agents in Git

| Agent | Model | Harness | Class | Tools |
|---|---|---|---|---|
| `homelab-knowledge` | `anthropic-claude-sonnet-5-5` | `kagent-memory` | read | `agent-docs` (`get_file_contents`, `search_code`), `brave-search` (`brave_web_search`, `brave_news_search`), `backstage-catalog` (`get-catalog-entity`), and 8 read tools of `kagent-tool-server`. No sub-agent. The "ask hk" agent |
| `k8s-reader` | `anthropic-claude-haiku-5-5` | `kagent-stateless` | read | the same 8 read tools of `kagent-tool-server`, nothing else. No memory |
| `k8s-agent` | `anthropic-claude-sonnet-5-5` | `kagent-memory` | admin | `kagent-tool-server` bound twice: read tools `requireApproval: false`, every write/destructive tool `requireApproval: true` |
| `istio-agent` | `anthropic-claude-sonnet-5-5` | `kagent-memory` | admin | the same split, with the Istio tools |
| `build-orchestrator` | `anthropic-claude-sonnet-5-5` | `kagent-memory` | admin | no tools of its own; sub-agents `k8s-agent` and `istio-agent` |
| `skill-suggester` | `anthropic-claude-haiku-5-5` | `kagent-chat` | read | none (called by the Backstage wizard) |
| `dungeon-crawler-carl-agent` | `anthropic-claude-haiku-5-5` | `kagent-chat` | read | `brave-search` (`brave_web_search`) for book facts it is unsure of |

The class label lives on the AgentTemplate and is checked against the capability taxonomy
(`base-apps/admission-policies/agent-capability-taxonomy.yaml`): a `read` template binds only
read tools, and every write/destructive tool must sit in a binding with `requireApproval: true`.
**Approval is per binding in v1alpha3** (v1alpha2 had a per-tool list), which is why the admin
agents bind `kagent-tool-server` twice. A binding must list its `tools`: an omitted or empty list
would expose every tool the server has, including ones a later upgrade adds, and is denied.

**Sub-agents hand off, they don't delegate.** On 0.10 a `type: Agent` tool called another agent
and got its answer back. In 1.0 a `subAgent` binding compiles the child template into the
parent's runtime, and routing to it transfers the conversation: the child answers the user
directly and the parent never sees the answer.[^build-orchestrator] So `build-orchestrator` is a
router that can't combine two specialists' results (its prompt says so), and `homelab-knowledge`
binds the read tools itself instead of delegating to `k8s-reader` as it did on 0.10. A sub-agent
runs under the parent's Harness but keeps its own bindings, approval gates included. kagent
rejects a sub-agent that has sub-agents of its own.

`homelab-agent`, a LangGraph BYO agent that duplicated `homelab-knowledge`, was retired on
2026-10-11: `kagent-langgraph` doesn't implement kagent 1.0's BYO contract (gRPC A2A, TaskStore).
Its manifests, database, Vault keys and roles, and ECR repository are gone.

## Harnesses (the runtime)
All three Harnesses use the Go ADK (`spec.kagent`), image
`ghcr.io/kagent-dev/kagent/golang-adk:1.0.0-alpha11` by digest, WorkerPool `kagent-default`, and
snapshot location `s3://ate-snapshots/kagent/`. They differ only in `spec.kagent`:

| Harness | Memory | Compaction | Used by |
|---|---|---|---|
| `kagent-memory` | `embedding-model-config` | none | homelab-knowledge, k8s-agent, istio-agent, build-orchestrator |
| `kagent-stateless` | none | none | k8s-reader |
| `kagent-chat` | `embedding-model-config` | every 5 user turns, overlap 2 | skill-suggester, dungeon-crawler-carl-agent |

Keep the image, pool and snapshot location identical across them; `tests/agent-capability`
checks the image pin, and the `agent-capability-runtime` admission policy allows only the
`kagent` runtime on an image from the taxonomy's `runtime-images` list. The CRD and Substrate
both require a digest (changing the content under a fixed reference would invalidate session
snapshots), and the digest moves only in lockstep with the kagent chart.

Go ADK behaviour that matters here:
- **Thinking is dropped.** The Claude 5.5 models think by default; the Go adapter maps only
  text and tool-use blocks, so reasoning isn't carried between tool calls (kagent#3026).
- **`maxTokens: 16000`** on both Anthropic ModelConfigs. The adapter's default is 8192, which
  caps thinking plus answer together, and a truncated answer is still reported as completed
  (kagent#2895).[^sonnet-config]
- **Prompt caching** is on (`promptCaching: true`, `cacheTTL: 5m`): the adapter puts cache
  breakpoints on the tools, the system prompt and the last message. Measured 2026-10-11: 48% of
  prompt tokens served from cache overall, 86–98% within a tool loop.
- No `temperature`/`topP`/`topK`: the 5.5 models reject non-default sampling parameters.
- Compaction (`kagent-chat`) works on the 1.0 Go ADK. It is kept off the tool-loop agents: on 0.10
  the summarizer sent a malformed request once delegated replies grew large. Re-test before
  adding it to them.
- The fallback, if the Go ADK breaks, is the Python ADK image (`kagent-adk`, already on the
  taxonomy's runtime list) through a Harness `workload.command` override. Untested here.

## Model configs
- **`anthropic-claude-sonnet-5-5`** and **`anthropic-claude-haiku-5-5`** (`model-configs/`):
  Sonnet 5.5 for the agents that reason over several tools, Haiku 5.5 for the rest. Both read the
  Vault-backed `kagent-anthropic` Secret, `stream: true`, `maxTokens: 16000`, prompt caching on.
  The API key never enters the sandbox: the controller compiles the Secret reference into an
  `ate-secret://` credential, and Substrate's egress gateway injects it into each model call.
  That needs the Substrate chart's `credentialProvider.namespacePolicies` to allow atespace
  `kagent` to read namespace `kagent`; without it every model call returns 403.
- **`embedding-model-config`** (`embedding-model-config.yaml`): Ollama `nomic-embed-text`
  (768 dimensions) at `http://ollama.ollama.svc.cluster.local:11434`, used by the memory-enabled
  Harnesses (`spec.kagent.memory.modelConfigRef`), not by any AgentTemplate. Memory vectors are
  stored with pgvector in `kagent_v1`; memories expire after the controller's default 15 days.
  This is why kagent depends on `ollama`, and why changing the embedding model is a migration.
- **`default-model-config`**: the chart's, described above.

## Tools via MCP servers
In v1alpha3 an AgentTemplate MCP binding accepts only a same-namespace `RemoteMCPServer`:

| RemoteMCPServer | Points at | Tools bound |
|---|---|---|
| `agent-docs` (`agent-docs-mcp-remote.yaml`) | `http://agent-docs-mcp.kagent:3000/mcp`, the read-only GitHub MCP server that the kmcp `MCPServer` `agent-docs-mcp` deploys (`ghcr.io/github/github-mcp-server`, `--read-only --toolsets repos`, token from Secret `agent-docs-github-mcp-token`) | `get_file_contents`, `search_code` |
| `brave-search` (`brave-search-mcp-remote.yaml`) | `http://brave-search-mcp.kagent:3000/mcp`, the official Brave Search MCP server that the kmcp `MCPServer` `brave-search-mcp` deploys (`docker.io/mcp/brave-search`, digest-pinned; key from Secret `brave-search-mcp-api-key`; only 2 of its 8 tools enabled). Web results are untrusted text, so it is bound to read-class agents only, never to the operator agents | `brave_web_search`, `brave_news_search` |
| `backstage-catalog` (`backstage-catalog-mcp.yaml`) | Backstage's MCP actions endpoint, `http://backstage.backstage.svc.cluster.local/api/mcp-actions/v1/catalog`; the `Authorization` header comes from Secret `backstage-mcp-token` and is injected by Substrate's egress gateway | `get-catalog-entity` |
| `kagent-tool-server` (chart) | `http://kagent-tools.kagent:8084/mcp`, the `kagent-tools` Deployment | read: `k8s_get_resources`, `k8s_get_resource_yaml`, `k8s_get_pod_logs`, `k8s_describe_resource`, `k8s_get_events`, `k8s_get_cluster_configuration`, `k8s_get_available_api_resources`, `k8s_check_service_connectivity`; the admin agents add the write/destructive tools in a gated binding |
| `kagent-api` (chart) | the controller's own `/mcp` | none |

The kmcp `MCPServer` only deploys a server and its Service; it registers no tools with agents.
`catalog.query-catalog-entities` is deliberately not bound (its input schema broke the 0.10 ADK
converter; re-test and classify it before adding it).

## Tool server RBAC (the floor under the capability classes)
`kagent-tools` is one Deployment and one ServiceAccount serving every agent's MCP tools,
read-only agents included. The chart binds that ServiceAccount to `*` on everything by default,
which is how the read-class `k8s-reader` listed every Secret in the cluster on 2026-07-15. So
`base-apps/kagent.yaml` sets `kagent-tools.rbac.create: false` (the chart still creates the
ServiceAccount), and `base-apps/cluster-rbac/kagent-tools.yaml` binds a **cluster-admin minus
Secrets** ClusterRole instead: every core resource except `secrets` (plus
`pods/log|exec|attach|portforward`), every non-core API group named explicitly, and kagent 1.0's
own `api.kagent.dev` read-only. A new API group is denied until a PR adds it, the same fail-closed
stance the taxonomy takes with new tools. No agent, admin class included, can read or write a
Secret through the tools; the only write path for Secrets is Vault + ESO from git. `pods/exec`
stays because `k8s_execute_command` is destructive-class and approval-gated. Contract pinned by
`tests/agent-capability/test_kagent_tools_rbac.py`.

## Secrets & database
Every `ExternalSecret` here is **credential-scoped** per the agent-identity contract
(`templates/agent-identity/README.md`): its own ESO ServiceAccount, its own `SecretStore`, its
own Vault kubernetes-auth role, and its own per-consumer Vault key. Nothing here reads the
destroyed monolithic `kagent` key.

| Secret (ExternalSecret if different) | SecretStore | ESO ServiceAccount | Vault role | Vault key (properties) |
|---|---|---|---|---|
| `agent-docs-github-mcp-token` | `vault-agent-docs-mcp` | `eso-agent-docs-mcp` | `kagent-agent-docs-mcp` | `kagent-agent-docs-mcp` (`github-token`) |
| `brave-search-mcp-api-key` | `vault-brave-search-mcp` | `eso-brave-search-mcp` | `kagent-brave-search-mcp` | `brave-search-mcp` (`api-key`); `scripts/provision-brave-search-vault.sh` |
| `backstage-mcp-token` | `vault-backstage-mcp` | `eso-backstage-mcp` | `backstage-mcp` | `kagent-backstage-mcp` (`token`) |
| `kagent-postgres` | `vault-kagent-db` | `eso-kagent-db` | `kagent-db` | `kagent-db` (`v1-user`, `v1-password`, `v1-db-name`) |
| `kagent-anthropic` (`kagent-anthropic-secrets`) | `vault-kagent-anthropic` | `eso-kagent-anthropic` | `kagent-anthropic` | `kagent-anthropic` (`anthropic-api-key`) |

Each Vault policy grants `read` on exactly one path (`k8s-secrets/data/<key>`). Substrate's CA
pools and database credentials are Substrate's (Vault keys `substrate-cas`, `substrate-db`, role
`substrate`); see `base-apps/substrate/docs.md`.

**Database.** kagent 1.0 uses database **`kagent_v1`** on the shared pgvector Postgres
(`postgresql.postgresql.svc.cluster.local:5432`, the plain Deployment, not CNPG). The controller
neither creates its database nor installs pgvector; `base-apps/postgresql/init-kagent-v1-db.yaml`
does: database `kagent_v1`, NOLOGIN role `kagent_owner` with the login user (Vault
`kagent-db`/`v1-user`) as a NOINHERIT member, schema `kagent` owned by `kagent_owner`, and pgvector
in schema `extensions`. The controller runs its migrations as `kagent_owner` in schema `kagent` and
refuses to start without pgvector. It reads `kagent-postgres` once, at startup. Sessions, tasks,
events (A2A protobuf), approvals and memory all live there; that is what the agent audit reads
(`--source v1`).

kagent 0.10's database **`kagent`** is kept as a **read-only archive**
(`default_transaction_read_only = on`) for audit history (`scripts/agent-audit.py --source v010`,
agent-audit-web). Its 0.10 memory wasn't migrated (the key format changed). Off-cluster dumps from
the cutover: `s3://mysql-backups-asela-cluster/pg/pgvector-kagent-archive-20261011T194718Z.sql.gz`
(and `pgvector-homelab_agent-archive-…` for the retired agent).

The pgvector Postgres has **no scheduled backup**, so neither `kagent_v1` nor the archive is
backed up beyond those dumps (`recovery/CLUSTER-RECOVERY.md`). Sessions are disposable by design;
memory is the only state worth keeping, and it resets at every alpha bump anyway.

## Exposure
Both public hosts are Gateway API `HTTPRoute`s in this namespace on the shared `main` Gateway
(`base-apps/istio-ingress/gateway.yaml`), each with its own cert-manager `Certificate` (issuer
`letsencrypt-route53`, DNS-01) and a `ReferenceGrant` letting the Gateway read the TLS Secret:

| Host | Route → backend | Listener | Certificate / grant |
|---|---|---|---|
| `kagent.arigsela.com` | `httproute.yaml`, `/` → `kagent-ui:8080` | `https-kagent` | `certificate.yaml` (`kagent-tls`), `reference-grant.yaml` |
| `kagent-mcp.arigsela.com` | `httproute-mcp.yaml`, `/mcp` and `/agents/kagent/` → `kagent-controller:8083` | `https-kagent-mcp` | `certificate-mcp.yaml` (`kagent-mcp-tls`), `reference-grant-mcp.yaml` |

- **A2A** (A2A v1, JSON-RPC): `POST https://kagent-mcp.arigsela.com/agents/kagent/<agent>`, agent
  card at `GET …/agents/kagent/<agent>/.well-known/agent-card.json`. In-cluster callers use
  `http://kagent-controller.kagent.svc.cluster.local:8083/agents/<ns>/<agent>` (Backstage does).
  The route is scoped to `/agents/kagent/`, so other namespaces' agents stay unreachable from
  outside.[^httproute-mcp] `scripts/ask-hk` is the client ("ask hk"; `--agent` for any other
  agent, `--context` to continue a conversation, `--approve`/`--reject` to decide a gated call);
  `scripts/capture-eval-answers.py` uses the same library (`scripts/kagent_a2a.py`). Every send
  requests the HITL extension (`https://kagent.dev/extensions/hitl/v1`): without it an approval
  pause has no request id and can only be cancelled. Answers take minutes; nothing on the path
  times out first.
- **`/mcp`**: the controller's MCP endpoint (`list_sessions`, `invoke_session`, checkpoint
  create/list, `fork_session`). It can't create a Session or invoke by agent name, so an MCP
  client (Claude Code has it registered as `kagent`) can't start a conversation: open one over
  A2A, then continue it with `invoke_session` and the same id. Approvals through MCP need the
  MCP tasks extension, which Claude Code doesn't declare; decide them with `ask-hk` or the UI.
- **Identity and access.** A2A runs in kagent's insecure mode: the caller names itself in the
  `X-User-Id` header, and a conversation belongs to that user (`scripts/ask-hk` defaults to
  `admin@kagent.dev`, Backstage sends `backstage-portal`). The Gateway's IP allow-list
  (`base-apps/istio-ingress/authorizationpolicy.yaml`) is the **only** control on both hosts;
  off-network calls get 403. kagent's trusted-proxy OIDC mode doesn't verify tokens, so it is not
  enabled. (The nginx basic auth on `/mcp` was dropped in the move to Gateway API, SPEC T52; its
  Vault-backed htpasswd was removed on 2026-10-11.)
- **Human approval (HITL).** A call through a `requireApproval: true` binding pauses the turn
  (task state `TASK_STATE_INPUT_REQUIRED`). Approve or reject it in the UI, or over A2A with the
  HITL extension `https://kagent.dev/extensions/hitl/v1`: the paused task carries a
  `tool_approval_request`, and the reply is a `tool_approval_response` with
  `approvals: [{id, approved, rejection_reason}]`. Verified 2026-10-11 on `k8s-agent`: reject
  keeps the object, approve executes the call.

Keep UI paths on `kagent.arigsela.com` and API paths on `kagent-mcp.arigsela.com` (the MCP
endpoint once shadowed the UI's own `/mcp` page). A new host needs a Gateway listener, a
`Certificate`, a `ReferenceGrant`, an allow-list rule, a hand-made Route 53 A record, and the
hostname in `MANAGED_HOSTNAMES` in `base-apps/wan-ip-monitor/cronjob.yaml`.

## Observability and audit
- **Traces and logs**: OpenTelemetry to Coroot. Actor output reaches stdout of the
  `kagent-default` worker pods, labelled with the session (`ate.actor.name: session-<id>`) and the
  template (`ate.template.name: <agent>-<hash>`); Alloy ships it to Loki.
- **Agent action record**: `scripts/agent-audit.py` (default `--source v1`) reads `kagent_v1`
  through the SELECT-only `kagent_audit_ro` role; the CronJobs in `base-apps/postgresql/` export it
  to S3, score session risk, and alert on a write/destructive call that ran without an approved
  decision. On kagent 1.0 that is judged per call. The Grafana rules fail closed (`noDataState:
  Alerting`): a check that stops reporting alerts. agent-audit-web shows both databases.
- **Backstage**: the kubernetes-ingestor builds agent entities from the `Agent` objects (each
  carries `terasky.backstage.io/add-to-catalog` and `component-type: kagent-agent`), and the
  entity card reads the Agent and its AgentTemplate (`base-apps/backstage/docs.md`).

## Evaluation gate
Every sync of the `kagent-secrets` app ends with the PostSync Job `eval-gate` (`eval-gate-job.yaml`,
code in the generated `eval-gate-code-configmap.yaml` from `scripts/eval_gate.py`; V93). It hashes
each agent's live definition (Agent, AgentTemplate, Harness, ModelConfig specs) and, when the hash
changed since the last scored run, asks the agent every question in `tests/eval-corpus/<agent>.yaml`
over in-cluster A2A as service account `eval-gate` (a projected token, audience `kagent`), scores the
answers with the rubric and whatever judge the `eval-gate` Secret allows (`judge: none` until the
owner runs `scripts/provision-eval-gate-vault.sh`), and writes one JSON line per agent
(`event: eval-gate`) that Loki carries to the Grafana rule `Agent eval regression` (→ Slack via n8n;
the dispatcher routes it to a human). With a GitHub token in that Secret it also posts the commit
status `eval/<agent>` on the synced revision. The baseline and hash per agent live in ConfigMap
`eval-gate-state`. A regression never fails the sync (exit 0; the status and the alert are the
gate); a run that could not score exits 1 and Argo shows the hook failed. Only `homelab-knowledge`
has a corpus today; a new agent needs a corpus file to be scored.

## Gotchas & tribal knowledge
- **Every alpha bump is a clean-database migration** so far (alpha11 itself needed one):
  sessions and memory reset, and audit history for the old alpha survives only in a dump. See
  the runbook's alpha-bump procedure.
- **Nodes.** Worker pods can't run on `k3s-control-01` (tainted; Substrate's atelet doesn't
  tolerate it), so all sessions run on the two workers. The WorkerPool has no pod anti-affinity:
  after a drain both workers can land on one node and stay there (seen 2026-10-11 after
  `patch.yml`; open as SPEC T102).
- **Reboots and drains** interrupt agents: a drain evicts workers and may crash a turn in flight,
  a worker that survives a reboot keeps its name but gets a new IP its actors never learn
  (substrate#1837, `ansible/playbooks/patch.yml` deletes a rebooted node's workers), and resume is
  tied to a node with a short deadline (substrate#1761). Substrate's single-replica components
  (ate-controller, atenet-router, podcertificate-controller, rustfs) make agents briefly
  unavailable while their node drains.
- **Substrate CAs expire after 365 days** and nothing renews them; the `substrate-ca-expiry` alert
  warns 30 days ahead. Rotation restarts kagent too: `base-apps/substrate/runbook.md`.
- **The `agent-docs` GitHub token is a fine-grained PAT** (read-only Contents + Metadata on
  `arigsela/kubernetes`) in Vault key `kagent-agent-docs-mcp`, property `github-token`; it expires
  and must be re-minted by the owner (the 2026-10-11 outage, SPEC T101, was an expired token).
  Rotation: `base-apps/kagent/runbook.md`, "can't read the repo".
- **`kagent-builtin-prompts`** is still rendered by the 1.0 chart; `homelab-knowledge`,
  `k8s-agent` and `istio-agent` include its `safety-guardrails` and `kubernetes-context` snippets
  (and `k8s-agent` also `tool-usage-best-practices`) through `promptTemplate.dataSources`.
  Removing the ConfigMap, or a chart bump that renames a snippet, breaks those prompts.
- The 0.10 A2A skills (`a2aConfig.skills`) are gone: 1.0 generates the agent card. The skills each
  agent advertised are kept as comments in its AgentTemplate.

[^build-orchestrator]: build-orchestrator AgentTemplate (sub-agent hand-off)
[^sonnet-config]: Sonnet 5.5 ModelConfig (maxTokens, prompt caching)
[^httproute-mcp]: kagent-mcp HTTPRoute (/mcp and /agents/kagent/)
