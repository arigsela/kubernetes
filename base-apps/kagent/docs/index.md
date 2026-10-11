---
type: "Kubernetes App Guide"
title: "kagent"
description: "Kubernetes-native AI agent platform (kagent Helm controller, declarative agents, MCP tool servers)"
app: kagent
catalog_entity: kagent
kind: docs
namespace: kagent
last_reviewed: 2026-10-11
status: stable
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
  - base-apps/kagent/model-configs/anthropic-claude-sonnet-5-5.yaml
  - base-apps/kagent/model-configs/anthropic-claude-haiku-5-5.yaml
  - base-apps/kagent/mcp-basic-auth-external-secret.yaml
  - base-apps/kagent/agents/homelab-knowledge.yaml
  - base-apps/kagent/agents/k8s-reader.yaml
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
---

# kagent

## What it is
kagent is a Kubernetes-native AI agent platform: a controller (installed via Helm, `base-apps/kagent.yaml`, `chart: kagent`, `repoURL: ghcr.io/kagent-dev/kagent/helm`, `targetRevision: 0.10.3`) that reconciles declarative CRDs — `Agent`, `ModelConfig`, `MCPServer`, `RemoteMCPServer` — into running agent workloads. The CRDs themselves come from a sibling Helm install, `base-apps/kagent-crds.yaml` (`chart: kagent-crds`, same repo/version). Both are deployed into the `kagent` namespace.

## Two Argo CD apps, one namespace
- **`base-apps/kagent.yaml`** installs the controller/UI/bundled agents via Helm `valuesObject` (no `path: base-apps/kagent`, so it has no directory-exclude concern). Notable values: the LLM provider (`providers.default: anthropic`, model `claude-haiku-5-5`, key from `apiKeySecretRef: kagent-anthropic`; no agent in git uses this chart default any more), the database wiring (`database.postgres.bundled.enabled: false`, `urlFile: /etc/kagent/secrets/db-url`, `vectorEnabled: true`, sourced from a mounted `kagent-db-credentials` Secret), per-agent enable switches for the chart's bundled agents (all off — `k8s-agent` and `istio-agent` are disabled here and owned in Git under `agents/` so their memory + HITL `requireApproval` gates are declarative), `grafana-mcp.enabled: false` (it never reached Grafana, so it and `observability-agent` were removed), no `OPENSHELL_GATEWAY_URL` (openshell is gone; setting it crashloops the controller), OpenTelemetry export to Coroot, and the upstream UI image (a Node 20 fork was retired in 2026-09 once the nodes were confirmed x86-64-v2 capable). Note these agent keys are **top-level** chart keys (Helm subchart aliases), not nested under an `agents:` map — the chart has no such map, and nesting them silently disables the whole block.
- **`base-apps/kagent-secrets.yaml`** (`path: base-apps/kagent`, `directory: {recurse: true}`) syncs everything in this directory: the `Agent`, `ModelConfig`, `MCPServer`/`RemoteMCPServer` manifests, the HTTPRoutes/Certificates/ReferenceGrants, and the `SecretStore`/`ExternalSecret`s that back them. This is the Application whose `directory.exclude` covers `catalog-info.yaml` and `mkdocs.yml`.

Every Declarative agent in git sets `spec.declarative.runtime: go` explicitly (since 2026-10-11, SPEC §T.94). The agents run Claude 5.5 models, which think by default. The python runtime (google-adk 1.x, pinned `<2` in kagent 0.10) cannot send a thinking block back on the next tool-loop turn and fails every multi-step question; the Go runtime drops thinking blocks instead. Two costs: the Go runtime ignores `context.compaction` (kagent-dev/kagent#2728), so no agent sets it, and reasoning is not carried between tool calls. kagent 1.0 (google-adk 2.11) removes the limitation; see `docs/plans/kagent-1-0-on-k8s-1-37-implementation-plan.md`.

## Agents in Git
Each agent carries a `capability.homelab/class` label (`read`/`admin`) checked against the capability taxonomy (`base-apps/admission-policies/agent-capability-taxonomy.yaml`). Delegation is capability-transitive: an agent's effective capability includes everything it can reach by delegating.

| Agent | Type | Model | Memory | Class | Notes |
|---|---|---|---|---|---|
| `homelab-knowledge` | Declarative | `anthropic-claude-sonnet-5-5` | yes | read | Repo/catalog Q&A (the "ask hk" agent); delegates live-state questions to `k8s-reader` |
| `k8s-reader` | Declarative | `anthropic-claude-haiku-5-5` | **none** | read | Read-only half of the Kubernetes capability; binds only read-classified `kagent-tool-server` tools, so nothing needs `requireApproval` |
| `k8s-agent`, `istio-agent` | Declarative | `anthropic-claude-sonnet-5-5` | yes | admin | Operator agents; mutating tools gated by `requireApproval` |
| `build-orchestrator` (`build-orchestrator.yaml`) | Declarative | `anthropic-claude-sonnet-5-5` | yes | admin | Delegates to `k8s-agent` and `istio-agent` |
| `skill-suggester`, `dungeon-crawler-carl-agent` | Declarative | `anthropic-claude-haiku-5-5` | yes | read | |

`homelab-knowledge` is the single knowledge agent. `homelab-agent`, a LangGraph BYO agent that duplicated it, was retired on 2026-10-11: `kagent-langgraph` doesn't implement kagent 1.0's BYO contract (gRPC A2A, TaskStore). Its manifests, `homelab_agent` database, Vault keys/roles and ECR repository were removed with it (Task 5.2 of `docs/plans/kagent-1-0-on-k8s-1-37-implementation-plan.md`); `scripts/provision-homelab-agent-vault.sh` is kept only as the reference pattern for per-consumer Vault provisioning.

## Model configs
- **`anthropic-claude-sonnet-5-5`** and **`anthropic-claude-haiku-5-5`** (`model-configs/`) — Claude Sonnet 5.5 for the agents that reason over several tools (`homelab-knowledge`, `k8s-agent`, `istio-agent`, `build-orchestrator`) and Haiku 5.5 for the rest. Both use the Vault-backed `kagent-anthropic` key, set `anthropic.maxTokens: 16000` (the Go runtime's 8192 default caps thinking plus answer and truncates silently, kagent-dev/kagent#2895) and no sampling parameters (the 5.5 models reject them). Agents on them ! run `runtime: go` (above).
- **`default-model-config`** — the chart's default `ModelConfig`, generated from the `providers` block in `kagent.yaml` (Anthropic, `claude-haiku-5-5`). No agent in git uses it; it is the default for agents created in the UI, which must also pick the Go runtime.
- **`embedding-model-config`** (`embedding-model-config.yaml`) — points at **Ollama** (`http://ollama.ollama.svc.cluster.local:11434`, model `nomic-embed-text`), used as `memory.modelConfig` by every Declarative agent that has a `memory` block (all but `k8s-reader`) for RAG/embedding recall. This is why the `kagent` component depends on `ollama`.

## Tools via MCP servers
Agents get tools by referencing `MCPServer`/`RemoteMCPServer` objects in their `spec.declarative.tools`. Two patterns exist side by side:
- **stdio proxy**: `agent-docs-mcp.yaml` deploys the read-only GitHub MCP server (`ghcr.io/github/github-mcp-server`, `--read-only --toolsets repos`) as a container; `agent-docs-mcp-remote.yaml` is the `RemoteMCPServer` that registers its tools (`get_file_contents`, `search_code`) with kagent — a container-only `MCPServer` does not register tools in this kagent version, only a `RemoteMCPServer` does.
- **In-cluster remote**: `backstage-catalog-mcp.yaml` points at Backstage's own MCP endpoint (`http://backstage.backstage.svc.cluster.local/api/mcp-actions/v1/catalog`) for resolved-entity/dependency lookups (`get-catalog-entity`), with the `Authorization` header injected from the `backstage-mcp-token` Secret.

Agents only get the `toolNames` they explicitly list (e.g. `agents/homelab-knowledge.yaml` binds `get_file_contents`/`search_code` from `agent-docs` plus `get-catalog-entity` from `backstage-catalog`, and delegates to `k8s-reader` via a `type: Agent` tool entry) — an unlisted tool resolves to nothing and the agent reports "Unknown Tool". A `type: Agent` entry naming an agent that is not deployed resolves to nothing in the same way, so trim delegations when disabling an agent.

## Tool server RBAC (the floor under the capability classes)
`kagent-tools` is one Deployment and one ServiceAccount serving every agent's MCP tools, read-only agents included. The chart binds that ServiceAccount to `*` on everything by default, which is how the read-class `k8s-reader` listed every Secret in the cluster on 2026-07-15 (`k8s_get_resources`, `resource_type: secret`, all namespaces). Since 2026-10, `base-apps/kagent.yaml` sets `kagent-tools.rbac.create: false` (the chart still creates the ServiceAccount) and `base-apps/cluster-rbac/kagent-tools.yaml` binds a **cluster-admin minus Secrets** ClusterRole instead: every core resource except `secrets` (plus `pods/log|exec|attach|portforward`), and every non-core API group named explicitly. A new API group is denied until a PR adds it, the same fail-closed stance the taxonomy takes with new tools. No agent, admin class included, can read or write a Secret through the tools; Secrets are Vault + ESO managed and the only write path is git. `pods/exec` stays because `k8s_execute_command` is destructive-class and approval-gated; bounding what a shell may touch is the tool-guard's job. Contract pinned by `tests/agent-capability/test_kagent_tools_rbac.py`.

## Secrets & database
Every `ExternalSecret` in `base-apps/kagent/` is **credential-scoped** per the agent-identity contract (`templates/agent-identity/README.md`): each has its own ESO ServiceAccount, its own `SecretStore`, its own Vault kubernetes-auth role, and its own per-consumer Vault key. No `ExternalSecret` in this namespace reads the monolithic `kagent` key any more, so a token minted for one consumer cannot read another's secrets.

| Secret (ExternalSecret if different) | SecretStore | ESO ServiceAccount | Vault role | Vault key |
|---|---|---|---|---|
| `agent-docs-github-mcp-token` | `vault-agent-docs-mcp` | `eso-agent-docs-mcp` | `kagent-agent-docs-mcp` | `kagent-agent-docs-mcp` |
| `backstage-mcp-token` | `vault-backstage-mcp` | `eso-backstage-mcp` | `backstage-mcp` | `kagent-backstage-mcp` |
| `kagent-db-credentials` | `vault-kagent-db` | `eso-kagent-db` | `kagent-db` | `kagent-db` |
| `kagent-anthropic` (`kagent-anthropic-secrets`) | `vault-kagent-anthropic` | `eso-kagent-anthropic` | `kagent-anthropic` | `kagent-anthropic` |
| `kagent-mcp-basic-auth` — **orphaned** | `vault-kagent-mcp-basic-auth` | `eso-kagent-mcp-basic-auth` | `kagent-mcp-basic-auth` | `kagent-mcp-basic-auth` |

Each Vault policy grants `read` on exactly one path (`k8s-secrets/data/<key>`).

`kagent-mcp-basic-auth` has had **no consumer since T52** (2026-07-31): it was the htpasswd for the old nginx `/mcp` ingress, and basic auth was dropped in the move to Gateway API (see *Exposure*). Its ExternalSecret, SecretStore and ESO ServiceAccount still sync but gate nothing; they are candidates for removal.

Every agent credential here is in Vault. The last exception, the UI-created `anthropic-claude-sonnet-4-6` key (owner-referenced to its ModelConfig), was retired on 2026-10-11 with that ModelConfig (SPEC §T.94).

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
