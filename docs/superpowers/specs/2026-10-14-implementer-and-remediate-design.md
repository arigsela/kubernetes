# Implement inside the platform, and remediate on signal — Design

**Date:** 2026-10-14
**Status:** approved in conversation 2026-10-14; plan to follow
**Follows:** `2026-10-13-callers-as-principals-and-eval-gate-design.md` (items 1 and 2 of the
whole-picture track) and `2026-10-12-signal-dispatch-and-promotion-design.md` (item 3)
**Fills:** the whole-picture brief's "what next" items 4 (implement inside the platform) and 5
(remediate on signal); ADP paths 3 (implement) and 8 (remediate), both PARTIAL on screen 09

## Problem

1. **No agent on the platform can change the repository.** A coding agent at the owner's desk
   writes most commits. In the cluster, every agent is read-only or gated: the operator agents
   can propose a mutating call that a human approves, but nothing can write a manifest, push a
   branch or open a pull request. Path 3 is PARTIAL on screen 09 for exactly this reason, and
   item 4 on screen 13 waited on item 2 (an identity of its own), which landed 2026-10-13.
2. **The diagnosis arrives unprompted; the fix is still typed by a human.** Since the
   dispatcher, an alert becomes an investigation and a finding in Slack. The finding often says
   what to change and where (a limit, a request). A human then opens that pull request by hand.
   Path 8 is PARTIAL because the first unprompted write has not been built.

### What shapes the design

- **Substrate sandboxes have no network egress.** The kagent `SandboxTemplate` type has no
  egress field and kagent creates standalone sandboxes with no egress policy; Substrate's egress
  contract routes every actor TCP connection through the policy enforcement point, which
  refuses what no policy allows. An in-cluster sandbox cannot clone from or push to GitHub at
  kagent 1.0.0-alpha11 / Substrate 0.5.0-alpha2. The brief's phrase "a sandbox template" is
  therefore not where the git work can happen today.
- **The read-only GitHub MCP server is already in the cluster.** `agent-docs-mcp` runs
  `ghcr.io/github/github-mcp-server` v1.14.0 in stdio mode under kmcp with a token from Vault,
  and the knowledge agent binds its read tools. The same server has the write toolsets
  (branches, file commits, pull requests) and, in stdio mode, mints GitHub App installation
  tokens itself (`--app-id`, `--app-installation-id`, `--app-private-key-path`).
- **The taxonomy has three classes and `write` must sit behind approval.** Opening a pull
  request mutates GitHub, never the cluster or an account. It fits none of the three.
- **The dispatcher may route only to read-only agents (V87).** By test, not only by prose.

## Goals and non-goals

Goals:
- An in-cluster agent that can read this repository, edit a manifest on a branch and open a
  pull request **under its own identity**, through the same triage, checks and promotion policy
  as every other PR. A human merges; nothing the agent does is a production change.
- The first unprompted write: the two resource alerts already investigated today ("Container
  near memory limit", "Pod CPU starved") route to that agent, which opens the PR when the
  evidence says the limit or request is too low, and only reports otherwise.
- Every existing gate stays where it is: the IP allow-list, verified callers (V91), the capability
  taxonomy at admission, the dispatcher's budget and dedupe, triage, the 22 checks, the main
  ruleset, promotion's two closed classes, the audit record.
- The whole-picture brief is rescored honestly when this lands: paths 3 and 8 BUILT, items 4
  and 5 on screen 13 done, the ladder's level-3 line updated.

Non-goals:
- A gated operator call for the "leak" case (the remediator handing off to k8s-agent for a
  rollout restart). Named follow-up; the approval mechanism already exists.
- Waiting on CI inside the agent's turn and fixing up the PR. The PR's checks are visible on the
  PR; a failing check is a signal a later table row could route.
- Routing failing checks, issues or triage labels to the implementer.
- A Substrate sandbox as the workbench. Revisit when kagent exposes an egress policy for
  sandboxes; the agent and tool contract below would not change, only where the tools run.
- Any agent merging anything. Promotion's classes are not widened.

## Design

One new agent, one new tool server, one new identity, one taxonomy class. Nothing else on the
platform changes shape.

### Identity: GitHub App `arigsela-implementer`

- Created by the owner like `arigsela-promotion`: homepage the repo URL, no webhook, installed
  on `arigsela/kubernetes` only. Permissions: **Contents read/write, Pull requests read/write,
  Metadata read, Checks read**. **No Workflows permission**: GitHub itself refuses any commit
  that touches `.github/workflows/`, independent of prompt and taxonomy.
- The merger (`arigsela-promotion`) and the author (`arigsela-implementer`) are never the same
  identity, and neither is Renovate's app (V90 holds).
- App ID, installation ID and private key live in Vault at `k8s-secrets/implementer-github-app`
  (properties `app-id`, `installation-id`, `private-key`), written by
  `scripts/provision-implementer-vault.sh` (same shape as the eval gate's script: `VAULT_ADDR`,
  the CLI's own login, key read from a file path, never from an argument or the environment).
  A per-consumer Vault role `implementer-mcp` bound to ServiceAccount `eso-implementer-mcp` in
  `kagent`; SecretStore `vault-implementer-mcp`; ExternalSecret rendering Secret
  `implementer-github-app` in `kagent` (`templates/agent-identity/README.md` pattern).

### Tool server: `implementer-mcp`

- kmcp `MCPServer` `implementer-mcp` in `kagent`, image `ghcr.io/github/github-mcp-server`
  v1.14.0 by digest, `transportType: stdio`, placed like `agent-docs-mcp` (infrastructure node).
  Args: `stdio --app-id $(APP_ID) --app-installation-id $(INSTALLATION_ID)
  --app-private-key-path /var/run/implementer/private-key --tools <explicit list>`. The key is a
  volume from the Secret; the ids are env from the same Secret. No `GITHUB_PERSONAL_ACCESS_TOKEN`.
- The explicit tool list is the whole surface. Read: `get_file_contents`, `search_code`,
  `list_commits`, `get_commit`, `list_branches`, `list_pull_requests`, `pull_request_read`.
  Propose: `create_branch`, `create_or_update_file`, `push_files`, `create_pull_request`,
  `update_pull_request`. **Never loaded:** `merge_pull_request`, `update_pull_request_branch`,
  `pull_request_review_write`, `add_comment_to_pending_review`, `delete_file`,
  `create_repository`, `delete_repository`, `fork_repository`, and every other toolset.
- `RemoteMCPServer` `implementer` registers it (`http://implementer-mcp.kagent:3000/mcp`,
  STREAMABLE_HTTP) so AgentTemplates can bind the tools by name.

### Taxonomy: a fourth class, `propose`

- `base-apps/admission-policies/agent-capability-taxonomy.yaml` gains a `propose` list holding
  exactly the five proposal tools above. Definition: *changes a proposal surface only, a branch
  or a pull request on this repository; never a cluster, an account or a merge.* Ordered
  read < propose < write < destructive.
- Template classes become `read`, `propose`, `write`, `admin`. Rules, extending the generator's
  1–7: a `read` template binds only read tools; a `propose` template binds read and propose
  tools; `write` and `admin` templates may also bind propose tools. Propose tools may sit in a
  binding **without** `requireApproval: true` (the merge is the human gate); write and
  destructive tools still must not. Delegation (rules 6–7): `read` may not sub-agent `propose`
  or higher; `propose` may not sub-agent `write` or `admin`.
- Consumers updated together, as today: `scripts/gen-agent-capability-policy.py` (the fourth
  CEL list and the two new denials), `scripts/validate-agent-capability.py` (the class → allowed
  classifications map gains `propose`; `MUST_APPROVE` stays `{write, destructive}`),
  `scripts/agent-audit.py` (the "ungated
  mutating call" finding ignores propose tools; they are recorded like every call), and the
  generated `agent-capability.yaml`.
- The seven read tools above that are not yet in the `read` list join it.

### Agent: `implementer`

- `AgentTemplate` `implementer` in `kagent`, label `capability.homelab/class: propose`,
  `anthropic-claude-sonnet-5-5`, Harness `kagent-stateless` (no memory: each task starts from
  the repo and the alert, nothing carried over). `Agent` `implementer` references both.
- Bindings: the `implementer` read tools (ungated), the `implementer` propose tools (ungated,
  class propose), `agent-docs` read tools, and k8s-reader's eight read tools from
  `kagent-tool-server` (ungated). No sub-agents.
- The prompt is the contract, and the tests pin its load-bearing lines:
  - Read `base-apps/<app>/docs.md` and the manifest's comments before changing a number.
  - Branch `agent/<slug>` from `main`; one commit; Conventional Commits title with a scope;
    body: the evidence quoted (lines, never values that look like secrets), what changed and
    why, "validation: CI" and the alert id or the requester's words.
  - At most three files. Never `.github/`, `terraform/`, `.claude/`, `CLAUDE.md`, `AGENTS.md`,
    `templates/`, `SPEC.md`, any `external-secret*.yaml`, `secret-store.yaml` or file holding a
    credential reference.
  - If a branch or an open PR for the slug exists, update that branch and report its link.
    Never open a second PR for the same slug.
  - Never merge, close, review or comment on a PR. Report the link and stop.
- Two entry points, one agent: a human via `scripts/ask-hk --agent kagent/implementer "..."`
  (the session owned by their Dex identity), the dispatcher via the table (owned by
  `system:serviceaccount:dispatcher:dispatcher`).

### Dispatch: the first unprompted write

- `pod-cpu-starved` and `container-near-memory-limit` route to `agent:implementer`. The prompt
  is the investigation prompt of today plus the decision and the action:
  - Evidence: describe the pod, last state and restart count, events, last 100 log lines, the
    manifest in git, the app's `docs.md`.
  - Decide: **limit too low** = steady use near the limit, no OOMKilled, no restart growth
    (CPU: throttled with steady demand). **Leak or contention** = growth over time, OOMKilled,
    or node pressure. Only the first case becomes a PR.
  - Act: raise the limit (or request) by half, rounded to a clean value, keeping the
    request-to-limit ratio, with a comment beside the number naming the alert and date. Then
    the PR, then the link in the answer. In the second case: the finding, no PR, and the
    sentence "no pull request: <reason>".
- V87 amended: a route may name a template whose every bound tool is in `read` or `propose`,
  with no `requireApproval: true` binding. `tests/dispatch/test_table.py` enforces it.
- One new limit in the table, `open_prs_max` (default 3): before an agent route whose template
  is class `propose`, the dispatcher counts the implementer app's open PRs
  (`GET /repos/arigsela/kubernetes/pulls?state=open`, filtered by the app's login) and routes to
  `none` with `reason: open_prs_max` when at or above it. Read-only call, unauthenticated
  (the repo is public), cached for five minutes; a failed count does not block (logged, routed).
- The finding path is unchanged: answer (with the PR link) → n8n `agent-findings` → Slack.

### Where the gates stay

| Gate | Mechanism | Unchanged? |
|---|---|---|
| Caller identity | V91: dispatcher SA token or Dex token, verified by the waypoint | yes |
| What the agent may bind | taxonomy at admission (fail-closed), extended with `propose` | extended |
| What the app may write | GitHub App permissions: one repo, no Workflows, no admin | new |
| What a PR may change | prompt deny list, three-file cap, tests on the prompt | new |
| Who merges | main ruleset (PR, rebase-only); promotion's two classes never admit an app that is not Renovate; a test pins `app/arigsela-implementer` → `owner` | yes, pinned |
| How often | dispatcher `investigations_per_hour`, `repeat_after_hours`, new `open_prs_max` | extended |
| Record | agent audit records every call; findings log links alert → session; PR body carries the alert id | yes |

### Failure handling

- **Branch or PR exists:** update the branch, report the existing link (prompt; a test on the
  prompt text).
- **GitHub refuses the write** (token not minted, missing permission, a workflows path): the
  tool error is reported in the finding; no retry inside the turn. The MCP pod's readiness and
  the dispatcher's finding log cover a dead tool server.
- **Wrong fix:** CI, triage and the human reading the diff. The PR cannot change the checks,
  the policy or the templates: by prompt, by taxonomy, by GitHub permission.
- **Count failure for `open_prs_max`:** log and route (the investigation budget still bounds
  it); never fail closed on a GitHub outage for a read-only investigation.

## Testing

- `tests/agent-capability/`: generator and validator cases for `propose` (read template
  binding a propose tool → denied; propose template binding a write tool → denied; propose
  template binding propose tools ungated → allowed; the generated policy carries the list;
  delegation rules). Audit: a propose tool invoked without an approval event is not a finding.
- `tests/dispatch/`: V87 amended; a row routing to a template with a write tool fails; the
  `open_prs_max` pre-check with a fake count (below, at, above, count failure).
- `tests/implementer/` (new): the MCPServer's `--tools` list equals the union of what the
  template binds from `implementer`; the propose tools bound equal the taxonomy's `propose`
  list; no excluded tool name appears in any implementer manifest; the template's class is
  `propose`; the Secret keys the ExternalSecret renders match the server's flags and mounts;
  the prompt contains each load-bearing line (deny list, three files, no second PR, never merge).
- `tests/promotion/`: a PR by `app/arigsela-implementer` decides `owner` under both classes.
- Live acceptance (recorded in SPEC): (a) `ask-hk --agent kagent/implementer` asks for a
  harmless comment change; a PR authored by the app appears, triage labels it, promotion leaves
  it, the owner closes or merges it. (b) a recorded "Container near memory limit" alert
  replayed through the dispatcher produces a PR whose body quotes the evidence; Slack carries
  the link.

## Rollout (each PR lands on its own; order matters)

1. Taxonomy class `propose`, generator, validator, audit, tests. No runtime change.
2. Vault provisioning script, Vault role, SecretStore, ESO ServiceAccount, ExternalSecret.
   **Owner step** between 2 and 3: create and install the GitHub App, run the script with the
   key file. Nothing depends on it until step 3.
3. MCPServer, RemoteMCPServer, AgentTemplate, Agent, `tests/implementer/`. Acceptance (a).
4. Dispatch table rows, `open_prs_max`, V87 amendment, dispatcher tests. Acceptance (b).
5. Docs: kagent and dispatcher `docs.md`/`runbook.md`, the taxonomy header, CLAUDE.md's
   promotion paragraph ("an agent's PR never merges itself"), SPEC rows, the whole-picture brief
   rescored.

## Invariants (new §V rows)

- **V94:** a tool in taxonomy class `propose` changes only a branch or a pull request on
  `arigsela/kubernetes`; it may be bound without `requireApproval`; `read` templates may not
  bind it; `write`/`destructive` still require approval. The implementer MCP server loads
  exactly the taxonomy's `propose` list plus named read tools and nothing else (test).
- **V95:** no PR authored by an in-cluster agent merges without a human: promotion's classes
  admit only Renovate's app and the owner; `app/arigsela-implementer` decides `owner` (test);
  the implementer binds no merge, review or comment tool (test); the main ruleset requires a PR.
- **V87 (amended):** the dispatch table routes only to templates whose every bound tool is in
  `read` or `propose` with no `requireApproval: true` binding.

## Owner actions (not automatable from this repo)

- Create GitHub App `arigsela-implementer` (homepage `https://github.com/arigsela/kubernetes`,
  no webhook; permissions Contents RW, Pull requests RW, Metadata R, Checks R; no Workflows),
  install it on `arigsela/kubernetes`, note the App ID and installation ID, download the private
  key once, run `scripts/provision-implementer-vault.sh --key-file <path>`, delete the file.
- Merge or close the acceptance PRs.

## What this is not yet

- The leak case still ends in a finding; the gated rollout restart is the next table row.
- One kind of change (a resource number in one manifest). Any other change arrives through a
  human prompt to the same agent, under the same contract.
- Level 4 on the ladder is unchanged: a signal becomes a *proposal*, never a shipped change.
