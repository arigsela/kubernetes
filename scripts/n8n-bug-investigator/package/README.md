# Bug investigator for n8n

Two n8n workflows that investigate bug reports with an AI agent. Someone labels a GitHub issue
`bug`; within minutes the issue gets a comment saying what probably went wrong, which change
caused it, and links to the code, pull request and docs that back each claim. Every link is
checked by code before it is posted, so the comment never cites a line, commit or quote that
does not exist.

The agent reads only. It cannot push, merge or edit anything; the workflow itself writes just
three things on the issue: its labels, a 👀 reaction while it works, and one comment.

Out of the box it works with **GitHub issues** and **GitHub code**, and optionally Loki and
Prometheus. For other ticket systems or telemetry (Monday.com, Datadog), see
[ADAPTERS.md](ADAPTERS.md): it shows which functions to change and what they must return.

## How it works

```
Bug investigator: pick  (every few minutes)
  Schedule ─▶ Pick issue ─▶ Investigate issue ──────────────┐  waits for the child
                                                            ▼
Bug investigator: investigate
  Called by the picker ─▶ Gather facts ─▶ Gathered? ─yes─▶ Investigate (AI Agent) ─▶ Verify ─▶ Render and post ─▶ Finish
                                              │               │  model + MCP tools            ▲
                                              no              └─ error ─▶ Agent failed ────────┘
                                              └──────────────────────────────────────────────▶ Finish
```

1. **Pick issue** finds the oldest open issue labelled `bug` that a person with write access (or
   a trusted bot) labelled, opened after `not_before`, and not already done, failed or skipped.
   It claims the issue with a 👀 reaction (only one run can win that) and adds the lock label.
   One investigation at a time; a lock older than 20 minutes is cleared as failed.
2. **Gather facts** collects, by script, what the agent should start from: the issue and its
   comments, pull requests merged in the last 14 days with their reviews, closed past bugs and
   the pull requests that fixed them, the rule IDs in your docs, and (if configured) the deploy
   timeline and telemetry pointers.
3. **Investigate** is n8n's AI Agent with Claude and the MCP tools you configure (the GitHub
   read-only server at least). It follows the method in `prompt.md` and answers with findings
   JSON: verdict, confidence, hypotheses and numbered evidence.
4. **Verify** re-checks every evidence item: the commit exists, the pull request exists, the
   file exists and the quoted line is really there, the doc quote is verbatim, the telemetry
   query really returns what it claims. Failed items are dropped, and the verdict is downgraded
   when what is left does not support it.
5. **Render and post** writes the comment (or edits its earlier one), with the agent's trail of
   tool calls folded underneath.
6. **Finish** sets the final label and removes the lock and the 👀.

### Input and output

| | What | Where |
|---|---|---|
| In | An open issue labelled `bug` by someone who may trigger it | your GitHub repo |
| In | Code, pull requests, docs (and optionally logs and metrics) | read by script and by the agent |
| Out | One comment: verdict, likely cause (change, live since, faulty line), hypotheses, evidence links, next step, the trail | the issue |
| Out | Labels: `ai-investigating` while it runs, then `ai-investigated` or `ai-investigation-failed` | the issue |
| Out | One saved execution per investigation (idle runs are not saved) | n8n's Executions |

A failure before the model was asked (GitHub down, say) only releases the lock, so the next
run retries (at most 3 times). A failure after it was asked is labelled failed and not retried:
remove the label to try again.

## What you need

- n8n **2.45 or newer** (built and tested on 2.45.5; older versions may not know these node versions).
- Python 3.11 or newer to run `build.py` (no packages to install).
- An Anthropic API key.
- Two GitHub fine-grained tokens on your repository:
  - **Workflow token** (the Code nodes): Issues read and write; Contents, Pull requests and
    Metadata read.
  - **Agent token** (the GitHub MCP server): Contents, Pull requests and Metadata read. No write.
- Docker, to run the GitHub MCP server (`docker-compose.yml`).

## Set it up

1. **Settings.** Copy `config.example.toml` to `config.toml` and change the lines marked
   `CHANGE`: the app name, the repository, `not_before` (set it to the time you switch it on,
   so old issues are left alone) and the MCP server's address. Read the rest; every setting has
   a comment. Edit `prompt.md` if you want the agent to know more about your app.

2. **GitHub MCP server.** `docker compose up -d` in this folder. Put the address n8n reaches it
   on in `[[mcp]] url` (the comments in `docker-compose.yml` cover n8n on the same machine, in
   Docker, and n8n Cloud).

3. **Labels.** In your repository create the labels from `[labels]` (Issues → Labels) so you
   choose their colours: `ai-investigating`, `ai-investigated`, `ai-investigation-failed`,
   `no-ai`. `bug` usually exists already.

4. **Secrets in n8n.**
   - The workflow token goes where the Code nodes can read it, under the name in
     `github.token_name`:
     - n8n Cloud, or self-hosted with Variables: an n8n **Variable** of that name.
     - Self-hosted otherwise: an **environment variable** of the n8n process, plus
       `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` so Code nodes may read it.
   - Credentials (Credentials → Add): **Anthropic** with your API key, and **Bearer Auth** with
     the agent token. Put their ids in `config.toml` (`credential_id`, from the credential's
     URL) or leave them blank and choose them after import.

5. **Build.** `python3 build.py config.toml` writes `out/bug-investigator-run.json` (the child)
   and `out/bug-investigator.json` (the parent).

6. **Import, child first.**
   - Self-hosted with the CLI: `n8n import:workflow --input=out/bug-investigator-run.json`, then
     the same for `out/bug-investigator.json`. The ids are kept, so nothing needs re-linking.
   - In the editor (and on n8n Cloud): in a new workflow, ⋯ menu → Import from File, pick the
     child and save it; then import the parent, open **Investigate issue** and choose the child workflow in
     its list (the editor gives imported workflows new ids). Choose the credentials on
     **Anthropic model** and **github** if you left their ids blank.

7. **Switch on.** Publish the child, then the parent. Open a test issue, add `bug`, and wait
   for the next run (every `every_minutes`). The child's Executions show each step.

To switch it off, unpublish the parent. To change a setting, edit `config.toml`, build again
and re-import (the CLI import replaces the workflow with the same id).

## Good to know

- **Cost.** Each investigation is one agent run with up to `max_iterations` tool calls; most
  take a few minutes. Prompt caching (`prompt_caching = "5m"`) cuts the cost of the repeated
  prompt. Check your Anthropic usage after the first few runs.
- **Timeouts.** An investigation may take up to 15 minutes before n8n stops it. n8n Cloud plans
  cap execution time; if yours is lower, investigations will end as failed.
- **Nothing happens?** Idle runs are not saved, by design (one every few minutes would flood
  the history). Check n8n's log for lines starting `bug-investigator:` (a missing or refused
  token says so there), and that the label was added by someone with write access.
- **Untrusted text.** Issue text, code and logs are treated as data, never as instructions; the
  agent has no write tools; and nothing it claims is posted unless the check passes. Tokens
  are scrubbed from errors and comments.

## Files

| File | What it is |
|---|---|
| `config.example.toml` | every setting, with comments |
| `prompt.md` | the agent's instructions; `{{app}}`, `{{repo}}` and the telemetry parts are filled in by `build.py` |
| `build.py` | builds the two workflows from your config |
| `docker-compose.yml` | the read-only GitHub MCP server |
| `ADAPTERS.md` | how to swap GitHub issues for Monday.com, or add Datadog |
| `engine/builder.py` | builds the workflow JSON |
| `engine/nodes/*.js` | the code of each Code node |
| `engine/lib/*.js` | the shared code each Code node bundles (`settings.js` holds the defaults) |
