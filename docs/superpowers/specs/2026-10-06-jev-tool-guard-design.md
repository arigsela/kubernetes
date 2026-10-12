# Jev tool-call guard for kagent agents

**Date:** 2026-10-06
**Status:** draft design, awaiting review
**Scope:** a new in-cluster component, `tool-guard`, that sits between the admin kagent agents (`k8s-agent`, `istio-agent`) and the kagent tool server, scores every write/destructive tool call with deterministic rules and Jev (TypeSafe System One), puts that assessment in front of the human before they approve, and refuses the calls the rules or the score say must not run.

## 1. Why

The human-in-the-loop gate on `k8s-agent` and `istio-agent` works again (the capability contract enforces `requireApproval` in CI and at admission), but it asks the human a bare question: "allow `k8s_execute_command` with these arguments?" The person approving sees the raw call and nothing else. There is no second opinion, no "this one touches Vault", no record of why it was allowed. The 2026-07-09 incident, fourteen shell commands inside `vault-0`, would have been fourteen approvals if the gate had been working, and a tired operator approving the third one in a row is exactly how a gate stays nominally intact while doing nothing.

pr-triage already shows the shape that fixes this for pull requests: deterministic policy first, Jev's calibrated probabilities second, escalation only when Jev is unsure, and the verdict shown to the human as context, never as a replacement for them. This spec applies that shape to agent tool calls. The agent action record (`scripts/agent-audit.py`) and its risk triage (`docs/superpowers/plans/2026-10-06-jev-agent-audit-risk.md`) judge sessions after the fact. The guard judges calls before they run.

## 2. Facts that shaped the design (probed 2026-10-06)

| Fact | Consequence |
|---|---|
| The admin agents bind tools from the chart-provided `RemoteMCPServer` `kagent-tool-server` at `http://kagent-tools.kagent:8084/mcp` (streamable HTTP, `timeout: 30s`, `sseReadTimeout: 5m0s`, `terminateOnClose: true`). | A guard can be another `RemoteMCPServer` in git that points at a proxy Deployment, which forwards to that URL. No chart change. |
| kagent's HITL is ADK's `requireApproval`: the runtime raises `adk_request_confirmation` **before** the tool call leaves the agent. Nothing we deploy runs between the agent's decision and the approval dialog. | A proxy cannot annotate the approval dialog. Pre-approval context can only enter through the conversation itself. |
| The capability admission policy and `validate-agent-identity.py` match tools by **name** (`toolNames`, `requireApproval`), and the validator exempts only `kagent-tool-server` from the "MCP server must exist in git" rule. | A guard server that re-exposes the same tool names passes both gates unchanged, provided its `RemoteMCPServer` manifest is in git. The taxonomy must gain any new tool the guard adds. |
| Declarative agents have no hook, callback or middleware configuration. `homelab-agent` (BYO LangGraph) does, but it is slated for retirement at kagent 1.0 and must not gain new dependencies. | The only two seats are the MCP path (a proxy) and the prompt (a tool the agent is instructed to call). |
| `requireApproval` is per tool ref. Moving the admin agents' tool ref from `kagent-tool-server` to the guard keeps the list on the new ref. | The human gate does not move or weaken. |
| There is no NetworkPolicy in the `kagent` namespace; egress is open. | The guard reaching `api.typesafe.ai` needs no network change. A future NetworkPolicy must allow it. |
| Images for in-house agents are built from `arigsela/claude-agents` and pushed to ECR with a `deploy-to-ecr.sh <tag>`; the ECR pull secret is injected at admission. Nodes are x86-64-v2 without AVX2. | Same build path. Pure-Python wheels only. |
| The kagent UI renders a tool's error result in the chat. **Assumption, to verify in the first spike.** | A denial's reason reaches the human through the agent's reply. |
| Whether kagent forwards a session or user identifier in MCP request headers is unknown. **Assumption, to verify in the first spike.** | Correlating guard verdicts with the action record by session id may not be possible in v1; correlation falls back to agent name plus timestamp. |

## 3. Decisions already made

| Question | Decision |
|---|---|
| Seat | Approval risk context (the operator's choice, 2026-10-06). Request routing for `homelab-knowledge` is a separate, later spec. |
| Who the guard protects | `k8s-agent` and `istio-agent` (class `admin`). `build-orchestrator` delegates to them and is covered transitively. `k8s-reader` stays on `kagent-tool-server` directly: read-only, nothing to guard. |
| Auto-approval | Never. The guard adds context before the human and a floor after; it does not replace the approval. |
| Jev's role | Second tier behind deterministic rules it cannot override. A rule denial is final. Jev can only add a denial or a flag, never remove one. |
| What leaves the cluster | The tool name, the agent name and the call's arguments, redacted with the same `redact_args()` the audit record uses, plus the agent's stated justification. This extends the 2026-10-06 egress decision from the audit record to the live call path. |
| Claude tier | None in the call path. Latency and a second credential are not worth it when a human is already the next step. |
| Rollout | `observe` mode first: score, log and expose the assessment, deny nothing. Switch to `enforce` after the operator has read two weeks of verdicts. |

## 4. Approaches considered

**A. Guard proxy, post-approval floor (recommended, with C).** A streamable-HTTP MCP server that re-exposes the tool server's tools. Read-class calls pass straight through. Write and destructive calls are scored: rules first, Jev second; `deny` returns a tool error carrying the reasons, `allow` forwards, every verdict is logged. Enforceable, fully deterministic on the rule tier, and invisible to the agent on the happy path. On its own it cannot inform the approval dialog, because the call reaches it only after approval.

**B. Move the approval out of kagent.** The guard owns the gate: it posts each gated call with its assessment to an approval page (agent-audit-web has the login and allow-list already) and blocks until someone approves. It would show the assessment before the decision. Rejected for v1: the capability policy requires `requireApproval` on every mutating tool ref, so kagent's own dialog cannot be removed, and a second dialog on a different site for every call is worse than the problem. Agent-audit-web is also read-only by design, and an approval store would break that boundary. Revisit if kagent exposes an approval webhook.

**C. Preflight assessment tool.** The guard also exposes one read-class tool, `assess_tool_call(tool, arguments, justification)`, and the admin agents' system prompts require calling it before any gated tool and repeating its verdict to the user. The assessment therefore lands in the chat immediately before the approval dialog. It is soft: it depends on the model following the prompt. Alone it is not enough; with A it gives the human the context before and the floor after, from one scorer with one set of rules.

**Chosen: A + C, one Deployment.** The agent calls `assess_tool_call` (prompted), tells the user "medium risk, touches namespace vault, reversible: no", kagent asks for approval, the human decides with that in view, the approved call goes through the guard, the same scorer re-runs it (the arguments may differ from the preflight), and the floor applies.

## 5. Design

### 5.1 Components

```
k8s-agent / istio-agent  (Agent CRs in git, tool ref -> RemoteMCPServer tool-guard)
        │  MCP streamable HTTP
        ▼
tool-guard  (Deployment + Service in kagent, image from claude-agents/tool-guard)
   ├─ rules          deterministic, from a ConfigMap in git (section 5.3)
   ├─ scorer         Jev client, questions in code (section 5.4)
   ├─ assess_tool_call   read tool: runs rules + scorer, returns the assessment
   ├─ passthrough    forwards allowed calls upstream, streams the result back
   └─ verdict log    one JSON line per gated call on stdout (argument-free)
        │  MCP streamable HTTP
        ▼
kagent-tools:8084/mcp  (chart-provided tool server, unchanged)
```

- **`RemoteMCPServer` `tool-guard`** (`base-apps/kagent/tool-guard-remote.yaml`): `url: http://tool-guard.kagent:8085/mcp`, same timeouts as the upstream. In git, so the identity validator's "server exists in git" rule holds.
- **Deployment `tool-guard`** (`base-apps/kagent/tool-guard.yaml`): one replica, `runAsNonRoot`, read-only root filesystem, resources small. Env: `UPSTREAM_MCP_URL`, `GUARD_MODE` (`observe` | `enforce`), `TYPESAFE_API_KEY` from the scoped Secret, `RULES_PATH` mounting the rules ConfigMap. Readiness: upstream `tools/list` succeeds.
- **Credential**: ESO ServiceAccount `eso-tool-guard`, SecretStore `vault-tool-guard` (role `tool-guard`), ExternalSecret `tool-guard-credentials` (`typesafe-api-key`), Vault key `k8s-secrets/tool-guard`. Provisioned by `scripts/provision-tool-guard-vault.sh`, same shape as the audit-risk script.
- **Agent changes**: `k8s-agent.yaml` and `istio-agent.yaml` tool refs renamed to `tool-guard`; `toolNames` gains `assess_tool_call`; `requireApproval` lists unchanged; system prompts gain the preflight paragraph (5.5).
- **Taxonomy**: `assess_tool_call` added as `read` in `agent-capability-taxonomy.yaml`; the generated admission policy regenerates.

### 5.2 Tool surface

On start the guard calls upstream `tools/list` and re-exposes exactly the tools present in the taxonomy (all three classes), with the upstream schemas unchanged. Tools the taxonomy does not know are not exposed: the same fail-closed rule the admission policy applies, now also at the server. Plus `assess_tool_call`:

```
assess_tool_call(tool: str, arguments: object, justification: str) ->
  {verdict: allow|flag|deny, risk: low|medium|high, reasons: [str],
   flags: {touches_secrets, beyond_one_namespace, hard_to_revert, matches_justification},
   decided_by: rules|jev|jev-unavailable, mode: observe|enforce}
```

### 5.3 Rules (deterministic, first, final on deny)

A ConfigMap `tool-guard-rules` in git, YAML:

```yaml
version: 1
deny:
  namespaces: [vault, kube-system, argocd, dex, cert-manager, external-secrets, istio-system, admission-policies]
  tools_in_denied_namespaces: [k8s_execute_command, k8s_delete_resource, k8s_patch_resource, k8s_apply_manifest]
  kinds: [Secret, ClusterRole, ClusterRoleBinding, ValidatingAdmissionPolicy, MutatingAdmissionPolicy, CustomResourceDefinition]
  exec_patterns: ["(^|\\s)rm\\s+-rf", "kubectl\\s+delete", "vault\\s+(write|delete|kv put)", "/vault/data"]
flag:
  namespaces_shared: [istio-ingress, postgresql, logging, kagent]
```

Semantics: a write/destructive call whose namespace argument is in `deny.namespaces` and whose tool is in `tools_in_denied_namespaces` is denied; a call whose `kind`/`resource_type` matches `deny.kinds` is denied; `k8s_execute_command` whose command matches an `exec_patterns` entry is denied. Rules match on the redacted arguments, so a pattern can never depend on a secret value. A rule denial never consults Jev. Argument field names (`namespace`, `resource_type`, `command`, `manifest`) are taken from the upstream tool schemas at start and the guard refuses to start if a rule names a field no schema has.

### 5.4 Scorer

State sent to Jev: `{agent, tool, class, arguments (redacted, <= 600 chars), justification, mode}`. Questions: a `choice` `risk` with criteria `low` (one namespaced object in an application namespace, revert undoes it), `medium` (several objects or a shared namespace, still reversible), `high` (cluster-scoped, Secrets, data, shell, or not explained by the justification); `noul` flags `touches_secrets`, `beyond_one_namespace`, `hard_to_revert`, `matches_justification`. Thresholds, versioned with the questions: deny if `p(high) >= 0.70` or `touches_secrets >= 0.80` or `matches_justification <= 0.20`; flag if `p(high) >= 0.30` or any other flag `>= 0.70`; otherwise allow. Confidence below `0.50` is `flag` with reason `jev unsure`, never `allow`. Jev unreachable: `flag` for write tools, `deny` for destructive tools in `enforce` mode (`decided_by: jev-unavailable`), so an outage degrades toward the human, not past them.

The `justification` is the agent's own text. Jev judges whether the arguments fit it; the agent cannot talk its way past a rule, and a justification that does not match the call is itself a denial reason. Prompt injection through tool arguments can at most skew Jev's numbers; the rule tier is immune, and that is why rules are first and final.

### 5.5 Prompt paragraph (both admin agents)

> Before calling any tool that changes the cluster (patch, apply, create, delete, label, annotate, execute), call `assess_tool_call` with the exact tool name and arguments you intend to use and one sentence of justification. Repeat its risk, flags and reasons to the user verbatim before you proceed. If it says `deny`, do not call the tool; explain the reason and ask the user how to proceed.

### 5.6 Verdict log and alerting

One JSON line per gated call on stdout: `{check: "tool-guard", at, agent, tool, class, verdict, risk, decided_by, reasons, flags, mode, args_sha256_12}`. No arguments and no justification text, the same rule as the audit summary. Loki keeps it 30 days. A Grafana rule fires on `verdict="deny"` in `enforce` mode and on `decided_by="jev-unavailable"`; a dashboard panel shows verdict counts per agent and the observe-mode "would deny" rate, which is the number the operator reads before switching modes.

### 5.7 Failure modes

| Failure | Behaviour |
|---|---|
| Guard pod down | The agents' tool calls fail at the MCP layer. kagent reports the tool error; nothing runs ungated because nothing runs. The Service has one replica; a second is a cost question, not a design one. |
| Upstream `kagent-tools` down | Guard readiness fails; same as above. |
| Jev down | 5.4: flag or deny, never allow. Logged with `decided_by: jev-unavailable`. |
| Rules ConfigMap malformed | Guard refuses to start (readiness fails). A bad rule must not become "no rules". |
| Tool renamed by a chart upgrade | Not exposed (not in the taxonomy); the agent reports "Unknown Tool", which is the existing fail-closed behaviour. |
| Agent skips the preflight | The post-approval floor still applies. The verdict log shows a gated call with no preceding assessment for the same `args_sha256_12`; a panel counts these. |

### 5.8 Testing

- Unit: rules engine (every rule kind, redacted-input invariance), scorer thresholds with fake Jev bodies, the unavailable paths, tool surface filtering against a fake upstream `tools/list`, verdict log argument-freedom.
- Admission fixtures (`tests/admission-policies`): the two Agent manifests bound to `tool-guard` pass; a variant that drops `assess_tool_call` from the taxonomy fails.
- Identity validator: the `tool-guard` RemoteMCPServer in git satisfies invariant 3.
- Spike before implementation: deploy the guard in `observe` mode wired to `istio-agent` only, exercise one approved `istio_apply_waypoint`, and confirm (1) the error text of a forced denial appears in the kagent chat, (2) which headers kagent sends, (3) end-to-end latency with Jev in the path (target under 2 s added).

## 6. Out of scope

- Request routing or refusal for `homelab-knowledge`.
- Replacing or moving kagent's approval dialog.
- A NetworkPolicy for `kagent` (egress stays open; this spec adds one more destination).
- Correlating verdicts with the action record by session id, unless the spike finds an identifier in the headers.
- Guarding `k8s-reader` or read-class tools.

## 7. Open questions for the review

1. Should `observe` mode also run the preflight prompt change, or only the proxy? Running both gives the operator the full picture; running only the proxy keeps the agents' behaviour unchanged for the first two weeks.
2. Is the `deny.namespaces` list right, and should `postgresql` be denied rather than flagged for `k8s_execute_command`?
3. One replica or two for the guard?
