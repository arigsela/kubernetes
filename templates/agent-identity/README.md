# Agent-Identity Contract ("Agent Principal")

A kagent agent's identity is its **agent-principal**:

> agent-principal = (the credentials it can obtain) + (its model) + (its capability surface)

kagent 1.0 (`api.kagent.dev/v1alpha3`, since 2026-10-11) splits an agent in three:

| Object | Says | Where |
|---|---|---|
| `AgentTemplate` | **what** the agent does: model, prompt, tools, sub-agents, and its `capability.homelab/class` label | `base-apps/kagent/agent-templates/` |
| `Harness` | **how** it runs: the runtime (`spec.kagent`), image, memory and compaction, Substrate worker pool and snapshot location | `base-apps/kagent/harnesses/` |
| `Agent` | the object users talk to: `spec.template.ref` + `spec.harnessRef`, nothing inline | `base-apps/kagent/agents/` |

An agent has no pod of its own. Each conversation is a session that runs as a gVisor actor
in a shared Agent Substrate worker pod (`base-apps/substrate/docs.md`), so there is no
per-agent ServiceAccount to hang identity on. Identity is expressed at the boundaries we
control: the credential boundary (ExternalSecret → SecretStore → Vault role), the model
boundary (ModelConfig), and the capability boundary (the AgentTemplate's `spec.tools`).

Model API keys and MCP header secrets don't reach the agent at all. The controller compiles a
Secret reference (`ModelConfig.spec.apiKeySecret`, `RemoteMCPServer.spec.headersFrom`) into an
`ate-secret://` credential, and Substrate's egress gateway injects it into the outbound
request. The sandbox never holds the key (see `base-apps/kagent/backstage-catalog-mcp.yaml`).

## The three invariants

1. **Scoped credentials.** Every `ExternalSecret` that materializes a real
   credential in the `kagent` namespace resolves through a *dedicated,
   path-scoped* `SecretStore`: its own ESO ServiceAccount plus a Vault role that
   reads only that secret's Vault path. Never the shared broad `vault-backend`
   store, and never the monolithic `kagent` Vault key (destroyed; checked
   cluster-wide, because a kagent credential declared in another namespace is still
   a kagent credential).
2. **In-git model identity.** Every `ModelConfig` an `AgentTemplate` or a `Harness`
   names (`spec.modelConfig`, `spec.kagent.memory.modelConfigRef`,
   `spec.kagent.compaction.summarizer.modelConfigRef`) exists as a manifest in git.
   (The chart-rendered `default-model-config` is exempt.)
3. **Declared capability surface.** Every `AgentTemplate` MCP binding
   (`spec.tools[].mcp`) names a `RemoteMCPServer` that exists in git and lists its
   `tools` explicitly: an omitted or empty list binds every tool the server has,
   including ones a future upgrade adds. Every `subAgent` template, every
   `Agent.spec.template.ref` and every `Agent.spec.harnessRef` resolves to an object
   in git, and no `Agent` carries an inline `spec.template` or `spec.harness`
   (it would bypass the checks on templates and harnesses). MCP servers rendered by
   the kagent Helm chart (`kagent-tool-server`) are exempt from the "exists in git"
   half, for the same reason `default-model-config` is: they are still declarative,
   versioned by the chart's `targetRevision`, and adopting them as standalone
   manifests would put the chart and the `kagent-secrets` Argo app in a tug-of-war
   over one object. That is not hypothetical: on 0.10 it was the failure mode that
   silently stripped the agents' HITL approval gates on every sync. The explicit
   `tools` half still applies to them.

The validator (`scripts/validate-agent-identity.py`) enforces these. **Every agent
and every credential is held to all three. There is no staging and no warn tier:
a violation is a hard failure, whichever agent it is on.** It also **fails closed**
(SPEC §V.84): a kagent manifest of a kind or version it doesn't understand is an
error, and so is finding no `AgentTemplate` or no `Agent`. A validator that only
knew the 0.10 shape would have found zero agents after the 1.0 cutover and passed.

It was staged once, for a good reason: increment 1 held only a pilot credential
and pilot agent to the bar and downgraded the rest to warnings, so the gate could
be green while the backlog stayed visible. That backlog is now empty, and keeping
the staging past that point made it a loaded gun. Stripping *every* tool name from
`k8s-agent` (an implicit bind-all on the agent holding `k8s_delete_resource` and
`k8s_execute_command`) produced a **warning and exit 0**. CI stayed green on a real
capability regression. `scripts/agent-identity-scope.txt` is deleted.

## Onboard a new agent identity-correctly

1. **Credential:** for each real credential the agent (or its MCP server) needs,
   give it its own Vault path (`k8s-secrets/<consumer>`), a Vault policy reading
   only that path, a Kubernetes-auth role bound to a dedicated ESO
   ServiceAccount, and a `SecretStore` using that SA/role (copy
   `serviceaccount.yaml` + `secretstore.yaml`, both `external-secrets.io/v1`).
   Point the `ExternalSecret` at that store and key. Never read the monolithic
   `kagent` key.
2. **Model:** the `AgentTemplate`'s `spec.modelConfig.name` and any memory or
   summarizer `modelConfigRef` on its `Harness` reference a `ModelConfig` in
   `base-apps/kagent/` (`model-configs/` or `embedding-model-config.yaml`). Never
   depend on an out-of-band, hand-applied ModelConfig.
3. **Capability:** in the `AgentTemplate`, list the `tools` of every `mcp` binding,
   and set `requireApproval: true` on the binding that holds any write or destructive
   tool. Approval is **per binding** in v1alpha3 (v1alpha2 had a per-tool list), so an
   agent that needs both binds the server twice:

   ```yaml
   tools:
     - mcp:
         server: {kind: RemoteMCPServer, name: kagent-tool-server}
         requireApproval: false
         tools: [k8s_get_resources, k8s_describe_resource]
     - mcp:
         server: {kind: RemoteMCPServer, name: kagent-tool-server}
         requireApproval: true
         tools: [k8s_patch_resource, k8s_delete_resource]
   ```

   The capability contract (below) enforces the gate and the class; this contract
   only requires that the list is explicit. Then add an `Agent` with
   `template.ref` + `harnessRef` (one of the Harnesses in `base-apps/kagent/harnesses/`).

## Two gates, deliberately

- **CI** (`scripts/validate-agent-identity.py`, `agent-identity-validate` job)
  checks all three invariants against **git**. It is the only gate that can see
  the "exists in git" half of invariants 2 and 3.
- **Admission** (`base-apps/admission-policies/agent-identity.yaml`, native
  ValidatingAdmissionPolicies, `[Deny]` with failurePolicy `Fail`, evaluated inside
  the API server) checks the structural invariants against **anything applied to
  the cluster**, including a hand-run `kubectl apply`, a Helm chart, or an operator
  writing an `AgentTemplate`. CI never sees those.

Neither subsumes the other. CI catches what git says; admission catches what the
cluster is actually asked to run.

The admission policies deny: an `ExternalSecret` in `kagent` using the broad
`vault-backend` store (top-level or per-item `sourceRef.storeRef`), one reading the
monolithic `kagent` Vault key (`data[].remoteRef` or `dataFrom[].extract`), and an
`AgentTemplate` (`api.kagent.dev/v1alpha3`) with an MCP binding whose `tools` list is
missing or empty (implicit bind-all). The inline-template/harness ban on `Agent` is
admission-enforced too, by `agent-capability-refs` in the capability contract.
**They carry no exclusions.**

## What this contract does NOT cover

Identity says *who an agent is*. It says nothing about what an agent may **do**:
that is the capability contract
(`base-apps/admission-policies/agent-capability.yaml`,
`scripts/validate-agent-capability.py`), which classifies every tool, forbids a
template from binding above its class or binding a sub-agent above it, requires
the approval gate on mutating bindings, and allows only the `kagent` runtime on
pinned images in a `Harness`. The two are complements; read both before
onboarding an agent.

## Follow-ons (not yet enforced)

- **Dedicated per-agent Anthropic keys / budget caps.** Every agent shares the one
  `kagent-anthropic` key today: no cost attribution, no spend cap, no way to tell
  which agent is burning tokens.
- **Egress control.** Sandbox egress goes through Substrate's egress gateway, which
  injects credentials, but no destination allow-list is configured, and there is no
  `NetworkPolicy` or Istio `AuthorizationPolicy` in `base-apps/kagent/`.
- **Caller identity.** A2A (`/agents/kagent/<agent>` on `kagent-mcp.arigsela.com`) is
  unauthenticated: the caller names itself in `X-User-Id`, and the Gateway's IP
  allow-list is the only control.
- Invariant 2 has no admission-time equivalent (admission cannot read git). A
  cluster-existence check on the referenced `ModelConfig` would be the closest
  analogue if it proves worth the moving parts.

## Done

- **Increment 1**: contract, validator, CI gate; proven end-to-end on a pilot
  slice (`agent-docs-github-mcp-token` / `homelab-knowledge`).
- **Increment 2**: every real credential in the `kagent` namespace is
  credential-scoped: `backstage-mcp-token`, the database credential and the MCP
  basic-auth htpasswd each resolved through their own ESO ServiceAccount,
  `SecretStore`, Vault kubernetes-auth role and per-consumer Vault key. (The
  Plex/qBit credential in the original backlog is gone: that integration was
  removed, having never worked.)
- **Increment 3**: the broad `vault-backend` store, the `kagent` Vault role and
  policy, and the monolithic `k8s-secrets/kagent` key are all destroyed. Kyverno
  enforced the contract at admission with no exclusions.
- **Increment 4**: the contract is closed. No pilot staging: every
  agent and credential is held to all three invariants as a hard failure. Agents
  are discovered by `kind` rather than by directory, so one placed outside
  `agents/` can no longer slip through unvalidated. A dangling delegation is an
  error too: `observability-agent` once delegated to `promql-agent`, an agent that
  did not exist, and nothing caught it.
- **Increment 5**: admission moved from the Kyverno ClusterPolicy to native
  ValidatingAdmissionPolicies (plan `docs/plans/k8s-136-features-implementation-plan.md`,
  Phase 3E). Kyverno's webhook failed OPEN whenever its single pod was down
  (`forceFailurePolicyIgnore`); the native policies run inside the API server with
  failurePolicy `Fail`. They also close two gaps the Kyverno rules had (per-item
  `sourceRef.storeRef`, `dataFrom[].extract`).
- **Increment 6** (2026-10-11): kagent 1.0. The validator and the admission policy
  moved to `api.kagent.dev/v1alpha3` in the same sync as the new CRDs (SPEC §V.84),
  and both fail closed on an agent manifest they don't understand. The 0.10 leftovers
  were removed: the MCP basic-auth htpasswd (no consumer since the move to Gateway
  API, SPEC T52) and the controller's `kagent-db-credentials` (1.0 reads
  `kagent-postgres-owner` and `kagent-postgres-readwrite`). See `docs/plans/kagent-1-0-on-k8s-1-37-implementation-plan.md`.
