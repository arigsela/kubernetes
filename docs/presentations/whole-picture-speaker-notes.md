# The whole picture

Eight illustrated infographics adapted from `docs/presentations/whole-picture-presentation-brief.md`, following its condensed sequence with the optional historical audit panel. Updated through PR 779 on 13 October 2026. Panels 03, 06 and 07 retain their original 12 October artwork and dated evidence. Status means implemented in this reference, not an assurance of production maturity. Runtime remains alpha.

## 01 — The whole picture

Source screens 03, 04, 06 and 08. Read from the tooling foundation through working paths to the agent harness and governance. The earlier concept, developer-workflow and agents-as-a-service decks each explain a slice of this map. The developer platform supplies a portal, delivery, resources, secrets, admission, data and telemetry. The agent layer adds context, capabilities, execution and evaluation, surrounded by identity, security and observability. Everything is represented by reviewed files and shared checks.

The harness has documentation, runbooks, shared prompts, memory and root contracts; a tool taxonomy and approval bindings; isolated execution with snapshots and a 30-day idle session TTL. Execution being built does not remove its alpha designation. Evaluation has eight golden questions and an on-demand judge, but is not an automatic model/prompt regression gate. Existing path implementations do not constitute a path registry. Non-human principals exist; caller authentication remains a distinct gap.

The brief attributes its three-layer frame and maturity vocabulary to Weave Intelligence and the Platform Engineering webinar “How to architect Agentic Development Platforms.” The public organizer listing confirms the session and its probabilistic/deterministic, identity, context and sandboxing themes: https://www.meetup.com/platform-engineers-los-angeles/events/314843859/ . That listing gives 18 June 2026, whereas the supplied brief says 19 June. The private transcript was not available for independent verification of the detailed layer model; those details are presented as the brief's mapping, not an independently verified quotation or endorsement. Attribution identifies the public framework, not the anonymous reference implementation.

## 02 — Three ways work moves

Source screen 05. Deterministic GitOps reconciles desired state; this is not a guarantee of healthy deployment. The probabilistic lane illustrates the judgment-bearing review cascade: deterministic rules first, then a classifier and escalation when uncertain. Its output is advisory. The hybrid path combines agent implementation, deterministic checks and human or policy promotion. Since 13 October, policy promotion is live for one constrained Renovate class; a model skip label alone is insufficient. Everything else remains owner-promoted. Fixes and retries are human-directed; there is no implemented token/retry budget. Infrastructure changes must be applied before merge.

## 03 — Governance surrounds the agent

Source screen 07. Identity asks who may act, security constrains the action and observability records what actually happened. Scoped service principals do not authenticate a human caller. Tool classification, approval gates, admission checks and CI work together. Audit calls, approval requests and traces support investigation. Argument redaction is best-effort, so an audit store must not be described as intrinsically free of sensitive data. No present-day zero-violation claim is made.

## 04 — Eight paths, one honest scorecard

Source screen 09, revised in PR 779. Six built, two partial, none missing. Dispatch and observation join context retrieval, validation, promotion and deployment as built paths. Implementation and remediation remain partial. Preserve canonical path numbering; the brief's prose placing implementation on the bottom row conflicts with its numbered table, so this deck follows the table.

The dispatcher was accepted on 12 October: a declared table maps alert titles to a read-only agent, a human or nobody, and CI requires complete coverage. Agent routes create a fresh session under the dispatcher identity, constrained by an hourly budget and fingerprint repeat window. Findings, still-running notifications or errors go through the existing notification service to chat, with secret scrubbing and a session reference for a human to continue. The original human alert path stays separate. Dispatch never answers an approval, never writes state, and is not platform-wide agent orchestration. Failing checks, issues and triage labels are not yet routed by this alert dispatcher. Sources: `base-apps/dispatcher/docs.md`, `base-apps/dispatcher/dispatch-table.yaml`, `scripts/dispatch.py`, and SPEC T105 acceptance.

Promotion is built with policy for one class and a human for the rest. The policy must parse from main. Eligible PRs come from the configured Renovate app in this repository and are not drafts; require `review:skip`, a deterministic skip shape re-derived from the diff (all docs, or only image/tag value changes totaling at most four changed lines), at most two files and no deny-listed paths. Every check run must finish successfully or be skipped; all commit statuses must be success, with none pending. Thus the showcase's shorthand “every check green” is not literally the implementation's predicate. A separate promoter App merges by rebase pinned to the evaluated head SHA, through a PR-only ruleset bypass; it cannot push to main. The author and promoter are separate principals. A PR cannot loosen its own policy. Every decision logs class/reasons. The live activation record reports an owner decision for PR 771, not evidence of a completed automatic merge. Sources: `docs/platform/promotion.md`, `.github/promotion-policy.yaml`, `.github/workflows/promote.yaml`, `scripts/promote.py`, SPEC T106/T107. Infrastructure still applies before merge.

Observation is built because alerts now start read-only investigations without a human prompt. Remediation remains partial: diagnosis can arrive unprompted, while actual fixes remain human-initiated and mutating operator tools remain approval-gated. In-cluster code writing and PR creation are still missing. This is a scoped path score, not a claim of full architecture completion.

## 05 — How much autonomy exists?

Source screen 10, revised in PR 779. Human-as-validator remains the daily mode. Level 3 remains partial: desk subagents, specialist handoffs and alert dispatch exist, but no agent orchestrates agents across the platform. General level 4 remains not yet, by choice, with one declared exception: the eligible Renovate class can merge and deploy by policy without a human. This is a configured and activated capability, not a claim that the dispatcher performs autonomous remediation or that an automatic merge was observed in the supplied acceptance record. Assess maturity per path.

## 06 — One recorded change

Source screen 11. Recorded 12 October 2026: CLI approval flow. Agent preparation covered nine files, +498/−31 and 49 tests. PR opened at 15:59Z and merged at 16:22Z: 23 minutes measures only that interval. Implementation and live acceptance sit outside it. The review classifier ran; its read probability of .47 exceeded the .30 read threshold. There were 27 check contexts, four skipped. This is distinct from the current validation workflow's 22 total jobs, including its change detector. An admission-test cluster startup timed out, then passed after a human rerun. The human merge used admin override. Live acceptance rejected without changing state or retrying; a new explicit request was approved. Documentation and memory were updated. A single recorded delivery is evidence of the operating process, not a safety guarantee or generalized speed metric.

## 07 — The audit that changed the controls

Source screen 12 and `scripts/agent-audit.py` module history. The historical query found 14 shell calls on 9 July 2026 and one delete call on 19 April 2026, with zero approval requests across that agent's audited history. Reconciliation had stripped the approval flag. The response combined a single taxonomy, admission plus CI enforcement, and daily comparison of policy against actual calls. Counts refer to this historical finding, not current usage. A configured gate needs evidence that it was exercised.

## 08 — Close the loop, in order

Source screen 13, revised in PR 779. Dispatcher node 03 is built as of 12 October; nodes 01, 02, 04 and 05 remain future work without delivery dates. Evaluation exists on demand but is not a regression gate. Caller authentication remains required before multi-user access. Dispatch was allowed to land first because it is read-only, uses a named session identity, and accepts the platform monitoring path; this does not close the gateway authentication gap. Failing checks, issues and triage labels remain future dispatch routes. In-platform implementation still needs proper identity and must produce PRs through the existing checks and promotion policy. Observation is already built; the remaining node 05 is remediation on signal, via proposed PRs or gated operator calls. Reuse existing secrets, policy, documentation, validation and audit.

Discussion: Which paths have owners? Which declared gates have never been queried? Who dispatches work today? What minimum identity, execution, evaluation and observability controls are required? Which path should earn the next level of autonomy?

## Delivery and scope

The PNGs are raster illustrations; the PDF embeds one image per landscape 16:9 page. Text is not selectable or natively editable. Exact generation prompts accompany the package. Labels and connectors were reviewed against the supplied brief, and the PDF was rendered and compared with the PNG sources. The roadmap routes evaluation through implementation before signal-driven remediation, making the gate prerequisite explicit. This artifact review does not certify the live platform or re-run its implementation tests.
