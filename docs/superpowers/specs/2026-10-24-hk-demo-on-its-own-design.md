# hk demo: "On its own", an alert the platform fixes end to end

**Date:** 2026-10-24. **Status:** design, for the owner's review. **Amends:**
`2026-10-22-hk-demo-design.md` and `2026-10-23-hk-demo-follow-ups-design.md` (both stay true for the
existing "Ask hk" tab). **Builds on:** the dispatcher (`base-apps/dispatcher/`, §T.105, remediate on
signal §T.117–§T.118), the implementer (§T.116) and the model gateway (§T.137).

## Why

On 2026-10-23 at 14:27 UTC the owner watched the platform handle an alert in Slack with nobody at
the keyboard:

1. Grafana fired "Container near memory limit" for `kagent/brave-search-mcp` (mcp-server at 91%).
2. The dispatcher matched its rule `container-near-memory-limit` and opened a session with
   `kagent/implementer` (0 open PRs, budget admitted).
3. The implementer read the pod, its logs and its manifest, diagnosed "limit too low, not a leak"
   and opened PR #952 (1152Mi → 1728Mi).
4. The dispatcher read the verdict `limit-too-low` (so no restart) and posted the finding to
   `#oncall-alerts` through n8n.
5. The alert resolved at 14:32. The owner later closed PR #952 without merging.

That is a second way the agent platform works. Nobody asks a question: an alert starts it, the
platform investigates and proposes a fix on its own, and a human decides. The owner wants to show
it the way the hk demo shows a question: the story in time order, plus a map of how the pieces fit
together.

## What changes for the person at the keyboard

- **Two tabs at the top of the page:** "Ask hk" (today's page, unchanged) and "On its own".
- **"On its own" lists runs:** the dispatcher's implementer investigations from the last 7 days,
  newest first. Each row shows the time, the alert, the target, the verdict and the PR with its
  state. New runs appear by themselves, because the dispatcher logs every investigation.
- **The map has ten boxes in four groups:**

  | Group | Boxes |
  |---|---|
  | Trigger | Grafana alert, Dispatcher |
  | Agent platform | Identity check, kagent (implementer), Sandbox |
  | Outside calls | Model gateway, Tools · GitHub |
  | Outcome | Pull request, Slack, Human decision |

  A box opens the same kind of panel as today: checks, numbers, logs, trace, config.
- **The right column is "The journey"** in place of the chat thread: the run's steps in time order,
  with UTC times. Clicking a step lights up the boxes it touched. The implementer's written
  finding, the PR's title and body, is one of the steps.
- **A Start button** begins a demo run (see "The demo trigger"). While a fill is on, a status line
  shows its progress toward the alert.

Mockup, approved by the owner on 2026-10-24: `hk-demo/.superpowers/brainstorm/*/content/journey-tab.html`
(local to the owner's checkout, not in git).

## Goals

1. Show an alert-driven run end to end, with the same evidence rules as the "Ask hk" tab.
2. Let the owner create a genuine run right before a meeting, in about 18 minutes, without touching
   the cluster by hand.
3. End the story at the human decision: the PR's state as it is now.

## Non-goals

- Live progress while the agent runs. The owner shows a run once the dispatcher has logged it;
  boxes fill as the trace and logs arrive, as they do today.
- Boxes for the restarter's session. When a verdict sends a run to the restarter (`leak`,
  `rollout-pending`), the journey shows the dispatcher's remediate line and the pause awaiting
  approval, and nothing more.
- The dispatcher's `none` routes, and investigations by agents other than the implementer.
- Keeping runs past Tempo's 7 days. Persistence is a separate, still-open topic.
- Any change to kagent, the agents' prompts or AgentTemplates, the mesh, Grafana's alert rules or
  the dispatch table. The demo workload's alert uses the existing rule `container-near-memory-limit`,
  which matches on the alert name only.
- A made-up alert posted to the dispatcher. The dispatcher's network policy admits only Grafana,
  and the Grafana step would be invented.

## Anti-goals (a failure even if everything works)

- **Overclaiming.** A ✓ that this run's own lines, spans or metrics do not back. A step with no
  evidence says so in words; one example is Slack's own delivery, which the cluster cannot see.
- **Leaking on the screen share.** The PR body and every line pass the app's existing masking.
- **The demo workload hurting real alerting.**
  - It never alerts unless started, and it stops by itself.
  - A demo PR left open counts toward the dispatcher's `open_prs_max` (3), which makes it skip real
    alert fixes. The page says so while the PR is open.
- **Steering the agent.** No prompt changes and no hidden instructions. The workload's docs say
  plainly what it is, and the verdict is the implementer's own.
- **Hurting Loki or Tempo.** The existing load limits apply: no day-wide scans and no polling loops.

## Decisions taken with the owner (2026-10-24)

- The tab, map, panel and journey column as in the mockup ("this is all correct").
- Runs are examples replayed from the logs. A new run shows up because the dispatcher logged it.
- The journey ends at a human decision.
- **The trigger is a real condition, never a made-up alert.**
  - A demo workload in git holds its memory above 90% of its limit until Grafana fires.
  - The owner starts it right before the meeting and closes the PR afterwards.
- **The workload's shape:**
  - It jumps to about 92% of its limit and stays flat. Steady use near the limit, with no OOMKilled
    and no restart growth, is what the implementer's instructions call "limit too low". Memory
    that creeps up would read as a leak.
  - It releases the memory after about 30 minutes, so the alert resolves on its own.
- **The Start button lives on the tab.** The app only sends the workload an HTTP request. It never
  writes to the cluster.
- The workload's manifest and docs say plainly that it is a demo workload.

## Evidence per box

| Box | Observed (✓) | Configured (⚙) / not visible |
|---|---|---|
| Grafana alert | The dispatcher's `dispatch` line with `source: grafana, status: firing` (Grafana delivered it, and when). The container's working set / limit over the 20 minutes before firing (one Prometheus range query). The `resolved` line. | The rule (`container-near-memory-limit`: over 90% for 15m, evaluated every 1m), excerpted from `base-apps/logging/grafana-alerting.yaml`. Grafana keeps no alert state history here, so the evaluation itself is not shown; the box says so. |
| Dispatcher | The `dispatch` line (rule, route, decision, open PRs). The `finding` line (session, state). The `remediate` line (verdict, and whether a restart followed). | Its limits (4 investigations an hour, the same alert once per 6 h, 3 open PRs) and the rule's row from `dispatch-table.yaml`. |
| Identity check | The waypoint's line for the dispatcher's call, with principal `dispatcher/dispatcher` (a workload, not a person). | The waypoint policy, as today. |
| kagent, Sandbox, Model gateway, Tools | Today's builders, on the implementer's trace. The trace is found by `gen_ai.conversation.id` = the finding's session. A dispatcher session has exactly one turn (§V.88), so that lookup is unambiguous. | As today. |
| Pull request | The `create_pull_request` span (it carries no URL). The PR itself, from GitHub: the implementer App's PR created between the dispatch and the finding, with its number, title, body and files changed. | The implementer's class `propose`: branches and PRs only, never a merge. |
| Slack | The `finding` line with `error: null`: n8n accepted the finding for `#oncall-alerts`. | Workflow `agent-findings`. Slack's own delivery is not visible from the cluster; the box says so. |
| Human decision | The PR's state now: open, merged (by whom, when) or closed unmerged. Read from GitHub's REST API, unauthenticated, as the dispatcher's open-PR count already does. Cached, and fetched only when a run is opened. | Amber while the PR is open ("waiting for a human"). It is a person's decision, so it never shows as a platform ✓. |

**Journey steps.** Each step comes from the dispatcher's lines, the trace or GitHub:
1. Alert firing.
2. The dispatcher's decision.
3. The implementer's session start.
4. Its tool calls, condensed: read the pod, its logs and the manifest; create a branch; write the
   file; open the PR.
5. The answer: the verdict, plus the PR's title and body when there is a PR.
6. The remediate decision.
7. The finding to Slack.
8. Alert resolved.
9. The human decision.

## The dispatcher change (kubernetes repo)

hk-demo cannot read the implementer's answer back from kagent (`GetTask`): the session belongs to
the dispatcher's identity, and giving hk-demo another identity's sessions is out of scope.

The answer also stays out of the dispatcher's log. That was a decision of the dispatcher's own
design (`2026-10-12-signal-dispatch-and-promotion-design.md`, "Observability": "never the answer
text"), because an answer can quote pod logs or a resource's YAML. A test pins it. This design
keeps that rule. *Amended while planning, 2026-10-24; the first draft logged the text.*

Instead, **the journey shows the pull request's title and body as the implementer's written
finding.** The implementer's prompt puts the detail there ("the pull request body carries the
detail"), and GitHub serves it, already public, to the same unauthenticated call that reads the PR's
state. For a run with no PR (`leak`, `contention`, `inconclusive`), the journey says that the answer
went to Slack and is not stored anywhere else, and shows the verdict.

The dispatcher's `finding` log line gains four fields, none of them answer text:
- `fingerprint`, which joins a `finding` line to its `dispatch` line;
- `task_id`;
- `verdict`;
- `target`: the alert's `namespace`, `pod` and `container` labels, needed for the memory series.

Its `remediate` line gains `fingerprint` and `session`.

**Runs logged before this change are not listed,** since without a fingerprint there is no reliable
join. The two such runs still inside Tempo's 7 days expire by 2026-10-30, and the Start button makes
fresh ones.

The change goes in `scripts/dispatch.py`, with the code ConfigMap regenerated
(`gen-dispatcher-configmap.py`) and its tests. The n8n payload is unchanged.

## The demo trigger

**Workload (kubernetes repo).** A new app, provisionally `alert-demo`, in its own namespace:
- **App setup:**
  - Onboarded to the agent-docs contract (`docs.md`, `runbook.md`, `catalog-info.yaml`), since the
    implementer reads an app's docs when it investigates.
  - One Deployment with one replica, running a small Python service.
- **API:**
  - `POST /fill`: allocate and touch memory until the container holds a fixed `FILL_MIB` (236).
    The manifest sets it next to the 256Mi limit, of which it is 92%. Hold it flat, then release
    after about 30 minutes.
  - **Why a fixed size** (planning, 2026-10-24, after the builder check): a fill that tracked the
    limit would make raising the limit useless, so an honest implementer could fairly answer "by
    design, no PR". With a fixed size, raising the limit is the real fix, and "limit too low" is
    the correct diagnosis. The cost: a merged demo PR keeps the next run under 90%, until a PR puts
    the limit back.
  - `POST /stop`: release early.
  - `GET /status`: the state, the start time and the target.
- **It must never restart during a fill,** because restart growth reads as a leak. The fill stays
  below the limit, and no liveness probe can fail under memory.
- **Only hk-demo may call it,** by NetworkPolicy plus the mesh's principal, following the repo's
  pattern.
- **Its docs say what it is:** the demo workload for the hk demo page, which holds memory on
  purpose while a demo run is on, so that "Container near memory limit" fires.

**Start (hk-demo).** `POST /api/journeys/start`, behind the same Dex login as `/api/ask`. Its
log line records who started it, as `hk-demo-run` lines do.

It refuses with the reason in words when:
- a fill is already running;
- the implementer has 3 or more open PRs (the dispatcher would skip the alert);
- the current pod was investigated in the last 6 hours (the dispatcher would skip the same
  fingerprint).

While a fill is on, the status line reads the workload's `/status` at most every 15 seconds, and
only while the tab is open. It shows "about N minutes until the alert".

The app gets no new RBAC and makes no cluster API writes. `openapi.yaml` and the Backstage copy are
updated together.

## Side effects the owner accepts

- **Each Start produces real artifacts:** a real Grafana alert and a real finding in
  `#oncall-alerts`, a real implementer session, and a real PR on `arigsela/kubernetes`.
- **Cost:** the implementer session costs a few cents to a few tens of cents, through the gateway,
  from the implementer group's $4/day.
- **Close the demo PR afterwards** (runbook). If one is merged, the limit grows by half, and the
  next demo stays under 90% and fires nothing, until a PR puts the limit back.
- **The implementer's verdict is its own.** If a run ends without a PR, the page shows that ending
  as it happened. The builder does not reword the docs to get a PR; that goes back to the owner.
- **Rehearse at least 6 hours before a meeting,** or roll the pod in between. The dispatcher
  investigates the same pod once per 6 hours.

## Left to the builder

- Names: the app, the tab label, box and step wording.
- The workload's limit (128Mi suggested), the hold time, and whether it runs from a ConfigMap
  script (the dispatcher's pattern) or its own image.
- How a step lights up boxes, cache times, the GitHub caching, and the join's edge cases.
- The order of the PRs. A sensible order:
  1. The dispatcher's `finding` line.
  2. The workload.
  3. hk-demo v0.4.0.
  4. The kubernetes PR that pins it and lets hk-demo reach the workload.
  5. One rehearsal run, end to end.
- A SPEC.md task for the work.
