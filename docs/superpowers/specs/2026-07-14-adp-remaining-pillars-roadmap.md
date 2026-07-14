# ADP Remaining Pillars — Roadmap (L02 Paths, L03 Evaluation, L03 Observability)

- **Date:** 2026-07-14
- **Status:** Draft for review
- **Frames onto:** the Weave Intelligence ADP model — L01 Tooling/IDP, L02 Paths, L03 Agent Infrastructure (7 pillars: Identity, Context, Capability, Execution, Evaluation, Security, Observability).
- **Predecessors (both shipped):** [Agent Identity](2026-07-11-agent-identity-principal-design.md), [Agent Capability Classes](2026-07-13-agent-capability-classes-design.md).

This is a **sequencing document**, not an implementation plan. It exists to pick
the next increment with the dependencies visible. Each track below gets its own
design doc before any code.

## Where we actually are

| Layer / pillar | State |
|---|---|
| **L01 IDP** | Strong. Backstage, Argo CD, Atlantis, Crossplane, Vault, Kyverno, Falco, Coroot, Loki, argo-rollouts. |
| **L02 Paths** | **Fragmented, not absent** — see Finding 3. |
| L03 **Identity** | ✅ Closed. Contract, universal hard-fail CI gate, Kyverno `Enforce`, no exclusions. |
| L03 **Security** | ✅ Closed. Capability classes, fail-closed tool taxonomy, `requireApproval` enforced, delegation cannot escalate. |
| L03 **Context** | ✅ Strong. agent-docs framework + Backstage catalog graph. |
| L03 **Capability** | ✅ Strong, and now *bounded* by the class contract. |
| L03 **Execution** | ⚠️ Unassessed. kagent runtime + openshell sandbox exist; never audited. Out of scope here. |
| L03 **Observability** | ❌ Weak — see Track O. |
| L03 **Evaluation** | ❌ Absent — see Track E. |

## Three findings that change the plan

These came out of a full inventory of the repo and cluster, and each one moves an
estimate.

> ## ⚡ O0 SPIKE RESULT (run 2026-07-14) — the data already exists
>
> **kagent already records everything Track O was going to build.** The spike is
> done; O1 collapses from "build a pipeline" to "expose what is there."
>
> kagent's Postgres (`postgresql.postgresql.svc:5432/kagent` — a **standalone**
> Postgres, *not* the CNPG cluster) holds:
>
> | table | rows | what |
> |---|---|---|
> | `session` | 91 | **carries `agent_id`** — attribution is solved |
> | `event` | 709 | the full event stream; `data` is JSON |
> | `task` | 143 | A2A tasks |
> | `tool` | 226 | tool registry |
> | `feedback` | 1 | **`is_positive`, `feedback_text`, `issue_type`** — human labels |
>
> `event.data` contains `function_call` / `function_response` with **tool name and
> full arguments** (`k8s_execute_command args=[command, namespace, pod_name]`,
> `get_file_contents args=[owner, path, ref, repo]`), `adk_request_confirmation`
> for HITL gates **including the outcome** (`"confirmed": false`), and
> `usage_metadata` with `total_token_count` / `prompt_token_count` /
> `thoughts_token_count`.
>
> Joining `event → session.agent_id` gives a **per-agent rollup today**: sessions,
> events, tool calls, approvals, and tokens. 113 events in one sample window
> summed **1,695,236 tokens**.
>
> **Three consequences that change the roadmap:**
>
> 1. **O1/O2 are mostly done.** The record exists with attribution, arguments, and
>    approval outcomes. The work is exposure, retention and query — not capture.
> 2. **Evaluation has a corpus already.** `session`/`task`/`event` is a real
>    conversation history, and `feedback` is a human-labelled ground-truth seed.
>    E1 gets much cheaper — bootstrap from actual history rather than authoring
>    fixtures cold.
> 3. **Per-agent cost attribution works TODAY, with no per-agent API keys.** This
>    materially changes the Identity §100 follow-on: dedicated Anthropic keys are
>    still useful for *isolation and revocation*, but they are **not** needed for
>    cost attribution. That item should be re-scoped before it is built.
>
> **It also proved the requireApproval incident, retroactively.** `k8s_agent`
> invoked `k8s_execute_command` **14 times on 2026-07-09** and `k8s_delete_resource`
> once, and raised **zero** approval requests in its entire history — while those
> tools were listed in its `requireApproval`. The gates were being stripped by Argo
> on every sync, exactly as suspected, and the evidence was sitting in this database
> the whole time. Nobody could see it because nothing queries it. *That is the
> argument for this pillar.*
>
> **Two cautions for the design:**
>
> - 🔴 **`event.data` contains full tool RESPONSES, which can contain secret
>   material** (e.g. `k8s_get_resource_yaml` of a Secret). Any export, dashboard, or
>   agent-readable view of this data is a **secret-exposure surface** and must be
>   redacted at the boundary. Do not pipe it into Loki or a catalog unfiltered.
> - **Ghost agents are in the history**: `helm_agent`,
>   `argo_rollouts_conversion_agent`, `dnd_agent` — agents that ran while believed
>   disabled (the broken `agents:` Helm nesting). More evidence that the record is
>   worth reading.

### Finding 1 — Observability is cheaper than it looks. The data may already exist.

*(Superseded by the O0 result above — kept for the reasoning.)*

The assumption was "we have no agent telemetry." That is **half wrong**:

- kagent already exports **OTel traces AND logs** to Coroot
  (`base-apps/kagent.yaml`, OTLP → `coroot-coroot.coroot:4317`).
- Coroot is **not metrics-only** — it is ClickHouse-backed and retains
  **traces for 7d** (`tracesTTL: 7d`), plus logs and profiles.
- Grafana Alloy ships **every pod's stdout** cluster-wide into Loki, retained
  **30d**.
- kagent runs its own **PostgreSQL + pgvector** database. It is an agent runtime:
  it almost certainly persists sessions, tasks and tool calls there. The schema is
  not in git (the controller owns its own migrations), so **what it already
  records is unknown and unverified.**

So the first Observability increment is not "build a pipeline." It is
**"find out what we already have"** — inspect the kagent Postgres schema and a
real Coroot trace, and establish whether tool-call spans (agent, tool, arguments,
approval outcome) are already being emitted or stored. That is a spike, not a
build, and it could collapse the whole track.

What is genuinely, confirmed missing:
- **No record of approval decisions.** `requireApproval` gates exist on three
  agents; nothing persists who approved what, when.
- **No Kubernetes API-server audit policy** anywhere in git.
- **Falco has no sink** (`falcosidekick.enabled: false`) — detections go to stdout
  and nothing routes or alerts on them. Three rules are enabled.
- **No OTel sampling config** is pinned, and no metrics exporter for kagent.

### Finding 2 — Evaluation is blocked on Observability, and the judge is not what we thought.

`.judge/rubric.md` grades **git diffs only** — correctness, security, tests,
scope. It is a **local Claude Code `Stop` hook** from a plugin outside this repo
(`codex-judge`), **not a CI gate**. It never sees an agent's answer.

There is **no** golden-answer corpus, **no** prompt-regression test, **no** LLM-as-
judge over agent responses, and — critically — **nothing captures an agent's output
in a scorable form.** Every `tests/agent-*` suite validates *manifests and
contracts*, not behaviour.

You cannot score what you did not record. **Evaluation depends on Track O.**

### Finding 3 — L02 is not empty. It is fragmented across two repos and a baked image.

The pieces of a golden path all exist, but nothing binds them and **none of it is
visible to this repo**:

- **Backstage scaffolder Templates are NOT in this repository.** They live in
  `arigsela/backstage` (`examples/templates/...`) and are **baked into the
  `backstage-portal:v1.4.5` container image** via `catalog.locations` `type: file`.
  Changing a path requires an image rebuild, not a manifest change. They are
  invisible to this repo's GitOps, review, and CI.
- **Crossplane XRDs exist** and are what a path would provision:
  `XApplication` (namespaced, Crossplane v2 — Deployment + Service + Ingress, with
  optional CNPG Postgres and AWS S3/IAM) and `XSmokeTestApp`.
- **An agent-facing registry contract already exists — and is dead.** Seven
  `agents.platform.ai/*` annotations (`skills`, `delegates`, `capabilities`,
  `a2a-endpoint`, …) are carried by **3 of 7 agents**, consumed by **nothing**, and
  enforced by **nothing**. Its own comment calls it "consumed by *future* MCP-backed
  assistants," and the spec it cites —
  `docs/superpowers/specs/2026-05-19-aicontext-catalog-kind-design.md` — **does not
  exist.** It is a dangling reference.
- The **TeraSky kubernetes-ingestor** already mirrors kagent Agents and Crossplane
  XRs into the Backstage catalog, and Backstage already exposes an **MCP Actions
  backend** that `homelab-knowledge` binds (`get-catalog-entity`).

So L02's work is **consolidation, not greenfield**: give paths a declarative
home that both humans and agents can enumerate, and either revive or kill the
`agents.platform.ai` contract.

### Prior art: the Golden POC (designed, never built)

`2026-05-02-golden-ai-platform-poc-design.md` + five phase plans designed exactly
this shape — Backstage Template → Crossplane → kagent, with **Langfuse** for agent
traces and solo.io `agentregistry`/`agentgateway`. **None of it was built** (zero
manifests, no namespaces). It was scoped as a 30-minute client demo, and it
predates the agent-docs, Identity and Capability work by ~2.5 months, so it does
not know about any of the contracts we now enforce. **Mine it for design, do not
resurrect it wholesale.** Langfuse in particular is worth evaluating against
"Coroot already retains traces" before adopting a second tracing backend.

## The dependency graph

```
  Track O  Observability ──────────► Track E  Evaluation
  (agent action record)             (score what was recorded)

  Track P  L02 Paths  ── independent of both ──►
```

**O gates E.** P is orthogonal and can run at any time.

## The hard architectural constraint (shapes Track O)

The kagent `Agent` CRD gives declarative agents **no `serviceAccountName`**. Every
agent shares one Kubernetes identity. Therefore:

> **Kubernetes API-server audit logs can never attribute an action to a specific
> agent.** Adding an audit policy would tell you "the kagent SA deleted a Pod" —
> never *which agent* did it.

This is the same constraint that shaped Identity (which is why credentials are
scoped at the ESO/Vault boundary, not the pod). Track O must therefore capture
attribution **at the kagent layer** — where the agent identity is known — not at
the Kubernetes boundary. Any design that reaches for an audit policy as the
primary mechanism is wrong on arrival.

---

## Track O — Observability

**Goal:** a queryable, attributable record of agent behaviour: *which agent called
which tool, with what arguments, was it approved, what did it return.*

| # | Increment | Size | Notes |
|---|---|---|---|
| ~~O0~~ | ~~Spike: what do we already have?~~ | — | ✅ **DONE 2026-07-14.** kagent already records agent-attributed tool calls with arguments, approval outcomes, and token usage. See the spike result above. |
| **O1** | **Expose the agent action record.** The data exists; nothing reads it. A read-only view/query surface over `event → session.agent_id`: which agent called which tool, with what arguments, approved or not, at what token cost. **Must redact tool responses** — they can contain secrets. | **S–M** | Was sized L. The capture problem is solved; this is exposure + redaction. |
| O2 | **Alert on the gaps the record reveals.** A gated tool invoked with no approval event is exactly the `requireApproval`-stripping incident, and it is now *detectable*. Make it an alert, not an archaeology exercise. | S | Directly justified by the spike. |
| O3 | **Falco gets a sink.** `falcosidekick.enabled: false` today — detections go to stdout and nothing reads them. Route them and prune the rule set. | S | Independent; any time. |
| O4 | **Retention + dashboard.** The record lives in kagent's *operational* DB with no TTL and no backup — it is not an audit store, and a session delete may cascade. Decide whether to copy it somewhere durable. Add a Grafana agent dashboard (none exists). | M | Retention is the real open question. |

**Do not** reach for a Kubernetes audit policy — see the constraint above. And note
the spike confirms it would not have helped: the attribution lives in kagent's DB,
which is exactly where the shared-ServiceAccount constraint predicted it must.

## Track E — Evaluation

**Goal:** know whether the agents are any *good*, not merely contained.

**Blocked on O1.** Nothing captures agent output in a scorable form today.

**No longer blocked.** The O0 spike found the corpus already exists: `session` /
`task` / `event` is a real conversation history (91 sessions, 709 events across 10
agents), and the `feedback` table already carries **human labels**
(`is_positive`, `feedback_text`, `issue_type`).

| # | Increment | Size | Notes |
|---|---|---|---|
| E1 | **Golden-answer corpus, bootstrapped from real history.** Mine `event` for actual Q&A pairs — start with `homelab-knowledge` (269 events; clearest ground truth, it answers from repo docs we control). Curate, don't author cold. **Redact before anything leaves the DB.** | S–M | Was M and blocked. Now unblocked and cheaper. |
| E2 | **Scoring harness.** LLM-as-judge over agent responses. Mirrors the `.judge` pattern but points at agent output rather than git diffs. Seed the rubric from the `feedback` table's existing human labels. | M | |
| E3 | **Regression gate.** Score on every agent/prompt/tool change; fail on regression. Makes the contract behavioural, not merely structural. | M | |
| E4 | **Promote `.judge` into CI.** It is currently a local Claude Code Stop hook from a plugin outside this repo — it does not run for anyone but you, on any machine but this one. | S | Cheap, independent, worth doing regardless. |

## Track P — L02 Paths

**Goal:** a declarative registry of golden paths that **both humans and agents**
can enumerate and invoke.

| # | Increment | Size | Notes |
|---|---|---|---|
| P1 | **Decide the `agents.platform.ai` contract's fate.** It is on 3/7 agents, consumed by nothing, enforced by nothing, and its spec doc does not exist. **Either write the spec, validate it, and give it a consumer — or delete it.** A contract nobody reads is worse than none: it looks like coverage. | S | Do this first; it is cheap and it unblocks the rest. |
| P2 | **Make paths visible to this repo.** Scaffolder Templates are baked into the Backstage image from another repo. At minimum, a path *registry* (what paths exist, what each provisions, what it requires) should live in git here, next to the XRDs it drives. | M | Does not require moving the Templates — only declaring them. |
| P3 | **Agent-consumable paths.** Expose the registry through the existing Backstage MCP Actions backend so an agent can enumerate and (eventually) invoke a path. The plumbing already exists — `homelab-knowledge` already binds `get-catalog-entity`. | M | The payoff: agents that are now safe to act finally have something *worth* acting on. |
| P4 | **A second real path.** Today the only meaningful XRD is `XApplication`. A platform with one path is a demo. | L | |

---

## Recommended sequence

1. **O0 (spike)** — a few hours, read-only, and it re-sizes the entire Observability track. There is no reason to plan O1 before knowing what kagent already records.
2. **P1** — cheap, and it removes a dead contract that currently *looks* like L02 coverage while providing none.
3. Then choose by appetite:
   - **Observability-first** (O1 → O2) if the goal is trustworthy agents you can audit — and it unblocks Evaluation.
   - **Paths-first** (P2 → P3) if the goal is agents doing useful work; it is independent and the ingredients are already there.
4. **E4** (promote `.judge` to CI) is cheap, independent, and can slot in anywhere.
5. **Evaluation proper (E1–E3)** after O1.

**O3 (Falco sink)** and **E4** are both small, independent, and can be picked up
in any gap.

## Success criteria for this document

This roadmap has done its job when each track has its own design doc and the
first increment of the chosen track is in flight. It is deliberately not an
implementation plan — the Identity and Capability increments both showed that the
inventory changes the design, and O0/P1 exist precisely to let that happen before
we commit.

## Open questions

1. **Does kagent already persist tool calls?** (O0 answers this. Everything in
   Track O is sized off it.)
2. **Langfuse, or Coroot?** The Golden POC assumed Langfuse. Coroot already
   retains traces for 7d. Adding a second tracing backend needs a real
   justification — likely "Coroot is service-shaped, not agent-shaped" — but that
   should be demonstrated, not assumed.
3. **Where do scaffolder Templates belong?** Leaving them baked in another repo's
   image means paths are invisible to this repo's review and CI. Moving them is a
   bigger change than this roadmap scopes.
4. **How long should an agent action record be retained?** Traces are 7d and Loki
   is 30d. An audit trail probably wants longer, which likely means it is not just
   a trace.
