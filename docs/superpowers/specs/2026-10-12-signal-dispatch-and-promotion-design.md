# Signal dispatch and promotion policy — Design

Status: draft 2026-10-12, for review before any code. Implementation plan:
[`../plans/2026-10-12-signal-dispatch-and-promotion.md`](../plans/2026-10-12-signal-dispatch-and-promotion.md).
Frames onto: the eight core paths of the Weave Intelligence ADP model as scored in
`docs/presentations/whole-picture-presentation-brief.md` (screen 09). This design closes
**path 1 · dispatch work** (not yet → built), moves **path 7 · observe** from partial
toward built (an agent now looks at a signal unprompted, read-only), and moves
**path 5 · promote change** from "one human" to "promotion as code" for one declared class
of pull requests.
Predecessors: the agent action record (`scripts/agent-audit.py`, 2026-07-14), the
capability taxonomy (`2026-07-13-agent-capability-classes-design.md`), the A2A client
(`scripts/kagent_a2a.py`, 2026-10-11) and PR triage (`arigsela/claude-agents`
`2026-09-30-pr-review-toolkit-design.md`).

## Problem

Three things the platform cannot do today, each of which a company asks about first:

1. **Nothing routes a signal to an agent.** Grafana fires six alert rules into one n8n
   workflow that posts to Slack. Every investigation starts with a human reading Slack
   and typing a question to an agent. The agents that could look (`k8s-reader`, read-only)
   sit idle. The model's word for the missing piece is *dispatch*: "air traffic control"
   that sends a signal to an agent, a human, or nobody.
2. **The routing that does exist is implicit.** A pull request routes itself to triage; the
   orchestrator agent hands off to specialists; alerts go to Slack. None of it is declared in
   one place a reviewer can read, so "what happens when X fires?" has no file to point at.
3. **Every merge is a human.** 40 of the last 100 merged PRs were Renovate's; 17 of those
   carried `review:skip` (a docs-only or an image-tag-bump-only change, at most 4 lines,
   that triage found nothing to read in). The owner still clicked merge on each. There is
   no written promotion policy, so "what merges without a human?" is answered by habit.

## Goals and non-goals

**Goals**

- A declared dispatch table in Git: signal → route, with a stated reason per row, consumed
  by one small service. Humans keep every alert they get today; the table only adds agent
  investigations.
- Investigations run under their own caller identity (`dispatcher`) so the kagent UI and the
  agent action record attribute them, and they are bounded: a budget per hour and a
  repeat window per alert fingerprint.
- Only read-only agents can be routed to. Enforced in CI by reading the agent templates and
  the capability taxonomy, not by convention.
- A written promotion policy, read at the PR's **base** commit (so a PR cannot loosen its own
  promotion), that merges exactly one class of pull requests without a human and says why
  on the PR. Everything else stays with the owner, as today.
- Every dispatch decision and every promotion decision is a JSON log line with a reason.

**Non-goals (this increment)**

- A GitHub-check-failure signal source. The schema allows a second `source`; the first
  useful recipient for it is the in-platform PR author (path 3), which does not exist yet.
- Agents that change anything on a signal (path 8, remediate). Routes are read-only by rule.
- Chat-based approval of agent calls. Approvals stay in the UI and `ask-hk`.
- Auto-merging anything an agent wrote, anything touching Terraform, workflows, policy,
  identity, secrets, databases or the agent runtime. The deny list is in the policy file.
- Replacing n8n as the Slack sink. n8n stays the only holder of the Slack token.

## Design: the dispatcher (paths 1 and 7)

### Shape

A new in-scope app `dispatcher` (namespace `dispatcher`): one Deployment running
`scripts/dispatch.py` from a generated ConfigMap, the same embed-the-tested-script pattern as
the agent-audit CronJob (`scripts/gen-agent-audit-cronjob.py`), with `scripts/kagent_a2a.py`
mounted beside it. Python 3.12 standard library only, so the container starts without a
`pip install` and the table is JSON, not YAML.

```
Grafana contact point "n8n"  ──(integration 1)──►  n8n /webhook/grafana-alerts ──► Slack   (unchanged)
                             ──(integration 2)──►  dispatcher /signal/grafana
                                                      │  match the table, check the budget
                                                      ├─ route none / resolved / unmatched → log line only
                                                      └─ route agent:<name> → A2A SendMessage as X-User-Id: dispatcher
                                                                 │ (new session, read-only agent, 300 s)
                                                                 └─► n8n /webhook/agent-findings ──► Slack #oncall-alerts
                                                                     (answer, session id, UI link)
```

**The human path does not pass through the dispatcher.** The contact point gets a second
integration; Grafana delivers to both. If the dispatcher is down, Slack is unaffected and the
only loss is the investigation. This is an invariant (§V below), not a courtesy.

### The table

`base-apps/dispatcher/dispatch-table.yaml` is a ConfigMap whose `dispatch.json` key is the
table. The ConfigMap is the source of truth (the taxonomy uses the same pattern); tests parse
it; the pod mounts it at `/etc/dispatch/dispatch.json`.

```json
{
  "version": 1,
  "limits": {"investigations_per_hour": 4, "repeat_after_hours": 6},
  "rules": [
    {"id": "pod-cpu-starved", "source": "grafana",
     "match": {"alertname": "Pod CPU starved"},
     "route": "agent:k8s-reader",
     "prompt": "Grafana alert '{alertname}' is firing for pod {labels[namespace]}/{labels[pod]}: {annotations[summary]} Investigate with your read-only tools: describe the pod, read its recent events and the last logs, and say whether the pod's own CPU limit or node contention is the likely cause, quoting what you saw. Do not propose running changes; a fix is a pull request.",
     "why": "A starved pod needs a describe + events + logs read before anyone can act; the read-only investigator does exactly that and nothing else."},
    {"id": "agent-ungated-tool", "source": "grafana",
     "match": {"alertname": "Agent invoked a gated tool with NO approval"},
     "route": "none",
     "why": "A security finding about the agents themselves; a human reads the record behind the SELECT-only credential. No agent investigates the agents."}
  ]
}
```

Rules are checked in order; the first whose `match` keys all equal the alert's labels wins.
`route` is `none` or `agent:<AgentTemplate name>`. Agent routes need a `prompt`; `{labels[x]}`
and `{annotations[x]}` placeholders render from the alert, missing keys render as `?`.

**Completeness, fail closed.** A test requires every rule title in
`base-apps/logging/grafana-alerting.yaml` to have a row. A new alert rule cannot be added
without deciding, in the table, whether an agent looks at it. `none` with a `why` is a
decision.

**Read-only by construction.** A test resolves every `agent:` route to
`base-apps/kagent/agent-templates/<name>.yaml` and requires: every bound tool is in the
taxonomy's `read` list, and no binding has `requireApproval: true`. The dispatcher never
sends approvals or answers to a paused task; if an agent pauses anyway, the finding says so
and points at the UI.

**Bounded.** `investigations_per_hour` caps A2A sends across all rules; `repeat_after_hours`
suppresses a second investigation of the same alert fingerprint. Both are in-memory
(single replica; a restart resets them; documented). Only `firing` alerts are routed;
`resolved` notifications are logged and dropped.

### Identity and record

Every A2A send carries `X-User-Id: dispatcher`. Sessions therefore show in the kagent UI under
that identity, the daily audit attributes the tool calls to the session, and `scripts/ask-hk
--user dispatcher --context <id>` can continue one by hand. No Vault credential: the in-cluster
A2A route and the n8n in-cluster webhooks are unauthenticated by design (the Gateway allow-list
and the WAF are the perimeter), exactly as the eval harness and the portal call them.

### Observability

One JSON line per decision on stdout (`{"event":"dispatch","alertname":…,"rule":…,"route":…,
"decision":"investigate|none|resolved|unmatched|skip-budget|skip-repeat"}`) and one per
completed investigation (`{"event":"finding",…,"session":…,"state":…,"error":…}`), never the
answer text (it goes to Slack and lives in the session). Alloy ships stdout to Loki; the
runbook gives the LogQL.

### Failure modes, stated

- Dispatcher down: no investigations; Slack unchanged. Argo CD health and the readiness probe
  are the signal. No alert rule for it in this increment (an alert about the thing that
  routes alerts is a later decision).
- Agent timeout (300 s): the finding posts "still running" and where to look (the session is
  listed under `dispatcher` in the UI; its id only comes back with a reply, so there is no
  link yet); the answer lands in the session.
- n8n `agent-findings` unreachable: the finding is lost but logged with the session id.
- Budget exhausted: logged `skip-budget`; the human still has the Slack alert.

## Design: promotion policy (path 5)

### Shape

`.github/promotion-policy.yaml` declares two classes. `scripts/promote.py` decides which one
a PR is in and, for `auto`, merges it. (Implementation notes, 2026-10-12: the policy is read at
the base **ref**, `main`, rather than `base.sha`, so a tightening applies to open PRs at once;
`auto` additionally re-derives triage's deterministic skip shape from the diff, because the
classifier can also emit `skip`; commit statuses such as Renovate's stability gate count
alongside check runs; `renovate.json5` and Crossplane packages are deny-listed.) `.github/workflows/promote.yaml` runs the script after
`validate` or `pr-triage` completes on a PR, with a token minted from a dedicated GitHub App.

```yaml
version: 1
# Read at the PR's base ref (main): a PR cannot loosen its own promotion.
auto:                                   # merged by the promotion workflow, no human
  authors: ["arigsela-renovate[bot]"]   # API login; `gh` shows it as app/arigsela-renovate
  require_labels: ["review:skip"]       # triage found nothing to read: docs-only, or an image-tag bump of <= 4 lines
  deny_prefixes:                        # never automatic, whatever triage said
    - terraform/            # applies before merge, via Atlantis
    - .github/              # the workflows and policies themselves
    - appsets/
    - ansible/
    - node-config/
    - base-apps/vault
    - base-apps/postgresql
    - base-apps/kagent
    - base-apps/substrate
    - base-apps/admission-policies
    - base-apps/istio-
    - base-apps/dex
    - base-apps/cluster-rbac
    - base-apps/dispatcher
  max_changed_files: 2
  merge_method: rebase                  # the only method the main ruleset allows
owner:                                  # everything else; the triage label says how closely to read
  approvers: ["arigsela"]
```

### Decision

`auto` when **all** hold, else `owner`, with every failed condition listed as a reason:

1. The PR is open, not a draft, from this repository, and GitHub reports it mergeable.
2. `user.login` is in `auto.authors`.
3. Every label in `auto.require_labels` is present.
4. No changed file path starts with a `deny_prefixes` entry; at most `max_changed_files` files.
5. Every check run on the head SHA has completed with `success`, `skipped` or `neutral`; there
   is at least one check run; none is queued or in progress. (The ruleset has no required
   status checks; the script is that check.)
6. The policy was read from the PR's base ref (`main`), and it parsed.

On `auto`: `PUT /repos/{repo}/pulls/{n}/merge` with `merge_method: rebase` and the head SHA
(so a push between decision and merge fails the merge rather than merging something unseen),
then one sticky comment `<!-- promotion -->` naming the class, the policy commit and the
checks. On `owner`: a log line, no comment (the triage comment already says what to read).

### Identity

A new GitHub App **arigsela-promotion** (owner creates it; permissions: Contents RW, Pull
requests RW, Checks R, Metadata R; installed on this repository only), with its private key in
the Actions secret `PROMOTION_APP_PRIVATE_KEY` and its client id in the variable
`PROMOTION_APP_CLIENT_ID`. The app is added to the `main` ruleset's bypass actors with mode
`pull_request`, so it can merge without the one required approval but cannot push to main.
Renovate's own app is *not* used: the author of a change and the thing that promotes it should
be different principals, and the audit trail should show it.

The workflow triggers on `workflow_run` only, so it always runs the version of
`promote.yaml` on `main`, never the one in the PR. `.github/**` is `review:read` under
triage and in `deny_prefixes` here, so a change to the promotion rules is always read by a
human and never promoted automatically.

Renovate's config does not change: `automerge` stays off in the bot. Promotion is the
platform's policy, not the dependency bot's setting.

## Invariants (new §V rows)

- **V86** Every Grafana alert reaches the human sink (n8n → Slack) by a path that does not
  pass through the dispatcher. The contact point has two integrations; the dispatcher never
  forwards to `grafana-alerts`.
- **V87** The dispatch table routes only to agents whose every bound tool is `read` in the
  taxonomy and whose bindings carry no `requireApproval: true`. CI-enforced
  (`tests/dispatch/`); the table is complete over the alert rules (every title has a row).
- **V88** The dispatcher sends A2A as `X-User-Id: dispatcher`, opens a new session per
  investigation, and never sends a `tool_approval_response` or an `ask_user_response`.
- **V89** A pull request is merged without a human only when `scripts/promote.py` decides
  `auto` under the policy read at the PR's base ref (`main`), the diff re-derives as triage's
  deterministic skip shape, every check run and commit status is green, and the PR's author
  is not a human.
  `.github/**`, `terraform/**` and the `deny_prefixes` list are never `auto`.
- **V90** The promotion identity is its own GitHub App, distinct from Renovate's, installed
  on this repository only, bypassing the ruleset in `pull_request` mode only.

## Owner actions (not automatable from this repo)

1. Create the GitHub App `arigsela-promotion`, install it on `arigsela/kubernetes`, store
   `PROMOTION_APP_CLIENT_ID` (variable) and `PROMOTION_APP_PRIVATE_KEY` (secret).
2. Add the app to the `main` ruleset's bypass actors, mode `pull_request`.
3. After the dispatcher merges: send one synthetic `Pod CPU starved` payload to the
   dispatcher from inside the cluster and confirm the Slack finding, the session in the
   kagent UI under `dispatcher`, and the audit attribution (the plan's acceptance task).

## What this is not yet

Dispatch covers one source (Grafana) and one action (investigate). The next sources are a
failing check on a PR (needs the in-platform PR author, path 3) and a Falco detection routed
to an investigator once Falco's output is judged safe to summarise into Slack. The promotion
class is deliberately the narrowest one that exists; widening it is a policy PR, read by a
human.
