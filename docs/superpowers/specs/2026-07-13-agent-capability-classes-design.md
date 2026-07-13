# Agent Capability Classes — Security Guardrails (increment 1) — Design

Status: draft for review
Pillar: L03 Security (agent guardrails)
Predecessor: [Agent Identity — "Agent Principal"](2026-07-11-agent-identity-principal-design.md)

## Problem

The Identity pillar answered *who an agent is* — each agent's credentials are now
scoped to a dedicated Vault path, proven read-own / denied-neighbour. It said
nothing about *what an agent may do*.

Today, nothing constrains capability:

1. **The existing `toolNames` rule is an anti-bind-all floor, not a fence.** It
   denies an empty/absent `toolNames`, and nothing more. Any agent may bind
   `k8s_delete_resource` and `k8s_execute_command` and pass admission.

2. **Delegation is an uncovered escalation path.** The rule's
   `[?type=='McpServer']` filter excludes `type: Agent` refs entirely.
   `homelab-knowledge` — whose own three tools are genuinely read-only, backed by
   a `--read-only` MCP binary — carries a delegation ref to `k8s-agent`, which
   holds delete/apply/exec. Its *effective* capability is admin.
   `build-orchestrator` binds no MCP tool at all yet delegates to both
   `k8s-agent` and `istio-agent`.

3. **Four mutating tools are bound but ungated.** `k8s_annotate_resource`,
   `k8s_remove_annotation`, `k8s_label_resource`, `k8s_remove_label` are on
   `k8s-agent` and absent from its `requireApproval`. Argo CD sync behaviour and
   Kyverno matching are both label/annotation-driven, so these tools can be used
   to disable the controls holding the agent in.

4. **`requireApproval` is hand-maintained and unasserted.** It lives in the same
   object an agent author edits, no policy requires it, and it has already been
   silently stripped once — for days, while Argo CD reported Synced/Healthy.

## Insight: where capability actually lives in kagent

The kagent `Agent` CRD gives declarative agents **no `serviceAccountName`**. All
agents share one chart-provided Kubernetes identity. There is therefore no
native per-agent principal to bind RBAC, NetworkPolicy, or audit attribution to.

Consequence for this design: **capability must be enforced at the declaration,
not at the Kubernetes API boundary.** An agent's capability is exactly the set of
tools it binds, plus — transitively — the tools of every agent it delegates to.
That set is fully knowable from Git, which is what makes both a CI gate and an
admission gate possible without inventing a new runtime identity.

## Goal

Make an agent's capability **declared, bounded, and non-escalating** — checkable
in CI and enforced at admission, in the same shape as the Identity contract.

## The contract (four invariants)

Every kagent `Agent` must satisfy:

1. **Declared class.** Carries the label `capability.homelab/class` with value
   `read`, `write`, or `admin`. No default. An agent with no class is denied.

2. **Tools within class.** Every bound tool is classified in the taxonomy, and
   its classification is permitted by the agent's class. A tool absent from the
   taxonomy is **denied** (fail-closed — a new tool from a chart upgrade must be
   triaged before it can be used).

3. **Mutating tools are gated.** Every bound tool classified `write` or
   `destructive` appears in that tool ref's `requireApproval`.

4. **Delegation does not escalate.** For every `type: Agent` tool ref, the
   delegate's class is ≤ this agent's class. Effective capability = own tools ∪
   delegates' effective capability.

### The class lattice

| Class    | May bind                    | Approval required for | May delegate to     |
|----------|-----------------------------|-----------------------|---------------------|
| `read`   | `read`                      | — (nothing to gate)   | `read`              |
| `write`  | `read`, `write`             | all `write`           | `read`, `write`     |
| `admin`  | `read`, `write`, `destructive` | all `write` + `destructive` | any            |

Ordering: `read` < `write` < `admin`.

## The taxonomy

A single source of truth in Git, applied as a ConfigMap so both gates read the
same content:

- `base-apps/kyverno-policies/agent-capability-taxonomy.yaml` → ConfigMap
  `agent-capability-taxonomy` (namespace `kyverno`), with keys `read`, `write`,
  `destructive` holding JSON arrays of tool names.
- Kyverno reads it via a `context.configMap` block + `parse_json`.
- The CI validator reads the same file from the working tree.

Initial classification of the tools bound today:

**`read`** (53) —
*k8s:* `k8s_get_resources`, `k8s_get_pod_logs`, `k8s_describe_resource`,
`k8s_get_events`, `k8s_get_resource_yaml`, `k8s_get_cluster_configuration`,
`k8s_get_available_api_resources`, `k8s_check_service_connectivity`,
`k8s_generate_resource`.
*istio:* `istio_proxy_status`, `istio_proxy_config`, `istio_version`,
`istio_analyze_cluster_configuration`, `istio_ztunnel_config`,
`istio_waypoint_status`, `istio_list_waypoints`, `istio_remote_clusters`,
`istio_generate_manifest`, `istio_generate_waypoint`.
*grafana:* `search_dashboards`, `get_dashboard_by_uid`,
`get_dashboard_panel_queries`, `query_prometheus`, `query_loki_logs`,
`query_loki_stats`, `list_prometheus_metric_names`,
`list_prometheus_metric_metadata`, `list_prometheus_label_names`,
`list_prometheus_label_values`, `list_loki_label_names`,
`list_loki_label_values`, `list_datasources`, `get_datasource`,
`list_alert_rules`, `get_alert_rule_by_uid`, `list_contact_points`,
`list_incidents`, `get_incident`, `list_teams`, `list_oncall_users`,
`list_oncall_teams`, `list_oncall_schedules`, `get_oncall_shift`,
`get_current_oncall_users`, `list_sift_investigations`,
`get_sift_investigation`, `get_sift_analysis`, `get_assertions`,
`find_slow_requests`, `find_error_pattern_logs`.
*github/backstage:* `get_file_contents`, `search_code`, `get-catalog-entity`

**`write`** (13) —
*k8s:* `k8s_patch_resource`, `k8s_apply_manifest`, `k8s_create_resource`,
`k8s_create_resource_from_url`, `k8s_annotate_resource`,
`k8s_remove_annotation`, `k8s_label_resource`, `k8s_remove_label`.
*istio:* `istio_apply_waypoint`, `istio_install_istio`.
*grafana:* `update_dashboard`, `create_incident`, `add_activity_to_incident`

**`destructive`** (3) — `k8s_delete_resource`, `k8s_execute_command`,
`istio_delete_waypoint`

`k8s_generate_resource` / `istio_generate_manifest` / `istio_generate_waypoint`
are classified `read`: they render YAML and return it, they do not apply it.
`k8s_execute_command` is `destructive` rather than `write` because it is an
arbitrary-code escape hatch whose blast radius is not bounded by its name.

## Current-state violations this surfaces

Applying the contract to the tree as it stands today:

| Agent | Declared class | Violation | Resolution |
|---|---|---|---|
| `k8s-agent` | `admin` | 4 `write` tools bound but absent from `requireApproval` | Add the four label/annotation mutators to `requireApproval` |
| `istio-agent` | `admin` | none — its approval list already covers its writes | Label only |
| `homelab-knowledge` | `read` | **delegates to `k8s-agent` (admin)** — escalation | See "Decision 1" |
| `build-orchestrator` | `admin` | binds no MCP tool, but delegates to two `admin` agents | Declare `admin` (its effective class) |
| `dungeon-crawler-carl-agent` | `read` | none (`tools: []`) | Label only |
| `skill-suggester` | `read` | none (`tools: []`) | Label only |
| `observability-agent` | `write` | **chart-rendered; 3 ungated mutating tools; dangling delegation** | See "Decision 2" |

### Decision 1 — the `homelab-knowledge` → `k8s-agent` delegation — RESOLVED: split

Dropping the delegation closes the hole but removes real capability:
`homelab-knowledge` uses it to answer live cluster questions ("why is this pod
crashlooping"), which is much of its value.

**Decision: split the capability rather than remove it.** Introduce a new
`k8s-reader` agent — class `read`, bound only to the read-classified `k8s_*`
tools — and repoint `homelab-knowledge`'s delegation from `k8s-agent` to
`k8s-reader`. `homelab-knowledge` keeps its cluster-investigation ability, and
its effective capability becomes genuinely read-only. `k8s-agent` remains the
`admin` operator agent for humans who need to act.

### Decision 2 — the chart-rendered `observability-agent` — RESOLVED: adopt as `write`, gated

Invariant 1 denies any `Agent` without a class label. The chart renders
`observability-agent` with no such label, so an Enforce policy matching
cluster-wide would **deny it on next Argo sync** and break the app. It therefore
has to be resolved before Enforce.

**Inspection of the live object disproved the "read-only" claim** made in the
repo's own comments. It binds 34 tools from `kagent-grafana-mcp` with **zero
`requireApproval`**, three of which mutate: `update_dashboard`, `create_incident`,
`add_activity_to_incident`. It also carries a **dangling `type: Agent` delegation
to `promql-agent`** — an agent that does not exist in the cluster and is
explicitly disabled in `base-apps/kagent.yaml`.

This escaped notice because `kagent-grafana-mcp` is one of the two MCP servers
**exempted by name** from the Identity contract's "must exist in Git" invariant,
so its 34 tools were never scrutinised. It is the exact failure mode invariant 2's
fail-closed rule exists to catch.

**Decision:** adopt into Git; declare class `write`; keep all 34 tools; add the
three mutating tools to `requireApproval`; **remove** the dead `promql-agent`
delegation (it points at nothing, so removing it restores no behaviour and breaks
none). Disable the chart copy, following the `k8s-agent`/`istio-agent` precedent.

## Enforcement

Two gates, mirroring the Identity increment.

**CI (`scripts/validate-agent-capability.py`) — authoritative for all four
invariants.** The full agent graph is in Git, so delegation transitivity is
static analysis: build the delegation DAG, compute each agent's effective class,
fail if any agent's declared class is below its effective class. Also detects
delegation cycles.

**Kyverno (`ClusterPolicy/agent-capability`, Enforce) — invariants 1–3.** These
are single-object checks and need no cross-object lookup. Ships directly in
`Enforce`, not staged through `Audit`, because the policy is verified before it
lands (see below) and the class labels ship in the same commit. Argo sync-waves
put the taxonomy ConfigMap and the Kyverno RBAC at wave `-1` and the policy at
wave `0`, so the policy never evaluates before its taxonomy exists.

**Invariant 4 (delegation) IS enforced at admission**, by rules 6–7: a `read`
agent may not delegate to a `write`/`admin` agent, and a `write` agent may not
delegate to an `admin` agent. Each reads the delegate's class with a Kyverno
`apiCall`.

This matters more than it first appeared, because the escalation is **reachable
by an agent, not only by a careless PR**: `k8s-agent` holds
`k8s_create_resource`, so it could be driven to create a `read`-class Agent that
delegates straight back to itself — binding no mutating tool of its own, and so
passing rules 1–5 cleanly. Leaving this to CI alone would have left a live
privilege-escalation path open at admission.

Rules 6–7 check **one hop**, which is inductively sufficient for admission: if
every agent is forbidden from delegating above its own class, no chain can exceed
its head's class. The case one-object-at-a-time cannot catch is a *later
promotion* — A(read) → B(read) is admitted, then B is promoted to `admin` and A
is never re-admitted. CI computes the full transitive closure over the Git graph
and catches it; background PolicyReports (now that Kyverno can read Agents)
re-scan existing agents and surface it.

The apiCall is the policy's only runtime lookup, and it fires **only** inside
`foreach tools[?type=='Agent']` — an agent with no delegations never triggers it.
So the blast radius of an apiCall failure is the delegating agents alone, not
every Agent in the cluster.

The existing `agent-mcp-tools-must-list-toolnames` rule is retained: it remains
the floor that makes invariant 2 meaningful (a bind-all would otherwise have no
`toolNames` to check against the taxonomy).

### The policy is generated, and the tested artifact is the shipped artifact

The policy **embeds the taxonomy inline** rather than reading
`ConfigMap/agent-capability-taxonomy` at admission time, and is compiled from it
by `scripts/gen-agent-capability-policy.py`. CI fails if the two drift
(`--check`).

This trades DRYness for two things worth more. A `context.configMap` lookup sits
at *rule* level, so it is evaluated for **every** Agent — if it failed for any
reason (RBAC, sync ordering, a schema change), every rule errors and Kyverno's
default `failurePolicy: Fail` denies **all** Agent writes, wedging the kagent app.
And the `kyverno` CLI has no cluster to resolve a ConfigMap against, so that
single riskiest path would have been the one path no test could cover: the policy
we shipped would not have been the policy we tested. Inlining removes the lookup
and closes both gaps at once. The duplication it introduces is exactly what the
generator plus the CI drift check exist to police.

### Verifying the policy

`tests/agent-capability/kyverno/run.sh` runs the **shipped policy file verbatim**
— no rewriting, no substitution — under the `kyverno` CLI, and asserts both
directions: all 8 real agents **pass** (a false positive would wedge the kagent
app on next sync), and each of **9 fixtures — one per rule** — is **denied** (a
false negative is a hole). It also fails if the policy has drifted from the
taxonomy. It runs in CI.

The one thing stubbed is the `apiCall` in rules 6–7, which the CLI cannot make;
`values.yaml` supplies the delegate's class per resource, so the delegation
deny-logic is genuinely exercised and only the lookup is mocked.

New manifests are covered by the repo's existing `yaml-lint` and
`kubernetes-validate` CI jobs, which run `yamllint` and `kubeconform` over changed
`base-apps/**/*.yaml`.

Two Kyverno behaviours cost real debugging time and are worth recording:

1. **Context variables do not resolve inside JMESPath filter expressions.**
   `toolNames[?contains(mutating, @)]` evaluates `mutating` to nil and the rule
   *errors*. Variables are substituted by `{{ }}` templating *before* JMESPath
   runs, so a context variable must be injected as a JSON literal:
   ``toolNames[?contains(`{{ mutating }}`, @)]``. Prefer the `AnyIn`/`AnyNotIn`
   condition operators, which compare arrays natively.
2. **A YAML folded scalar (`>-`) preserves newlines literally** when continuation
   lines are more-indented, embedding `\n` into the JMESPath so it fails to parse
   and the variable resolves to nil. Keep every `jmesPath` on one line.

**Kyverno's background scanning never worked for Agents.** `agent-identity`
declares `background: true` and has produced *zero* PolicyReports, because
Kyverno's reports and background controllers have no RBAC to read
`kagent.dev/Agent`. Admission enforcement was unaffected — the webhook is handed
the object in the AdmissionReview payload — but there has been no report and no
visibility into agents that already exist. Fixed by
`base-apps/kyverno-policies/kyverno-kagent-read-rbac.yaml`.

## Success criteria

1. Every `Agent` in Git carries a valid `capability.homelab/class`.
2. `homelab-knowledge`'s effective capability is `read` — proven by the validator
   computing its transitive closure, not by inspection.
3. An `Agent` binding a tool outside its class is denied at admission (probe).
4. An `Agent` binding a `write`/`destructive` tool absent from `requireApproval`
   is denied at admission (probe).
5. Stripping `requireApproval` from `k8s-agent` fails CI **and** admission — the
   silent-strip regression is structurally impossible.
6. An unclassified tool name is denied (fail-closed probe).
7. All kagent apps remain Synced/Healthy; no agent loses intended function.

## Safety, blast radius & rollback

The policy denies `Agent` writes only. Worst case is a failed Argo sync on the
kagent app, not a runtime outage — running agents are unaffected by an admission
denial on an update. Rollback is reverting the ClusterPolicy to `Audit`.

**The sequencing risk is real and is handled by sync-waves, not by staging.** An
Enforce policy that evaluated before the agents carried their class labels — or
before its taxonomy ConfigMap existed — would block the kagent app. Both are
ordered explicitly: the taxonomy ConfigMap and the Kyverno RBAC are at Argo
sync-wave `-1`, the policy at wave `0`, and the labelled agents ship in the same
commit as the policy. The offline verification (all 8 real agents pass, zero rule
errors) is what makes shipping straight to `Enforce` defensible rather than
reckless.

The one link not covered offline is the `context.configMap` lookup itself: the
`kyverno` CLI has no cluster to resolve it against, so the taxonomy is inlined
for the test. If that lookup were to fail in-cluster, every rule errors and
Kyverno's default `failurePolicy: Fail` denies all Agent writes — fail-closed and
safe, but it would wedge the kagent app until reverted. Watch the first sync.

## Non-goals (documented follow-ons)

- **Egress control.** No NetworkPolicy on kagent pods; agents have unrestricted
  egress. Needs its own increment.
- **Audit trail.** No record of which agent invoked which tool with what
  arguments. Blocked on the shared-ServiceAccount problem: API-server audit logs
  cannot attribute an action to an agent. Likely needs a kagent-layer hook.
- **Per-agent budget / rate limits.** All agents share one Anthropic key with no
  quota or spend cap.
- **Falco has no sink** (`falcosidekick.enabled: false`) — detections go to
  stdout and nothing reads them.

## Open questions

1. ~~Decision 1~~ — **resolved: split, via a new read-only `k8s-reader`.**
2. ~~Decision 2~~ — **resolved: adopt `observability-agent` as class `write`,
   gate its three mutating tools, drop the dead `promql-agent` delegation.**
3. Should `write` really require approval for *every* tool, or is that too noisy
   in practice for an agent a human is actively driving? (Leaning: keep it — the
   gate is per-invocation and the agents are low-traffic. Revisit if it bites.)
4. `kagent-tool-server` and `kagent-grafana-mcp` remain exempt from the Identity
   contract's "MCP server must exist in Git" invariant, because both are
   chart-rendered. The capability taxonomy closes the *tool-level* half of that
   gap (their tools must now be classified to be bindable), but the servers
   themselves are still unreviewable in Git. Worth its own increment.
