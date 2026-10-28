# Adapting it: Monday.com tickets, Datadog telemetry

Out of the box the investigator reads tickets from GitHub issues and code from GitHub. This
guide shows where to change that. The code samples are **untested starting points**: they show
the shape and the calls, and you will need to check them against Monday's and Datadog's current
API docs and try them on a test board.

What does not change: the agent loop, the read-only GitHub MCP server, the checks on code
evidence (commits, pull requests, files, doc quotes), and the comment layout. The code stays on
GitHub; only where tickets come from and go to, and where telemetry comes from, move.

## The seams

| To change | File | Function | What the rest of the workflow expects |
|---|---|---|---|
| Which ticket to work on | `engine/nodes/pick.js` (calls `engine/lib/pick.js`) | `pickIssue` | `{ issue: { number, title, body, created_at, html_url, labels }, filedBy }`, or no item when there is nothing to do |
| The ticket text | `engine/lib/gather.js` | `ticketFacts` | `{ number, title, body, author, created_at, state, url, labels: [], comments: [{ author, created_at, body }] }` |
| Past bugs | `engine/lib/gather.js` | `pastBugs` | `[{ number, title, closed_at, url, body, fixed_by: [{ number, title, url }] }]` |
| The request to the agent | `engine/lib/gather.js` | `buildMessage` | a string: the request line, then the facts markdown |
| Posting the result | `engine/nodes/render-post.js` (calls `engine/lib/render.js`) | `postComment` | `'created'` or `'edited'` |
| Final state | `engine/nodes/finish.js` | (inline) | sets done / failed / released from `outcome(item)` |
| Ticket evidence | `engine/lib/verify.js` | `makeVerifyCtx` → `issueState` | `{ state, title, url, labels }`, or `null` when it does not exist |
| Telemetry queries | `engine/lib/verify.js` | `makeVerifyCtx` → `query`, `checkItem`, `exploreLink` | `{ count }`, `{ refused }` or `{ error }` |
| How evidence is shown | `engine/lib/render.js` | `KIND_LABEL`, `evidenceLink`, `renderCore` | — |

After any change: `python3 build.py config.toml`, then import again (the CLI import replaces
the workflows; in the editor, re-import and re-link as in README step 6).

### Things every adapter can use

- **Secrets.** `__secret('NAME')` returns the n8n Variable `NAME`, else the environment
  variable `NAME`, else `''`. Every Code node has it. Never put a key in `config.toml`: it ends
  up in the workflow JSON.
- **HTTP.** In a node, `const http = httpFromHelpers(this.helpers)` (from `./n8n`) gives
  `http({ method, url, headers, body })` → `{ status, headers, body }` (JSON parsed, no throw on
  HTTP errors). Pass `http` into your library functions, as `makeGitHub(http, token)` does.
- **Settings.** `build.py` copies the settings it knows into each Code node's `CONFIG`. For a
  new table in `config.toml` (say `[monday]`), add one line to the `config = {...}` dictionary
  in `build.py`: `"monday": cfg.get("monday") or {},`. Then read `CONFIG.monday` in your code.
- **Trying a change.** In n8n, run the child once on a test ticket, then pin the output of
  **Gather facts** and **Investigate** (pin data) and re-run single nodes with "Execute step"
  while you work, so you do not pay for the agent each time.

## Monday.com instead of GitHub issues

### How the concepts map

| GitHub issue | Monday.com |
|---|---|
| repository | a board (`board_id`) |
| issue, its number | an item, its id (a large number: keep it as a string) |
| `bug` label added by a writer | a status column set to, say, `Investigate` (anyone who can edit the board) |
| lock / done / failed / skip labels | the same status column: `AI investigating`, `AI investigated`, `AI failed`, `No AI` |
| comments | updates (their `body` is HTML) |
| the 👀 claim | nothing equivalent; see "One at a time" below |

Settings to add to `config.toml` (and the `build.py` line above):

```toml
[monday]
board_id = "1234567890"
status_column = "status"         # the column id, not its title (board → column menu → copy id)
token_name = "MONDAY_TOKEN"      # n8n Variable / environment variable with a Monday API token
api_version = ""                 # pin the API-Version header to a current stable version
statuses = { bug = "Investigate", lock = "AI investigating", done = "AI investigated", failed = "AI failed", skip = "No AI" }
```

### 1. A small Monday client: `engine/lib/monday.js`

```js
'use strict';
const API = 'https://api.monday.com/v2';

function makeMonday(http, token, apiVersion) {
  async function gql(query, variables = {}) {
    const headers = { Authorization: token, 'Content-Type': 'application/json' };
    if (apiVersion) headers['API-Version'] = apiVersion;
    const r = await http({ method: 'POST', url: API, headers, body: { query, variables } });
    if (r.status !== 200 || (r.body && r.body.errors)) {
      throw new Error(`Monday API: HTTP ${r.status} ${JSON.stringify((r.body && r.body.errors) || r.body).slice(0, 300)}`);
    }
    return r.body.data;
  }
  return { gql };
}

module.exports = { makeMonday };
```

Keep `module.exports` as above. `build.py` bundles every module a node asks for: in a node
write `__require('./monday')`, in another lib file `require('./monday')` (single quotes, a
lower-case name).

### 2. Picking: replace `pickIssue` in `engine/nodes/pick.js`

The rule stays: the oldest item in `Investigate`, created after `not_before`, and nothing in
`AI investigating` (one at a time). An item stuck in `AI investigating` for more than 20 minutes
was left by a run that died: set it to `AI failed`.

```graphql
query ($board: ID!, $col: String!, $value: String!) {
  items_page_by_column_values(board_id: $board, limit: 50,
      columns: [{ column_id: $col, column_values: [$value] }]) {
    items { id name created_at url updated_at creator { name } }
  }
}
```

Run it once with `$value` = the lock status (if any item is there and fresh, return no item:
busy) and once with the bug status. Then claim the oldest:

```graphql
mutation ($board: ID!, $item: ID!, $col: String!, $value: String!) {
  change_simple_column_value(board_id: $board, item_id: $item, column_id: $col, value: $value) { id }
}
```

and return `[{ json: { issue: { number: item.id, title: item.name, body: '', created_at:
item.created_at, html_url: item.url, labels: [] }, filedBy: item.creator && item.creator.name } }]`.

**One at a time.** GitHub's 👀 reaction lets exactly one run win a claim. Monday has no such
operation, so rely on one n8n instance running the picker on a schedule (the default), and
re-read the item after setting the status if you ever run two.

**The item's text.** A Monday item often has no description; the report lives in its first
update. Use the first update's `text_body` as `body` (step 3 does this for the facts).

### 3. Ticket facts and past bugs: `engine/lib/gather.js`

Replace the body of `ticketFacts(gh, repo, n, own)` with a Monday read (pass the Monday client
in through `cfg` or a new argument) that returns the same shape:

```graphql
query ($ids: [ID!]) {
  items(ids: $ids) {
    id name created_at url state creator { name }
    column_values { id text }
    updates(limit: 20) { text_body created_at creator { name } }
  }
}
```

- `body`: the oldest update's `text_body`; `comments`: the other updates, oldest first. Leave
  out your own investigation updates (they start with the header from `[comment]`).
- `labels`: `[]`, or the texts of columns you want the agent to see (priority, component).
- `state`: `'OPEN'`.

`pastBugs`: items in `AI investigated` (or a "Done" group) with their first update as `body`;
`fixed_by` can be `[]`, or the pull requests a "Fix PR" link column points at.

`buildMessage(issue, repo, md)` starts the agent's request with "Investigate bug report #N in
owner/repo". Change it to name the Monday item, and in `prompt.md` replace "bug report" with
"Monday item" where it helps. Keep `repo` meaning the GitHub repository: the code checks need it.

### 4. Posting: `engine/nodes/render-post.js`

`renderComment` returns GitHub markdown. A Monday update takes HTML, so convert it first. Two
ways:

- Add n8n's **Markdown** node (Markdown → HTML) after **Render and post**, and do the post in a
  small Code node after it. Simplest.
- Or convert in code with a few regular expressions for headings, bold, links and lists.
  `<details>` blocks may be dropped by Monday; that only hides the trail.

```graphql
mutation ($item: ID!, $body: String!) { create_update(item_id: $item, body: $body) { id } }
```

Posting a new update each run is fine. To edit the previous one instead, keep its id in a text
column and use the edit mutation if your API version has one.

### 5. Finishing: `engine/nodes/finish.js`

Keep `outcome(item, CONFIG.labels)` and map its result to a status:

```js
const o = outcome(item, CONFIG.labels);
const status = o.outcome === 'investigated' ? CONFIG.monday.statuses.done
  : o.outcome === 'failed' ? CONFIG.monday.statuses.failed
  : CONFIG.monday.statuses.bug;   // released: back in the queue for the next run
```

then `change_simple_column_value` as in step 2. Remove the `releaseClaim` call (no reaction).

### 6. Ticket evidence: `engine/lib/verify.js`

The agent may cite a ticket (`"kind": "issue"`) or a similar past bug. `issueState(n)` checks it
on GitHub; point it at Monday (`items(ids: [n]) { name url state }` → `{ state: 'OPEN', title,
url, labels: [] }`, `null` if not found). If you would rather the agent did not cite tickets,
say so in `prompt.md` and remove `issue` from the evidence kinds there.

## Datadog for telemetry

Two separate parts: what the agent can look at, and what the checker can re-check. You can do
the first without the second; the agent then reports telemetry as unchecked (README, `notes`).

### What the agent sees

1. **Tools.** Give the agent a Datadog MCP server as another `[[mcp]]` block. Datadog offers
   one (check Datadog's docs for its address and whether your plan includes it); otherwise run
   your own small MCP server with a log search and a metrics query tool.
   ```toml
   [[mcp]]
   node = "datadog"
   url = "https://…"                    # the MCP server's address
   tools = []                           # empty: all of its tools; or list the read-only ones
   auth = "multipleHeadersAuth"         # an n8n credential with DD-API-KEY and DD-APPLICATION-KEY
   credential_id = ""
   credential_name = "Datadog read-only"
   ```
   Use an application key scoped to reading logs and metrics.
2. **Pointers.** Tell the agent what to look for, in `[telemetry] notes`, for example
   `notes = ['Service "checkout", env "prod"; errors are status:error.']` (single quotes in
   TOML keep the text exactly as written).
   With `notes` and no `url`, `prompt.md` tells the agent telemetry is not re-checked.

### What the checker re-checks

The checker re-runs every telemetry query the agent cites and drops the ones that return
nothing (or something when the agent said "none"). Built in are Loki (`logql`) and Prometheus
(`promql`). For Datadog add two evidence kinds, say `dd_logs` and `dd_metrics`:

1. **`engine/lib/verify.js`, `checkItem`:** route them to the same check:
   ```js
   if (['logql', 'promql', 'dd_logs', 'dd_metrics'].includes(kind)) return checkQuery(ctx, item, findings);
   ```
2. **`makeVerifyCtx` → `query(kind, expr, startIso, endIso)`:** call Datadog for the new kinds
   and return `{ count }` (or `{ error }`). The keys come from `__secret` in the Verify node;
   pass them in through the options.
   ```js
   if (kind === 'dd_logs') {
     const r = await http({ method: 'POST', url: `https://api.${site}/api/v2/logs/events/search`,
       headers: { 'DD-API-KEY': apiKey, 'DD-APPLICATION-KEY': appKey, 'Content-Type': 'application/json' },
       body: { filter: { query: expr, from: startIso, to: endIso }, page: { limit: 100 } } });
     if (r.status !== 200) return { error: `Datadog logs: HTTP ${r.status}` };
     return { count: ((r.body && r.body.data) || []).length };
   }
   if (kind === 'dd_metrics') {
     const q = new URLSearchParams({ from: String(Date.parse(startIso) / 1000 | 0),
       to: String(Date.parse(endIso) / 1000 | 0), query: expr });
     const r = await http({ method: 'GET', url: `https://api.${site}/api/v1/query?${q}`,
       headers: { 'DD-API-KEY': apiKey, 'DD-APPLICATION-KEY': appKey } });
     if (r.status !== 200) return { error: `Datadog metrics: HTTP ${r.status}` };
     const points = ((r.body && r.body.series) || []).flatMap((s) => s.pointlist || []);
     return { count: points.filter(([, v]) => v !== null && v !== 0).length };
   }
   ```
   `site` is `datadoghq.com`, `datadoghq.eu`, `us5.datadoghq.com` and so on.
3. **`exploreLink`:** return a Datadog link for the new kinds, so each evidence item links to
   what the checker saw. The Logs Explorer takes the query and the window in milliseconds:
   `https://app.${site}/logs?query=${encodeURIComponent(expr)}&from_ts=${startMs}&to_ts=${endMs}`.
4. **`engine/lib/render.js`:** add the labels, `KIND_LABEL = { logql: 'Loki', promql:
   'Prometheus', dd_logs: 'Datadog logs', dd_metrics: 'Datadog metrics' }`, and add the new
   kinds wherever `renderCore` and `evidenceLink` test for `'logql' || 'promql'`, so they are
   shown as telemetry with their query.
5. **`prompt.md`:** describe the new kinds where the evidence rules are, for example:
   - `dd_logs` / `dd_metrics`: the exact Datadog query is re-run over [`start`, `end`] (at most
     24 h). `expect: "found"` needs at least one log event or one non-zero point; `expect:
     "none"` needs none. Copy the query exactly as you ran it.

   and remove the sentence that says telemetry cannot be re-checked (it comes from `build.py`'s
   `TELEMETRY_UNCHECKED`; set your own text there).

## Checklist

- [ ] A test board or repository, and `not_before` set to now.
- [ ] New settings added to `config.toml` and passed through `build.py`.
- [ ] Every replaced function returns the shape in the seams table.
- [ ] Secrets only through `__secret`, never in `config.toml`.
- [ ] Built, imported, run on a test ticket; the child's execution checked node by node.
