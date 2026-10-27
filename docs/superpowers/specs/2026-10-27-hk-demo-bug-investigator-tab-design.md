# hk demo: "Bug investigator", a bug report to verified findings and a fix PR

**Date:** 2026-10-27. **Status:** design; the trigger half (the log contract) ships with it, the
tab is built in arigsela/hk-demo. **Amends:** `2026-10-24-hk-demo-on-its-own-design.md` (its rules
and builders are reused; nothing in the existing tabs changes). **Builds on:** the kagent bug
investigator (§T.146: agent `bug-investigator`, tool server `taskboard-telemetry`, CronJob
`bug-investigator-trigger`, the implementer's fix PRs on Taskboard).

## Why

On 2026-10-27 the platform took a Taskboard bug from signal to fix with nobody at the keyboard:

1. 18:05:02 the trigger's alert check saw 5xx answers on `/api/tasks` and filed taskboard#30.
2. 18:06:05 the next run picked it up, gathered facts (20 PRs, 6 deploys) and asked
   `kagent/bug-investigator` over A2A.
3. 18:08:24 the agent answered after 11 tool calls in 96 s: likely cause PR #21 at `tasks.py:58`.
4. 18:08:32 the trigger verified every evidence item (7 of 7) and posted the comment.
5. 18:09:38 `kagent/implementer` opened fix PR #31 with a regression test; Taskboard CI went green.

The owner shows this to a CTO who runs the same flow as a ticket-to-LLM workflow. The existing
artifact tells the story for the GitHub Action version; the platform version needs the same story
**with the platform's own evidence for each step**: who called the agent (identity), where it ran
(sandbox), what it cost (model gateway), what it was allowed to touch (tools, and the queries the
telemetry server refused), what a script checked (verify), and what a person still decides.

## What changes for the person at the keyboard

- **A new tab, "Bug investigator"**, beside "Ask hk", "On its own" and "The platform".
- **It lists runs:** the trigger's investigations of the last 7 days, newest first, each with the
  issue title, how it started (a person's `bug` label, or the alert check), the verdict and
  "verified n of m".
- **Picking a run** shows the ticket and the verdict on top (summary, confidence, likely cause,
  documented rule), the boxes below, the agent's trail beside them, and the evidence list with what
  verify kept and what it dropped and why.

## Goals

1. Show one bug report end to end, report → verified findings → fix PR → a person's decision, with
   the same evidence rules as the other tabs.
2. Show what only the platform gives: the caller's identity, a sandbox per session, the model
   gateway's budget group, the tool allow-lists and the telemetry scope refusals, the audit trail.
3. Read nothing from `arigsela/taskboard`. It is private and hk-demo reads GitHub unauthenticated.

## Non-goals

- Starting a run from the page. Runs start from a `bug` label or the alert check, as they do
  without the page.
- Live progress while a run is in flight; boxes fill as lines and spans arrive, as on "On its own".
- Showing the comment's markdown as GitHub renders it, or the fix PR's diff (both are in the
  private repo). The page shows the same findings from the `posted` line, and the PR's number and
  branch from the `fix_handoff` line.
- The GitHub Action version (it has its own artifact) and its comparison mode.
- Keeping runs past Loki's and Tempo's retention, beyond what saved runs already keep.

## Anti-goals

- **Overclaiming.** A ✓ the run's own lines, spans or metric samples do not back. A step with no
  evidence says so in words (for example the fix PR's merge, which the page cannot see).
- **Leaking.** No email (masked `a•••@domain` by the trigger and again by `hk_demo.redact`), no
  token, no tool response, no file contents or log lines from Taskboard. The trail carries tool
  names and targets, never results.
- **Reading the private repo** by any route, including a token added "just for the demo".
- **Hurting Loki or Tempo.** One 7-day line query per list refresh, cached; per-run queries bounded
  to the run's window; `|=` substring filters before any regex.

## Data sources

| Source | What it gives | How |
|---|---|---|
| The trigger's lines (Loki) | Every step of a run, per the log contract below | `{namespace="kagent", pod=~"bug-investigator-trigger-.*"} \|= "bug-investigator-trigger"`, 7 days for the list, the run's window for one run; joined by `run` (the Job name) and `issue` |
| Session evidence (Tempo, Loki) | Identity, kagent, sandbox, model gateway, tools for the agent's session and for the implementer's | hk-demo's existing `evidence.py` builders, the trace found by `gen_ai.conversation.id` = `answered.session` (and `fix_handoff.session`). Each is a new single-turn session, so the lookup is unambiguous. New `AgentProfile`s for `bug-investigator` and (already there) `implementer` |
| The telemetry server's adapter lines (Loki) | Calls to `taskboard-telemetry`, and refusals (`isError` from the scope check, `Unknown tool` from the allow-list) | `{namespace="kagent", pod=~"taskboard-telemetry-mcp-.*"} \|= "mcp.method.name"`, bounded to the session's window |
| The deploy pin commits (GitHub, public) | When a merged fix went live | `arigsela/kubernetes` commits on `base-apps/taskboard/code-configmap.yaml` titled `taskboard: deploy <sha> (<title> (taskboard#N))`; N = the fix PR's number. Unauthenticated, cached, as the PR reads on "On its own" |

## Boxes

`Report | Alert → Trigger → Gather → Agent → Verify → Comment → Fix PR → People / Deploy`

| Box | Observed (✓) | Configured (⚙) / not visible |
|---|---|---|
| Report or Alert | `investigating` (`alert`, `filed_by`, `opened_at`, `title`); for an alert, `alert_issue_filed` (`route`, `count`, `first_seen`) | The Grafana rule "Taskboard server errors" (same expression as the trigger's check), excerpted. Who labelled is a login, never an email |
| Trigger | The run's lines exist (`run` = Job name). Identity: the waypoint's line for the A2A call with principal `kagent/bug-investigator-trigger` (a workload, not a person) | CronJob every 2 min, Forbid, 20 min deadline; the GitHub token is Taskboard-only and never reaches the agent |
| Gather | `gathered` (`prs`, `deploys`, `problems`, `seconds`) | What gather reads (ticket, merged PRs, deploy timeline, past bugs, docs index) |
| Agent | `answered` (`session`, `verdict`, `tool_calls`, `seconds`, `trail`); kagent, sandbox, model gateway and tools builders on its trace; telemetry refusals in the window | Class `read`, `kagent-stateless`, budget group `bug-investigator`, the 11 bound tools |
| Verify | `posted.verified` of `posted.total`; each evidence item kept or dropped with its reason; `dropped_other` (docs rule, likely-cause refs, verdict, past bugs) | What verify checks per kind (commit, PR, file line and quote, doc quote, query re-run) |
| Comment | `posted` (`done`, `comment_url`) | The link opens only for people with access to the private repo; the page shows the findings from the line |
| Fix PR | `fix_handoff` (`outcome`, `pr`, `session`, `seconds`); the implementer's session evidence by `fix_handoff.session` | The implementer is class `propose`: a branch and a PR, never a merge. `existing_pr` = it pointed at someone else's PR and opened none |
| People / Deploy | A deploy pin commit naming `taskboard#<pr.number>`: deployed, and when | Amber until then: review and merge are a person's, and the page cannot see the PR's state |

**Journey steps** (time order): alert or label → picked → facts gathered → the agent's trail (each
tool call: tool, target, ok) → answer → verified n of m → comment posted → fix requested → PR → deployed.

## Log contract

Every line is one JSON object from `scripts/bug_investigator_trigger.py` with `time`,
`component: "bug-investigator-trigger"`, `event` and `run` (the Job name, `RUN_ID`; null outside a
Job). A line stays under 16 KB: lists are shortened from the end, largest list first, and a
shortened list sets `<list>_truncated: true`. Model and ticket text is token-scrubbed and
email-masked before it is logged; every string is masked again when the line is written.

| Event | Fields |
|---|---|
| `alert_issue_filed` | `issue`, `route`, `count`, `title`, `first_seen` (UTC ISO) |
| `investigating` | `issue`, `title`, `alert` (bool: the issue has `from-alert`), `filed_by` (login of who added `bug`), `opened_at` (issue created_at) |
| `gathered` | `issue`, `prs`, `deploys`, `problems`, `seconds` |
| `answered` | `issue`, `session`, `verdict`, `tool_calls`, `seconds`, `trail`: up to 40 `{"tool", "target" (≤120 chars: path, `#N method`, query, ref; `repo:` prefix when the call named a repo), "ok"}` in call order. `ok` is true/false when the call's result part was in the A2A task, null when it was not |
| `posted` | `issue`, `verdict`, `verified`, `total`, `session`, `done`, `title`, `ticket` (body, ≤600), `confidence`, `summary` (≤600), `likely_cause` (`{change_ref, live_since, code_ref}` or null), `docs_rule` (`{path, rule_id, quote ≤200}` or null), `hypotheses` (`[{id, statement ≤200, status, evidence: [ids]}]`), `evidence` (`[{id, kind, ref ≤160, verified, reason}]`, kept and dropped, in id order; a file ref is `path:line`), `dropped_other` (`[{id, ref, reason}]`), `comment_url`, `next_step` (≤300) |
| `fix_handoff` | `issue`, `outcome` (the PR URL, `existing pull request`, `no pull request`, `failed`), `session` (the implementer's), `pr` (`{url, number, head}` or null), `existing_pr` (`{url, number, head}` or null), `seconds` |
| `investigation_failed` | `issue`, `spent`, `stage` (`prepare`, `ask`, `answer`, `post`), `error` |
| others | `idle`, `skipped_untrusted`, `alert_step_failed`, `core_unavailable`, `label_failed`, `fix_handoff_failed`, `crashed`: `run` plus their existing fields |

Lines before 2026-10-27 (taskboard #30, #32, #33) lack the new fields; the tab lists them with
what they have and says so.

## Rules (from the hk demo's own, unchanged)

1. Every ✓ comes from this run's own lines, spans or metric samples; anything else is ⚙.
2. Never render an email, a token, a cookie or a tool response; `hk_demo.redact` runs on every
   field even though the trigger masks first.
3. Text goes in with `textContent` only; no inline script or style.
4. Loki: `|=` before any regex, windows bounded to the run.
5. `arigsela/taskboard` is never read.

## Saved runs

As on the other tabs: a settled run (its `posted` line, plus the `fix_handoff` line when a fix was
requested, and 5 minutes for spans to land) is saved to Valkey under `hk-demo:ev1:bug:<run>`,
zlib JSON, TTL 8 days; picking a saved run is instant. A run whose evidence read failed is not
saved and is retried after an hour.

## Left to the builder

- The list and run endpoints (and `openapi.yaml`), the module beside `journeys.py`, the boxes and
  the JavaScript, tests with recorded lines; a minor version bump (every hk-demo PR bumps).
- Whether the trail column groups calls by tool server (GitHub reads, telemetry).
