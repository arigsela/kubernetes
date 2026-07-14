# Agent-Identity Contract ("Agent Principal")

A kagent agent's identity is its **agent-principal**:

> agent-principal = (the credentials it can obtain) + (its model) + (its capability surface)

Because the kagent `Agent` CRD's `spec.declarative.deployment` has no
`serviceAccountName`, declarative agents cannot carry a distinct pod
ServiceAccount. Identity is therefore expressed at the boundaries we control:
the credential boundary (ExternalSecret → SecretStore → Vault role), the model
boundary (ModelConfig), and the capability boundary (`Agent.spec.declarative.tools`).

## The three invariants

1. **Scoped credentials.** Every `ExternalSecret` that materializes a real
   credential in the `kagent` namespace resolves through a *dedicated,
   path-scoped* `SecretStore` — its own ESO ServiceAccount + a Vault role that
   reads only that secret's Vault path — not the shared broad `vault-backend`
   store, and not the monolithic `kagent` Vault key.
2. **In-git model identity.** Every agent's `modelConfig` (and
   `memory.modelConfig`) references a `ModelConfig` that exists as a manifest in
   git. (Chart-generated configs such as `default-model-config` are exempt.)
3. **Declared capability surface.** Every `Agent.spec.declarative.tools`
   `McpServer` ref names an MCP server that exists in git and lists non-empty
   `toolNames` (no implicit bind-all). MCP servers rendered by the kagent Helm
   chart (`kagent-tool-server`, `kagent-grafana-mcp`) are exempt from the
   "exists in git" half, for the same reason `default-model-config` is: they are
   still declarative — versioned by the chart's `targetRevision` — and adopting
   them as standalone manifests would put the chart and the `kagent-secrets`
   Argo app in a tug-of-war over one object. That is not hypothetical: it is the
   failure mode that silently stripped the agents' HITL `requireApproval` gates
   on every sync. The `toolNames` half still applies to them.

The validator (`scripts/validate-agent-identity.py`) enforces these. **Every agent
and every credential is held to all three. There is no staging and no warn tier —
a violation is a hard failure, whichever agent it is on.**

It was staged once, for a good reason: increment 1 held only a pilot credential
and pilot agent to the bar and downgraded the rest to warnings, so the gate could
be green while the backlog stayed visible. That backlog is now empty — every
credential is scoped and the broad store is retired — and keeping the staging past
that point made it a loaded gun. Stripping *every* `toolName` from `k8s-agent` (an
implicit bind-all on the agent holding `k8s_delete_resource` and
`k8s_execute_command`) produced a **warning and exit 0**. CI stayed green on a real
capability regression. `scripts/agent-identity-scope.txt` is deleted.

## Onboard a new agent identity-correctly

1. **Credential:** for each real credential the agent (or its MCP server) needs,
   give it its own Vault path (`k8s-secrets/<consumer>`), a Vault policy reading
   only that path, a Kubernetes-auth role bound to a dedicated ESO
   ServiceAccount, and a `SecretStore` using that SA/role (copy
   `serviceaccount.yaml` + `secretstore.yaml`). Point the `ExternalSecret` at
   that store and key. Never read the monolithic `kagent` key for a new
   consumer.
2. **Model:** reference a `ModelConfig` that lives in
   `base-apps/kagent/model-configs/` (or the chart's `default-model-config`).
   Never depend on an out-of-band, hand-applied ModelConfig.
3. **Capability:** list explicit `toolNames` for every `McpServer` tool ref.

## Two gates, deliberately

- **CI** (`scripts/validate-agent-identity.py`, `agent-identity-validate` job)
  checks all three invariants against **git**. It is the only gate that can see
  the "exists in git" half of invariants 2 and 3.
- **Admission** (`base-apps/kyverno-policies/agent-identity.yaml`, Kyverno
  ClusterPolicy, `Enforce`) checks the structural invariants against **anything
  applied to the cluster** — including a hand-run `kubectl apply`, a Helm chart,
  or an operator writing an `Agent`. CI never sees those.

Neither subsumes the other. CI catches what git says; admission catches what the
cluster is actually asked to run.

The Kyverno policy denies: an `ExternalSecret` in `kagent` using the broad
`vault-backend` store, one reading the monolithic `kagent` Vault key, and an
`Agent` whose `McpServer` tool ref lists no `toolNames` (implicit bind-all).
**It carries no exclusions.**

## What this contract does NOT cover

Identity says *who an agent is*. It says nothing about what an agent may **do** —
that is the capability contract
(`base-apps/kyverno-policies/agent-capability.yaml`,
`scripts/validate-agent-capability.py`), which classifies every tool and forbids
an agent from binding above its class or delegating above it. The two are
complements; read both before onboarding an agent.

## Follow-ons (not yet enforced)

- **Dedicated per-agent Anthropic keys / budget caps.** Every agent shares the one
  `kagent-anthropic` key today: no cost attribution, no spend cap, no way to tell
  which agent is burning tokens.
- **Egress control.** No `NetworkPolicy` or Istio `AuthorizationPolicy` anywhere
  in `base-apps/kagent/`; agent pods have unrestricted egress.
- Invariant 2 has no admission-time equivalent (Kyverno cannot read git). A
  cluster-existence check on the referenced `ModelConfig` would be the closest
  analogue if it proves worth the moving parts.

## Done

- **Increment 1** — contract, validator, CI gate; proven end-to-end on a pilot
  slice (`agent-docs-github-mcp-token` / `homelab-knowledge`).
- **Increment 2** — every real credential in the `kagent` namespace is
  credential-scoped: `backstage-mcp-token`, `kagent-db-credentials` and
  `kagent-mcp-basic-auth` each resolve through their own ESO ServiceAccount,
  `SecretStore`, Vault kubernetes-auth role and per-consumer Vault key. (The
  Plex/qBit credential in the original backlog is gone — that integration was
  removed, having never worked.)
- **Increment 3** — the broad `vault-backend` store, the `kagent` Vault role and
  policy, and the monolithic `k8s-secrets/kagent` key are all destroyed. Kyverno
  enforces the contract at admission with no exclusions.
- **Increment 4 (this one)** — the contract is closed. No pilot staging: every
  agent and credential is held to all three invariants as a hard failure. Agents
  are discovered by `kind` rather than by directory, so one placed outside
  `agents/` can no longer slip through unvalidated. A dangling `type: Agent`
  delegation is now an error too — `observability-agent` carried one to
  `promql-agent`, an agent that does not exist, and nothing caught it.
