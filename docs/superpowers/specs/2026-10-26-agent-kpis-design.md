# Agent KPIs: the agent-first IDP playbook's eight KPIs, measured (§T.144)

**Date:** 2026-10-26 · **Owner:** arigsela · **Status:** implemented in the PR that adds this file

## Intent

The owner is showing the platform to a company's CTO and comparing it with the
platformengineering.org playbook ["Agent-first IDP: how big tech and the clouds are preparing
platforms for agents"](https://platformengineering.org/blog/agent-first-idp-how-big-tech-and-the-clouds-are-preparing-platforms-for-agents)
(2026-10-16). The playbook's practice 9 says to measure an agent platform by agent-scenario KPIs
instead of portal usage. A review of the platform against the playbook found strong telemetry
(traces, the action record) but none of its eight KPIs reported anywhere. The owner asked for the
missing KPIs on a Grafana dashboard to showcase, then left: "im leaving so fully implement the
things we discussed". Every open question below is therefore a call, listed so the owner can
check it.

## Goals

- All eight KPIs on one dashboard (Agents folder, uid `agent-kpis`), each defined in its tile's
  description so a reader can tell what is counted.
- Built from data the platform already has where it exists; one small new job where it does not.
- The same guarantees as the audit: argument-free, read-only, no identity on a dashboard.

## Non-goals

- No new datasource in Grafana (a Postgres datasource would put a database credential in Grafana).
- No change to the collector, the dispatcher or kagent.
- No targets or SLOs: the KPIs are measured, not judged. Thresholds can follow once there is a
  month of data.

## Anti-goals

- A number that looks confident and means nothing: a ratio with nothing behind it shows "—", and
  0 % only when there was something and none of it succeeded; the eval gate's adversarial test
  sessions are never counted as scenarios (the adapters' refusal count is the one exception,
  because their log does not say who asked).
- A hand-written score. The capability tile is computed from files CI checks.

## The KPIs and where each comes from

| # | Playbook KPI | How it is measured here | Source |
|---|---|---|---|
| 1 | Share of capabilities safely available to agents | The playbook's eight capability areas; *open* = an agent serves it; *safely open* = every agent in it has golden tasks of its own | `scripts/agent-capability-map.yaml` + `tests/eval-corpus/`, computed at generation |
| 2 | Intent success rate | Agent tasks that ended `completed`, of all that ended, eval gate excluded | `agent-kpis` (kagent task states) |
| 3 | Time to a safe change | Medians: the implementer's finished alert investigations (alert → diagnosis); its PRs opened → merged; people's PRs for comparison | `agent-kpis` (tasks, GitHub) |
| 4 | Cost per scenario | Estimated model cost per agent run, and per agent | collector counters |
| 5 | Approval cycle time | Request → decision on a gated call: median, p90, count; eval gate excluded | `agent-kpis` (HITL records) |
| 6 | Policy violation rate | Writes that ran without an approved decision / tool calls, both from the job's hours, eval gate excluded; calls refused by adapters and admission denials beside it (stopped before running) | `agent-kpis`, adapter logs, API server metrics |
| 7 | Reduction in platform support load | Distinct firing alerts an agent investigated before a person looked, of all firing alerts | dispatcher lines |
| 8 | Scenarios completed without manual escalation | Alert investigations ending in a diagnosis the platform acts on, of all | dispatcher `remediate` lines |

## Calls made without the owner

1. **A CronJob that prints Loki lines, not an exporter.** Same pattern as the audit jobs
   (`gen-agent-audit-cronjob.py`, script in a ConfigMap, SELECT-only role, stdout → Loki). Hourly
   at :05 over the previous clock hour; a suspended backfill job covers the week before, run once.
   The backfill asks Loki what is already printed and ends at the hourly job's earliest hour, and
   refuses after a previous backfill, when Loki does not answer and between :00 and :10, so a
   late or repeated run can't count an hour twice (found in review: ending "one hour before now"
   did overlap every hour printed since the deploy).
2. **kagent's task states, not trace errors, for intent success.** A task that paused for an
   approval and then finished is one success; a span error says nothing about that.
3. **"Safely open" = golden tasks of its own.** Every agent gets the shared attack and canary
   sample; the playbook's evals are golden tasks with expected behavior, so the bar is an agent's
   own corpus file. This leaves *observe* (k8s-reader) and *provision* (skill-suggester) open but
   not safe, which is the honest result.
4. **Support load for a one-person platform** is the owner's toil: how many alerts arrived already
   investigated. Only 2 of 14 alert rules route to an agent, some by design, so this KPI is low and
   says why in its description.
5. **Escalation** = an investigation that ended inconclusive, failed or timed out. A restart that
   waits for a person's approval, or a PR that waits for a merge, is the designed gate, not an
   escalation.
6. **Caller as a class.** The session's creator (the identity the mesh verified) becomes person,
   dispatcher, slack, eval and so on; no email or service account leaves the job.
7. **GitHub unauthenticated.** The repo is public; one request an hour fits the 60-an-hour limit
   beside the dispatcher's twelve.
8. **Lookback 8 days.** A decision whose request is older still counts, without a wait time.

## What is left to the owner

- After merge: run the backfill once (`kubectl create job -n postgresql
  --from=cronjob/agent-kpis-backfill agent-kpis-backfill-1`). Until then, KPIs 2, 5 and 6 (and
  the agent half of 3) fill in hour by hour.
- Merging restarts Grafana (one more source in the `dashboards-agents` projected volume): about
  20 s of downtime.
- Golden tasks for k8s-reader and skill-suggester would move tile 1 from 4 to 6 of 8.
