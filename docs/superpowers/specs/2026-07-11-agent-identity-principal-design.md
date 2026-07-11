# Agent Identity — "Agent Principal" Pattern (first increment) — Design

- **Date:** 2026-07-11
- **Status:** Approved (design); implementation plan to follow
- **Depends on:** the kagent platform (`base-apps/kagent/`), Vault + External Secrets (`base-apps/vault/`, `base-apps/external-secrets.yaml`), and the agent-docs framework whose contract/validator/pilot conventions this mirrors (`templates/agent-docs/README.md`, `scripts/validate-agent-docs.py`).
- **Frames onto:** the Weave Intelligence ADP model — Layer 3 **Identity** pillar. This is the first increment of that pillar, deliberately advancing Identity because it is the article's "bind identity first" primitive and our weakest L3 pillar despite strong Context/Capability.

## Problem

kagent agents share one coarse identity. Every agent runs as the `default` ServiceAccount in the `kagent` namespace, and every credential the namespace consumes resolves through a single `SecretStore` (`vault-backend`) bound to one broad Vault role (`kagent`) that can read the entire monolithic `kagent` KV key (DB creds + GitHub token + Backstage token + MCP basic-auth, all together). All agents also share one Anthropic key. Consequences:

- **No least privilege.** One leaked namespace token / one over-broad role exposes every secret. The GitHub-repo-read token, the DB credentials, and the ingress basic-auth blob are all reachable through the same grant.
- **Out-of-band identity.** `homelab-knowledge` references a `ModelConfig` (`anthropic-claude-sonnet-4-6`) whose manifest is not in git — it was applied by hand, so it is invisible to GitOps and to review.
- **No principal to build on.** There is no articulated "this is what agent X is and may reach," so the downstream Identity work (budget, egress, dedicated keys) has nothing to attach to.

A hard architectural constraint shapes the solution: the kagent `Agent` CRD's `spec.declarative.deployment` exposes only `annotations`, `env`, `imagePullSecrets`, `labels`, `replicas`, `volumes` — **no `serviceAccountName`**. Declarative agents therefore cannot each carry a distinct pod ServiceAccount without being rewritten as bring-your-own-container agents (too heavy). Identity for a declarative agent must be expressed at the boundaries we *do* control.

## Insight: where identity actually lives in kagent

An agent pod barely holds credentials — it holds the model key and talks to everything else over HTTP. The real, controllable boundaries are:

1. **Credentials** — materialized by `ExternalSecret` → `SecretStore` → Vault role. A `SecretStore` authenticates to Vault with an **ESO ServiceAccount we fully control** (a plain SA, unaffected by the declarative-agent limitation). Per-consumer credential scoping is achievable by giving each credential its own `(ESO SA + Vault role + Vault path + SecretStore)`.
2. **Model** — the `ModelConfig` (holds the Anthropic key via `apiKeySecretRef`). A per-agent, in-git ModelConfig is achievable.
3. **Capability surface** — the agent's `spec.declarative.tools` (`toolNames` + which MCP servers + which sub-agents). Already declarative and per-agent; the kagent-native authorization boundary.

So the **agent-principal** = *(the credentials it can obtain) + (its model) + (its capability surface)*. Credential-scoping teeth land on the ExternalSecret/SecretStore/Vault boundary, not the agent pod.

## Goal

Establish a reusable, GitOps-native agent-principal pattern for kagent agents, and prove real least-privilege end-to-end on one slice — the `homelab-knowledge` agent and the `agent-docs-mcp` credential it depends on.

## Approach

**Convention + validator, no new CRD.** The agent-principal is not a new object; it is a set of invariants a validator asserts by reading the manifests that already exist (`Agent`, `ModelConfig`, each credential's `ExternalSecret`/`SecretStore`). This reuses the agent-docs culture exactly (contract in `templates/`, Python validator in CI, proven on a pilot) and introduces no source-of-truth duplication. (Rejected: a per-agent `agent-principal.yaml` source-of-truth — it duplicates `Agent.tools`/`ModelConfig` and drifts. Deferred: Kyverno admission enforcement — see Follow-ons.)

## The contract (three invariants)

Documented in `templates/agent-identity/README.md`. An agent-principal is correct when:

1. **Scoped credentials.** Every `ExternalSecret` that materializes a real credential in the `kagent` namespace resolves through a **dedicated, path-scoped `SecretStore`** — its own ESO ServiceAccount + a Vault role that reads only that secret's Vault path — not the shared broad `vault-backend`/`kagent` role, and not the monolithic `kagent` Vault blob.
2. **In-git model identity.** Every agent's `modelConfig` (and `memory.modelConfig`) references a `ModelConfig` that exists as a manifest in git.
3. **Declared capability surface.** Every `Agent.spec.declarative.tools` entry references an MCP server / sub-agent that exists in git, and every `McpServer` tool ref lists explicit `toolNames` (no implicit bind-all).

Scope of enforcement this increment: the invariants are *checked* for all in-scope agents but only *satisfied end-to-end* for the pilot slice; other consumers are recorded as known follow-ons (see Non-goals) rather than failing the build. The validator distinguishes "pilot must pass" from "known-unscoped, warn" so the gate is green while the backlog stays visible.

## Component 1 — Credential-scoping teeth (the proven slice: agent-docs MCP)

Today `agent-docs-mcp-external-secret.yaml` reads `key: kagent, property: agent-docs-github-token` through `SecretStore vault-backend` (role `kagent`). Target state:

**Vault (operator-run writes; the root token stays with the user):**
- Move the GitHub token to its own path: `k8s-secrets/kagent-agent-docs-mcp` (e.g. property `github-token`).
- Create Vault policy `kagent-agent-docs-mcp` granting read on only `k8s-secrets/data/kagent-agent-docs-mcp`.
- Create a Kubernetes-auth role `kagent-agent-docs-mcp` bound to the new ESO ServiceAccount (`eso-agent-docs-mcp`) in namespace `kagent`, mapped to that policy.
- **Narrow** the broad `kagent` policy so it no longer grants the `kagent-agent-docs-mcp` path (this is what turns the change into a real privilege *reduction*, not just a move).

**Git (this increment):**
- New `ServiceAccount eso-agent-docs-mcp` in `kagent`.
- New `SecretStore vault-agent-docs-mcp` (auth: kubernetes, role `kagent-agent-docs-mcp`, `serviceAccountRef: eso-agent-docs-mcp`).
- Update `agent-docs-mcp-external-secret.yaml` to reference `vault-agent-docs-mcp` and the new `key: kagent-agent-docs-mcp, property: github-token`.

**Ordering (reversible):** add new Vault path + role + policy → apply the new SA/SecretStore/ExternalSecret → verify the token still materializes and `homelab-knowledge` still reads the repo → narrow the old `kagent` policy last. Rollback = revert the manifests; the old Vault path may remain until verified.

## Component 2 — Model identity (structural)

Create `base-apps/kagent/model-configs/anthropic-claude-sonnet-4-6.yaml`, pulling `homelab-knowledge`'s currently out-of-band `ModelConfig` into git with `apiKeySecretRef: kagent-anthropic` (the existing shared key — "structural only"; no new billing this round). This satisfies invariant #2. Because the manifest is *adopting* a resource that already exists live, its content must match the live object (Anthropic provider, `claude-sonnet-4-6` model, `apiKeySecretRef: kagent-anthropic`) so Argo CD sync is a no-op adoption, not a mutation. Swapping to a dedicated key later is a one-line change.

## Component 3 — Enforcement (validator)

`scripts/validate-agent-identity.py` (sibling to `validate-agent-docs.py`), pure functions + CLI, with pytest coverage and a CI job. It reads the in-scope agent manifests and asserts the three invariants:

- `check_credential_scoping(repo_root)` — each credential-bearing `ExternalSecret` in `base-apps/kagent/` uses a dedicated scoped `SecretStore` (not `vault-backend`) and a per-consumer Vault key (not `key: kagent`). Pilot (`agent-docs-github-mcp-token`) is a hard failure; the other known-unscoped consumers emit warnings listing the backlog.
- `check_model_config_in_git(repo_root)` — every `modelConfig`/`memory.modelConfig` name referenced by an `Agent` resolves to a `kind: ModelConfig` manifest in `base-apps/kagent/`.
- `check_capability_surface(repo_root)` — every `Agent.tools` `McpServer` ref names an MCP server that exists in git and lists non-empty `toolNames`; every `Agent`/sub-agent ref exists.

An in-scope list (analogous to `scripts/agent-docs-scope.txt`) names which agents/consumers are held to the pilot bar vs. warn-only, so the gate is deterministic.

## The contract doc + templates

`templates/agent-identity/README.md`: the agent-principal model, the three invariants, and an onboarding checklist ("to add an agent identity-correctly: give each new credential its own ESO SA + scoped Vault role + path + SecretStore; reference an in-git ModelConfig; list explicit toolNames"). Optionally a `templates/agent-identity/secretstore.yaml` + `serviceaccount.yaml` copy-paste pair for a scoped credential.

## Success criteria

1. `templates/agent-identity/README.md` documents the pattern and the three invariants.
2. `scripts/validate-agent-identity.py` exists with pytest coverage and a CI job, and passes (pilot invariants satisfied; known-unscoped consumers warn).
3. The `agent-docs-mcp` GitHub token resolves through a dedicated `(eso-agent-docs-mcp SA + kagent-agent-docs-mcp Vault role + kagent-agent-docs-mcp path + vault-agent-docs-mcp SecretStore)`, and the broad `kagent` role no longer grants that path.
4. `homelab-knowledge`'s `ModelConfig` is a git-tracked manifest referenced by invariant #2.
5. End-to-end proof: `homelab-knowledge` still answers a repo question after the cutover (the scoped token still materializes).
6. `yamllint` + `kubeconform` pass on the new/changed manifests; no unrelated manifest is modified.

## Safety, blast radius & rollback

- **Additive except one credential's source path.** New SA/SecretStore/ModelConfig/validator/docs; the only behavior-relevant change is where the `agent-docs-mcp` token is read from. No workload image, ingress, RBAC, or network change.
- **Only real risk is the Vault re-pathing** — mitigated by add-before-remove ordering, an explicit end-to-end verification step before narrowing the old policy, and the operator holding the root token.
- **Rollback:** revert the batch's manifests; leave the old Vault path in place until the new path is verified. No runtime state to unwind.

## Non-goals (documented follow-ons)

- **Kyverno admission enforcement (C)** — a policy that denies MCP-server ExternalSecrets on the broad store and agents referencing out-of-band ModelConfigs, at deploy time. Planned as the next Identity increment.
- **Scoping the other credentials** — Backstage MCP token, DB credentials, MCP basic-auth, Plex/qBit creds — same pattern, later batches.
- **Dedicated Anthropic keys / budget caps / egress control** — the rest of the Identity pillar (cost attribution via per-agent keys or a LiteLLM proxy; NetworkPolicy/Istio egress).
- **The other declarative agents** (`plex-stack-diagnostics`, `dungeon-crawler-carl`, `skill-suggester`, `build-orchestrator`) — onboarded to the pattern in follow-on batches.
- **Per-agent pod ServiceAccounts** — not achievable for declarative agents (CRD limitation); revisit only if agents move to BYO-container mode.

## Open questions

- **Exact Vault path/property names** — `kagent-agent-docs-mcp` / `github-token` proposed; finalized during implementation to match the operator's Vault layout conventions.
- **Validator scope file format** — reuse the newline-delimited `scope.txt` convention or inline the in-scope list in the script; decided during implementation (favor the existing `scope.txt` convention for consistency).
