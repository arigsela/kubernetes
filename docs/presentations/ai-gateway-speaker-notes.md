# How our AI gateway works — companion notes

Prepared for Ari Sela on 10 November 2026. Configuration reviewed at repository revision `02dd5725c1833e5cde19f3cf6e65e1f679c2dbad` (3 November 2026). This is a repository-based explanation, not a live measurement or a health/security assessment.

## Format and reading order

Three landscape illustrated-notebook panels were chosen under the brief's readability fallback: panel 1 covers regions 1–2; panel 2 covers regions 3–4; panel 3 covers regions 5–7. The PDF contains raster pages, not editable diagrams, selectable text, or a tagged accessible document. This file supplies a text alternative.

Owner-approved attribution: **Ari Sela**. Each footer reads: “Ari Sela · A homelab reference platform · configuration as of 3 November 2026” / “drawn from the repository, not a live measurement”.

## Visible copy and diagram text alternative

The following preserves the brief's visible copy and describes the illustrations. Additional visible diagram labels are “One shared limit”, “request”, “answer”, and “For the ten groups shown here.” Page numbers are 1 / 3 through 3 / 3. The scope label limits the pathway caption to the ten configured groups.


Seven regions, read top to bottom, quoted from the source brief (so the drawing instructions
are in the brief's words).

### Region 1. Title

- Title: **How our AI gateway works**
- Subtitle: A small working example of "governance follows the work", the idea in Human Driven
  AI's *AI Routers Are Changing AI Governance*.

### Region 2. Why we built it

A short dated note, visually a pinned paper note with a magenta marker (magenta marks a risk).

- Heading: **One limit stopped everything**
- Copy: On 14 October 2026 every AI agent here shared one spending limit. It ran out, and all
  of them stopped at once.
- Takeaway line: So each group of agents got its own daily limit, enforced at one checkpoint.

Illustration: a row of small agent figures all greyed out behind one shared meter at empty.

### Region 3. The pathway (the central diagram, the largest region)

A horizontal process, left to right, five nodes. Arrows show the direction a request travels.

1. **A person or an alert** asks for work.
2. **An agent**, drawn inside a sealed box labeled *sealed workspace*. A small crossed-out key
   beside it, labeled *holds no real key*.
3. **The gateway**, drawn as a checkpoint or gate. This is the focal node; make it the largest.
4. **The AI model provider**, outside a dotted line labeled *outside our platform*.
5. Below node 2, a second branch going down: **Tools** (look things up, propose a change,
   change something). The branch to *change something* passes through a second, smaller gate
   labeled **a person says yes** (detail in region 5).

Arrow directions: 1 → 2 → 3 → 4, and the answer returns 4 → 3 → 2 → 1 as one lighter return
arrow. The tool branch leaves node 2, not the gateway: the gateway governs model calls, and
the approval gate governs actions. Do not draw tools behind the gateway.

Caption under the diagram: **Every model request takes this one path.**

### Region 4. What the gateway checks

Three numbered checks drawn at the checkpoint, then what it does, then what it records.

- **1. Who is asking?** Each group of agents is recognized by its own pass.
- **2. Is this model approved for them?** Each group has a short list of models it may use.
- **3. Is there budget left today?** Each group has its own daily limit.
- Then: **Only now does the gateway add the real key and send the request on.**
- A small ledger illustration: **Recorded for every request: which group, which model, how
  much it cost. Not recorded: the question or the answer.** Kept 30 days.

Beside it, a small meter panel:

- Heading: **When a group's budget runs out**
- 80% used: a warning goes to the owner.
- 100% used: that group's requests are refused until midnight (UTC).
- **Every other group keeps working.**

Illustration: ten small meters in a row, one at empty with a closed gate, nine untouched. Ten
is the real number of groups; draw exactly ten. Label the range: *daily limits from $0.50 to
$10 per group*.

### Region 5. Match the model to the work, and keep a person on the risky steps

Two side-by-side panels.

Left panel, **The right size of model for the job**:

- Looking things up, simple suggestions → a smaller, cheaper model.
- Investigating problems, writing fixes → a more capable model.
- Note in yellow: People choose the model for each agent in advance, in a reviewed file. The
  gateway does not choose; it only refuses a model that is not on the list.

Right panel, **Three kinds of action, one needs a yes**:

- **Read**: look at information. No approval.
- **Propose**: draft a change for a person to accept or reject. No approval to draft; a person
  must accept it before anything changes.
- **Change or delete**: waits until a named person approves that exact action. The decision is
  recorded.

### Region 6. The rules are written down

One short strip.

- **The limits and lists live in one reviewed file.** Changing one is a change request someone
  approves. There is no override switch.

### Region 7. Where this matches the article, and what we have not built

Two columns. Left in blue with check marks, right in a plain outlined box (not magenta, not
crossed out: these are choices, not failures).

**What we share with the article**

- Governance follows the pathway, not a list of tools.
- Policy is a technical control, not a reminder to be careful.
- We started with a real problem, then chose the technology.
- Smaller models for simpler work.
- A person approves high-risk actions.

**What we chose not to build yet**

- A router that picks the model per request.
- Scanning requests for sensitive information.
- Switching to a second provider if the first is down.
- One provider, one small platform: these were not our problem yet.


## Glossary (engineering references, not image copy)


These terms are engineering references and do not appear in the illustrations.

| On the image | In the repository |
|---|---|
| the gateway | A standalone agentgateway (v1.7.0) in namespace `model-gateway`, in front of the Anthropic API. `base-apps/model-gateway/` |
| a group of agents | One entry under `groups:` in `base-apps/model-gateway/budgets.yaml`: the agents that share one virtual key and one budget |
| its own pass | The group's virtual key, injected into the request by the sandbox's egress proxy, so the agent's sandbox never holds it |
| the real key | The upstream Anthropic API key used by this pathway; the gateway mounts it. Separate copies and retained direct-key configurations exist outside this pathway, as qualified below |
| sealed workspace | A gVisor sandbox per agent session (Agent Substrate) |
| approved models | `allowedModels` per key in `base-apps/model-gateway/configmap.yaml` |
| daily limit | `daily_usd` per group; a 24 h window from 00:00 UTC; `onBudgetExceeded: Block` |
| refused | HTTP 429 `budget_exceeded` from the gateway |
| a warning goes to the owner | Grafana alerts to Slack at `warn_at: 0.8`, on block, and when the gateway is down |
| smaller / more capable model | Claude Haiku 5.5 / Claude Sonnet 5.5 |
| read / propose / change or delete | The capability classes in `base-apps/admission-policies/agent-capability-taxonomy.yaml` |
| a person says yes | `requireApproval: true` on the tool binding in an AgentTemplate, e.g. `base-apps/kagent/agent-templates/restarter.yaml` |
| one reviewed file | `budgets.yaml`; `scripts/gen-model-budgets.py` generates the gateway config from it and CI fails if it is stale |


## Evidence and status

Paths below are relative to the repository root, at the pinned revision. Read manifests and documentation support configured intent; they do not establish current runtime behavior. The dated incident is a recorded observation, not an incident independently observed during this task.

| Claim on the image | Status | Source |
|---|---|---|
| On 14 October 2026 one shared spending limit ran out and every agent stopped | Dated recorded observation | `SPEC.md` §T.119; `docs/superpowers/specs/2026-10-23-model-gateway-budgets-design.md`, "Why" |
| Each group has its own daily limit; over it, only that group is refused until 00:00 UTC | Confirmed configuration | `base-apps/model-gateway/budgets.yaml`, `configmap.yaml`, `docs/index.md` |
| Ten groups, limits from $0.50 to $10 a day | Confirmed configuration; recounted ten groups before rendering | `base-apps/model-gateway/budgets.yaml` |
| Warning at 80%, alert on block | Confirmed configuration | `budgets.yaml` (`warn_at`), `base-apps/model-gateway/docs/index.md`, "Alerts" |
| The agent's workspace holds no real provider key; the gateway adds it | Confirmed configuration | `base-apps/model-gateway/docs/index.md`, "How a model call flows" |
| Each group may use only its listed models | Confirmed configuration | `configmap.yaml` (`allowedModels`) |
| The record holds group, model and cost, not the question or answer; kept 30 days | Confirmed configuration | `base-apps/model-gateway/docs/index.md`, "What it keeps"; `request-log-retention.yaml` |
| Smaller model for lookups and suggestions, larger for investigation and fixes | Confirmed configuration | `budgets.yaml` (`models:` per group) |
| Change-or-delete actions wait for a person's approval; the decision is recorded | Confirmed configuration | `base-apps/admission-policies/agent-capability.yaml`; `base-apps/kagent/agent-templates/restarter.yaml`, `k8s-agent.yaml`; `docs/presentations/safe-agent-deployment-speaker-notes.md` panels 2–3 |
| Proposing a change needs no approval, but a person must accept it | Confirmed configuration | `base-apps/kagent/agent-templates/implementer.yaml`; same speaker notes, panel 2 |
| Limits change only through a reviewed change request; no override | Confirmed configuration | `budgets.yaml` header; design spec, Goals 4 |
| Routing, sensitive-data scanning and failover were not built | Confirmed (stated non-goals) | Design spec, "Non-goals" |


### Verification details and qualifications

- Parsed `budgets.yaml` again: exactly ten group entries (`hk`, `build`, `implementer`, `restarter`, `docs-checker`, `bug-investigator`, `k8s-reader`, `skill-suggester`, `dcc`, `ui-default`); all have `gateway: true`. Minimum daily limit $0.50, maximum $10. The UI-default entry has no named agents in git; ten groups does not mean ten currently running agents.
- Checked generated configuration for per-key model lists, daily windows and `onBudgetExceeded: Block`. Checked deployment for agentgateway v1.7.0 and retention manifest for deletion of request-log entries older than 30 days.
- The 80% warning is based on estimated spend over a rolling 24 hours. Gateway enforcement uses the UTC calendar day. The warning can therefore arrive early or late relative to the gateway's daily counter; it is not a precise instantaneous 80% trigger on that counter. Sources: budget header, gateway guide “Alerts”, design spec “Calls I made”.
- The incident wording is supported by the dated design-spec “Why” account and repository ledger reference T.119. Historical design tables have fewer groups and older limits; the image uses the checked configuration instead.
- Model sizing is a simplified illustration. Lookup/suggestion groups use the smaller model, while knowledge work and investigations can use the more capable model. `docs-checker` permits both models and selects by graph node in its runtime. The gateway itself does not select a model.
- Approval policy requires gated bindings for write/destructive tools. The restarter records the human decision and its identity; the implementer can draft branches and proposals without approval but does not expose merge tools. Source comments and companion deck scope are evidence of intended behavior, not a newly exercised approval test.
- The reviewed-file statement expresses the intended configuration workflow. It is not a claim that repository access controls, administrators, or all privileged paths have been independently audited.

## Scope and limitations for the reader

This is one person's homelab reference platform. Its agent runtimes, kagent and Substrate, remain alpha software. The illustration makes no claim of enterprise readiness, comprehensive security, savings, availability or completeness.

The pathway caption applies to the ten configured groups. It does not claim that the gateway is impossible to bypass or that every project uses it. Two older model configurations still reference the direct provider credential, other projects use direct provider calls, and the evaluation judge has a separate copy of a provider key. “The gateway adds the real key” describes the illustrated path; it does not assert that no other copy exists anywhere.

Daily limits are checked before a request and charged after it finishes. Requests already in flight can exceed the stated limit. A budget is not an exact invoice cap. The gateway uses one replica; outages can stop all groups, and unflushed counts can be lost on restart. A shared provider-side limit remains a backstop and can still stop all groups if reached. “Every other group keeps working” describes an isolated group's budget refusal, not immunity to shared failures.

The gateway passes requests through unchanged. It does not inspect sensitive content, select a model, or switch to another provider. Request metadata is retained without questions or answers in the gateway ledger; this is not a blanket claim about all other platform logs, tool outputs, traces, or provider retention. The configured cleanup removes rows older than 30 days on its schedule, not necessarily at the exact second they turn 30 days old.

Reading and drafting proposals are not approval-gated. Accepting a proposal and approving a runtime action are different decisions. Operational approvals cover the actual requested call and arguments. The three action categories are a teaching simplification of a taxonomy that also distinguishes write/admin classes. Prompt instructions about targets, filenames and branch names are not independent authorization checks. The shared tool server retains broad privileges, and readable data can still be disclosed; excluding direct Secret API access does not eliminate indirect exposure through logs, configuration or execution.

The companion deployment notes also limit the surrounding platform claims: Git-only agent-definition controls have documented exemptions and do not cover every resource or deletion. Caller identity has residual shared-identity and reachability gaps; a sandbox is not proof against escape, and host components have substantial access. Evaluation happens after sync, holds affect selected new-session entry points, and they do not revoke existing sessions or automatically roll back deployments. Audit redaction is best-effort and depends on collection working. No diagram here expands those controls' scope.

The article comparison is Ari Sela's interpretation, not an endorsement by its author. The article name appears only in the first panel's subtitle; its branding and the author's name are not used. The supplied brief summarizes the article. An attempt to retrieve its URL during preparation failed, so the article's full wording and publication date were not independently verified. Reference: [AI Routers Are Changing AI Governance](https://humandrivenai.com/ai-routers-are-changing-ai-governance-human-driven-ai/).

## Provenance and checks

Source brief: `docs/presentations/ai-gateway-presentation-brief.md`. Scope reference: `docs/presentations/safe-agent-deployment-speaker-notes.md`, especially panels 1–5. Images were created with the built-in image generation tool; the generation prompts and per-panel PNGs are not kept in the repo. All illustrations are explanatory, not screenshots or measurements. No cluster, agent, deployment or demo commands were run.

### Final artifact verification

Three PNGs, each 1672 × 941 pixels, assembled as three raster PDF pages of 960 × 540.287 points. Original aspect ratios are preserved with no cropping or stretching. PDF reopened, page count and one image per page verified, all three pages rendered and visually inspected. Checked rendered text, attribution, dated footer, request and answer directions, tool branch originating at the agent, and approval only before changing something. The budget row contains exactly ten meters: one exhausted and nine unaffected. No technical product names, source paths, status codes, unsupported dollar figures, or claims of live measurement appear on the images. Article attribution is confined to the first subtitle.


### Owner-requested footer corrections

Corrected panel 2 to read “drawn from the repository, not a live measurement” and standardized panel 3’s badge to “3 / 3”. Regenerated both with the built-in image tool, rebuilt the PDF and PNG ZIP, and inspected enlarged footer regions rendered from the rebuilt PDF. All PNGs remain 1672 × 941 pixels.
