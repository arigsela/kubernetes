# n8n Bug Investigator (POC) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run an n8n version of the Taskboard bug investigator (n8n's AI Agent node on Claude Sonnet 5.5, with the same prompt, tools and checks) in parallel with `kagent/bug-investigator`, so both answer the same `bug` issues and their comments can be compared.

**Architecture:** Two GitOps-managed n8n workflows. A small parent, `bug-investigator-n8n-poc`: Schedule → Pick issue → Investigate issue (Execute Workflow); its idle runs are not saved. A child, `bug-investigator-n8n-poc-run`: Gather facts → AI Agent → Verify → Render and post → Finish; every run is saved. One investigation runs at a time. The deterministic steps are JavaScript ports of the bug-investigator core (gather, verify, render), kept in `scripts/n8n-bug-investigator/lib/`, unit- and parity-tested against the Python core in CI, and inlined by a generator into the Code nodes (each node gets only the modules it requires). The agent binds the same 11 tool names as the kagent AgentTemplate: six GitHub reads through a new read-only github-mcp-server in namespace `n8n`, five telemetry tools through the existing `taskboard-telemetry` server.

**Tech Stack:** n8n 2.42.3 (Code node JavaScript on the internal task runner, AI Agent, Anthropic Chat Model, MCP Client Tool, Execute Workflow); Node.js 22 and Docker (tests only); Python 3.12 + pytest (generator, parity tests); github-mcp-server v1.14.0 behind agentgateway v1.6.0; Vault + External Secrets.

**Spec:** the **Design** section below is the spec for this plan. Background, read before starting:
`docs/plans/bug-inspector-agent.md` (the kagent handover), `base-apps/kagent/bug-investigator-trigger.yaml` and
`scripts/bug_investigator_trigger.py` (the pipeline being mirrored), and the core package in
`base-apps/kagent/bug-investigator-core-configmap.yaml` (source `arigsela/claude-agents` `bug-investigator/` @ `bf05291`).

---

## Review fixes (2026-10-28)

A `/code-review` of the first draft found 15 problems. This version fixes all of them; the owner chose the defaults
for #8, #12 and #15.

| # | Problem in the first draft | Fix | Where |
|---|---|---|---|
| 1 | Six copies of every module made a ~400 KB ConfigMap (over the 256 KiB client-side-apply annotation); 720 idle runs a day each saved the whole workflow | Per-node bundles, a 192 KiB budget test, and a parent/child split whose idle runs are not saved | Decision 9, Task 9 |
| 2 | `countResults` turned server-truncated output into 0 and ignored `+Inf`, so `expect: none` could pass while data existed | Truncated or unparseable output is "could not count"; the wrapper's `total` is used; ±Inf counts | Task 3, Task 6 |
| 3 | `GET /commits/{sha}` accepts commits on unmerged branches; the core accepts only commits reachable from main | Reachability through `compare/{head}...{sha}` | Task 6 |
| 4 | A new MCP session (one stdio child on the shared telemetry server) per verify query, never closed | One session per verify, `DELETE` at the end | Task 3, Task 6 |
| 5 | No limit on concurrent investigations | One at a time: a fresh lock anywhere makes Pick idle | Decision 8, Task 4 |
| 6 | A bad workflow import fails the shared `set -e` initContainer and crash-loops n8n; the real import test was optional | A mandatory Docker import test in CI, and POC import lines that cannot fail the initContainer | Task 9 |
| 7 | During a rollout two n8n pods fire the schedule; the label lock is check-then-add | A 👀 reaction claim (GitHub answers 201 once, then 200) | Decision 8, Task 4 |
| 8 | Activation would investigate the issues already open (#30, #37) | `NOT_BEFORE`: issues created earlier are never picked | Decision 7, Task 4, Task 11 |
| 9 | Unclear which lock event counts; events not paginated; endless retries starve newer issues | Latest lock event, all pages, at most 3 attempts | Task 4 |
| 10 | Hundreds of sequential REST calls in Gather with no timeouts; a runner kill mislabels a prepare failure | GraphQL for PRs, one compare per deploy, 20 s request timeout, a Gather deadline, a request-budget test | Task 3, Task 5 |
| 11 | The AI Agent's output shapes were never observed | The spike runs the agent with a real tool call and a forced error; the trail fixture is that recording | Task 1, Task 7, Task 9 |
| 12 | The contamination flag missed kagent's other fix branches, and kagent's memory handed it earlier answers | Flag any `agent/taskboard-issue-` branch; drop kagent's labels from the facts; kagent's memory off during rehearsals | Decisions 6 and 10, Tasks 5, 7, 11 |
| 13 | n8n's facts and verify roots differed from kagent's | Bot authors as `app/…`, merged PRs in `gh pr list`'s order, code-point clipping, the deploy checkout's cone | Tasks 2, 5, 6 |
| 14 | Three parity tests were flaky, impossible, or blind to a partial scrub | Frozen `utc_now`, message-class comparison, repeated secrets | Tasks 2, 6 |
| 15 | Both PATs in the shared `n8n` Vault key through `vault-backend` | Per-consumer Vault paths, policies, roles, ESO ServiceAccounts and SecretStores, and a provisioning script | Decision 4, Tasks 8, 9, 11 |

## Design

### What runs today (the thing being mirrored)

`bug-investigator-trigger` (CronJob, every 2 min, `concurrencyPolicy: Forbid`, `scripts/bug_investigator_trigger.py`):
(a) files `bug` issues from Taskboard 5xx alerts; (b) picks the oldest open `bug` issue without `investigated`,
`rehearsal` or `investigation-failed` whose `bug` label was added by a writer or `arigsela-implementer[bot]`;
(c) clones `arigsela/taskboard` (single-branch `main`) and `arigsela/kubernetes` (blobless, single-branch, cone-mode
sparse checkout of `base-apps/taskboard`); (d) gathers facts with the core (`gather.gather("full", …)`,
`gather.to_markdown`) and appends remembered outcomes; (e) sends `MESSAGE` + facts to `kagent/bug-investigator` over
A2A (480 s); (f) verifies with `verify.verify` (telemetry re-run directly against Loki/Prometheus), renders with
`render.render` relabelled to `mode=kagent`, posts or edits its comment, labels `investigated`; (g) hands a verified
cause to `kagent/implementer` for a fix PR. Failure after the agent was asked → `investigation-failed`; failure before
→ retried next run.

### The n8n version

| Step | kagent today | n8n POC |
|---|---|---|
| Trigger | CronJob `*/2 * * * *`, one run at a time | Parent workflow, Schedule Trigger every 2 minutes; at most one investigation at a time (Decision 8) |
| Pick | rule (b) on kagent's labels | the same rule on its **own** labels: skips `n8n-investigated`, `n8n-investigation-failed`, `rehearsal` and issues created before `NOT_BEFORE`; does nothing while any issue holds a fresh `n8n-investigating` lock. Ignores kagent's labels, so an issue kagent finished is still investigated. The claim is a 👀 reaction |
| Gather | git clones + core gather | GitHub REST/GraphQL with the same facts; `toMarkdown` byte-identical to `gather.to_markdown`. Ticket comments containing `<!-- bug-investigator` and kagent's labels (`investigated`, `investigation-failed`, `fix-proposed`) are dropped (kagent drops only `mode=` comments) |
| Memory | "Remembered outcomes" (§T.148) | none; kagent's memory is off during rehearsals (Decision 10) |
| Ask | A2A, Sonnet 5.5 via the model gateway | Child workflow: AI Agent node, Anthropic Chat Model `claude-sonnet-5-5`, max tokens 16000, at most 40 iterations, workflow timeout 900 s |
| Prompt | AgentTemplate `systemPrompt` + `MESSAGE` | identical text: the generator copies the systemPrompt; `buildMessage` reproduces `MESSAGE` (CI checks both) |
| Tools | `implementer` (6 reads) + `taskboard-telemetry` (5) | `n8n-github-read-mcp` (the same 6, read-only on the server side) + `taskboard-telemetry` (the same 5) |
| Verify | core verify on clones | JS port. Files through the GitHub API at the heads captured in gather (deploy files limited to what kagent's sparse checkout holds); commits only when reachable from those heads; telemetry re-run through `taskboard-telemetry` (its scope check) in one MCP session; output the server truncated is "could not count" |
| Post | comment `<!-- bug-investigator mode=kagent -->` (App bot), label `investigated` | comment `<!-- bug-investigator mode=n8nagent -->` (the PAT's user), label `n8n-investigated` |
| Fix PR, alert-first | yes | no |
| Failure | `investigation-failed` / retry | `n8n-investigation-failed` / lock and claim removed for a retry, at most 3 attempts |

### Decisions

1. **Own labels and marker.** Both versions run on the same issue. kagent's gather already drops every
   `mode=` comment, so it never reads n8n's. `mode=n8n` is taken (the GitHub Action's ticket-and-code baseline), hence
   `n8nagent`.
2. **Not the `implementer` tool server.** Its agentgateway allows `create_branch`, `create_or_update_file`,
   `push_files` and `create_pull_request` to any caller that reaches it (`mcpAuthorization` has no caller check). The n8n agent
   gets a new server that is read-only on the server side (`--read-only --tools …`) with a read-only token.
3. **Direct Anthropic key, not the model gateway.** The gateway's NetworkPolicy admits only `ate-system` and
   `logging`, `budgets.yaml` requires every group to have kagent agents, and n8n's Anthropic node honouring a
   custom base URL is unconfirmed. The spend cap is the Anthropic workspace limit on the POC key; cost is compared
   from Anthropic's usage per key. (Task 1: n8n's Anthropic credential does have a Base URL field, so the third
   reason is gone; the first two still hold.)
4. **Static fine-grained PATs, each with its own Vault identity.** n8n reads env once at start; GitHub App
   installation tokens expire hourly. PAT A (n8n env): `arigsela/taskboard`, Issues read/write, Contents, Pull
   requests and Metadata read. PAT B (MCP server): Contents, Pull requests and Metadata read. `arigsela/kubernetes` is
   public, so both can read it. Following the agent-identity contract (`templates/agent-identity/README.md`,
   invariant 1, and CLAUDE.md Important Notes 7), each PAT has its own Vault path, a policy that reads only that path,
   a Kubernetes-auth role bound to a dedicated ESO ServiceAccount, and its own SecretStore:
   - PAT A: `k8s-secrets/n8n-bug-investigator`, property `gh-token`; policy and role `n8n-bug-investigator`; SA
     `eso-n8n-bug-investigator`; SecretStore `vault-n8n-bug-investigator`; Secret `n8n-bug-investigator-gh`, key `token`.
   - PAT B: `k8s-secrets/n8n-github-read-mcp`, property `github-token`; policy and role `n8n-github-read-mcp`; SA
     `eso-n8n-github-read-mcp`; SecretStore `vault-n8n-github-read-mcp`; Secret `n8n-github-read-mcp-token`, key
     `GITHUB_PERSONAL_ACCESS_TOKEN`.

   Neither reads the shared `n8n` key or uses `vault-backend`, so `n8n-secrets` is untouched.
   `scripts/provision-n8n-bug-investigator-vault.sh` (modelled on `provision-brave-search-vault.sh`) writes the
   policies and roles, and the tokens only from env. Ari runs it.
5. **A JavaScript port with parity tests**, not calls into Python: this n8n has no Python task runner, and the POC
   tests what an n8n-only build can do (the question behind it: an n8n build like a CTO friend's).
6. **No cross-reading, and flag what leaks.** n8n drops every `<!-- bug-investigator` comment (kagent's fix comment
   included) and kagent's labels from its facts. n8n's agent can still list kagent's fix PRs (branches
   `agent/taskboard-issue-<N>`) mid-run. A run whose tool calls or results mention any such branch, **for any issue
   number**, is flagged in its comment with the branches it saw. Rehearsal issues re-file bugs kagent already fixed
   under other numbers, so this issue's branch alone is not enough.
7. **Kill switch and start line.** The generator constant `ACTIVE`; the workflows ship inactive and Task 11 turns
   them on. The same activation PR sets `NOT_BEFORE` (UTC ISO time, a generator constant): Pick never takes an issue
   created before it, so the issues already open (#30 and #37 today, both `investigated, fix-proposed`, #37 with
   kagent's open fix PR #38) are left alone. The generator refuses `ACTIVE = True` without `NOT_BEFORE`.
8. **One investigation at a time, claimed atomically.** Like kagent's `concurrencyPolicy: Forbid`, Pick does nothing
   while any open `bug` issue holds a fresh `n8n-investigating` lock. That alone is not a mutex. The n8n Deployment
   rolls with surge, so old and new pods overlap for minutes and both fire the schedule, and adding a label that is
   already there succeeds. The claim is therefore a 👀 (`eyes`) reaction from PAT A's user. GitHub's "create reaction
   for an issue" answers `201` when it creates the reaction and `200` when that user's reaction already exists. Only
   the `201` caller adds the lock label and proceeds; a `200` caller ends the run. Every lock release deletes our
   reaction too.
9. **Two workflows; idle runs not saved.** n8n saves the whole workflow with each execution
   (`EXECUTIONS_DATA_SAVE_ON_SUCCESS=all` here) and prunes at 10,000 executions across all workflows by default. 720
   idle runs a day of the full workflow would fill n8n's database (on the plain `postgresql` Deployment, which has
   no scheduled backup) and push every other workflow's history out. The
   parent (Schedule, Pick, Execute Workflow; a few KB) sets `saveDataSuccessExecution: "none"` and still saves errors.
   The child holds the investigation and saves every run. The parent waits for the child. The n8n Argo app keeps
   client-side apply; the 192 KiB budget leaves room under the 256 KiB annotation limit, so no `ServerSideApply`
   switch for every n8n resource is needed.
10. **kagent's memory is off during rehearsals.** The trigger adds the verified outcomes of earlier issues to
    kagent's facts (§T.148). A rehearsal re-files those bugs, so kagent would be handed the cause n8n has to find.
    Task 11 sets `MEMORY_ENABLED=0` on the trigger for the rehearsal and removes it afterwards. That also keeps the
    rehearsal answers out of the memory.

### What n8n does not get (say so in the docs)

No sandbox per run, no admission or capability check, no action record, not on the Agent Platform dashboards or the
eval gate, no daily budget at the gateway. PAT A sits in n8n's environment, readable by anyone who can edit a
workflow (`N8N_BLOCK_ENV_ACCESS_IN_NODE=false`), even though Vault scopes it to its own path.

### Non-goals

Fix PRs, alert-first issues, memory, the hk-demo tab and the Grafana live dashboard, the eval gate, and the
Monday/Datadog variant (a later step: swap the ticket and telemetry adapters).

## Global Constraints

- Branch `feat/n8n-bug-investigator-poc` from `origin/main`, in a worktree: the main checkout sits on a merged branch with untracked files.
- Never push to `main`, merge, `kubectl apply/edit/patch`, or write Vault. Ari creates the PATs and the Anthropic key, runs `scripts/provision-n8n-bug-investigator-vault.sh`, and creates the n8n credential (Task 11).
- n8n image: `n8nio/n8n:2.42.3@sha256:240eaa2a3d491adac5817aa4c3f1c521bb18ec79f6e448517158d5220ee0f37b`.
- Model `claude-sonnet-5-5`, max tokens `16000`, AI Agent `maxIterations` `40`. Child `executionTimeout` `900`, parent `960`. Schedule every `2` minutes.
- Repo `arigsela/taskboard`; deploy repo `arigsela/kubernetes`, path `base-apps/taskboard`; namespace `taskboard`.
- GitHub tools, exactly: `get_file_contents`, `search_code`, `list_commits`, `get_commit`, `list_pull_requests`, `pull_request_read`.
- Telemetry tools, exactly: `query_loki_logs`, `query_loki_stats`, `list_loki_label_names`, `list_loki_label_values`, `query_prometheus`.
- MCP URLs: `http://n8n-github-read-mcp.n8n.svc.cluster.local:3000/mcp`, `http://taskboard-telemetry-mcp.kagent.svc.cluster.local:3000/mcp`.
- Datasource UIDs: Loki `loki`, Prometheus `PBFA97CFB590B2093`. Grafana for links: `https://grafana.arigsela.com`.
- Labels: `n8n-investigating` (lock), `n8n-investigated`, `n8n-investigation-failed`. Claim: an `eyes` reaction by PAT A's user. Never add or remove `investigated`, `investigation-failed`, `fix-proposed`, `from-alert` or `bug`.
- One investigation at a time. Issues created before `NOT_BEFORE` are never picked.
- Stale lock: the **latest** `labeled` event for `n8n-investigating` (events read across all pages) older than 20 minutes → released to `n8n-investigation-failed`. An issue locked 3 times without finishing → `n8n-investigation-failed`.
- Comment marker `<!-- bug-investigator mode=n8nagent -->`; header `🔎 Bug investigation — n8n AI Agent (POC)`; footer lead `n8n/bug-investigator-poc`.
- Workflow ids `bug-investigator-n8n-poc` (parent) and `bug-investigator-n8n-poc-run` (child). ConfigMap `n8n-workflow-bug-investigator`, keys `bug-investigator.json` and `bug-investigator-run.json`, at most 192 KiB as a file.
- HTTP: every request has a 20 s timeout. Gather and Verify stop at a deadline 60 s below the task runner's timeout: 240 s (the runner's default 300 s, not overridden in the cluster; Task 1).
- Vault: only the per-consumer paths, stores and roles of Decision 4; never the `n8n` key or `vault-backend`.
- JS: CommonJS, no npm dependencies, no I/O except through injected `http`/clients; every exported function is deterministic given its inputs (time is passed in as `now`, the deadline clock as an injected `clock`).
- Tests: one pytest suite, `tests/n8n_bug_investigator/`. The JS runs under `node` (22) from pytest; the import test runs the pinned n8n image under Docker. Missing `node` or `docker` fails the suite, never skips.
- Generated files only via `scripts/gen-n8n-bug-investigator-workflow.py --write`; CI runs `--check`.
- Commits: Conventional Commits, scope `n8n` (e.g. `feat(n8n): …`), with the session's attribution trailers.

## Review Focus

- kagent's fix PR, for any issue, read by n8n's agent while it investigates: the n8n comment must say so (Task 7, `test_flags_any_kagent_fix_branch`).
- An execution killed by the 900 s timeout leaves `n8n-investigating` on the issue: the next pick releases a lock whose latest lock event is older than 20 minutes to `n8n-investigation-failed` (Task 4, `test_stale_lock_is_released_as_failed`, `test_stale_lock_uses_the_latest_lock_event`).
- An issue kagent already labelled `investigated` must still be picked by n8n, and n8n never touches kagent's labels (Task 4, `test_ignores_kagents_labels`; Task 9, `test_finish_never_touches_kagent_labels`).
- The agent's answer is not findings JSON (the max-iterations message, prose, JSON cut off at max tokens): no comment, `n8n-investigation-failed` (Task 6, `test_parse_findings_rejects`; Task 9, `test_finish_answer_failure_is_spent`).
- PAT A in an error message, a log line or the comment: scrubbed with the token as an extra secret (Task 3, `test_errors_never_carry_the_token`).
- Two pods fire the same schedule during a rollout: only one investigates (Task 4, `test_claim_is_won_by_one_caller`, `test_one_investigation_at_a_time`).
- Activation leaves the issues opened before it alone (Task 4, `test_issues_before_not_before_are_skipped`; Task 9, `test_active_requires_not_before`).
- A commit only on an unmerged branch (kagent's fix PR) is not verified (Task 6, `test_unreachable_commit_is_dropped`).
- Telemetry output the server truncated never confirms `expect: none` (Task 3, `test_count_results`; Task 6, `test_truncated_telemetry_is_not_counted`).
- The generated ConfigMap stays under budget, n8n accepts both workflows, and a failed POC import cannot stop the n8n pod (Task 9, `test_configmap_size_budget`, `test_n8n_accepts_the_workflows`, `test_poc_import_cannot_block_the_pod`).
- One MCP session per verify on the shared telemetry server, closed at the end (Task 6, `test_one_mcp_session_per_verify`).

## File map

| File | Responsibility |
|---|---|
| `scripts/n8n-bug-investigator/lib/util.js` | time, clip/fence, window, scrub (ports of the core's `util` and `gather` helpers) |
| `scripts/n8n-bug-investigator/lib/github.js` | GitHub REST/GraphQL client over an injected `http` |
| `scripts/n8n-bug-investigator/lib/mcp.js` | minimal Streamable-HTTP MCP client (`openSession`, `callTool`), `countResults` |
| `scripts/n8n-bug-investigator/lib/pick.js` | eligibility, trust check, claim, lock, stale lock, attempts, `releaseClaim` |
| `scripts/n8n-bug-investigator/lib/gather.js` | facts over the API, `toMarkdown`, `buildMessage` |
| `scripts/n8n-bug-investigator/lib/verify.js` | `parseFindings`, `verify`, `makeVerifyCtx` |
| `scripts/n8n-bug-investigator/lib/render.js` | `renderComment`, `trailFromSteps`, `sawKagentWork`, `postComment` |
| `scripts/n8n-bug-investigator/lib/finish.js` | `outcome`: the final labels |
| `scripts/n8n-bug-investigator/lib/n8n.js` | `httpFromHelpers`: adapts `this.helpers.httpRequest` to `http` |
| `scripts/n8n-bug-investigator/nodes/*.js` | the Code nodes' entry snippets (n8n globals; kept under 30 lines each) |
| `scripts/n8n-bug-investigator/spike/` | Task 1's reproducible spike workflows and node-shape probe |
| `scripts/gen-n8n-bug-investigator-workflow.py` | builds both workflow JSONs, the ConfigMap and the pod annotation |
| `scripts/provision-n8n-bug-investigator-vault.sh` | Vault policies and roles for both PATs; writes the tokens only from env (Ari runs it) |
| `scripts/compare-bug-investigations.py` | reads both comments on issues and prints the comparison |
| `base-apps/n8n/bug-investigator-workflow-configmap.yaml` | generated |
| `base-apps/n8n/bug-investigator-secrets.yaml` | PAT A: ESO ServiceAccount, SecretStore, ExternalSecret → Secret `n8n-bug-investigator-gh` |
| `base-apps/n8n/github-read-mcp.yaml` | read-only GitHub MCP server (SA, ConfigMap, Service, Deployment, NetworkPolicy) |
| `base-apps/n8n/github-read-mcp-secrets.yaml` | PAT B: ESO ServiceAccount, SecretStore, ExternalSecret → Secret `n8n-github-read-mcp-token` |
| `base-apps/n8n/deployments.yaml` | PAT A env, workflow mount, isolated import lines, checksum |
| `base-apps/kagent/bug-investigator-trigger.yaml` | `MEMORY_ENABLED: "0"` for the rehearsal only (Task 11) |
| `tests/n8n_bug_investigator/` | `conftest.py`, `run.js` (node runner), `fixtures/`, `test_*.py` |
| `.github/workflows/validate.yaml`, `AGENTS.md`, `SPEC.md`, `base-apps/n8n/docs.md`, `runbook.md` | CI job, tables, ledger rows, docs |

---

## Tasks

### Task 1: Spike: confirm what n8n 2.42.3 can do, and capture node shapes

**Prerequisite:** Task 11 Step 0 (Ari): the POC Anthropic key exists and sits in `~/.config/n8n-bug-investigator-spike.env`
(`ANTHROPIC_API_KEY=…`, mode 600), outside the repo.

**Files:**
- Create: `scripts/n8n-bug-investigator/spike/spike-workflow.json`, `spike/agent-workflow.json`, `spike/subworkflow-parent.json`, `spike/subworkflow-child.json`, `spike/node-shapes.js`
- Create: `tests/n8n_bug_investigator/fixtures/n8n-2.42.3-nodes.json`, `fixtures/intermediate-steps.json`, `fixtures/agent-error-item.json`
- Modify: this plan, section "Task 1 results" (append)

**Interfaces:**
- Produces: `n8n-2.42.3-nodes.json` = `{"<node type>": {"typeVersion": <number>, "parameters": {…the parameter names and shapes the generator sets…}}}` for `n8n-nodes-base.scheduleTrigger`, `n8n-nodes-base.code`, `n8n-nodes-base.if`, `n8n-nodes-base.executeWorkflow`, `n8n-nodes-base.executeWorkflowTrigger`, `@n8n/n8n-nodes-langchain.agent`, `@n8n/n8n-nodes-langchain.lmChatAnthropic`, `@n8n/n8n-nodes-langchain.mcpClientTool`. Task 9 reads it.
- Produces: `intermediate-steps.json` (a real AI Agent run's `intermediateSteps`, scrubbed) and `agent-error-item.json` (the item on the agent's error output). Task 7 and Task 9 read them.
- Produces the confirmed facts below: the MCP session header name, the response content type, the task runner's timeout, and the workflow setting that lets the parent call the child. Task 3 and Task 9 rely on them.

- [x] **Step 1: Start the same n8n locally.** `docker run -d --name n8n-spike -p 5678:5678 --env-file ~/.config/n8n-bug-investigator-spike.env -e N8N_BLOCK_ENV_ACCESS_IN_NODE=false -e SPIKE_TOKEN=spike-secret n8nio/n8n:2.42.3@sha256:240eaa2a3d491adac5817aa4c3f1c521bb18ec79f6e448517158d5220ee0f37b`
- [x] **Step 2: Code-node HTTP.** `spike-workflow.json`: Manual Trigger → Code node that runs `this.helpers.httpRequest({method:'GET', url:'https://api.github.com/zen', returnFullResponse:true, ignoreHttpStatusErrors:true, json:true, timeout: 20000})`, the same against `https://api.github.com/repos/arigsela/does-not-exist`, and one against `http://10.255.255.1` with `timeout: 2000`. It returns `{status, headerKeys, envSeen: $env.SPIKE_TOKEN === 'spike-secret'}` for the first two and the elapsed time and error code for the third. Import with `docker cp` + `docker exec n8n-spike n8n import:workflow --input=/tmp/spike-workflow.json`, run `docker exec n8n-spike n8n execute --id=spike --rawOutput`.
  Pass: statuses `200` and `404` (no throw), headers present, `envSeen: true`, and the third throws within 3 s.
- [x] **Step 3: MCP over Streamable HTTP.** `kubectl -n kagent port-forward svc/taskboard-telemetry-mcp 3000:3000`. Add a second Code node that POSTs `initialize` (protocolVersion `2025-06-18`) to `http://host.docker.internal:3000/mcp` with `Accept: application/json, text/event-stream`, then `notifications/initialized`, `tools/list` and two `tools/call`s with the returned session header, then `DELETE /mcp` with that header. Pass: five tool names equal to the telemetry list, both calls answer on one session, and `DELETE` answers 2xx or 405. Record the session header name, whether replies come as JSON or `text/event-stream`, and the `DELETE` status.
- [x] **Step 4: Read-only GitHub server flags.** Pipe `initialize`, `notifications/initialized`, `tools/list` into `docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN=dummy ghcr.io/github/github-mcp-server:v1.14.0@sha256:7aaeeec9ae4fe9a736d100c1ff0798f3c219b5009e05f5d3945fcacb13cc196b stdio --read-only --tools get_file_contents,search_code,list_commits,get_commit,list_pull_requests,pull_request_read`.
  Pass: exactly the six tools listed.
- [x] **Step 5: The AI Agent, for real.** Create the Anthropic credential in the spike container from the env file at run time: `n8n import:credentials` from a JSON built in the scratchpad and deleted right after, never in the repo. If the CLI will not take a plain key, Ari creates the credential once in the local UI. `agent-workflow.json`: Manual Trigger → AI Agent (`claude-sonnet-5-5`, `maxIterations` 3, `returnIntermediateSteps: true`, `onError: continueErrorOutput`), with an MCP Client Tool to the port-forwarded telemetry server (selected tool `list_loki_label_names`). Text: "Call list_loki_label_names once, then answer with the single word done."
  Record into `intermediate-steps.json`, scrubbed: `action.tool`, the full `action.log` text, the type of `action.toolInput`, and the type and shape of `observation` (string, or an array of content parts).
  Then force an error: point the MCP Client Tool at `http://host.docker.internal:3999/mcp` (closed) and, separately, set the model to `claude-does-not-exist`. Record the error-output item into `agent-error-item.json`: its keys, and whether it carries the input item's fields.
  Pass: a tool call is recorded and both errors arrive on the error output.
- [x] **Step 6: Sub-workflow and saving.** `subworkflow-child.json` (Execute Workflow Trigger accepting all input → a Code node echoing it) and `subworkflow-parent.json` (Manual Trigger → Code node returning `{issue: {number: 1}}` → Execute Workflow by the child's id, waiting for it). Give the parent `saveDataSuccessExecution: "none"`. Import both with the CLI and run the parent. Pass: the parent gets the child's output; the Executions list keeps the child's run and not the parent's. Record the parameter names (workflow id resource locator, the wait option) and whether the child needed a `callerPolicy` setting to be callable after a CLI import.
- [x] **Step 7: Node shapes and runner timeout.** `node-shapes.js`, run with `docker exec n8n-spike node /tmp/node-shapes.js`, loads each node class from the image's installed packages and prints its latest `typeVersion` and the parameter names used for: the schedule interval; Code `jsCode` and `mode`; If conditions; Agent `promptType`, `text`, `options.systemMessage`, `options.maxIterations`, `options.returnIntermediateSteps`; the Anthropic model (resource-locator shape) and `options.maxTokensToSample`; MCP Client Tool endpoint URL, transport, and selected-tools fields; Execute Workflow source, workflow id and wait option; Execute Workflow Trigger input mode. It also prints the task runner's default task timeout (`N8N_RUNNERS_TASK_TIMEOUT` in `@n8n/config`). Write the fixture file from its output.
- [x] **Step 8: Import check.** Import a skeleton of both workflows with one node of each type, wired as in Task 9, using the fixture's shapes; `n8n export:workflow --id=…` and confirm the parameters survive. Pass: import succeeds and the export keeps every parameter.
- [x] **Step 9: Record and decide.** Append "Task 1 results" to this plan: each pass or fail, the session header, the content type, the `DELETE` status, the runner timeout, the typeVersions, the `callerPolicy` finding, and the agent's `action.log` and `observation` shapes. If Step 2, 3, 4, 5 or 6 fails, **stop and report to Ari**; the design depends on them. `docker rm -f n8n-spike`; delete the scratchpad credential JSON.
- [x] **Step 10: Commit.** `git add scripts/n8n-bug-investigator/spike tests/n8n_bug_investigator/fixtures docs/superpowers/plans/2026-10-28-n8n-bug-investigator-poc.md` → `docs(n8n): spike n8n 2.42.3 for the bug investigator POC (§T.152)`

### Task 2: Test harness, `util.js`, and the CI job

**Files:**
- Create: `tests/n8n_bug_investigator/conftest.py`, `tests/n8n_bug_investigator/run.js`, `tests/n8n_bug_investigator/test_util.py`
- Create: `scripts/n8n-bug-investigator/lib/util.js`
- Modify: `.github/workflows/validate.yaml` (new job), `AGENTS.md` (suite table: `n8n_bug_investigator` needs Node 22 and Docker)

**Interfaces:**
- Produces (pytest fixtures): `core` → the Python core package (`gather`, `verify`, `render`, `util`, `grafana`, `scope` modules) imported from the `data` of `base-apps/kagent/bug-investigator-core-configmap.yaml` written into a temp `src/bug_investigator/`; `frozen_now` → a fixed aware `datetime` that it also monkeypatches into every core module's `utc_now` (`util`, `verify`, `gather`); `js(module, fn, *args, ctx=None)` → the JSON result of calling `lib/<module>.js` export `fn` with JSON `args` (awaited if it returns a promise). `ctx` names a fake built by `run.js` from a JSON spec (Tasks 4–7 add fakes: `fakeHttp`, `fakeGitHub`, `fakeVerifyCtx`).
- Produces (`util.js`): `parseTime(s) → Date|null`, `iso(d) → string|null` (`%Y-%m-%dT%H:%M:%SZ`), `short(s) → string` (`%Y-%m-%d %H:%M UTC` or `?`), `clip(s, n) → string`, `fence(text) → string`, `window(start, end, fallback, now, padMs = 7_200_000, capMs = 86_400_000) → [Date, Date]`, `scrubText(s, extra = []) → string`, `scrub(obj, extra = []) → obj`. Ports of `util.parse_time`, `util.iso`, `gather._short`, `gather._clip`, `gather._fence`, `util.window`, `util.scrub_text`, `util.scrub`.
  `clip` counts Unicode code points (`Array.from`), as Python `len` and slicing do, and strips Python `str.strip()`'s whitespace set. Every replacement in `scrubText` is global.

- [ ] **Step 1: Write the failing parity tests** in `test_util.py`, each running both sides on one case list:

```python
def test_parse_time_matches_core(core, js):
    for s in ["2026-10-27T15:43:00Z", "2026-10-27 15:43:00+02:00", "2026-10-27T15:43:00.1234567Z",
              "2026-10-27T15:43:00", "", "nope", None]:
        py = core.util.iso(core.util.parse_time(s))
        assert js("util", "isoOfParsed", s) == py      # isoOfParsed(s) = iso(parseTime(s)), test-only export

def test_window_matches_core(core, js, frozen_now): ...
    # 6 cases: both None, start only, end only, end > now, start >= end, > 24 h.
    # Python reads the patched utc_now; JS gets frozen_now as `now`. No wall clock on either side.
def test_short_clip_fence_match_core(core, js): ...
    # gather._short/_clip/_fence on 5 inputs each, incl. a text with ``` and ~~~~, an emoji straddling
    # the clip point, a combining mark at the cut, and leading/trailing \x1c and  .
def test_scrub_matches_core(core, js):
    secret = "extra-secret-123"
    for s in ["Bearer abcdefghijklmnop", "ghp_" + "a" * 20, "github_pat_" + "b" * 22, "sk-ant-api03-xyz12345",
              "sha 0123456789abcdef0123456789abcdef01234567", "taskboard_http_requests_total_bucket_" + "x" * 30,
              "AKIAABCDEFGHIJKLMNOP", "Mixed41CharsBlobAAAAAAAAAAAAAAAAAAAAAAAAAaaaa9",
              f"token {secret} and again {secret}.", "ghp_" + "c" * 20 + " then ghp_" + "d" * 20]:
        out = js("util", "scrubText", s, [secret])
        assert out == core.util.scrub_text(s, [secret])
        assert secret not in out
```

- [ ] **Step 2: Run them to see them fail.** `python -m pytest tests/n8n_bug_investigator/test_util.py -q` → FAIL (`util.js` missing).
- [ ] **Step 3: Write `conftest.py` and `run.js`**, then implement `util.js` with the signatures above.
- [ ] **Step 4: Run them to see them pass.** Same command → PASS.
- [ ] **Step 5: CI job.** Add job `n8n-bug-investigator` to `validate.yaml`, modelled on the single-suite `tests/wan_ip/` job (same pinned `setup-python`), plus `actions/setup-node` pinned by full commit SHA (look up the current v4 release's SHA), `node-version: 22`, `pip install pyyaml==6.0.2 pytest==8.3.3`, `python -m pytest tests/n8n_bug_investigator/ -q`. The runner's Docker is used from Task 9 on.
- [ ] **Step 6: Commit.** `feat(n8n): bug-investigator JS util with parity tests against the core (§T.152)`

### Task 3: GitHub and MCP clients

**Files:**
- Create: `scripts/n8n-bug-investigator/lib/github.js`, `lib/mcp.js`, `lib/n8n.js`
- Test: `tests/n8n_bug_investigator/test_clients.py`

**Interfaces:**
- Consumes: `util.scrubText`.
- Produces:
  - `http` contract (all libs): `async http({method, url, headers, body, timeoutMs = 20000}) → {status: number, headers: object (lower-case keys), body: any}`. Never throws on HTTP status; throws on a network error or timeout, with a scrubbed message.
  - `httpFromHelpers(helpers) → http` in `n8n.js`: wraps `helpers.httpRequest({method, url, headers, body, json: true, returnFullResponse: true, ignoreHttpStatusErrors: true, timeout: timeoutMs})` (Task 1 confirmed the field names and the timeout).
  - `makeGitHub(http, token, {api = "https://api.github.com"} = {}) → gh` with `get(path, query?)`, `getAll(path, query?, maxPages = 10)` (follows `link: <…>; rel="next"`), `post(path, body)`, `patch(path, body)`, `del(path)`, `request(method, path, body?) → {status, body}` (raw, for the claim's 200/201), `graphql(query, variables)`, `login()` (cached `GET /user`.login). A non-2xx answer (except through `request`) throws `GitHubError` with `status`, `path`, and a message scrubbed with `[token]`. Every call counts toward `gh.requests` (Task 5's budget test reads it).
  - `openSession(http, url) → {callTool(name, args) → {isError: boolean, text: string}, close()}`: `initialize` → `notifications/initialized` once, then `tools/call` per call, carrying the session header from Task 1. Accepts a JSON body or `text/event-stream` (the last `data:` line whose JSON-RPC `id` matches). A JSON-RPC `error` returns `{isError: true, text: error.message}`. `close()` sends `DELETE` with the session header and ignores its answer.
  - `callTool(http, url, name, args)`: one-shot `openSession` → `callTool` → `close`.
  - `countResults(text) → number|null`, the meaning of `grafana.count_results` over the telemetry server's output (`mcp_server._render`):
    - JSON array of log entries (`{line}`) → its length.
    - Series (`[{values:[{value}]}]` or `{resultType, series}`) → the count of samples whose value is a number other than 0 and NaN. `"+Inf"` and `"-Inf"` count, as Python's `inf` does.
    - The `{truncated: true, total, items}` wrapper: log entries → `total`; series → the count over `items` when it is above 0, else `null`.
    - Text ending in ` ...[truncated]`, or anything unparseable → `null` ("could not count").

- [ ] **Step 1: Write the failing tests** (fake `http` replays canned `{status, headers, body}` by `METHOD url`):
  - `test_get_all_follows_link_next` (3 pages → 3 requests, concatenated items);
  - `test_errors_never_carry_the_token`: a 401 whose body echoes the token, and a network error whose message echoes it → both thrown messages contain `[redacted]` and not the token;
  - `test_graphql_returns_data_and_raises_on_errors`;
  - `test_call_tool_json_reply` and `test_call_tool_sse_reply` (the session header from initialize is sent on the next requests);
  - `test_session_is_reused_and_closed`: two `callTool`s on one session → one `initialize`, then `DELETE` with the session header on `close()`;
  - `test_call_tool_error_is_not_thrown` (`isError: true`);
  - `test_count_results`: 3 log entries → 3; series with `"0"`, `"NaN"`, `"1.5"`, `"+Inf"` → 2; a truncated wrapper of 5 log entries with 2 kept → 5; a truncated series wrapper with 0 kept → `null`; a `{resultType, series}` text cut with ` ...[truncated]` → `null`; `"oops"` → `null`;
  - `test_http_from_helpers_maps_full_response` (fake `helpers.httpRequest` returning `{statusCode, headers, body}`; `timeout` passed through).
- [ ] **Step 2: Run** `python -m pytest tests/n8n_bug_investigator/test_clients.py -q` → FAIL.
- [ ] **Step 3: Implement** `github.js`, `mcp.js`, `n8n.js` with the signatures above.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit.** `feat(n8n): GitHub and MCP clients for the bug-investigator workflow (§T.152)`

### Task 4: Pick the issue

**Files:**
- Create: `scripts/n8n-bug-investigator/lib/pick.js`
- Test: `tests/n8n_bug_investigator/test_pick.py`

**Interfaces:**
- Consumes: `gh` from Task 3.
- Produces: `LABELS = {bug: "bug", lock: "n8n-investigating", done: "n8n-investigated", failed: "n8n-investigation-failed", rehearsal: "rehearsal"}`; `TRUSTED_BOTS = ["arigsela-implementer[bot]"]`; `LOCK_STALE_MS = 1_200_000`; `MAX_ATTEMPTS = 3`; `CLAIM = "eyes"`.
  `async pickIssue(gh, repo, now, {notBefore}) → {issue: {number, title, body, created_at, html_url, labels: string[]} | null, filedBy: string|null, released: number[], exhausted: number[], idle: string|null}`. `notBefore` is required (a `Date`; missing → throws).
  Pure helpers: `labelledBy(events, issue) → string|null` (latest `labeled` event for `bug`, else the author); `mayTrigger(permission, login) → boolean` (same rule as the trigger's `may_trigger`: trusted bot, `role_name` ∈ {admin, maintain, write} or `permission` ∈ {admin, write}); `lockAddedAt(events) → Date|null` (the **latest** `labeled` event for the lock); `lockAttempts(events) → number` (count of `labeled` events for the lock).
  `async releaseClaim(gh, repo, n)`: deletes the `eyes` reactions on issue `n` whose `user.login === await gh.login()`; others are kept.
  `pickIssue`:
  1. Lists open `bug` issues oldest first (`getAll /repos/{repo}/issues?labels=bug&state=open&sort=created&direction=asc`, pull requests skipped).
  2. For every issue holding the lock, reads its events with `getAll` (all pages). A lock whose `lockAddedAt` is older than `LOCK_STALE_MS` is swapped for `failed`, its claim released, its number added to `released`. If any lock is still fresh, it returns `issue: null, idle: "busy #<n>"`.
  3. Walks the rest oldest first. It skips issues with `done`, `failed` or `rehearsal`, and issues created before `notBefore`. It runs the trust check (`getAll …/issues/{n}/events`, `GET /repos/{repo}/collaborators/{login}/permission`). An issue with `lockAttempts >= MAX_ATTEMPTS` gets `failed`, is added to `exhausted`, and the walk goes on.
  4. The first eligible issue is claimed with `gh.request("POST", …/issues/{n}/reactions, {content: "eyes"})`. `201` → add the lock (`POST …/issues/{n}/labels`) and return the issue. `200` → another execution holds it: return `issue: null, idle: "claimed elsewhere #<n>"`. Anything else throws.

- [ ] **Step 1: Write the failing tests** with a fake GitHub (canned issues, paginated events, permissions, reactions; records label and reaction calls):
  - `test_picks_oldest_trusted_bug`;
  - `test_skips_own_done_failed_rehearsal_and_fresh_lock`;
  - `test_ignores_kagents_labels`: an issue with `investigated` and `fix-proposed` is picked;
  - `test_untrusted_labeller_is_skipped` (role `read`), `test_trusted_bot_is_picked`;
  - `test_one_investigation_at_a_time`: a fresh lock on #5 → nothing picked although #3 is eligible;
  - `test_claim_is_won_by_one_caller`: reaction answers `200` → no lock label added, `issue: null`; `201` → lock added;
  - `test_stale_lock_is_released_as_failed`: latest lock event 21 min before `now` → `released == [n]`, calls remove `n8n-investigating`, add `n8n-investigation-failed`, delete our reaction; at 19 min it is untouched and the run is idle;
  - `test_stale_lock_uses_the_latest_lock_event`: lock events at −60 min and −5 min (a retry) → fresh, untouched;
  - `test_events_are_paginated`: the latest lock event is on page 2;
  - `test_attempts_are_capped`: 3 earlier lock events and no lock now → `n8n-investigation-failed`, in `exhausted`, and the next issue is picked;
  - `test_issues_before_not_before_are_skipped`;
  - `test_release_claim_deletes_only_ours` (another user's 👀 is kept);
  - `test_lock_is_added_to_the_picked_issue`, `test_no_issue_returns_null`;
  - `test_never_touches_kagent_labels`: across all cases, no recorded call names `investigated`, `investigation-failed`, `fix-proposed`, `from-alert` or `bug`.
- [ ] **Step 2: Run** `python -m pytest tests/n8n_bug_investigator/test_pick.py -q` → FAIL.
- [ ] **Step 3: Implement** `pick.js`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit.** `feat(n8n): pick Taskboard bug issues one at a time on the POC's own labels (§T.152)`

### Task 5: Gather facts

**Files:**
- Create: `scripts/n8n-bug-investigator/lib/gather.js`
- Test: `tests/n8n_bug_investigator/test_gather.py`, `tests/n8n_bug_investigator/fixtures/facts-full.json`, `fixtures/facts-sparse.json`, `fixtures/github-api/*.json`, `fixtures/gh-pr-list.json`

**Interfaces:**
- Consumes: `gh` (Task 3), `util` (Task 2).
- Produces:
  - `async gatherFacts(gh, cfg, issueNumber, now, {clock, deadlineMs}) → facts`, with `cfg = {repo, deployRepo, deployPath, namespace, days: 14}`. The returned `facts` has exactly the keys and shapes of `gather.gather("full", …)`: `mode: "full"`, `generated_at`, `repo`, `namespace`, `ticket`, `problems`, `app_head`, `deploy_repo`, `deploy_path`, `deploy_head`, `deploys`, `merged_prs`, `past_bugs`, `docs`, `telemetry`. A section that fails becomes `[]` plus a `problems` entry, as in Python. Passing the deadline (`clock() > deadlineMs`, checked before each request) throws `GatherTimeout`; the node turns it into an unspent `prepare` failure.
    - Authors, as `gh`'s JSON shows them: a bot (REST `user.type == "Bot"`, GraphQL `__typename == "Bot"`) becomes `app/<slug>` (no `[bot]`) for the ticket, its comments, PRs, reviews and PR comments. Review comments keep the REST login (`arigsela-implementer[bot]`), as `gh api` does.
    - Heads: `GET /repos/{r}/commits/main` → sha.
    - Ticket: `GET /repos/{repo}/issues/{n}` and its comments (`getAll`). Drop comments whose body contains `<!-- bug-investigator`. Drop kagent's labels (`investigated`, `investigation-failed`, `fix-proposed`) and the POC's own `n8n-*` labels from `ticket.labels`.
    - Deploys: `GET /repos/{deployRepo}/commits?path={deployPath}/code-configmap.yaml&sha=main&per_page=30`, then the file at each sha; `SOURCE_COMMIT_RE` from gather.py; `live_since` = commit date + 2 min.
    - Merged PRs: one GraphQL query in `gh pr list --state merged --limit 50`'s order: `repository.pullRequests(states: MERGED, first: 50, orderBy: {field: CREATED_AT, direction: DESC})` with the fields of `gh pr list --json number,title,mergedAt,author,body,files,url,mergeCommit,labels` plus `reviews(first: 100)` and `comments(first: 100)` (`gh pr view --json reviews,comments`). Keep those merged in the last `days` and not `rehearsal`. Review comments per kept PR via `getAll /repos/{repo}/pulls/{n}/comments`.
    - Deploy containment: `base` = the newest main commit before `since − 1 day` (`GET /repos/{repo}/commits?sha=main&until=…&per_page=1`). Per deploy, one `getAll /repos/{repo}/compare/{base}...{source_commit}` gives the set of commits that deploy contains since `base`. `live_since`/`deployed_by` = the oldest deploy whose set contains the PR's merge commit, or whose source is that commit. That is 1 + deploys compare calls, instead of PRs × deploys.
    - Past bugs: closed `bug` issues (no PRs, not `rehearsal`, not this issue), each with GraphQL `closedByPullRequestsReferences(first: 10) {nodes {number title url}}`.
    - Docs: `GET /repos/{r}/git/trees/{head}?recursive=1`; markdown under `docs/` (app) and under `{deployPath}/` (deploy); each file at the head; `RULE_RE` and the first `# ` title as in gather.py.
    - Telemetry: `telemetryPointers(namespace)`, identical to gather.py.
  - `toMarkdown(facts) → string`: byte-identical to `gather.to_markdown(facts)`.
  - `buildMessage(issueNumber, repo, factsMd) → string`: byte-identical to the trigger's `MESSAGE.format(issue=…, repo=…, facts=…)`.

- [ ] **Step 1: Record the reference.** Once, with read-only `gh`, save `gh pr list --repo arigsela/taskboard --state merged --limit 50 --json number,author` into `fixtures/gh-pr-list.json`, and the matching GraphQL and REST answers into `fixtures/github-api/` (no tokens; the repo is the owner's).
- [ ] **Step 2: Write the failing tests:**
  - `test_to_markdown_matches_core` for both fixtures: `js("gather", "toMarkdown", facts) == core.gather.to_markdown(facts)`;
  - `test_build_message_matches_trigger`: load `scripts/bug_investigator_trigger.py` as a module (the way `tests/bug_investigator_trigger/conftest.py` loads it) and compare with `MESSAGE.format`;
  - `test_rule_re_matches_core` on 12 doc lines (`- **P4** …`, `| C3 |`, `### S1: …`, `` `P10` ``, `P4x` (no match), `— dash` cases);
  - `test_telemetry_pointers_match_core`;
  - `test_merged_prs_match_gh_pr_list`: the PR numbers and authors gather keeps equal those of `fixtures/gh-pr-list.json` filtered the way gather.py filters them;
  - `test_bot_authors_match_gh`: an issue opened by the App, a bot review and a bot review comment → `app/arigsela-implementer`, `app/arigsela-implementer`, `arigsela-implementer[bot]`;
  - `test_kagent_and_own_labels_are_dropped`;
  - `test_gather_facts_from_api`: a fake GitHub from `fixtures/github-api/` → `facts` deep-equals the expected JSON. Covers a dropped kagent comment and a dropped `<!-- bug-investigator fix -->` comment, a rehearsal PR, a PR older than 14 days, `live_since` from the second-oldest deploy, a past bug with its closing PR, a docs file with three rules, and a failing past-bugs call (`past_bugs == []` plus one `problems` entry);
  - `test_gather_request_budget`: 21 merged PRs and 6 deploys (today's volume) → at most 60 requests;
  - `test_gather_deadline_is_a_prepare_failure`: a fake clock past the deadline → `GatherTimeout`, no further requests.
- [ ] **Step 3: Run** `python -m pytest tests/n8n_bug_investigator/test_gather.py -q` → FAIL.
- [ ] **Step 4: Implement** `gather.js`.
- [ ] **Step 5: Run** → PASS.
- [ ] **Step 6: Commit.** `feat(n8n): gather bug-investigator facts over the GitHub API, markdown identical to the core (§T.152)`

### Task 6: Parse and verify findings

**Files:**
- Create: `scripts/n8n-bug-investigator/lib/verify.js`
- Test: `tests/n8n_bug_investigator/test_verify.py`, `tests/n8n_bug_investigator/fixtures/verify-cases.json`

**Interfaces:**
- Consumes: `util`, `gh`, `openSession`/`countResults` (Task 3).
- Produces:
  - `parseFindings(text) → findings`: port of the trigger's `parse_findings` (code fence, leading text, `raw_decode` of the first object, `verdict` required). It throws `Error` whose message starts with the same fixed text as Python's: `the agent's answer is empty`, `no JSON object in the agent's answer`, `the agent's answer is not valid JSON`, `` the agent's JSON has no `verdict` ``. The detail after it (the decoder's position, Python's `repr`) is free-form.
  - `async verify(findings, ctx) → verified`: port of `verify.verify` (duplicate ids, every evidence kind, hypotheses downgrade, `docs_rule`, `likely_cause` change/code, verdict downgrades, `similar_past_bugs`, `verification` block, scrub with `ctx.secrets`). It keeps the Python check order and drop reasons, except telemetry reasons, which carry the server's text.
  - ctx contract: `{issue, issueCreated: Date|null, now: Date, repos: {app, deploy}, heads: {app, deploy}, secrets: string[], grafanaUrl, async commit(key, sha) → fullSha|null, async fileLines(key, path) → string[]|null, async prState(n) → {state: "MERGED"|"OPEN"|"CLOSED", title, url}|null, async issueState(n) → {state, title, url, labels: string[]}|null, async query(kind, expr, startIso, endIso) → {count}|{error}, async close()}`. `roots(prefer)` order and `_split_line`/`_find_quote`/`_norm` semantics as in verify.py. Python reads under a checkout root; here a path with `..` or a leading `/` resolves to "not found".
  - `makeVerifyCtx(gh, http, cfg, facts, now, secrets, {clock, deadlineMs}) → ctx`:
    - `commit` → `GET /repos/{r}/commits/{sha}` resolves the full sha, then `GET /repos/{r}/compare/{head}...{full}`: status `behind` or `identical` means the commit is in the head's history, as in the trigger's single-branch clones. Anything else → `null` ("commit not found in the checkouts", as Python).
    - `fileLines` → contents at `facts.app_head` / `facts.deploy_head`, base64-decoded and split like Python `splitlines()`. A deploy path resolves only where kagent's cone-mode sparse checkout of `{deployPath}` has files: under `{deployPath}/`, at the repo root, or directly in an ancestor directory (`base-apps/<file>`). Anything else → `null` with no request.
    - `prState` → `GET pulls/{n}` (`merged_at` → `MERGED`);
    - `issueState` → `GET issues/{n}`;
    - `query` → one MCP session on `TELEMETRY_URL`, opened on the first query and closed by `ctx.close()` (which `verify` calls in `finally`): `("query_loki_logs", {datasourceUid: "loki", logql, startRfc3339, endRfc3339, limit: 100})` or `("query_prometheus", {datasourceUid: "PBFA97CFB590B2093", expr, startTime, endTime})`. `isError` → `{error: text}`. `countResults` → `null` → `{error: "the telemetry server truncated the output; could not count it"}` (stricter than Python, which counts every frame). Else `{count}`. Links: Grafana Explore, a port of `grafana.explore_link`.

- [ ] **Step 1: Write the failing tests:**
  - `test_parse_findings_matches_trigger`: 6 accepted texts (bare object, ```json fence, a leading sentence, trailing text, nested braces in strings, unicode) equal the trigger's `parse_findings`;
  - `test_parse_findings_rejects`: `""`, `"Agent stopped due to max iterations."`, a JSON array, an object without `verdict`, JSON cut off mid-string. Each throws, and the message starts with the same fixed text as Python's message for that input.
  - `test_verify_matches_core`: for each case in `verify-cases.json`, build a temp app and deploy git repo with the case's files, commits and side branches. The Python side gets them the way the trigger clones them (`clone` from `scripts/bug_investigator_trigger.py` against `file://` URLs: single-branch app clone, blobless cone-mode sparse deploy clone). It runs `verify.verify` with `Ctx(mode="full", telemetry_mode="direct", …)`, `utc_now` frozen by `frozen_now`. Its runner serves `git` for real and `gh` from the case, and its `gquery` returns frames with the case's counts. `telemetry_mode="direct"` matters: the default `grafana` mode with no token skips every query. Run JS `verify` with `fakeVerifyCtx` built from the same case and the same `now`.
    Assert equal: kept evidence ids, hypothesis statuses and evidence ids, `verdict`, `docs_rule` kept or dropped, `likely_cause` (`change_verified`/`code_verified` truthiness, `unconfirmed`), `similar_past_bugs` numbers, `verification.verified`/`total`, and the drop reasons of non-telemetry items.
    Cases:
    - duplicate id, a bad SHA, a short SHA, an unknown commit, a commit only on an unmerged side branch (dropped on both sides);
    - a PR claimed merged but open; a rehearsal issue;
    - a missing file, a line out of range, a quote 3 lines away (kept) and 4 lines away (dropped);
    - a doc quote differing only in backticks and whitespace (kept);
    - a quote from `base-apps/taskboard.yaml` and one from a repo-root file in the deploy repo (kept: the cone holds them), and one from `base-apps/other/x.yaml` (dropped);
    - logql found/none, a promql zero-only result (dropped as nothing found), a `+Inf` series (found);
    - an out-of-scope query (fake returns `{error}`);
    - `likely_cause` with a supported hypothesis, and with no supported hypothesis (→ `unclear`);
    - `not_a_bug` without a verified docs rule (→ `unclear`);
    - a similar past bug that is this issue.
  - `test_make_verify_ctx_reads_at_the_heads` (fake http asserts `ref={head}` on contents calls; a deploy path outside the cone → `null` with no request);
  - `test_unreachable_commit_is_dropped`: compare answers `diverged` → `commit` is `null`;
  - `test_truncated_telemetry_is_not_counted`: an `expect: none` item whose server output ends in ` ...[truncated]` is dropped with the "could not count" reason;
  - `test_one_mcp_session_per_verify`: 4 telemetry items → one `initialize`, four `tools/call`, one `DELETE`, also when the third call throws.
- [ ] **Step 2: Run** `python -m pytest tests/n8n_bug_investigator/test_verify.py -q` → FAIL.
- [ ] **Step 3: Implement** `verify.js`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit.** `feat(n8n): verify bug-investigator findings link by link, as the core does (§T.152)`

### Task 7: Render, flag and post

**Files:**
- Create: `scripts/n8n-bug-investigator/lib/render.js`
- Test: `tests/n8n_bug_investigator/test_render.py`, `fixtures/verified-*.json` (uses Task 1's `fixtures/intermediate-steps.json`)

**Interfaces:**
- Consumes: `util.scrubText`, `gh`.
- Produces:
  - `renderComment(verified, {issueNumber, model, stats: {turns, duration_ms}, trail, contaminated: string[]}) → string`. It equals the Python `render.render(verified, "full", model, stats, "", trail)` after three replacements: the `full` marker → `<!-- bug-investigator mode=n8nagent -->`, `## 🔎 Bug investigation — full` → `## 🔎 Bug investigation — n8n AI Agent (POC)`, `<sub>full mode · ` → `<sub>n8n/bug-investigator-poc · `. With a non-empty `contaminated`, the line `⚠️ While it ran, this agent read kagent's fix pull request branch(es) <branches, comma-separated>; compare with care.` sits right above the `---` footer separator.
  - `trailFromSteps(steps) → {steps: [{n, narration, tool, input, result}]}`, from the AI Agent's `intermediateSteps` in the shape Task 1 recorded: `tool` = `action.tool` without the MCP node prefix (`github_`, `telemetry_`); `input` = `action.toolInput` minus n8n's routing key `tool`, as compact JSON; narration = the text parts of `action.messageLog[0].content` (≤ 300 chars; `action.log` is only `Calling <node name>`); result = `observation`, a JSON string `[{"response":[{"type":"text","text":…}]}]`, its texts joined, ≤ 200 chars (the raw string when it does not parse).
  - `sawKagentWork(steps) → string[]`: every `agent/taskboard-issue-<N>` branch, for any `N`, found in any `observation` or `toolInput` (as text), sorted and unique.
  - `async postComment(gh, repo, issueNumber, body) → "created"|"edited"`: our comment is the one carrying the `n8nagent` marker whose `user.login === await gh.login()`. PATCH it if found, else POST.

- [ ] **Step 1: Write the failing tests:**
  - `test_render_matches_core_relabelled` for three verified fixtures (likely cause with telemetry, not a bug with a docs rule, unclear with drops and `could_not_check`), with and without a trail;
  - `test_contamination_line_sits_above_the_footer`;
  - `test_trail_from_intermediate_steps` on Task 1's recorded fixture;
  - `test_flags_any_kagent_fix_branch`: `agent/taskboard-issue-37` in an observation while investigating #41 → `["agent/taskboard-issue-37"]`; the same inside a content-part array and inside a `toolInput` → found; `agent/taskboard-issue-` with no number → not;
  - `test_post_edits_only_our_own_comment`: a kagent comment, an Action `mode=n8n` comment, and an `n8nagent` comment by another login are never edited.
- [ ] **Step 2: Run** `python -m pytest tests/n8n_bug_investigator/test_render.py -q` → FAIL.
- [ ] **Step 3: Implement** `render.js`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit.** `feat(n8n): render and post the n8n investigation comment, flagging kagent fix PRs seen (§T.152)`

### Task 8: Read-only GitHub MCP server and the Vault identities

**Files:**
- Create: `base-apps/n8n/github-read-mcp.yaml`, `base-apps/n8n/github-read-mcp-secrets.yaml`, `base-apps/n8n/bug-investigator-secrets.yaml`, `scripts/provision-n8n-bug-investigator-vault.sh`
- Test: `tests/n8n_bug_investigator/test_github_read_mcp.py`, `tests/n8n_bug_investigator/test_credentials.py`

**Interfaces:**
- Produces: Service `n8n-github-read-mcp` (port 3000, `/mcp`), consumed by Task 9's MCP Client Tool.
- Produces: both credential files and the provisioning script (Decision 4). `bug-investigator-secrets.yaml`: SA `eso-n8n-bug-investigator`, SecretStore `vault-n8n-bug-investigator` (role `n8n-bug-investigator`), ExternalSecret `n8n-bug-investigator-gh` (key `n8n-bug-investigator`, property `gh-token` → secret key `token`). Task 9's env var reads that Secret.

- [ ] **Step 1: Write the failing tests:**
  - `test_tools_are_exactly_the_bug_investigator_github_reads`: `mcpAuthorization` allow list = `--tools` list = the six names = the `implementer` tools bound in `base-apps/kagent/agent-templates/bug-investigator.yaml`;
  - `test_server_runs_read_only` (`--read-only` in args);
  - `test_images_match_agent_docs_mcp` (agentgateway and github-mcp-server digests equal `base-apps/kagent/agent-docs-mcp.yaml`'s);
  - `test_checksum_config_matches_local_yaml` (sha256 of the `local.yaml` text, the convention of `tests/agent-identity/test_mcp_server_manifests.py`);
  - `test_serviceaccount_mounts_no_token`;
  - `test_network_policy_admits_only_n8n_and_prometheus`: ingress from pods `app: n8n` in namespace `n8n` on 3000, and from namespace `logging` on 15020 only;
  - `test_token_comes_from_its_own_store` (in `test_credentials.py`): ExternalSecret `n8n-github-read-mcp-token` → SecretStore `vault-n8n-github-read-mcp` (role `n8n-github-read-mcp`, SA `eso-n8n-github-read-mcp`), key `n8n-github-read-mcp`, property `github-token`, secret key `GITHUB_PERSONAL_ACCESS_TOKEN`;
  - `test_no_poc_credential_uses_the_shared_store` (in `test_credentials.py`): no ExternalSecret this plan adds uses `vault-backend` or Vault key `n8n`;
  - `test_pat_a_comes_from_its_own_store` (in `test_credentials.py`): ExternalSecret `n8n-bug-investigator-gh` → SecretStore `vault-n8n-bug-investigator` (role `n8n-bug-investigator`, SA `eso-n8n-bug-investigator`), key `n8n-bug-investigator`, property `gh-token`, secret key `token`;
  - `test_provision_script_matches_manifests` (in `test_credentials.py`): the script's paths, properties, policies, roles, SAs and namespace equal both credential files', and it reads the tokens only from `N8N_BUG_INVESTIGATOR_GH_TOKEN` and `N8N_GITHUB_READ_MCP_TOKEN`.
- [ ] **Step 2: Run** `python -m pytest tests/n8n_bug_investigator/test_github_read_mcp.py tests/n8n_bug_investigator/test_credentials.py -q` → FAIL.
- [ ] **Step 3: Write the manifests and the script.** Copy `base-apps/kagent/agent-docs-mcp.yaml`'s shape with the names `n8n-github-read-mcp`, namespace `n8n`, `nodeSelector: node.kubernetes.io/workload: application` (as n8n, no toleration), `envFrom` the new Secret, and a NetworkPolicy. Header comment: why it exists (Decision 2), and that the Service has no auth and the NetworkPolicy is what limits callers. `github-read-mcp-secrets.yaml` and `bug-investigator-secrets.yaml` each copy `agent-docs-mcp-secret-store.yaml`, `eso-agent-docs-mcp-serviceaccount.yaml` and `agent-docs-mcp-external-secret.yaml`. The script copies `provision-brave-search-vault.sh` (`--dry-run`, policy and role per path, tokens only from env, "NOT SET" guidance).
- [ ] **Step 4: Run** → PASS; `yamllint -c .yamllint.yaml base-apps/n8n/github-read-mcp*.yaml`; kubeconform per `AGENTS.md`; `bash -n` and `shellcheck` on the script.
- [ ] **Step 5: Commit.** `feat(n8n): a read-only GitHub MCP server and per-consumer Vault identities for the bug-investigator POC (§T.152)`

### Task 9: Workflow generator and n8n wiring

**Files:**
- Create: `scripts/n8n-bug-investigator/lib/finish.js`, `scripts/n8n-bug-investigator/nodes/{pick,gather,verify,render-post,agent-failed,finish}.js`
- Create: `scripts/gen-n8n-bug-investigator-workflow.py`, generated `base-apps/n8n/bug-investigator-workflow-configmap.yaml`
- Modify: `base-apps/n8n/deployments.yaml`, `AGENTS.md` (generator table), `.github/workflows/validate.yaml` (`--check` beside the other generators)
- Test: `tests/n8n_bug_investigator/test_finish.py`, `tests/n8n_bug_investigator/test_workflow.py`

**Interfaces:**
- Consumes: every `lib/*.js`; `fixtures/n8n-2.42.3-nodes.json` (typeVersions, parameter shapes), `fixtures/agent-error-item.json`; `spec.systemPrompt` of `base-apps/kagent/agent-templates/bug-investigator.yaml`; Task 1's runner timeout.
- Produces:
  - `outcome(item) → {add: string[], remove: string[], outcome: "investigated"|"failed"|"released"}` in `finish.js`:
    - success → add `n8n-investigated`, remove `n8n-investigating`;
    - `failure.spent` → add `n8n-investigation-failed`, remove `n8n-investigating`;
    - unspent failure → remove `n8n-investigating`.

    The Finish snippet applies it, then `releaseClaim`.
  - Item contract between nodes:
    - Pick (parent) → `{issue, filedBy, released, exhausted}`; it returns no items when idle, which ends the parent run (not saved).
    - Child input = Pick's item.
    - Gather → `{issue, facts, message, askedAt, failure?}`, with `failure = {stage: "prepare", spent: false, error}` (a `GatherTimeout` included).
    - Agent → `{output, intermediateSteps}`.
    - Verify → `{issue, verified, stats, trail, contaminated, failure?}` (`stage: "answer"` for parse errors, spent).
    - Render and post → `{issue, posted, verdict, verified_n, total, failure?}` (`stage: "post"`, spent).
    - Agent failed → `{issue, failure: {stage: "ask", spent: true, error}}`.
    - Finish → `{issue: n, outcome, verdict?, verified_n?, total?, contaminated?}`.
    - `Verify` and `Agent failed` read `issue` and `facts` from `$('Gather facts').item.json`, never from the agent's item: the error output carries only what `agent-error-item.json` shows.
    - Every Code node catches its own errors into `failure` and passes an existing `failure` through untouched.
  - Generator constants: `WORKFLOW_ID = "bug-investigator-n8n-poc"`, `RUN_WORKFLOW_ID = "bug-investigator-n8n-poc-run"`, `ACTIVE = False`, `NOT_BEFORE = None`, `ANTHROPIC_CREDENTIAL = {"id": "REPLACE_WITH_CREDENTIAL_ID", "name": "Anthropic — bug investigator POC"}`, `CONFIGMAP_BUDGET = 196_608`, plus the Global Constraints values. `ACTIVE = True` with `NOT_BEFORE = None` exits non-zero. CLI: `--repo-root . --write | --check`.
  - Parent graph (fixed node ids): `Every 2 minutes` → `Pick issue` → `Investigate issue` (Execute Workflow, `RUN_WORKFLOW_ID` from the database, waits for it). Settings: `{"executionOrder": "v1", "executionTimeout": 960, "saveDataSuccessExecution": "none", "saveDataErrorExecution": "all"}`; `active` = `ACTIVE`.
  - Child graph (fixed node ids):
    - `Called by the picker` (Execute Workflow Trigger, all input) → `Gather facts` → `Gathered?` (If: `failure` empty).
    - `Gathered?` true → `Investigate` (AI Agent, `onError: continueErrorOutput`, sub-nodes: `Claude Sonnet 5.5` [lmChatAnthropic], `github` and `telemetry` [mcpClientTool, selected tools only]). n8n prefixes every MCP tool with its node's name, so the model sees `github_get_file_contents`, `telemetry_query_loki_logs` and so on (Task 1); the short node names keep that readable.
    - `Investigate` success → `Verify` → `Render and post` → `Finish`.
    - `Investigate` error → `Agent failed` → `Finish`. `Gathered?` false → `Finish`.
    - Settings: `{"executionOrder": "v1", "executionTimeout": 900, "saveDataSuccessExecution": "all", "saveDataErrorExecution": "all"}`, no `callerPolicy` (Task 1: not needed after a CLI import); `active` = `ACTIVE`, because n8n 2.x refuses to call an inactive sub-workflow (Task 1).
  - Each Code node's `jsCode` = a bundle prelude of **only the `lib/*.js` modules its snippet requires, transitively** (each wrapped in a module function, with a local `require` that resolves `./name`) + its `nodes/*.js` snippet. Snippets read `$env.BUG_INVESTIGATOR_GH_TOKEN`, call `httpFromHelpers(this.helpers)`, pass the token as a scrub secret, and set `deadlineMs` to the node's start plus 240 s (the runner's 300 s task timeout − 60 s, Task 1).
  - ConfigMap `n8n-workflow-bug-investigator` (keys `bug-investigator.json`, `bug-investigator-run.json`; the file at most `CONFIGMAP_BUDGET` bytes); pod annotation `checksum/bug-investigator-workflow` = sha256 (first 16 hex) of both keys' text concatenated in sorted-key order.
  - `deployments.yaml`:
    - The volume `bug-investigator-workflow` mounted read-only at `/workflows-bug-investigator` in the initContainer.
    - After the existing pairs, one guarded line: `{ n8n import:workflow --input=/workflows-bug-investigator/bug-investigator-run.json && n8n import:workflow --input=/workflows-bug-investigator/bug-investigator.json && n8n update:workflow --id=bug-investigator-n8n-poc-run --active=<ACTIVE in lower case> && n8n update:workflow --id=bug-investigator-n8n-poc --active=<ACTIVE in lower case>; } || echo "bug-investigator POC import failed; the other workflows are unaffected" >&2`. Under the script's `set -e`, a POC failure can neither stop the other imports nor fail the initContainer.
    - Env `BUG_INVESTIGATOR_GH_TOKEN` from Secret `n8n-bug-investigator-gh` key `token`, `optional: true`.
    - A comment on each.

- [ ] **Step 1: Write the failing tests:**
  - `test_finish.py`: `test_finish_success`, `test_finish_answer_failure_is_spent`, `test_finish_prepare_failure_releases_lock`, `test_finish_never_touches_kagent_labels`.
  - `test_workflow.py`:
    - `test_generated_files_are_current` (`--check` exits 0);
    - `test_system_prompt_is_the_agenttemplates`;
    - `test_agent_tools_are_the_agenttemplates` (the two MCP Client Tools' selected tools = the template's two bindings);
    - `test_model_and_limits` (`claude-sonnet-5-5`, 16000, 40, `returnIntermediateSteps: true`, timeouts 900 and 960);
    - `test_graph_wiring` (both graphs as listed above, error output included);
    - `test_parent_does_not_save_idle_runs` (parent `saveDataSuccessExecution: "none"`, errors saved; child saves all);
    - `test_every_code_node_parses` (`node --check` on `async function f(){<jsCode>}` per node);
    - `test_each_node_bundles_only_what_it_requires` (e.g. `Pick issue` holds no `gather`, `verify` or `render` module);
    - `test_configmap_size_budget` (the generated file ≤ `CONFIGMAP_BUDGET` bytes);
    - `test_node_types_match_spike_fixture`;
    - `test_n8n_accepts_the_workflows` (Docker, mandatory): run the pinned n8n image, `n8n import:workflow` both files, `n8n export:workflow` both ids, and every node parameter survives;
    - `test_init_container_imports_and_sets_active` (the guarded line above, after the existing pairs, `--active=<ACTIVE>`);
    - `test_poc_import_cannot_block_the_pod`: run the initContainer script under `sh` with stub `n8n` commands where the POC import fails → exit 0, and the other workflows' imports still ran;
    - `test_checksum_annotation_is_current`;
    - `test_active_requires_not_before` (the generator refuses `ACTIVE = True` without `NOT_BEFORE`, and the Pick snippet passes `NOT_BEFORE` through);
    - `test_agent_failed_reads_gather_facts` (the snippets read `$('Gather facts').item.json`);
    - `test_gh_token_env_is_optional` (`BUG_INVESTIGATOR_GH_TOKEN` from Secret `n8n-bug-investigator-gh` key `token`, `optional: true`; its ExternalSecret uses `vault-n8n-bug-investigator`, key `n8n-bug-investigator`, property `gh-token`);
    - `test_n8n_secrets_is_untouched` (`base-apps/n8n/external-secrets.yaml` names no `bug-investigator` or `github-read` property);
    - `test_no_secret_in_generated_json` (no `ghp_`, `github_pat_`, `sk-ant-`).
- [ ] **Step 2: Run** `python -m pytest tests/n8n_bug_investigator/test_finish.py tests/n8n_bug_investigator/test_workflow.py -q` → FAIL.
- [ ] **Step 3: Implement** `finish.js`, the node snippets and the generator. Then edit `deployments.yaml` as listed. Run `scripts/gen-n8n-bug-investigator-workflow.py --repo-root . --write`.
- [ ] **Step 4: Run** → PASS, the Docker import test included; yamllint and kubeconform on the changed manifests.
- [ ] **Step 5: Commit.** `feat(n8n): the bug-investigator POC workflows, generated and imported inactive (§T.152)`

### Task 10: Comparison script, docs, ledger

**Files:**
- Create: `scripts/compare-bug-investigations.py`; Test: `tests/n8n_bug_investigator/test_compare.py`
- Modify: `base-apps/n8n/docs.md`, `base-apps/n8n/runbook.md`, `base-apps/kagent/taskboard-telemetry-mcp.yaml` (header comment only: "also called by n8n's bug-investigator POC workflow, one session per verify"), `SPEC.md`; regenerate with `scripts/gen-techdocs.py --repo-root .` and `scripts/gen-okf.py --repo-root .`

**Interfaces:**
- Produces: `parse_comment(body) → {mode, verdict, confidence, verified, total, change, code, turns, duration_s, contaminated} | None`; CLI `compare-bug-investigations.py --repo arigsela/taskboard --issues N [N …] [--json out.json]`. The token comes from `GH_TOKEN` or `gh auth token`. The script prints one markdown row per issue and system: issue, system (`kagent` | `n8nagent`), verdict, confidence, verified/total, change, code line, turns, duration, contaminated (the branches, or none).

- [ ] **Step 1: Write the failing tests:** `test_parses_kagent_and_n8n_comments` (bodies produced in-test by the core's render + the trigger's relabel, and by `renderComment`); `test_ignores_other_comments` (Action `mode=full`/`mode=n8n`, fix comments); `test_table_has_a_row_per_system`.
- [ ] **Step 2: Run** `python -m pytest tests/n8n_bug_investigator/test_compare.py -q` → FAIL.
- [ ] **Step 3: Implement the script.** Docs:
  - `docs.md`: a "Bug investigator (POC)" section with the Design table (short), the two workflows, the new server, the env var and its Vault identity, the labels and the 👀 claim, one-at-a-time and `NOT_BEFORE`, what n8n does not get, and the kill switch; add the new files to `sources`.
  - `runbook.md`, four symptoms:
    - an issue stuck with `n8n-investigating` (released with its 👀 after 20 min; check the child execution);
    - `n8n-investigation-failed` (find the child execution, fix, remove the label; an issue that hit 3 attempts too);
    - "the POC is idle although a bug is open" (a fresh lock on another issue, the issue predates `NOT_BEFORE`, or a stray 👀 from PAT A's user);
    - "stop the POC" (`ACTIVE = False`, regenerate, PR).
  - `runbook.md`, one how-to: rotate PAT A or PAT B (re-run `scripts/provision-n8n-bug-investigator-vault.sh` with the new token in env; ESO refreshes within 1 h or force-sync the ExternalSecret; roll the n8n pod for PAT A).
  - SPEC: row `T152|~|**n8n bug investigator POC (owner 2026-10-28: run an n8n AI Agent version in parallel with kagent/bug-investigator to compare behavior).** …|V112`.
  - SPEC invariant `V112:` the POC never adds or removes kagent's labels, posts only `mode=n8nagent` comments, opens no PRs or issues, runs at most one investigation at a time (fresh lock ⇒ idle; claim = PAT A's 👀, only a `201` proceeds), never picks an issue created before `NOT_BEFORE`, binds exactly the AgentTemplate's 11 tool names through server-side read-only GitHub and the scoped telemetry server, sends the AgentTemplate's systemPrompt verbatim, and reads each PAT through its own Vault path, policy, role, ESO ServiceAccount and SecretStore (never the `n8n` key or `vault-backend`); CI: `tests/n8n_bug_investigator/`.
- [ ] **Step 4: Run** the suite, `python3 scripts/validate-agent-docs.py --repo-root .`, `scripts/gen-techdocs.py --repo-root . --check`, `scripts/gen-okf.py --repo-root . --check` → all pass.
- [ ] **Step 5: Commit, then open the PR.** `docs(n8n): bug-investigator POC docs, comparison script and SPEC §T.152/§V.112`. The PR body lists the validation run, says the workflows ship **inactive**, and lists Ari's prerequisites from Task 11 (the provisioning script before merge).

### Task 11: Prerequisites (Ari), activation, rehearsal (Layer 2)

**Files:**
- Modify: `scripts/gen-n8n-bug-investigator-workflow.py` (`ANTHROPIC_CREDENTIAL["id"]`, `ACTIVE = True`, `NOT_BEFORE`), regenerated files; `base-apps/kagent/bug-investigator-trigger.yaml` (rehearsal only); this plan ("Layer 2 results")

- [ ] **Step 0 (Ari, before Task 1):** Create an Anthropic key for the POC with a workspace spend limit. Put it in `~/.config/n8n-bug-investigator-spike.env` (`ANTHROPIC_API_KEY=…`, mode 600) for the spike.
- [ ] **Step 1 (Ari, before the Task 10 PR merges):**
  - Create PAT A and PAT B (Decision 4; 90-day expiry).
  - Port-forward Vault and log in (the script's header says how), then `N8N_BUG_INVESTIGATOR_GH_TOKEN=<A> N8N_GITHUB_READ_MCP_TOKEN=<B> scripts/provision-n8n-bug-investigator-vault.sh` (`--dry-run` first). Without it, only the two new ExternalSecrets fail; `n8n-secrets` is untouched, n8n still starts (the env var is optional), and the MCP server waits for its Secret.
  - Create the labels `n8n-investigating`, `n8n-investigated`, `n8n-investigation-failed` on `arigsela/taskboard`.
- [ ] **Step 2 (Ari, after it syncs):**
  - Confirm `n8n-github-read-mcp` is Running, both ExternalSecrets are `SecretSynced`, and both workflows are imported inactive.
  - In the n8n UI, create the credential **Anthropic — bug investigator POC** with the Step 0 key and copy its id from the URL.
- [ ] **Step 3 (agent): activation PR.**
  - Set the credential id, `ACTIVE = True` and `NOT_BEFORE` = the time the PR is opened (UTC), regenerate, run the suite.
  - PR `feat(n8n): activate the bug-investigator POC workflow (§T.152)`; Ari merges.
  - Check, 10 minutes after the pod rolls: the parent shows active in the UI; no child execution ran; #30 and #37 got no `n8n-*` label and no 👀.
- [ ] **Step 4 (Ari + agent): rehearsal.**
  - Agent: PR `chore(bug-investigator): kagent memory off for the n8n POC rehearsal (§T.152)` adding `MEMORY_ENABLED: "0"` to the trigger CronJob's env; Ari merges. Confirm the next trigger run logs no `recalled` event.
  - Ari confirms which cases in `tests/eval-corpus/bug-investigator.yaml` are still live, opens one issue per case with its text, and labels `bug`. n8n takes them one at a time, so allow about 15 minutes per issue.
  - Open the first child execution and confirm both MCP Client Tools listed their tools and answered. A connection error to `taskboard-telemetry-mcp.kagent` means the n8n → kagent path is blocked (Task 1 tested it through a port-forward only): stop and report.
  - Wait for both comments on every issue.
  - Run `scripts/compare-bug-investigations.py --issues …`.
  - Add each run's model cost: n8n from Anthropic usage for the POC key; kagent from Grafana **Model Gateway & Budgets**, group `bug-investigator`.
  - Save the comments, then delete the rehearsal issues (handover §1, "Rehearsal issues leak answers").
  - Agent: PR removing `MEMORY_ENABLED` from the trigger; Ari merges.
- [ ] **Step 5: Record** the table, cost, failures and any contamination flags under "Layer 2 results" below. Commit `docs(n8n): bug-investigator POC rehearsal results (§T.152)`.

---

## Task 1 results

Run 2026-10-28 against `n8nio/n8n:2.42.3@sha256:240eaa2a…` in Docker Desktop, the telemetry server through
`kubectl port-forward`, and the POC Anthropic key. Every step passed.

| Step | Result |
|---|---|
| 2 Code-node HTTP | Pass. `https://api.github.com/zen` → 200, the missing repo → 404, neither throws. Response keys `body`, `headers` (lower-case), `statusCode`, `statusMessage`. `$env` readable. `timeout: 2000` to a black hole throws `timeout of 2000ms exceeded` (`ECONNABORTED`) after 2.0 s. |
| 3 MCP over Streamable HTTP | Pass. Session header **`mcp-session-id`** (36-char UUID). Every reply is **`text/event-stream`**; `notifications/initialized` → 202 with no body. `tools/list` = the five telemetry tools. Two `tools/call` on one session. `DELETE /mcp` → **202**; the session is then gone (400 `invalid session ID header`). Prometheus series come as `{"resultType", "series": [{"labels", "values": [{"time", "value"}]}]}`. |
| 4 Read-only GitHub server | Pass. Exactly the six tools, each `readOnlyHint: true`. |
| 5 AI Agent | Pass, with three findings (below). One real `query_prometheus` call. Both forced errors (MCP server unreachable; unknown model) arrive on the agent's error output. Recorded: `fixtures/intermediate-steps.json`, `fixtures/agent-error-item.json`. |
| 6 Sub-workflow and saving | Pass after one finding (below). The parent gets the child's items back. With `saveDataSuccessExecution: "none"` the parent's runs (busy and idle) are soft-deleted as they finish, so pruning removes them; the child's runs are kept. No `callerPolicy` needed. |
| 7 Node shapes, runner | typeVersions: scheduleTrigger 1.4, code 2, if 2.3, executeWorkflow 1.4, executeWorkflowTrigger 1.2, agent 3.1, lmChatAnthropic 1.6, mcpClientTool 1.4. Task runner: internal mode, **task timeout 300 s**, max concurrency 10 (image defaults; the cluster Deployment sets no `N8N_RUNNERS_*`). |
| 8 Import check | Pass. Skeletons of both workflows, wired as Task 9, import and export with every parameter, setting and connection intact. |

Findings that change later tasks (already written into them):

1. **MCP tool names are prefixed** with the MCP Client Tool node's name (`buildMcpToolName`, always on in 2.42.3):
   a node named `Taskboard telemetry` gives `Taskboard_telemetry_query_prometheus`. The AgentTemplate's systemPrompt
   names the bare tools (all 11, 1–2 times each). The nodes are named `github` and `telemetry`, the prompt stays
   verbatim, and the trail strips the prefix (Task 7, Task 9). The comparison has to bear this in mind: kagent's
   agent sees bare names.
2. **The agent's step shape:** `action.log` is just `Calling <node name>`; the model's text before a call is in
   `action.messageLog[0].content` (empty in the spike). `action.toolInput` carries an extra routing key `tool`.
   `observation` is a JSON string `[{"response":[{"type":"text","text":…}]}]` (Task 7).
3. **The error output item** keeps the input item's fields and adds `error`, a short message (`Error in sub-node
   Taskboard telemetry`, `The resource you are requesting could not be found`); the detail is on the failing
   sub-node. An MCP server that is down at connect fails the whole agent run.
4. **A sub-workflow must be active to be called** (`Workflow is not active and cannot be executed`). The child
   follows `ACTIVE` too (Task 9).

Also noted:
- n8n's Anthropic credential has a **Base URL** field (Decision 3).
- MCP Client Tool 1.4 caches its MCP session within an execution (`enableSessionCache`), so the agent's own tool
  calls do not open a session each.
- `n8n import:credentials` accepts a plain-text key and encrypts it at rest.
- `n8n execute` next to a running server needs its own `N8N_RUNNERS_BROKER_PORT` (5679 is taken).
- `update:workflow` warns that a running n8n must restart; in the initContainer n8n is not running yet.
- Not tested: the in-cluster path from namespace `n8n` to `taskboard-telemetry-mcp.kagent`. From git: the kagent
  waypoint's AuthorizationPolicy allows every port except the controller's 8083, and neither namespace has a
  NetworkPolicy on that path. Task 11 Step 4 still checks it live.

## Layer 2 results

_(appended by Task 11)_

## Open questions for Ari

1. The spend limit on the POC Anthropic key (needed at Task 11 Step 0, before Task 1).
2. Should kagent's fix hand-off stay on during rehearsals? Its PRs can be read by n8n's agent mid-run (flagged, not prevented). Default: stays on.
3. How long the POC runs before `ACTIVE = False`. Default: two weeks, then decide.

Decided 2026-10-28: issues opened before activation are skipped via `NOT_BEFORE` (Decision 7); kagent's memory is off
during rehearsals (Decision 10); each PAT gets its own Vault identity (Decision 4).
