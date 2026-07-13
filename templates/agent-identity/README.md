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

The validator (`scripts/validate-agent-identity.py`) enforces these. Enforcement
is staged: the pilot credential and pilot agent named in
`scripts/agent-identity-scope.txt` are hard failures; every other unscoped
consumer or unresolved reference is a warning (visible backlog).

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

## Follow-ons (not yet enforced)

- Kyverno admission policy enforcing invariants 1 & 2 at deploy time. Until this
  lands, the invariants are checked only in CI — a direct `kubectl apply` can
  still violate them.
- Onboarding the remaining declarative agents.
- Dedicated per-agent Anthropic keys / budget caps; egress control.

## Done

- **Increment 1** — contract, validator, CI gate; pilot credential
  (`agent-docs-github-mcp-token`) and pilot agent (`homelab-knowledge`).
- **Increment 2** — every real credential in the `kagent` namespace is now
  credential-scoped: `backstage-mcp-token`, `kagent-db-credentials` and
  `kagent-mcp-basic-auth` each resolve through their own ESO ServiceAccount,
  `SecretStore`, Vault kubernetes-auth role and per-consumer Vault key. Nothing
  in this namespace reads the monolithic `kagent` key any more. (The Plex/qBit
  credential named in the original backlog is gone — that integration was
  removed, having never worked.)
