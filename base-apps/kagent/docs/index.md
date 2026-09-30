---
type: "Kubernetes App Guide"
title: "kagent"
description: "Kubernetes-native AI agent platform (kagent Helm controller, declarative agents, MCP tool servers)"
app: kagent
catalog_entity: kagent
kind: docs
namespace: kagent
last_reviewed: 2026-09-30
status: current
tags: [ai-agent, kagent, mcp, anthropic]
sources:
  - base-apps/kagent.yaml
  - base-apps/kagent-secrets.yaml
  - base-apps/kagent-crds.yaml
  - base-apps/kagent/embedding-model-config.yaml
  - base-apps/kagent/kagent-anthropic-external-secret.yaml
  - base-apps/kagent/kagent-anthropic-secret-store.yaml
  - base-apps/kagent/eso-kagent-anthropic-serviceaccount.yaml
  - base-apps/kagent/external-secrets.yaml
  - base-apps/kagent/eso-agent-docs-mcp-serviceaccount.yaml
  - base-apps/kagent/agent-docs-mcp-secret-store.yaml
  - base-apps/kagent/model-configs/anthropic-claude-sonnet-4-6.yaml
  - base-apps/kagent/mcp-basic-auth-external-secret.yaml
  - base-apps/kagent/agents/homelab-knowledge.yaml
  - base-apps/kagent/agents/k8s-reader.yaml
  - base-apps/kagent/agents/homelab-agent.yaml
  - base-apps/kagent/homelab-agent-external-secret.yaml
  - base-apps/kagent/homelab-agent-secret-store.yaml
  - base-apps/kagent/homelab-agent-db-external-secret.yaml
  - base-apps/kagent/homelab-agent-db-secret-store.yaml
  - base-apps/kagent/homelab-agent-serviceaccount.yaml
  - base-apps/kagent/agent-docs-mcp.yaml
  - base-apps/kagent/agent-docs-mcp-remote.yaml
  - base-apps/kagent/backstage-catalog-mcp.yaml
  - base-apps/kagent/httproute.yaml
  - base-apps/kagent/httproute-mcp.yaml
  - base-apps/kagent/certificate.yaml
  - base-apps/kagent/certificate-mcp.yaml
  - base-apps/kagent/reference-grant.yaml
  - base-apps/kagent/reference-grant-mcp.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - base-apps/postgresql/init-homelab-agent-db.yaml
---

# kagent

## What it is
kagent is a Kubernetes-native AI agent platform: a controller (installed via Helm, `base-apps/kagent.yaml`, `chart: kagent`, `repoURL: ghcr.io/kagent-dev/kagent/helm`, `targetRevision: 0.10.2`) that reconciles declarative CRDs — `Agent`, `ModelConfig`, `MCPServer`, `RemoteMCPServer` — into running agent workloads. The CRDs themselves come from a sibling Helm install, `base-apps/kagent-crds.yaml` (`chart: kagent-crds`, same repo/version). Both are deployed into the `kagent` namespace.

## Two Argo CD apps, one namespace
- **`base-apps/kagent.yaml`** installs the controller/UI/bundled agents via Helm `valuesObject` (no `path: base-apps/kagent`, so it has no directory-exclude concern). Notable values: the LLM provider (`providers.default: anthropic`, model `claude-haiku-4-5-20251001`, key from `apiKeySecretRef: kagent-anthropic`), the database wiring (`database.postgres.bundled.enabled: false`, `urlFile: /etc/kagent/secrets/db-url`, `vectorEnabled: true`, sourced from a mounted `kagent-db-credentials` Secret), per-agent enable switches for the chart's bundled agents (all off — `k8s-agent` and `istio-agent` are disabled here and owned in Git under `agents/` so their memory + HITL `requireApproval` gates are declarative), `grafana-mcp.enabled: false` (it never reached Grafana, so it and `observability-agent` were removed), no `OPENSHELL_GATEWAY_URL` (openshell is gone; setting it crashloops the controller), OpenTelemetry export to Coroot, and the upstream UI image (a Node 20 fork was retired in 2026-09 once the nodes were confirmed x86-64-v2 capable). Note these agent keys are **top-level** chart keys (Helm subchart aliases), not nested under an `agents:` map — the chart has no such map, and nesting them silently disables the whole block.
- **`base-apps/kagent-secrets.yaml`** (`path: base-apps/kagent`, `directory: {recurse: true}`) syncs everything in this directory: the `Agent`, `ModelConfig`, `MCPServer`/`RemoteMCPServer` manifests, the HTTPRoutes/Certificates/ReferenceGrants, and the `SecretStore`/`ExternalSecret`s that back them. This is the Application whose `directory.exclude` covers `catalog-info.yaml` and `mkdocs.yml`.

Every Declarative agent in git sets `spec.declarative.runtime: python` explicitly. Since chart 0.10 the CRD default is `go`, which would silently move an agent that omits the field to a different runtime image — and the go runtime ignores `context.compaction` (kagent-dev/kagent#2728).

## Agents in Git
Each agent carries a `capability.homelab/class` label (`read`/`admin`) checked against the capability taxonomy (`base-apps/admission-policies/agent-capability-taxonomy.yaml`). Delegation is capability-transitive: an agent's effective capability includes everything it can reach by delegating.

| Agent | Type | Model | Memory | Class | Notes |
|---|---|---|---|---|---|
| `homelab-knowledge` | Declarative | `anthropic-claude-sonnet-4-6` | yes | read | Repo/catalog Q&A (the "ask hk" agent); delegates live-state questions to `k8s-reader` |
| `k8s-reader` | Declarative | `default-model-config` | **none** | read | Read-only half of the Kubernetes capability; binds only read-classified `kagent-tool-server` tools, so nothing needs `requireApproval` |
| `k8s-agent`, `istio-agent` | Declarative | `default-model-config` | yes | admin | Operator agents; mutating tools gated by `requireApproval` |
| `build-orchestrator` (`build-orchestrator.yaml`) | Declarative | `default-model-config` | yes | admin | Delegates to `k8s-agent` and `istio-agent` |
| `skill-suggester`, `dungeon-crawler-carl-agent` | Declarative | `default-model-config` | yes | read | |
| `homelab-agent` | BYO | `claude-sonnet-5` (env `MODEL_NAME`) | own Postgres DB | read | LangGraph container; see below |

## Model configs
- **`default-model-config`** — the chart's default `ModelConfig`, generated from the `providers` block in `kagent.yaml` (Anthropic, `claude-haiku-4-5-20251001`). Used by every Declarative agent except `homelab-knowledge`.
- **`anthropic-claude-sonnet-4-6`** (`model-configs/anthropic-claude-sonnet-4-6.yaml`) — a sonnet-tier `ModelConfig` serving `claude-sonnet-4-6`, used by `homelab-knowledge` (it needs larger context and more reliable tool-calling for multi-step delegation). It stays on 4.6 because `claude-sonnet-5` breaks kagent's python runtime on multi-step tool loops (see the manifest header and the runbook). Its `apiKeySecret` (`anthropic-claude-sonnet-4-6`) is a dedicated key, **not** the shared `kagent-anthropic` one, and it is the one agent credential here that is **not in Vault** — see *Secrets & database*.
- **`embedding-model-config`** (`embedding-model-config.yaml`) — points at **Ollama** (`http://ollama.ollama.svc.cluster.local:11434`, model `nomic-embed-text`), used as `memory.modelConfig` by every Declarative agent that has a `memory` block (all but `k8s-reader`) for RAG/embedding recall. This is why the `kagent` component depends on `ollama`.

## Tools via MCP servers
Agents get tools by referencing `MCPServer`/`RemoteMCPServer` objects in their `spec.declarative.tools`. Two patterns exist side by side:
- **stdio proxy**: `agent-docs-mcp.yaml` deploys the read-only GitHub MCP server (`ghcr.io/github/github-mcp-server`, `--read-only --toolsets repos`) as a container; `agent-docs-mcp-remote.yaml` is the `RemoteMCPServer` that registers its tools (`get_file_contents`, `search_code`) with kagent — a container-only `MCPServer` does not register tools in this kagent version, only a `RemoteMCPServer` does.
- **In-cluster remote**: `backstage-catalog-mcp.yaml` points at Backstage's own MCP endpoint (`http://backstage.backstage.svc.cluster.local/api/mcp-actions/v1/catalog`) for resolved-entity/dependency lookups (`get-catalog-entity`), with the `Authorization` header injected from the `backstage-mcp-token` Secret.

Agents only get the `toolNames` they explicitly list (e.g. `agents/homelab-knowledge.yaml` binds `get_file_contents`/`search_code` from `agent-docs` plus `get-catalog-entity` from `backstage-catalog`, and delegates to `k8s-reader` via a `type: Agent` tool entry) — an unlisted tool resolves to nothing and the agent reports "Unknown Tool". A `type: Agent` entry naming an agent that is not deployed resolves to nothing in the same way, so trim delegations when disabling an agent.

## homelab-agent (BYO)
`agents/homelab-agent.yaml` is a **BYO** `Agent`: kagent runs a container we build (LangGraph, source in `arigsela/claude-agents` → `homelab-agent/`, built and pushed with its `deploy-to-ecr.sh <tag>`, image `852893458518.dkr.ecr.us-east-2.amazonaws.com/homelab-agent:<tag>`) instead of rendering one from a Declarative spec. It answers the same three skill areas as `homelab-knowledge` (repo knowledge, cluster troubleshooting, deployment guidance) using the same backends, wired as env literals: `agent-docs-mcp` for repo reads, Backstage's catalog MCP, and `k8s-reader` over A2A for live state.
- **Contract (kagent 0.9.11-era BYO, still used on 0.10.2):** no `ports` field — kagent assumes A2A on `:8080` and creates the Service; no `a2aConfig` (Declarative-only) — skills are advertised by the agent card the container serves at `/.well-known/agent.json`. No `ModelConfig` either: the model is the `MODEL_NAME` env and the key is a pod env. It runs `claude-sonnet-5`, which works here (images v0.4.0+; earlier images sent `temperature=0`, which Sonnet 5 rejects) even though Declarative agents can't use it.
- **Pod identity:** ServiceAccount `homelab-agent` (`homelab-agent-serviceaccount.yaml`), non-credential-bearing — secrets arrive as env from the ESO-created Secrets below. The ECR pull secret is injected at admission (`base-apps/admission-policies/inject-ecr-pull-secret.yaml`), not declared.
- **Conversation memory:** a dedicated `homelab_agent` database + owner role in the shared pgvector Postgres, created by the idempotent Sync-hook Job `base-apps/postgresql/init-homelab-agent-db.yaml` (DB-level isolation from kagent's own data). The agent reaches it through `MEMORY_DB_URL`, templated by `homelab-agent-db-external-secret.yaml`; embeddings come from Ollama `nomic-embed-text` (`MEMORY_NAMESPACE=homelab-agent`). An empty `MEMORY_DB_URL` turns memory off.
- **Credentials:** its own Anthropic key and its own copy of the Backstage MCP token (no reuse of `kagent-anthropic`/`backstage-mcp-token`), each from a scoped Vault path — see the table below. The Vault side was provisioned once by hand; the steps are in the runbook.
- **Agent-identity gates:** the CI validator (`scripts/validate-agent-identity.py`) and the `agent-identity` admission policy read `spec.declarative.*` for their model/toolNames checks, so they pass a BYO agent without inspecting it. Its `ExternalSecret`s are still gated like everyone else's.
- **Coexistence:** `homelab-agent` runs alongside `homelab-knowledge`. The original design planned to retire `homelab-knowledge` once A2A parity held; that was reversed on 2026-09-26 (Decisions table in `docs/plans/kagent-1-0-feasibility-spikes-implementation-plan.md`): **`homelab-knowledge` stays the single knowledge agent, and `homelab-agent` is retired at the kagent 1.0 migration**, whose BYO contract (gRPC A2A, no checkpoint API) `kagent-langgraph` doesn't support yet. Don't build new dependencies on `homelab-agent`.
- **Deliberately not done:** schema-scoped (rather than database-owner) DB role, per-agent Anthropic budget caps, a NetworkPolicy (none exists in the namespace; egress is open).

## Secrets & database
Every `ExternalSecret` in `base-apps/kagent/` is **credential-scoped** per the agent-identity contract (`templates/agent-identity/README.md`): each has its own ESO ServiceAccount, its own `SecretStore`, its own Vault kubernetes-auth role, and its own per-consumer Vault key. No `ExternalSecret` in this namespace reads the monolithic `kagent` key any more, so a token minted for one consumer cannot read another's secrets.

| Secret (ExternalSecret if different) | SecretStore | ESO ServiceAccount | Vault role | Vault key |
|---|---|---|---|---|
| `agent-docs-github-mcp-token` | `vault-agent-docs-mcp` | `eso-agent-docs-mcp` | `kagent-agent-docs-mcp` | `kagent-agent-docs-mcp` |
| `backstage-mcp-token` | `vault-backstage-mcp` | `eso-backstage-mcp` | `backstage-mcp` | `kagent-backstage-mcp` |
| `kagent-db-credentials` | `vault-kagent-db` | `eso-kagent-db` | `kagent-db` | `kagent-db` |
| `kagent-anthropic` (`kagent-anthropic-secrets`) | `vault-kagent-anthropic` | `eso-kagent-anthropic` | `kagent-anthropic` | `kagent-anthropic` |
| `homelab-agent-secrets` | `vault-homelab-agent` | `eso-homelab-agent` | `homelab-agent` | `homelab-agent` |
| `homelab-agent-db` | `vault-homelab-agent-db` | `eso-homelab-agent-db` | `homelab-agent-db` | `homelab-agent-db` |
| `kagent-mcp-basic-auth` — **orphaned** | `vault-kagent-mcp-basic-auth` | `eso-kagent-mcp-basic-auth` | `kagent-mcp-basic-auth` | `kagent-mcp-basic-auth` |

Each Vault policy grants `read` on exactly one path (`k8s-secrets/data/<key>`). The `homelab-agent-db` role is bound to `eso-homelab-agent-db` in **both** `kagent` (the agent's DSN) and `postgresql` (the init Job's password, `base-apps/postgresql/homelab-agent-db-external-secret.yaml`).

`kagent-mcp-basic-auth` has had **no consumer since T52** (2026-07-31): it was the htpasswd for the old nginx `/mcp` ingress, and basic auth was dropped in the move to Gateway API (see *Exposure*). Its ExternalSecret, SecretStore and ESO ServiceAccount still sync but gate nothing; they are candidates for removal.

**Exception — not in Vault:** the `anthropic-claude-sonnet-4-6` Secret (the `apiKeySecret` of the ModelConfig of the same name) was created by the kagent UI, has no ExternalSecret, and carries an **ownerReference to the ModelConfig**. Renaming or deleting that ModelConfig makes Argo prune it and the garbage collector delete the Secret, losing the key. To rotate the key, update the Secret directly; to rename, move the key into Vault + ESO first (manifest header).

The broad `vault-backend` `SecretStore` is **gone**. It authenticated as the namespace's `default` ServiceAccount against Vault role `kagent`, which could read the monolithic `k8s-secrets/kagent` key holding every credential at once. Its last consumer was `kagent-anthropic-secrets`, an `ExternalSecret` that existed only in-cluster (tracked by a since-deleted Argo app `kagent-config`); it is now adopted here (`kagent-anthropic-external-secret.yaml`) and scoped like everything else, and the broad store, the `kagent` Vault role/policy and the monolithic key have all been removed.

kagent uses the **shared PostgreSQL** instance (`base-apps/postgresql/`) rather than the chart's bundled DB (`database.postgres.bundled.enabled: false` in `kagent.yaml`): `external-secrets.yaml` here syncs `kagent-db-credentials` (`db-url`, `db-user`, `db-password`, `db-name`) from Vault key `kagent-db`, matching the `kagent` role/database that `postgresql`'s `init-kagent-db` Job provisions with the `vector` extension enabled (see `base-apps/postgresql/docs.md`). The controller mounts that Secret's `db-url` at `/etc/kagent/secrets/db-url` (`kagent.yaml`'s `controller.volumes`/`volumeMounts`).

## Exposure
Both public hosts are Gateway API `HTTPRoute`s in this namespace, attached to the shared `main` Gateway (`base-apps/istio-ingress/gateway.yaml`), each with its own cert-manager `Certificate` (issuer `letsencrypt-route53`, DNS-01) and a `ReferenceGrant` letting the Gateway read the TLS Secret:

| Host | Route → backend | Listener | Certificate / grant |
|---|---|---|---|
| `kagent.arigsela.com` | `httproute.yaml`, `/` → `kagent-ui:8080` | `https-kagent` | `certificate.yaml` (`kagent-tls`), `reference-grant.yaml` |
| `kagent-mcp.arigsela.com` | `httproute-mcp.yaml`, `/mcp` → `kagent-controller:8083` | `https-kagent-mcp` | `certificate-mcp.yaml` (`kagent-mcp-tls`), `reference-grant-mcp.yaml` |

Both hosts are IP-restricted by the Gateway's allow-list (`base-apps/istio-ingress/authorizationpolicy.yaml`). For `kagent-mcp` that allow-list is now the **only** control: `/mcp` (`invoke_agent`/`list_agents`, used by Claude Code via `~/.claude.json`) has no auth of its own, and the nginx basic auth was dropped rather than rebuilt (T52: Istio has no equivalent; ext_authz or Dex OIDC were the alternatives). Agents are also invocable in-cluster over **A2A** at `http://<agent>.kagent.svc.cluster.local:8080`.

The MCP endpoint deliberately lives on its **own host**. It used to be served at `kagent.arigsela.com/mcp`, where it shadowed the kagent UI's own "MCP & tools" page (also `/mcp`) and every `/mcp/*` UI route. Keep UI-facing paths on `kagent.arigsela.com` and API/endpoint paths on `kagent-mcp.arigsela.com`.

A new host needs: a listener on the `main` Gateway, a `Certificate` on `letsencrypt-route53` (DNS-01, so no inbound challenge traffic), a `ReferenceGrant` (without it the listener comes up silently certless), an allow-list rule in `authorizationpolicy.yaml` (the Gateway is deny-by-default), a Route 53 A record (still created by hand), and the hostname added to `MANAGED_HOSTNAMES` in `base-apps/wan-ip-monitor/cronjob.yaml` so that record follows WAN-IP rotations (the monitor only moves existing records, it never creates one).

> The `agents.platform.ai/*` annotation contract was **removed** (2026-07-14). It
> duplicated data already in the `Agent` spec (`skills` mirrored `a2aConfig.skills`,
> `delegates` mirrored `tools[type: Agent]`), was carried by only 3 of 8 agents and
> consumed by nothing. If agent-to-agent discovery is wanted, derive it from the
> `Agent` CR (which Backstage already ingests) rather than hand-maintaining a
> parallel copy. See `docs/superpowers/specs/2026-07-14-adp-remaining-pillars-roadmap.md` (P1).
