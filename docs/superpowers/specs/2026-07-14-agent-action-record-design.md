# Agent Action Record (Observability O1) — Design

- **Date:** 2026-07-14
- **Status:** Draft for review
- **Pillar:** L03 Observability, increment O1
- **Roadmap:** [ADP Remaining Pillars](2026-07-14-adp-remaining-pillars-roadmap.md)
- **Depends on:** the O0 spike (done — the data already exists)

## Problem

We have no way to answer *"which agent called which tool, with what arguments, was
it approved?"* — not because the data is missing, but because **nothing reads it.**

The O0 spike found kagent already persists all of it in its own Postgres:
`session` (carrying `agent_id`), `event` (the JSON event stream), `task`, and
`feedback`. `event.data` holds `function_call` / `function_response` with tool name
and full arguments, `adk_request_confirmation` for HITL gates *including the
outcome*, and `usage_metadata` token counts.

The cost of not reading it is not hypothetical. The spike ran one query and found:

```
k8s_agent — gated tools actually invoked:
  k8s_execute_command    14 calls   on 2026-07-09
  k8s_delete_resource     1 call    on 2026-04-19

approval requests raised by k8s_agent in ALL its history:  0
```

Fourteen arbitrary shell commands executed through a human-approval gate that was
silently doing nothing, because Argo was stripping `requireApproval` on every sync.
The evidence sat in that database for months. **Nobody could see it because nothing
queried it.**

## Goal

A **read-only, redacted, agent-attributed record of tool invocations** — queryable
today, and shaped so O2 (alerting) and Evaluation (E1) can build on it without
re-deriving it.

## The hard constraint: this data is a secret-exposure surface

`event.data` contains **full tool responses**. `k8s_get_resource_yaml` against a
Secret returns its base64 payload verbatim. `k8s_get_pod_logs` returns whatever the
app logged. So the raw event stream must be treated as **containing live
credentials**, and any consumer — Grafana, Loki, a dashboard, an agent — is an
exfiltration path.

The design principle follows directly:

> **Redact at extraction, never at display.** The raw `event` table is never
> exposed to Grafana, Loki, an agent, or a human dashboard. Everything downstream
> reads the redacted record, not the source.

### What the record includes, and what it must not

| Field | Included? | Why |
|---|---|---|
| timestamp, `session_id`, `agent_id` | ✅ | attribution — the whole point |
| tool name | ✅ | what was called |
| tool **arguments** | ✅ **redacted** | *see below* — this is the hard case |
| approval requested / outcome (`confirmed`) | ✅ | a HITL gate you cannot audit is a gate you cannot trust |
| token usage | ✅ | cost attribution; no secret content |
| tool **response body** | ❌ **NEVER** | this is where secrets live. Record that a response occurred, its byte length, and a content hash — never the content. |

**Arguments are the genuinely hard case, and we keep them deliberately.** The most
security-relevant thing in the whole record is
`k8s_execute_command args={command: ...}` — auditing it *is the point*, and
dropping it would gut the record. But a command can itself carry a secret
(`kubectl create secret ... --from-literal=...`).

Resolution: keep arguments, redact **values** by pattern (high-entropy strings,
`token`/`password`/`secret`/`key`-shaped keys, PEM blocks, base64 blobs over a
length threshold), and truncate. Accept that argument redaction is
**best-effort, not a guarantee** — and say so plainly rather than implying the
output is safe to publish. The output is *safer*, not *safe*: treat it as
internal, not public.

Response bodies get no such nuance: they are excluded categorically, because
unlike arguments they carry no audit value that justifies the risk.

## Approach

**A script, not a service.** `scripts/agent-audit.py` — pure functions plus a CLI,
mirroring `validate-agent-*.py`. It connects read-only, extracts, redacts, and
emits. No new infrastructure, no new attack surface, and the redaction logic is
unit-testable, which is the part that actually has to be right.

Deliberately **not** a Grafana Postgres datasource: Grafana would query the raw
table, which would put unredacted secrets on a dashboard. That is the failure this
design exists to prevent.

## Components

1. **`scripts/agent-audit.py`**
   - `--since` / `--agent` / `--tool` filters.
   - `--format table|json`.
   - `--ungated` — *the query that matters*: every invocation of a
     taxonomy-classified `write`/`destructive` tool with **no approval event in its
     session**. This is the `k8s_agent` finding, generalised. It reuses the
     capability taxonomy (`agent-capability-taxonomy.yaml`) as the source of
     "which tools should have been gated" — one source of truth, already enforced
     at admission.
   - `--cost` — per-agent token rollup.

2. **Redaction module** (in the same script, separately tested)
   - Categorical: drop all `function_response` bodies; keep name, byte length, hash.
   - Pattern-based on argument *values*.
   - Fail-closed: an argument value that cannot be classified is redacted, not
     passed through.

3. **Tests** — `tests/agent-audit/`. The redaction tests are the important ones:
   feed known secret shapes through and assert they do not survive. A redactor
   nobody tested is a redactor that does not work.

## Success criteria

1. `agent-audit.py --ungated` reproduces the `k8s_agent` finding from the spike
   (14 × `k8s_execute_command`, 0 approvals) **from the CLI, not by hand**.
2. A known secret planted in a tool response never appears in any output format.
3. Per-agent cost rollup matches the spike's totals.
4. Read-only: the script holds no write grant and the DB user it uses cannot mutate.
5. Runs from a laptop against a port-forward, and is CronJob-shaped for later.

## Safety, blast radius & rollback

- **Read-only.** No cluster or DB mutation. Worst case is a bad query.
- **The output is the risk, not the code.** Redaction is best-effort on arguments;
  treat output as internal. Do not wire it to Slack, a public dashboard, or an
  agent-readable tool without a further review of the redaction.
- Rollback: delete the script. Nothing depends on it.

## Non-goals (follow-ons)

- **O2 — alerting.** `--ungated` returning rows should page someone. Needs a
  scheduled runner and a sink; the detector lands here, the alerting does not.
- **O4 — durable retention.** The record lives in kagent's *operational* DB with no
  TTL and no backup; a session delete may cascade. Copying it somewhere durable is
  a separate decision, and it is the point at which retention policy has to be
  chosen.
- **Evaluation (E1).** Needs the *response bodies* this design deliberately
  excludes. That is a different consumer with a different risk profile and must be
  scoped on its own — it is not a flag on this script.
- A Grafana dashboard — only over the *redacted* store, once one exists (O4).

## Open questions

1. **How does the script authenticate to Postgres?** kagent's own credentials are
   in `kagent-db-credentials` (read/write). An audit tool should have its own
   **read-only DB role** — which means a Vault-scoped credential, exactly the
   pattern the Identity pillar established. Doing that properly is arguably part of
   this increment rather than a follow-on.
2. **Is `agent_id` stable?** It appears as `kagent__NS__k8s_agent`. Ghost agents
   (`helm_agent`, `dnd_agent`) appear in history but no longer exist. The record
   must not assume a live `Agent` CR exists for every `agent_id` it reports.
