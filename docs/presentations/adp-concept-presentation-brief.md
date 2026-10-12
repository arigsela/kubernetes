# Agentic Developer Platform — concept briefing, screen by screen

**Purpose:** a complete description of the 15-screen "Agentic Developer Platform" briefing
(the interactive HTML version is `adp-concept.html`, published 2026-10-12; the infographic rendering built from this brief is `adp-concept-infographics.pdf` beside it, with `adp-concept-speaker-notes.md`), written for an agent
that will turn it into infographics. Every screen below gives: the message, the exact copy,
the visual to draw, any interaction the HTML version had (so you can decide what to replace it
with in a static medium), and speaker notes. Data provenance is at the end.

**Audience:** engineering leadership; first target is the company's AI pathfinders group (the
people setting AI strategy). They are technical but not Kubernetes specialists. Vocabulary
rule: name mechanisms in plain words first; the specific tool goes in a smaller "in the
reference" line, never in the headline.

**Framing:** "here is the concept, and here is a working reference". The reference is a small
Kubernetes platform (three decade-old servers) that was built like a production platform and
then extended so AI agents run on it as governed principals. It is **anonymized**: no
hostnames, no repository name, no personal names, no company names. Call it "the reference"
or "the reference platform".

**Tone:** confident, plain, evidence-first. Failures are shown as deliberately as features.
Say "alpha" about the agent runtime; do not soften it. No "enterprise-grade", no
"revolutionary", no emoji.

**Format of the source:** guided (presented live by the owner), so each screen carries little
text and the speaker notes carry the argument. For an infographic, promote the speaker note's
key sentence onto the visual where it helps.

## Visual identity (from the HTML version, "Bold Signal")

Carry this over unless the infographic agent has a house style to follow.

- **Background:** near-black with a subtle diagonal gradient (`#1a1a1a` → `#2d2d2d` → `#1a1a1a`).
- **Accent:** one orange, `#FF5722`, used for the focal card, bullets, numbers, highlights.
  A soft version at 14% opacity for hot panels. A single green, `#7ed957`, only for "done /
  ok" states.
- **Text:** white `#ffffff`; secondary text white at 62% opacity. Dark text `#1a1a1a` on the
  orange card.
- **Type:** display face Archivo Black (headlines, numbers, labels in small caps with wide
  tracking); body face Space Grotesk; a monospace for recorded output and code.
- **Frame:** every screen has its number top-left in orange (01…15) and a breadcrumb of the
  sections top-right (Concept · Platform · How it works · In use · Governance · Reference ·
  Discussion) with the current one lit. A footer line left (section) and right (a one-line
  takeaway).
- **Components:** an orange focal card for title/close; thin-bordered translucent panels for
  content; "stat tiles" with a big orange number and a small grey label; "nodes on a track"
  for flows (circles joined by a line, the active one filled orange, done ones outlined green,
  a stop node outlined orange with a glow); transcript panels with small-caps role labels
  (orange for the agent, grey for the human, green for system/ok).
- **A `recorded` pill** (monospace, thin border) marks every screen whose content is a real
  transcript.

## Section map

| # | Section | Screen |
|---|---|---|
| 01 | Concept | Title |
| 02 | Concept | Agents are new users of the platform |
| 03 | Platform | Three layers (interactive map) |
| 04 | How it works | A read request, hop by hop (animated) |
| 05 | How it works | A mutating request that stops at the gate (animated) |
| 06 | In use | Recorded: ask the platform a question |
| 07 | In use | Recorded: the approval gate, both ways |
| 08 | In use | Recorded: reaching outside, carefully (web search) |
| 09 | Governance | What makes it safe to run (six controls) |
| 10 | Governance | One set of docs for humans and agents |
| 11 | Reference | The reference in numbers |
| 12 | Reference | What it took, in what order |
| 13 | Reference | What maps to a company, and what doesn't yet |
| 14 | Discussion | Five questions for the group |
| 15 | Discussion | Close |

---

## 01 — Title

**Message:** this is a concept talk backed by something real.

**Copy (on the orange card):**
- Headline: *An Agentic Developer Platform*
- Sub: *What it is, what changes for developers, and a working reference you can poke at.*

**Copy (below the card, secondary):**
> The reference is a small Kubernetes platform built the way a production one would be, then
> extended so AI agents run on it as first-class, governed principals. Every screen marked
> `recorded` shows real output from it.

**Footer:** left "Engineering leadership briefing", right "2026".

**Visual:** the orange card bottom-left of a dark gradient, nothing else. Restraint is the point.

**Speaker note:** Set expectations: concept backed by a working reference, not a product
pitch. The reference is deliberately small so the argument is about the design, not the
hardware. Recorded screens are real transcripts, lightly trimmed.

## 02 — Agents are new users of the platform, not a new platform

**Message:** the agent layer reuses the developer platform; an agent is a principal.

**Headline:** *Agents are new users of the platform, not a new platform*

**Body:**
> An Agentic Developer Platform treats an AI agent the way it treats a developer or a CI job:
> **it gets an identity, a defined set of permissions, an audit trail, and the same paved
> roads**. The agent layer sits on top of the developer platform you already need, and
> reuses it.

**Two callouts (orange panels):**
- *The shift* — Today an agent is usually a script with someone's credentials. On an ADP it is
  a principal: its own credential, scoped to one job, with every action recorded and the risky
  ones gated by a human.
- *Why it matters now* — The capability is cheap. The governance is what decides whether you
  can let agents touch real systems.

**Visual idea for an infographic:** two silhouettes side by side, "developer" and "agent", each
with the same four badges: identity, permissions, audit trail, paved road. Same badges, same
colors; that sameness is the picture.

**Footer right:** "Three layers, built bottom-up".

**Speaker note:** Most of the work is not AI work. It is identity, policy, audit, docs and a
runtime. The model is the easy part to swap.

## 03 — Three layers. Click any component. (interactive)

**Message:** three layers; layer 3 adds no new secrets system, deploy path or docs system; it
reuses layers 1 and 2.

**Headline:** *Three layers. Click any component.* (for a static version: *Three layers*)

**Visual:** three stacked horizontal bands, bottom to top: Layer 1 · Developer platform ("what
you need anyway"), Layer 2 · Paved roads ("how work flows"), Layer 3 · Agent infrastructure
("what makes agents safe to run"), the third band tinted orange. Each band holds pill-shaped
component chips. In the HTML, clicking a chip filled a side panel with three labelled lines:
*What it is / What it is used for / In the reference.* For an infographic, render each
component as a small card with those three lines, or pick the six starred ones and show the
rest as chips.

**Components (name — what it is — what it is used for — in the reference):**

Layer 1 · Developer platform
- **GitOps** ★ — Every change to the platform is a commit; a controller applies it and reverts
  anything applied by hand. — Agents, their tools and their permissions are manifests in the
  same repo, reviewed the same way. — Argo CD app-of-apps; 59 applications; nothing applied by hand.
- **Infrastructure as code** — Cloud and host configuration declared and applied through pull
  requests, with the apply gated before merge. — The agent runtime, its storage and its
  credentials were provisioned the same way as everything else. — Terraform via Atlantis,
  Crossplane, Ansible.
- **Secrets** ★ — A central vault; each consumer gets one credential it can read, nothing
  else. — Each agent and each tool server is such a consumer. Keys are injected at the
  network edge, never inside the agent. — Vault with KMS auto-unseal, External Secrets, one
  role per credential.
- **Edge** — One declared route per hostname, an IP allow-list, a web application firewall,
  automated TLS. — Agents are reachable only through this edge, with the same allow-list as
  everything else. — Istio Gateway API, per-host routes, Coraza WAF.
- **Policy** ★ — Admission rules in code, generated from one source of truth and tested
  against a real API server in CI. — The rules that refuse an unsafe agent definition are
  ordinary admission policies. — Native admission policies (CEL), 100+ test fixtures.
- **Data & backups** — Databases with scheduled backups and restore drills that run in CI. —
  Agent memory and the audit record live in the same databases, with the same backups. —
  CloudNativePG, pgvector, drilled restore scripts.
- **Observability** — Metrics, logs, traces and runtime security signals, with alerts that
  fail closed. — Agent calls are traced; the audit alert fires if the audit stops reporting. —
  Prometheus, Grafana, Loki, Coroot, Falco.
- **Developer portal** — A catalog of services with generated documentation and a scaffolder
  for new ones. — Agents appear in the catalog like any service; their docs are the same
  pages. — Backstage with TechDocs.

Layer 2 · Paved roads
- **Scaffolder** — A template that creates a new service with its docs, secrets pattern and
  deployment wired in. — The paved road a new agent (or a new team) starts on. — Backstage
  scaffolder template.
- **Dependency updates** — A bot opens upgrade pull requests weekly and never merges. — Model
  runtime and agent platform versions move in lockstep, behind approval. — Renovate with
  grouped, approval-gated rules.
- **PR triage** — A classifier labels every pull request skip / skim / read and explains why;
  it never blocks. — Review attention is rationed by risk, including changes to agent
  permissions. — A calibrated classifier; policy in a file.
- **Scheduled work** — Cron-driven jobs: audits, exports, digests, cleanups. — The daily agent
  audit and its export to long-term storage. — CronJobs and workflow automation.

Layer 3 · Agent infrastructure
- **Runtime** — Each conversation runs as an isolated sandbox that suspends to a snapshot
  between turns and resumes anywhere. — Isolation from the host; durable sessions; idle agents
  cost nothing. — kagent 1.0 on Agent Substrate (gVisor actors). Alpha.
- **Identity** ★ — An agent is a principal: its own credential, scoped to exactly what it
  needs. — A compromised agent has nothing worth stealing and nowhere it can reach. —
  Per-agent Vault role; credential injection at egress; default-deny egress.
- **Capability** ★ — Every tool is classified read / write / destructive; write tools must sit
  behind approval. — The dangerous things are impossible, not discouraged. — One taxonomy file
  → generated admission policy → CI validator, all fail closed.
- **Approval** ★ — A protocol-level pause before a mutating tool runs; a human approves or
  rejects. — Operators keep control without reading every step. — Per-binding
  requireApproval; approve/reject over the agent protocol or the UI.
- **Tools (MCP)** — Capabilities are standard tool servers: docs, catalog, cluster, web
  search. — Adding a capability is a reviewed change, not a code change in the agent. — MCP
  servers deployed as services; agents bind named tools only.
- **Memory** — Long-term facts per agent and user, retrieved by meaning. — Agents remember
  decisions across conversations. — pgvector with a locally hosted embedding model.
- **Audit** — Every tool call and approval decision recorded, risk-scored and exported. — The
  question "what did the agents do?" has a precise answer. — Daily audit job, classifier
  triage, S3 record, web UI.
- **Evaluation** — A corpus of questions with known-good answers; scored by rules, then a
  cheap classifier, then a frontier model only when unsure. — A model or prompt change is
  measured, not felt. — Eval corpus plus a cascade judge.

**Footer right:** "Everything in layer 3 reuses layers 1 and 2".

**Speaker note:** Walk two or three components live (GitOps, Identity, Approval). Make the
point that layer 3 added no new secrets system, no new deploy path, no new docs system.

## 04 — Follow one question through the platform (animated, read path)

**Message:** every hop is a separate, auditable component; the agent never holds a credential.

**Headline:** *Follow one question through the platform*
**Sub:** *"Is the embedding service healthy, and which Kubernetes version do the nodes run?"*

**Visual:** five nodes on a horizontal track, left to right. In the HTML a Play button lit
each node in turn (2.2 s apart) and swapped a caption under the track. For a static version,
draw all five with their captions as numbered callouts.

| # | Node | Under the node | Caption when lit |
|---|---|---|---|
| 1 | Gateway | IP allow-list, TLS, WAF | **Gateway.** The request arrives over TLS from an allow-listed address. Nothing else reaches the agents. |
| 2 | Agent sandbox | one isolated actor per conversation | **Sandbox.** A conversation actor is resumed from its snapshot. It holds no credentials; its outbound traffic is default-deny. |
| 3 | Docs tool | the same docs humans read | **Docs tool.** The agent reads the service's architecture page and runbook: the same files the portal renders for humans. |
| 4 | Cluster tool | read-only, scoped service account | **Cluster tool.** It queries live state through a read-only tool server with its own scoped service account. |
| 5 | Answer | with what it checked | **Answer.** Healthy, 0 restarts, and a note on exactly which sources it used. The call is in the audit record. |

**Footer right:** "Every hop is a separate, auditable component".

**Speaker note:** The agent never holds the API key: it is injected at the gateway on the way
out. The cluster tool runs as its own service account that cannot read Secrets.

## 05 — Now a request that changes something (animated, write path)

**Message:** the approval gate is enforced by three independent layers, not by prompt text.

**Headline:** *Now a request that changes something*
**Sub:** *"Delete the ConfigMap `approval-gate-probe` in the default namespace."*

**Visual:** same five-node track; node 4 is the gate (a lock glyph ⚿, outlined orange with a
glow). The animation stops at the gate; a second press continues to node 5.

| # | Node | Under the node | Caption |
|---|---|---|---|
| 1 | Gateway | same entry point | **Gateway.** Same entry point, same allow-list. |
| 2 | Operator agent | has mutating tools | **Operator agent.** This agent binds mutating tools, so it carries the `admin` class and stricter rules. |
| 3 | Checks first | reads the object, confirms the label | **Checks first.** It reads the object and confirms the throwaway label before proposing anything. |
| ⚿ | Approval gate | a human decides | **Approval gate.** The delete call pauses. The runtime marks it approval-required; policy and CI guarantee the binding is gated. A human sees the exact call and arguments. |
| 5 | Action + record | executed, audited | **Action + record.** On approve, the call runs once and both the call and the decision are recorded. On reject, nothing happens and the agent does not retry. |

**Standing caption before play:** The gate is not a prompt instruction; it is enforced by the
runtime, by admission policy, and by CI.

**Infographic idea:** show the three enforcement layers as three concentric rings around the
gate node: *runtime binding* (inner), *admission policy* (middle), *CI validator* (outer).

**Footer right:** "Three independent layers enforce the gate".

**Speaker note:** The agent's own binding marks mutating tools as requiring approval;
admission policy refuses any agent definition that binds a mutating tool without it; CI
refuses the same before merge. A prompt cannot talk its way past any of them.

## 06 — In use: ask the platform a question `recorded`

**Message:** answers say what they checked; follow-ups keep context; facts persist.

**Headline:** *In use: ask the platform a question*

**Bullets:**
- One command from a terminal, a chat window, or another agent over a standard protocol.
- The answer says **what it checked** and what it did not, every time.
- Follow-ups keep context; facts it is told to remember persist across conversations.

**Transcript (verbatim, trimmed; keep the wording):**
- *Developer:* Using live cluster state: is the embedding service healthy, and how many
  restarts does its pod have?
- *Knowledge agent:* Yes, the deployment is healthy. Its pod has **0 restarts**, running since
  2026-10-06 with no previous termination, so no OOM kills or crash loops.
- *(system-style aside, grey, left-ruled):* What I checked: the read-only cluster tool for the
  deployment, its pod and recent events. I used no repo docs. If you want the pod template
  label fixed, make that change as a PR: the cluster is GitOps-driven, so don't change it
  directly.

**Footer right:** "Trimmed transcript, reference platform, 2026-10-11".

**Speaker note:** Point at the last sentence: the agent itself tells the developer to go
through Git. That comes from the docs it reads, not from a hardcoded rule.

## 07 — In use: the approval gate, both ways `recorded`

**Message:** the approval is a protocol-level pause; reject is terminal for that call;
approve runs it exactly once.

**Headline:** *In use: the approval gate, both ways*

**Bullets:**
- The agent reads before it writes, then asks.
- **Reject** leaves the object untouched; the agent does not retry on its own.
- **Approve** executes once, and the call and the decision land in the audit record.

**Transcript (verbatim):**
- *Operator agent:* The ConfigMap exists and matches your description. It has the label
  `test.homelab/throwaway: "true"`. I'm deleting it now.
- *(system, orange-ruled):* Tool `k8s_delete_resource` requires approval before execution.
  **Waiting for a human.**
- *Human · reject* → "The delete didn't go through. The approval step rejected the call, so
  the ConfigMap is still in the default namespace. I haven't retried."
- *Human · approve* → "The ConfigMap **approval-gate-probe is deleted** from the default
  namespace. A follow-up get on it failed, confirming it is gone."

**Infographic idea:** a fork: one request, the gate in the middle, two outcomes drawn as
branches with the quoted result under each. This is the screen that lands with leadership.

**Footer right:** "Same request, run twice: rejected, then approved".

## 08 — In use: reaching outside, carefully `recorded`

**Message:** a capability (web search) is a governed addition; untrusted text never sits
next to a mutating tool.

**Headline:** *In use: reaching outside, carefully*

**Bullets:**
- Web search is a tool like any other: declared, classified read-only, bound only to agents
  that cannot change anything.
- The agent cites what it used and flags where the source may not match reality.
- Untrusted text never sits next to a mutating tool.

**Transcript (trimmed):**
- *Developer:* What changed in the most recent release of our agent runtime? Use web search
  and cite the URL.
- *Knowledge agent:* Summarised the latest release notes with sources from the vendor's
  documentation site and GitHub releases.
- *(aside):* What I checked: news search and web search for the release notes. I did not read
  the repo docs or the live cluster, because this was an upstream question. Caveat: the public
  docs still call 1.0 an alpha; check what's deployed before relying on these notes.

**Footer right:** "Search added in one pull request: a server, a secret, two taxonomy entries".

**Speaker note:** Adding a capability is a governed change: the tool had to be classified in
the taxonomy before any agent could bind it, and the operator agents deliberately did not get it.

## 09 — What makes it safe to run

**Message:** six controls; none depend on the model behaving.

**Headline:** *What makes it safe to run*

**Six panels (title — text — optional monospace evidence line):**
1. **Identity** — One credential per agent and per tool, scoped to one path in the secrets
   vault. Keys are injected at the network edge; the agent process never holds them.
2. **Capability** — Every tool is classified `read`, `write` or `destructive` in one file.
   Admission policy refuses an agent that binds a write tool without approval. — evidence:
   `denied: binds write/destructive tools in an MCP binding without requireApproval: true`
3. **Audit** — Every tool call is recorded. A daily job flags any write with no approval
   decision; the alert fires if the audit itself goes quiet. — evidence:
   `{"check":"agent-audit-ungated","severity":"ok","ungated_invocations":0}`
4. **Evaluation** — A corpus of known-good answers, scored by rules first, a cheap classifier
   second, a frontier model only when unsure.
5. **Isolation** — Each conversation runs in its own kernel-isolated sandbox with
   default-deny egress, suspended to a snapshot between turns.
6. **Change control** — An agent is a manifest in Git. Adding a tool, a model or a permission
   is a reviewed pull request, triaged by a classifier before a human reads it.

**Footer right:** "The denial text and the audit line are real outputs".

**Speaker note:** The design principle: assume the model can be talked into anything, and
make the dangerous things impossible rather than discouraged.

## 10 — One set of docs for humans and agents

**Message:** documentation is a contract, not a wiki; humans and agents read the same files.

**Headline:** *One set of docs for humans and agents*

**Bullets:**
- Every service carries a short architecture page and a runbook under a schema; a validator
  fails the build if they drift.
- The developer portal renders them. The agents retrieve from the same files.
- So the runbook a human follows at 2 a.m. is exactly what the agent reads when asked "why is
  this failing?"
- Agents reading stale docs was the first failure mode; the contract is what fixed it.

**Visual:** a four-node track: *docs.md · runbook.md* (per service, schema-checked) →
*Generators* (portal pages, index) → *Portal* (what people read) → *Agents* (what agents
retrieve). Draw the portal and the agents as two arrows out of the same generator, to show
one source feeding both.

**Footer right:** "Documentation is a contract, not a wiki".

**Speaker note:** For a company this is the cheapest high-leverage move; it pays off for
onboarding before any agent exists.

## 11 — The reference, in numbers

**Message:** every property shown exists, is tested, and has a known cost; scale is not the point.

**Headline:** *The reference, in numbers*

**Six stat tiles (number — label):**
- **7** — agents in Git: knowledge, read-only investigator, two operators, an orchestrator,
  two chat agents
- **59** — services and platform components, all deployed from Git, all healthy
- **0** — tool calls that changed something without an approval, since go-live
- **48%** — of model input served from cache; up to 98% inside a tool loop
- **85** — written invariants; every incident becomes one
- **~$10** — per month of cloud spend, on three decade-old servers

**Line under the tiles:** Scale is not the point of the reference. The point is that every
property on the previous screens exists, is tested, and has a known cost.

**Footer right:** "Measured 2026-10-11".

**Speaker note:** Cloud spend excludes model usage (small at this traffic; caching halves it).
The hardware is sunk cost.

## 12 — What it took, and in what order

**Message:** order matters more than tooling choices; the model is the smallest decision.

**Headline:** *What it took, and in what order*

**Five-node track, all nodes "done" (green outline):**
1. **Developer platform** — GitOps, secrets, policy, backups, portal. Most of the calendar time.
2. **Identity first** — one credential per agent, before any agent could act
3. **Capability + audit** — the taxonomy, admission policy, the action record
4. **Sandboxed runtime** — isolated, snapshot-able agents; the last step, not the first
5. **Evaluation** — a corpus and a judge; still the thinnest layer

**Three lesson cards (orange left rule):**
- **The model is the smallest decision.** The reference swapped model generations twice in
  one day without touching the governance.
- **Failures taught more than features.** A runtime change once blinded the audit for 90
  minutes while reporting "0 findings". Now a known gated call is replayed after every such
  change.
- **Say "alpha" out loud.** The runtime is pre-release software; the design holds, the
  operations are still rough.

**Footer right:** "Order matters more than tooling choices".

**Speaker note:** If asked "how long": the developer platform is months of evenings; the
agent layer on top of it was weeks. The order is the transferable lesson.

## 13 — What maps to a company, and what doesn't yet

**Headline:** *What maps to a company, and what doesn't yet*

**Bullets:**
- **Maps directly:** agents as principals in your existing identity provider; a tool taxonomy
  owned per team; the audit record shipped to your SIEM; the docs contract as an onboarding
  standard.
- **Needs a decision:** who approves what. The reference has one human; a company needs
  approval routing by team and risk class.
- **Still early:** the sandboxed runtime is alpha software, and evaluation needs a real corpus
  per domain.

**Callout (orange):** *A sensible first step* — Pick one read-only agent for one team: answers
questions from that team's docs and systems, cannot change anything, every call recorded. The
governance built for it is the governance everything after it reuses.

**Infographic idea:** a three-column "maps / decide / early" board, with the callout as a
highlighted "start here" card.

**Footer right:** "Start read-only; earn write access with the audit trail".

**Speaker note:** Resist "an agent that does deploys" as the first project. The read-only
agent builds the identity, taxonomy and audit muscles with no blast radius.

## 14 — Questions worth deciding as a group

**Headline:** *Questions worth deciding as a group*

**Five questions (no tooling in any of them):**
1. Which existing platform pieces would agents reuse unchanged, and which are missing
   (identity for non-humans, a secrets pattern, policy-as-code)?
2. Who classifies tools, and who approves a write? Per team, per risk class, or centrally?
3. What is the first read-only agent, and whose docs would it read?
4. What does the audit record need to feed, and who reads it?
5. How do we evaluate an agent before and after a model change?

**Footer right:** "Five questions, no tooling in any of them".

**Speaker note:** End on the questions rather than a recommendation. The pathfinders group
owns the strategy; the reference gives them something concrete to argue against.

## 15 — Close

**Copy (orange card):**
- Headline: *The agents are the visible part. The platform underneath is what makes them safe
  to run.*
- Sub: *The reference is live and can be demoed on request: a question, an approval, a refusal.*

**Footer:** left "Thank you", right "Recorded screens: 2026-10-11".

**Speaker note:** Offer the live demo: ask a question, trigger the approval gate, show a bad
agent manifest being refused by policy.

---

## Data provenance (for the numbers and quotes)

All measured or recorded on the reference platform on 2026-10-11 unless noted.

| Claim | Source |
|---|---|
| 7 agents, their roles | the agent manifests in the platform repository |
| 59 services/components, all healthy | the GitOps controller's application list at the time |
| 0 ungated tool calls since go-live | the daily audit job's output (`ungated_invocations: 0`) |
| 48% of model input from cache; 86–98% in tool loops | token usage stored per model call (30 calls at measurement) |
| 85 invariants | the platform's specification ledger |
| ~$10/month cloud | the cloud budget target; some months reach ~$26 with domain renewals |
| Approval transcript | the same delete request sent twice over the agent protocol, rejected then approved |
| Policy denial text | a server-side dry-run of a deliberately bad agent definition |
| "blinded the audit for 90 minutes" | a logged incident the same day: a runtime change altered the event format the audit parsed |
| Swapped model generations twice in one day | first to a new model on the old runtime, then onto the new runtime |
| Agent runtime is alpha | its upstream release channel (alpha11 at the time) |

## Do not claim

- High availability (the reference has a single control plane).
- Production readiness of the agent runtime (it is alpha).
- Uptime figures (none are tracked).
- Any company, person or hostname: the reference is anonymous by design.
- That the web-search agents can read full pages (they work from search snippets).

## If the infographic needs fewer screens

Keep, in this order: 02 (the shift), 03 (three layers), 05 (the gate), 07 (gate transcript),
09 (six controls), 13 (what maps), 14 (questions). That is the argument in seven panels.
