# hk demo: follow-up turns and the implementer handoff

Amends `2026-10-22-hk-demo-design.md`, which listed "Follow-up turns" and "Agents other than hk" as
non-goals. Agreed with the owner on 2026-10-23; built under the owner's standing grant for this
demo app (§T.138).

## Why

In a rehearsal the owner asked hk to check `base-apps/*/catalog-info.yaml` for drift. hk found some
and offered a pull request, but the page could not take the "yes": every question was a fresh
session. hk is read-only (capability class `read`) and cannot open a pull request anyway. The
owner wants the audience to see a real pull request opened, and hk to stay read-only.

The story the page tells: the agent that reads cannot write, the agent that writes can only
propose, and a human merges.

## What changes for the person at the keyboard

- **A conversation has turns.** The first question starts a fresh hk session, as before. Anything
  typed after an answer continues that hk session (A2A `contextId`), so "check the other apps too"
  works.
- **`implementer: <request>`** (case-insensitive, at the start) sends the request to
  `kagent/implementer` instead. The app opens an implementer session the first time and continues
  it after that. Plain text goes back to hk. No other agent name is recognised.
- **Each agent hears what the other said since its own last turn.** The app adds the other agent's
  latest question and answer to the message, labelled as evidence from another agent, not
  instructions (the same idea as the Slack bridge's "Context from Slack"). So the implementer gets
  hk's drift finding with the "yes", and a later hk turn can see the implementer's PR link.
- **New conversation** starts over.
- **The answer column is the conversation thread**: one entry per turn, labelled `hk` or
  `implementer`, with the question and the answer. A pull-request URL in an answer is a link.
  Clicking a turn shows that turn's map, panel and progress. A new turn is selected when it starts.
- **Recent runs lists conversations.** Picking one replays every turn in order. A replayed
  conversation can be continued, because the app rebuilds its sessions from its own log lines.

## What the boxes show for an implementer turn

- **kagent:** span `invoke_agent implementer`; configured: AgentTemplate `implementer`, capability
  class `propose` (branches and pull requests on arigsela/kubernetes; it never merges).
- **Tools:** its GitHub tools run on `implementer-mcp`, which is adapter-fronted like `agent-docs`,
  so each call's allow-list line is observed evidence. The proposal tools it binds (`create_branch`,
  `create_or_update_file`, `push_files`, `create_pull_request`; `update_pull_request` is in the
  taxonomy but not bound) carry a configured note: class `propose`, no approval gate, the merge is the human gate (§V.94). A
  successful `create_pull_request` span is an observed check.
- **This app:** which turn this is, whether it continued a session or started one, and how much of
  the other agent's answer it carried.
- **Config tab** excerpts for the kagent and Tools boxes come from the implementer's AgentTemplate
  and `implementer-mcp.yaml`.
- A follow-up hk turn shows the sandbox *resuming* the session's snapshot
  (`ate.router.resume`) instead of creating an actor. The existing check already says so.

## Rules that hold (unchanged from the first design)

- ✓ only from the turn's own spans, log lines or metrics (§V.107). Masking, the CSP, Loki/Tempo
  load limits and the app's own identity are unchanged.
- One agent call in flight at a time. The next question is accepted once the current turn has its
  answer (or failed). The previous turn's trace and log lookups may still be running, and they keep
  filling that turn's boxes. (Before: the next question waited for every box.)

## Per-turn evidence in a shared session (my calls)

A continued session has one `contextId` across turns, so two lookups could mix turns:

- The Prometheus fallback for the trace (`agent_run_start_seconds{gen_ai_conversation_id}`) is
  skipped for a continued session. The Tempo search by task id is per turn.
- The Prometheus fallback is also skipped for a session's first turn once another turn shares its
  `contextId` (one series per turn; which one comes first is arbitrary).
- The run-logs query matches the trace id **or** `session-<contextId>`. A line that lacks the trace
  id counts for a turn only from that turn's own start (for a continued session) up to the next
  same-session turn's start. A session line written after the answer but after the next turn has
  started (a late checkpoint) is shown with the next turn: the lines carry no turn.

## Review decisions (2026-10-23, before release)

- Only an answered turn is carried: a turn whose task ended failed, canceled, rejected or paused
  carries nothing, so the implementer gets the last real finding.
- Lookups before a turn starts are capped at 10 s each (rebuilding a conversation from Loki; the
  carried answer from GetTask): oauth2-proxy gives a request 30 s, and a run that starts after the
  browser was told it failed would still open a pull request. A slow carry is skipped; a slow or
  failed Loki read is a 503, not "no such conversation".
- The app's 7-day run lines are read at most every 10 s.
- The page blocks Send, Enter, New conversation and Recent runs while a question is being sent,
  shows the question as the server recorded it (masked), and replays a conversation one turn at a
  time: the next turn starts when the previous one's boxes are complete, or 20 s after its answer.

## Data

- Each `hk-demo-run` log line gains `agent`, `conversation_id`, `turn`, `continued` and `carried`
  (characters of the other agent's answer sent). Older lines without them read as a one-turn hk
  conversation whose id is the run id.
- `POST /api/ask` takes an optional `conversation_id` and returns `run_id`, `conversation_id`,
  `agent` and `turn`. An unknown conversation is 404. `GET /api/conversations` lists conversations
  with their turns. `openapi.yaml` and the Backstage copy are updated together.

## Side effects the owner accepted

- Every implementer turn opens a real pull request, or updates its open one for the same file.
- The dispatcher skips alert-driven fixes while the implementer has `open_prs_max` (3) pull
  requests open. Close demo pull requests after a rehearsal (runbook).
- The implementer's contract allows at most three files per pull request. A drift fix across many
  apps should be scoped in the request.

## Non-goals

- Other agents, approval pauses, and any change to kagent, the agents or the mesh. The hk-demo
  service account already reaches every agent path (waypoint rule 2).
