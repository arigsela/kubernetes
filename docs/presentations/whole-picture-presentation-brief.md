# The whole picture — how the pieces fit, and what comes next, screen by screen

**Purpose:** a complete description of a 14-screen briefing that places every piece of the
reference platform on one map: the published reference architecture for agentic development
platforms (tooling, path definitions, agent infrastructure, all as code), the maturity ladder
it describes, and the eight core paths it says an organisation has to nail. The other three
decks each took one slice (the governance that makes agents safe to run; agents changing the
platform; agents consumed through many doors). This one is the map they sit on, with an honest
score per piece and the order of what gets built next. It is written for an agent that will
turn it into infographics (the rendering built from it is `whole-picture-infographics.pdf`
beside it, with `whole-picture-speaker-notes.md`, 2026-10-13), in the same form as
`adp-concept-presentation-brief.md`, `dev-workflow-presentation-brief.md` and
`agents-as-a-service-presentation-brief.md`. Every
screen gives: the message, the exact copy, the visual to draw, and speaker notes. Data
provenance is at the end.

**As of:** rescored 2026-10-13 with one promotion class. Later the same day a second class was
added: the owner's docs-only pull requests (`*.md` or `docs/`, ≤10 files, never the agent
instruction files) merge by policy too. Where this brief and the deck say "one declared class",
read "two declared classes, both trivial by construction"; the scorecard and ladder are unchanged.
Later still on 2026-10-13, callers became principals (screen 07) and evaluation became a post-deploy
gate (screen 06); the "needs a decision" column on screen 14 no longer includes caller identity.

**Audience:** engineering leadership; first target is the company's AI pathfinders group. They
are technical but not Kubernetes specialists. Vocabulary rule: name the mechanism in plain
words first; the specific tool goes in a smaller "in the reference" line, never in the headline.

**Framing:** "the pieces were built one at a time; here is the architecture they add up to, and
the gaps the architecture makes visible". The frame is borrowed, deliberately: it is the
three-layer agentic development platform reference architecture from Weave Intelligence (the
Platform Engineering community's "How to architect Agentic Development Platforms" webinar,
2026-06-19), so the audience can check the reference against a public model rather than
against the owner's opinion. The reference is the same anonymized platform as the other three
decks: a small Kubernetes platform run as a GitOps repository by one owner, where a coding
agent writes most of the commits and seven agents run in the cluster as governed principals.
Call it "the reference". No hostnames, repository name, personal or company names.

**Relationship to the other decks:** this deck does not repeat their recorded transcripts. It
points at them (screen 02) and adds two recorded screens of its own: one change passing
through every layer in 23 minutes (screen 11), and the query that justified the whole
governance layer (screen 12). Screens 06 and 07 reuse the ADP deck's controls, re-sorted into
the reference architecture's vocabulary.

**Tone:** confident, plain, evidence-first. Every mapped piece carries one of three states,
**built / partial / not yet**, and the "not yet" column is as prominent as the "built" one.
Say "alpha" about the agent runtime. No "AI-powered", no "transformation", no emoji.

**Format of the source:** guided (presented live by the owner), so each screen carries little
text and the speaker notes carry the argument. For an infographic, promote the speaker note's
key sentence onto the visual where it helps.

## Visual identity

Identical to the other three decks so the four can be shown as a set. Carry over unless the
infographic agent has a house style to follow:

- **Background:** near-black with a subtle diagonal gradient (`#1a1a1a` → `#2d2d2d` → `#1a1a1a`).
- **Accent:** one orange, `#FF5722`, for the focal card, bullets, numbers, highlights; 14% opacity
  for hot panels. A single green, `#7ed957`, only for "built / ok" states. For this deck add a
  third state colour: **white at 35% opacity with a dashed border** for "not yet". "Partial" is
  an orange outline, not filled.
- **Text:** white; secondary text white at 62% opacity. Dark text on the orange card.
- **Type:** display face Archivo Black; body face Space Grotesk; a monospace for recorded output.
- **Frame:** screen number top-left in orange (01…14); a breadcrumb of the sections top-right
  (Concept · The frame · Layers · Paths · In use · Next · Discussion) with the current one lit;
  footer left (section) and right (a one-line takeaway).
- **Components:** orange focal card for title/close; thin-bordered translucent panels; stat tiles
  (big orange number, small grey label); a **status chip** on every mapped piece (BUILT in green
  outline, PARTIAL in orange outline, NOT YET dashed grey), used on screens 04 to 10 and 13; the
  **three-layer stack** (tooling at the bottom, paths in the middle, agent infrastructure on
  top, a thin "as code" band spanning all three on the left edge) drawn once on screen 03 and
  repeated as a small locator glyph in the corner of screens 04 to 08 with the current layer lit.
- **A `recorded` pill** marks every screen whose content is a real transcript, comment, log or
  query result.

## Section map

| # | Section | Screen |
|---|---|---|
| 01 | Concept | Title |
| 02 | Concept | Three slices, one platform |
| 03 | The frame | The reference architecture, with the reference on it |
| 04 | Layers | Layer 1 · The developer platform, as built |
| 05 | Layers | Layer 2 · Three kinds of path |
| 06 | Layers | Layer 3 · The harness: context, capability, execution, evaluation |
| 07 | Layers | Layer 3 · Governance: identity, security, agent observability |
| 08 | Layers | Everything as code, in one repository |
| 09 | Paths | The eight core paths, scored |
| 10 | Paths | Where the reference sits on the maturity ladder |
| 11 | In use | Recorded: one change through every layer, in 23 minutes |
| 12 | In use | Recorded: the query that justified the governance layer |
| 13 | Next | What gets built next, in what order |
| 14 | Discussion | What maps, five questions, close |

---

## 01 — Title

**Message:** this is the map; the other three decks are the territory.

**Copy (on the orange card):**
- Headline: *The whole picture*
- Sub: *How the pieces of an agentic development platform fit together, scored honestly, with
  the next pieces in order.*

**Copy (below the card, secondary):**
> The frame is a published reference architecture. The pieces are a working, anonymized
> reference platform. Every piece carries one of three states: built, partial, not yet.

**Footer:** left "Engineering leadership briefing", right "2026".

**Visual:** the orange card bottom-left; faint in the background, the three-layer stack from
screen 03 as a line drawing, unlabelled.

**Speaker note:** Say what this deck is not: not a product, not a maturity badge. It is one
owner's platform placed on a public model so the group can argue with the model, not with the
owner. The three earlier decks are the evidence; this one is the index.

## 02 — Three slices, one platform

**Message:** the earlier decks each showed one slice; this deck shows where the slices sit.

**Headline:** *Three slices, one platform*

**Visual:** the three-layer stack, small, in the centre. Three callout cards around it, each with
an arrow to the layer(s) it covered:
- **Agents as governed principals** (the ADP concept deck) → points at layer 3. *Identity,
  capability classes, the approval gate, the audit record. Seven panels, two recorded.*
- **Agents changing the platform** (the development workflow deck) → points at layer 2. *Spec,
  agent, triage, checks, merge-as-deployment, the loop back. Seven panels, two recorded.*
- **Agents consumed through many doors** (the agents-as-a-service deck) → points at the boundary
  between layers 2 and 3. *One definition, one gateway, five doors, one record. Seven panels,
  three recorded.*

**Standing caption:** Each slice was presented as a thing that works. This deck asks the other
question: what is the shape they add up to, and what is missing from that shape?

**Footer right:** "The same platform, three times; now once".

**Speaker note:** If the group has seen the other decks, keep this to twenty seconds. If not,
this screen tells them which deck to ask for afterwards.

## 03 — The reference architecture, with the reference on it

**Message:** a published three-layer model; the reference fills every box, unevenly.

**Headline:** *The reference architecture, with the reference on it*

**Sub (secondary):** *From a public reference architecture for agentic development platforms,
June 2026. Nothing in the developer platform is thrown away; two things are added on top.*

**Visual:** the three-layer stack, large, with a thin vertical "everything as code" band on the
left spanning all three layers.

| Band | What the model says | In the reference | State |
|---|---|---|---|
| **Layer 1 · Tooling** (bottom) | The internal developer platform you need anyway: delivery, resources, security, observability, a portal. Unchanged by agents. | GitOps, infrastructure as code, a secrets vault, an edge with allow-list and firewall, admission policy, databases with drills, metrics/logs/traces, a developer portal. 59 applications. | BUILT |
| **Layer 2 · Path definitions** (middle) | How work moves through the tooling: **deterministic** paths (same input, same output), **probabilistic** paths (an agent reasons and acts), and **hybrid** paths that chain the two. | Deterministic: the check wall and the deployment. Probabilistic: PR triage, the knowledge agent. Hybrid: an agent's change passing the gates with a human deciding. No path registry yet. | PARTIAL |
| **Layer 3 · Agent infrastructure** (top) | New with agents. A **harness** (context, capabilities, execution, evaluation) and **governance** (non-human identity, security, agent-level observability). Universal across every kind of agent platform. | Agent definitions as manifests; a tool taxonomy and admission policy; sandboxed sessions; an audit record with a daily check; an evaluation corpus with a judge. Runtime is alpha. | BUILT (evaluation thin) |
| **As code** (left band) | Prompts, paths, skills and model configuration are code: versioned, reviewed, auditable, like everything else. | One repository holds all of it; every change is a pull request through the same triage and checks. | BUILT |

**Footer right:** "Two things added; nothing removed".

**Speaker note:** The model's own claim is that layer 1 does not change and layer 3 is
universal: the same harness and governance serve a sales or legal agent platform as a software
one. The reference supports both halves: nothing in layer 1 was built for agents, and nothing
in layer 3 knows what the agents are for. The next five screens walk the bands.

## 04 — Layer 1 · The developer platform, as built

**Message:** every piece in this layer predates the agents and was reused unchanged.

**Headline:** *Layer 1 · The developer platform, as built*

**Visual:** the five planes the model uses for an internal developer platform, as five columns,
each with its components as chips and a status chip at the bottom. The locator glyph top-right
with layer 1 lit.

| Plane | Components (plain words) | In the reference | State |
|---|---|---|---|
| **Developer control** | A portal with a catalog, generated docs and a scaffolder for new services | Backstage with TechDocs; the new-service template | BUILT |
| **Integration & delivery** | Every change is a commit; a controller applies it and reverts anything applied by hand; cloud and hosts declared as code; dependency updates as pull requests | Argo CD app-of-apps; Terraform via Atlantis (applied before merge); Crossplane; Ansible; Renovate weekly | BUILT |
| **Resources** | Compute, databases with scheduled backups and restore drills in CI, object storage | k3s on three servers; CloudNativePG with pgvector; S3 for state, logs, backups and the audit record | BUILT |
| **Security** | A secrets vault with one credential per consumer; an edge with an IP allow-list, firewall and automated TLS; admission rules in code tested against a real API server | Vault with KMS auto-unseal and External Secrets; Istio Gateway API, Coraza WAF, cert-manager; 12 validating and 1 mutating admission policies, generated and drilled in CI | BUILT |
| **Observability** | Metrics, logs, traces and runtime security signals with alerts that fail closed | Prometheus, Grafana, Loki (30-day logs), Coroot (7-day traces), Falco; alerts routed to chat through workflow automation | BUILT |

**Standing caption:** None of this was built for agents. All of it is what the agents reuse:
the same vault, the same edge, the same deploy path, the same docs.

**Footer right:** "Built first, over months of evenings".

**Speaker note:** This is the screen where a company recognises its own platform. The only
unusual choice is admission policy as generated code drilled against a real cluster in CI; that
choice is what later let the agent guardrails fail closed for free.

## 05 — Layer 2 · Three kinds of path

**Message:** the reference already runs all three kinds of path; the hybrid one is where the
human's job changed.

**Headline:** *Layer 2 · Three kinds of path*

**Visual:** three horizontal tracks, one per kind, each with three to five nodes. Deterministic
nodes are plain circles; probabilistic nodes have a soft orange halo; the hybrid track mixes
both and has the gate glyph (⚿) from the ADP deck. Locator glyph with layer 2 lit.

| Kind | Definition (the model's) | The reference's example | Nodes | State |
|---|---|---|---|---|
| **Deterministic** | Same input, same output; nothing reasons | **Deploy.** A merge to main is applied by the controller, drift is reverted, infrastructure applies before merge. | commit → controller → cluster → healthy | BUILT |
| **Probabilistic** | An agent reasons and acts; the output is a judgement | **PR review.** Every pull request is read by a cascade (rules, then a small classifier, then a frontier model) that labels it skip / skim / read and explains why. It never blocks. | PR opened → rules → classifier → label + comment | BUILT |
| **Hybrid** | A probabilistic step inside deterministic gates, with retries bounded and a human deciding on evidence | **Validate and promote a change.** A coding agent writes the change under a written contract; 22 validation jobs gate it; the agent fixes what fails when asked; a human merges on the evidence, not by re-reading the code; for one declared class (a dependency bump triage found nothing to read in) a policy merges instead. | spec → agent → ⚿ checks → human or policy merge → deploy | BUILT (retry limit is the human) |

**Standing caption:** The model's phrase for the hybrid path is "the human reviews the evidence
of the fix, not the code". That is exactly what the triage label and the check wall exist to
produce.

**Footer right:** "The human moved from typing to deciding".

**Speaker note:** The one honest gap on this screen: the model wants the retry count on the
hybrid path bounded by token cost. On the reference it is bounded by the owner's patience; a
flaky check is re-run by hand (screen 11 shows one). That is a path-definition gap, not a
tooling gap.

## 06 — Layer 3 · The harness: context, capability, execution, evaluation

**Message:** four harness components; three are built, the fourth is a tool, not yet a gate.

**Headline:** *Layer 3 · The harness*
**Sub:** *What an agent is given before it runs, and how its work is judged after.*

**Visual:** four panels in a row, each with a status chip. Locator glyph with layer 3 lit.

1. **Context** — BUILT. *What the agent knows going in.* Every service carries a short
   architecture page and a runbook under a schema; a validator fails the build on drift; the
   portal renders them and the agents read the same files. Five shared prompt snippets are
   versioned in one place. Long-term memory per agent and user, retrieved by meaning. The coding
   agent gets the same treatment: a written contract in the repository root. — *In the
   reference: the agent-docs contract, a built-in prompts ConfigMap, pgvector memory on the
   harness.*
2. **Capability** — BUILT. *What the agent may do.* Every tool is classified read / write /
   destructive in one file: 71 tools, 55 / 13 / 3. A write tool may only be bound behind an
   approval; a destructive one only by an admin-class agent. A tool missing from the file is
   denied, so a runtime upgrade cannot widen any agent silently. 60 tools bound across seven
   agents, 17 of them gated. — *In the reference: one taxonomy → generated admission policy →
   CI validator, all fail closed.*
3. **Execution** — BUILT, alpha. *Where the agent runs.* Each conversation is a kernel-isolated
   sandbox that suspends to a snapshot between turns and resumes anywhere; the API key never
   enters it; idle sessions are deleted after 30 days. — *In the reference: kagent 1.0 on Agent
   Substrate, gVisor actors, two shared workers.*
4. **Evaluation** — BUILT (thin). *Was the work right?* A corpus of golden questions mined from the
   agents' real history (eight so far, each answer verified against repo facts), scored by rules
   first, a small classifier second, a frontier model only when unsure. Since 2026-10-13 it runs
   automatically after any deploy that changed an agent's definition: a commit status on the
   change and an alert on regression. One agent has a corpus. — *In the reference: the eval
   corpus, the cascade judge, the post-sync gate.*

**Footer right:** "Four built; evaluation is the thinnest".

**Speaker note:** The model says evaluation is where the industry struggles most. The reference
agrees from experience: it is the thinnest layer, and the corpus is eight questions. Say the
number; it is more convincing than "we have evals".

## 07 — Layer 3 · Governance: identity, security, agent observability

**Message:** governance is the half of layer 3 that does not depend on the model behaving.

**Headline:** *Layer 3 · Governance*
**Sub:** *Who the agent is, what it may not leak, and what it did.*

**Visual:** three panels, each with a status chip and a monospace evidence line where one exists.

1. **Non-human identity** — BUILT. Each agent and each tool server is a principal with its own
   credential, scoped to one path in the vault; keys are injected at the network edge, never
   inside the agent. Since 2026-10-13 every caller is a principal too: the mesh verifies a token
   (the person's single sign-on, or a short-lived platform token for an automation) in front of
   the agent gateway and writes the verified identity into the request; the record, memory and
   session ownership key on it. Remaining: the UI is one identity until it gets its own login.
   — evidence line: `one Vault role per credential; 0 API keys on clients; 2 token issuers, 0
   self-declared identities`
2. **Security** — BUILT. The capability classes above; admission refuses an unsafe agent
   definition; the firewall and allow-list at the edge; the audit record redacts tool responses
   at its boundary because they can contain secret material; the evaluation corpus is checked
   for secrets in CI. — evidence line: `denied: binds write/destructive tools in an MCP binding
   without requireApproval: true`
3. **Agent observability** — BUILT. Not the application's metrics: the agent's. Every tool call
   recorded with its arguments and its approval outcome; a daily job flags any write with no
   decision and alerts if the audit itself goes quiet; a classifier triages sessions by risk;
   the record is exported to object storage and read in a small web UI; token use is stored per
   model call (48% of input served from cache, 86–98% inside a tool loop); traces go to the
   same tracing backend as the applications. — evidence line:
   `{"check":"agent-audit-ungated","severity":"ok","ungated_invocations":0}`

**Standing caption:** The model's own test for layer 3 is "universal across agent platforms".
None of these three panels knows what the agents are for.

**Footer right:** "Assume the model can be talked into anything".

**Speaker note:** Spend the time on the identity gap. It is the piece a company would do
differently from day one (agents and callers in the corporate identity provider), and the
reference's workaround (allow-list plus one shared identity) is the kind of thing that is fine
for one owner and wrong for a team.

## 08 — Everything as code, in one repository

**Message:** the model's "agent infrastructure as code" is literal on the reference; one
repository, one review path, for every kind of thing.

**Headline:** *Everything as code, in one repository*

**Visual:** a single tall "repository" panel with rows, each row a kind of thing with a small
file-type glyph, and on the right one shared column: *reviewed by → triage → 22 checks →
human merge → applied*. Draw the shared column once, spanning all rows.

| Kind of thing | What it is in the repository | Who consumes it |
|---|---|---|
| An agent | a definition file: prompt, model, tool bindings, which bindings need approval | the runtime, the admission policy, the audit |
| How an agent runs | a harness file: runtime image by digest, memory, snapshot location | the runtime |
| A tool server | a server manifest plus one scoped credential from the vault | the agents that bind it |
| What tools may do | one taxonomy file → a generated admission policy | the API server, CI, the audit |
| Shared prompt text | one versioned snippet file | six agents at once |
| Docs and runbooks | a schema-checked page pair per service | the portal, the agents, the on-call human |
| The coding agent's contract | a contract file in the repository root | the coding assistant |
| How pull requests are triaged | a policy file with thresholds and component weights | the triage workflow |
| What "valid" means | the check workflow: 22 jobs and the generators they drift-check | every pull request |
| The evaluation corpus | golden questions with cited sources | the judge |
| The ledger | tasks, invariants, bugs and research notes: 104 / 85 / 28 / 45 | the owner, the agent, the manifests that cite it |
| The audit jobs | four scheduled jobs: export, ungated check, risk triage, archive | the record, the alerting |

**Standing caption:** Since July, 79% of commits to this repository carry an agent co-author
(568 of 717). The agent's own definition is changed the same way, by a pull request it may have
written and a human merged.

**Footer right:** "One review path for prompts, policy and pipelines".

**Speaker note:** The model warns that prompts and skill files will have the same versioning
and audit problems every platform had before. The reference's answer is unglamorous: they are
files, next to the manifests, reviewed by the same workflow. Nothing special was built for them.

## 09 — The eight core paths, scored

**Message:** the model names eight paths that make up the development life cycle; the
reference has six built and two partial; the last missing one, dispatch, was built on 2026-10-12.

**Headline:** *The eight core paths, scored*

**Visual:** a 2 × 4 grid of cards in the model's order, top row the pre-merge paths, bottom row
the post-merge ones. Each card: the path name, its kind (DET / PROB / HYBRID), one line on what
does it today, and a status chip. No card is empty; the two PARTIAL cards (implement,
remediate) sit at the ends of the bottom row, and the dispatch card carries a small "new" tag.

| # | Path | Kind | On the reference today | State |
|---|---|---|---|---|
| 1 | **Dispatch work** — route a signal to the right agent or human | hybrid | A declared table maps every alert title to a read-only agent investigation, a human, or nobody; CI refuses an alert with no row. Budgeted per hour, deduplicated per alert, and the dispatcher sends under its own identity and never answers an approval. The finding reaches chat with the session to continue by hand. A pull request still routes itself to triage. | BUILT (read-only) |
| 2 | **Retrieve context** — assemble what the work needs | prob | The docs contract, the catalog, long-term memory, the ledger, the repository contract. The strongest path. | BUILT |
| 3 | **Implement** — write and modify code | prob | A coding agent at the owner's desk writes most commits under the contract. No in-cluster agent writes code or opens a pull request; the runtime supports sandboxes, none is defined. | PARTIAL |
| 4 | **Validate change** — gate it | hybrid | 22 jobs: lint, schema, contracts, generated-file drift, admission and upgrade drills against a real cluster; the agent fixes failures when asked. | BUILT |
| 5 | **Promote change** — merge, approve, release | hybrid | Triage labels and explains. A written policy, read from the main branch so a pull request cannot loosen it, merges one class with no human: dependency bumps that triage found nothing to read in, outside a deny list, every check green, by a dedicated identity that is not the author's. A human merges everything else, rebase-only. Infrastructure applies before merge. | BUILT (policy for one class, one human for the rest) |
| 6 | **Deploy** — provision and roll out | det | The controller applies main, prunes drift, self-heals; per-app AWS resources are declared alongside. | BUILT |
| 7 | **Observe** — know it is working | hybrid | Metrics, logs, traces, runtime security with fail-closed alerts; a read-only agent inspects live state when asked, and since the dispatcher, unprompted: an alert becomes an investigation and a finding in chat, secrets scrubbed. The audit's risk triage is a classifier. | BUILT |
| 8 | **Remediate** — diagnose and fix production | prob | Two operator agents with every mutating tool behind approval, decided in the UI or from the command line. The diagnosis now arrives unprompted; the fix is still human-initiated. | PARTIAL |

**Standing caption:** The model's advice is to start with two paths, validate change and PR
review, because they force the minimum infrastructure: identity, dispatch, execution,
evaluation, observability. The reference started with exactly those two and now has all five;
dispatch came last, and started read-only.

**Footer right:** "Six built, two partial, none missing".

**Speaker note:** The scoring is the point of the deck. A company's map will differ, but the
exercise is the same: eight cards, three states, and the weakest card is where the next design
document goes. Say out loud that dispatch came last because one owner was his own dispatcher,
and that when it came it was read-only by design: a signal opens an investigation, never a
change. Promotion is the same shape: one declared class, a policy the pull request cannot edit.

## 10 — Where the reference sits on the maturity ladder

**Message:** level 2 as the daily mode, level 3 in places, level 4 declined on purpose.

**Headline:** *Where the reference sits on the maturity ladder*

**Visual:** four rungs drawn as four stacked bands, bottom to top, each with a status chip and
one line of evidence. The band for level 2 is filled orange (where the reference lives); level
3 is outlined orange; level 4 is dashed with a small lock glyph.

| Level | The model's definition | The reference | State |
|---|---|---|---|
| **1 · Human in the loop** | Assistance in the editor; the human does the work | Yes, and it is the smallest part | BUILT |
| **2 · Human as validator** | Agents do the work; humans check it | The daily mode: the agent writes, triage and checks grade, the human merges. 79% of commits since July. | BUILT |
| **3 · Human orchestrates** | Agents are directed in parallel; throughput is the measure | In places: the coding assistant runs sub-agents; an in-cluster orchestrator hands off to specialists; one plan became 26 merged pull requests in a day; on the platform, a dispatcher hands alerts to an agent unprompted. No agent orchestrates other agents across the platform. | PARTIAL |
| **4 · Human out of the loop** | No human trigger; a signal becomes a shipped change | Declined, with one declared exception: a dependency bump that triage found nothing to read in is merged and deployed by policy with no human. Every other mutation has a human trigger or a human gate; scheduled jobs are deterministic. | NOT YET, by choice (one class excepted) |

**Standing caption:** The model says most organisations are between levels 1 and 2 and that
level 2 is unreachable without a platform. The reference is a one-person demonstration that
the platform, not the model, is what gets you there.

**Footer right:** "Level 2 daily; level 3 in places; level 4 for one declared class".

**Speaker note:** Resist the urge to claim level 3. The honest statement is that throughput
rose (26 pull requests in a day) because of orchestration at the desk, not on the platform. The
dispatcher from screen 09 is the first level-3 piece on the platform; the next is an agent that
hands work to agents. The level-4 exception is deliberately boring: it is the class a human was
merging without reading, now merged by a policy the pull request cannot change.

## 11 — In use: one change through every layer, in 23 minutes `recorded`

**Message:** one real change touched every band of the map, and the map is why it was safe
to merge in 23 minutes.

**Headline:** *In use: one change through every layer*
**Sub:** *The change that let an operator approve or reject an agent's paused tool call from the
command line. 2026-10-12.*

**Visual:** a vertical timeline with eight nodes, each tagged with the band it belongs to (L1 /
L2 / L3 / as-code) and a "who" badge (HUMAN / AGENT / MACHINE). Times are relative to the pull
request opening.

| When | What happened | Band | Who |
|---|---|---|---|
| before | The owner asked how best to talk to the agents from a coding assistant; the agent researched the public protocol docs and proposed three fixes. The owner: "the word is given". | L2 · decide | HUMAN + AGENT |
| before | The agent wrote the change: a protocol extension in the client, two command-line flags, 49 tests, two runbook entries. Nine files, +498 / −31. | as-code · implement | AGENT |
| 0:00 | Pull request opened. Triage read it: the small classifier was nearly split (skim 0.50, read 0.47) and the policy's threshold rounded it up to **read**, with four specific things to look at. | L2 · PR review (prob) | MACHINE |
| 0:01 | 27 checks started; 4 skipped as not applicable to the changed files. | L2 · validate (det) | MACHINE |
| during | One check failed: the admission drill's throwaway cluster timed out booting. The owner re-ran it; it passed. The retry was a human. | L2 · validate | HUMAN |
| 0:23 | Merged, rebase-only, with the admin override. | L2 · promote (hybrid) | HUMAN |
| after | Acceptance on the live platform: an operator agent asked to delete a throwaway object; the call paused; rejected from the command line (object untouched, no retry); asked again; approved (object gone). | L3 · execution + approval | HUMAN + AGENT |
| after | The runbook, the coding assistant's shortcut and the agent's memory were updated in the same change set. | as-code · close the loop | AGENT |

**Recorded fragment (monospace, trimmed; the triage comment's signals block):**
```
decided_by: jev
size: large (529 lines)
hot components: base-apps/kagent, tests/eval-corpus
Jev: P(skip)=0.03 P(skim)=0.50 P(read)=0.47 confidence=0.25
```

**Standing caption:** Twenty-three minutes from open to merge is not the achievement. The
achievement is that it was safe: a classifier rationed the human's attention, 23 machines
checked the change, and the live acceptance ran against a gate that policy guarantees exists.

**Footer right:** "Fast because the gates are cheap, not because they are skipped".

**Speaker note:** Point at the two human nodes: a retry and a merge. That is the whole human
contribution between the decision and the running system. Then point at the policy rounding
the near-split classifier up to "read": the policy is written to be conservative when unsure,
which is a one-line setting and the right default.

## 12 — In use: the query that justified the governance layer `recorded`

**Message:** the governance layer exists because one query against the agents' own history
showed a gate had been silently stripped for months.

**Headline:** *In use: the query that justified the governance layer*
**Sub:** *The audit's own origin story, from its source file.*

**Visual:** a single dark panel with the monospace finding, and under it a three-node track
labelled "what it caused": taxonomy → admission policy → daily audit check, all three with
BUILT chips.

**Recorded fragment (monospace, verbatim from the audit tool's header):**
```
k8s_agent — gated tools actually invoked:
  k8s_execute_command    14 calls   on 2026-07-09
  k8s_delete_resource     1 call    on 2026-04-19

approval requests raised by k8s_agent in ALL its history:  0
```

**Bullets:**
- The agent's definition listed those tools as requiring approval. The deployment tooling was
  stripping the field on every sync. The runtime recorded every call; nothing read the record.
- One query found it. The response was not a prompt change: a tool taxonomy that denies by
  default, an admission policy that refuses a definition binding a write tool without a gate, a
  CI validator that refuses the same before merge, and a daily job that flags any gated call
  with no decision and alerts if it goes quiet.
- Since go-live of the current runtime: zero gated calls without a decision.

**Footer right:** "You cannot score what you did not record".

**Speaker note:** The model's harness-and-governance split can sound abstract. This is the
concrete version: capability without observability was a gate that did not exist. The lesson
the reference drew is the order on screen 13: record first, then gate, then evaluate.

## 13 — What gets built next, in what order

**Message:** the map makes the next pieces obvious, and their order follows the
dependencies, not the excitement; one of the five is already solid.

**Headline:** *What gets built next, in what order*

**Visual:** a five-node track, nodes NOT YET (dashed) except where marked BUILT, each with the
path or pillar it fills (from screens 06, 07, 09) and one line of "why this order". Under the
track, a small "what a new agent or path reuses" checklist panel.

| # | Piece | Fills | Why now, why this order |
|---|---|---|---|
| 1 | **Evaluation as a gate** — score the knowledge agent against the corpus after every deploy that changed it; grow the corpus from real history | harness · evaluation (BUILT 2026-10-13, post-deploy) | Done as a post-sync gate (a status on the change, an alert on regression); pre-merge would need an in-cluster runner. Still thin: one agent, eight questions. |
| 2 | **Callers as principals** — authenticate the gateway's callers, one identity per person and per automation, in front of the shared one | governance · identity (BUILT 2026-10-13) | Done in the mesh: a waypoint in front of the agent gateway verifies the person's SSO token or an automation's platform token and sets the identity; the runtime's own calls stay inside the platform. Still thin: the UI is one identity until it gets its own login. |
| 3 | **A dispatcher** — a declared table from signal to recipient (an agent, a human, nobody), applied by a service | path 1 · dispatch (BUILT 2026-10-12) | Done, read-only as planned: an alert opens an agent *investigation*, never a change, and the policy-merged promotion class landed beside it (2026-10-13). Still to route: failing checks, issues, triage labels. |
| 4 | **Implement inside the platform** — a sandbox template so an in-cluster agent can run checks and open a pull request under its own identity, through the same triage, checks and promotion policy | path 3 · implement (PARTIAL → BUILT) | Needs 2 (its own identity); 3 is in place to hand it work. The pull request path is unchanged; the only new thing is who opens it. |
| 5 | **Remediate on signal** — the investigation's proposed fix becomes a pull request (via 4) or a gated operator call | path 8 (PARTIAL → BUILT) | Last, because it is the first path where an unprompted agent touches production, and it inherits every gate above. The observe half (path 7) is done. |

**Checklist panel — what a new agent or path reuses, unchanged:** the vault and its one-role-
per-credential pattern · the edge and allow-list · the tool taxonomy and admission policy ·
the docs contract · the triage policy and the 22 checks · the audit record and its daily check
· the evaluation corpus and judge. *A new agent is one definition file and one taxonomy review.
A new path is one workflow and one dispatch row.*

**Standing caption:** Nothing on this list is a new platform. Each is one row on the map
moving from dashed to solid, in the order the arrows point.

**Footer right:** "Record, then gate, then evaluate, then dispatch".

**Speaker note:** If asked for dates: none promised. The point is the ordering and that each
item is small because of what is already there. If asked why the dispatcher landed before
callers were principals (item 2): because it is read-only, sends under its own named identity,
and only an alert from the platform's own monitoring can reach it; the gateway gap still bounds
what it may be given, which is why item 4 waits.

## 14 — What maps, five questions, close

**Headline:** *What maps to a company, and what doesn't yet*

**Three columns:**
- **Maps directly:** the three-layer map as a planning tool (fill your own cards, three states);
  layer 1 reused unchanged; everything-as-code through the existing review path; the eight-path
  scoring; the order on screen 13.
- **Needs a decision:** who owns each path (a platform team, each product team, or split at the
  harness/governance line); who classifies tools and approves writes; where the dispatcher's
  table lives and who may add a row.
- **Still early:** the runtime is alpha; evaluation corpora are per domain and start small; the
  reference has one human and one shared caller identity, which a company replaces on day one.

**Five questions (no tooling in any of them):**
1. Which of the eight paths does your organisation already run, and which kind is each: deterministic, probabilistic, hybrid?
2. Where is your gate that nobody has queried: a permission the tooling might be stripping, a record nothing reads?
3. Who is the dispatcher today, and what would a declared table of signal → recipient change about their job?
4. What is your minimum infrastructure before a second team gets an agent: identity, dispatch, execution, evaluation, observability, and which of the five do you have?
5. Which level on the ladder do you want to be at in a year, and which single path moves you there?

**Close (orange card):**
- Headline: *The pieces were built one at a time. The map is what they add up to.*
- Sub: *Four decks, one anonymized reference, every screen traceable. The platform is live and
  can be shown: a question, a gate, a refusal, a pull request.*

**Footer:** left "Thank you", right "Recorded screens: 2026-10-12".

**Speaker note:** End on question 2. It is the one that sends people back to their own
platform with something to check tomorrow, and it is the reference's own origin story.

---

## Data provenance (for the numbers and quotes)

All measured on the reference repository and platform on 2026-10-12 unless noted.

| Claim | Source |
|---|---|
| The reference architecture: three layers, three path kinds, harness (context, capabilities, execution, evaluation) and governance (identity, security, observability), "as code", the four maturity levels, the eight core paths, "start with validate change and PR review", the minimum infrastructure list | the Weave Intelligence ADP model as presented in the Platform Engineering community webinar "How to architect Agentic Development Platforms", 2026-06-19 (transcript in the owner's notes); the roadmap document in the repository frames onto the same model |
| 59 applications | 47 hand-written Argo CD Applications plus 12 generated by the managed-apps ApplicationSet |
| 12 validating and 1 mutating admission policies | the admission-policies manifests |
| 7-day traces, 30-day logs | the Coroot instance (`tracesTTL: 7d`) and the logging stack's retention |
| 71 classified tools: 55 read / 13 write / 3 destructive | the taxonomy ConfigMap's section counts |
| 60 tools bound, 17 gated, 7 agents, 7 tool servers | the agent templates' bindings, as counted for the agents-as-a-service deck |
| 5 shared prompt snippets | the built-in prompts ConfigMap |
| 30-day idle session retention; two shared workers; gVisor | the kagent deployment manifest and its docs (`sessionIdleTTL: 720h`) |
| 8 golden questions, mined from real history | the evaluation corpus file and its header |
| 48% of model input from cache; 86–98% in tool loops | token usage stored per model call, measured 2026-10-11 |
| Policy denial text; audit `ungated_invocations: 0` | a server-side dry-run of a bad agent definition; the daily audit job's output (both as recorded for the ADP deck, 2026-10-11) |
| Ledger: 104 tasks, 85 invariants, 28 bugs, 45 research notes | row counts in the ledger's four tables |
| Four scheduled audit jobs | the CronJob names in the postgresql app (export, ungated, risk, archive) |
| 22 validation jobs | the check workflow (22 jobs plus a change detector); 27 checks on the recorded PR including triage, autosync and three Terraform statuses, 4 skipped |
| 79% agent co-authored commits since July (568 of 717); 74% since October (115 of 155) | `git log --since`, counting the agent co-author trailer |
| The recorded change: opened 15:59Z, merged 16:22Z, 9 files, +498/−31, label read, P(skim)=0.50 / P(read)=0.47 / confidence 0.25, 27 checks, 4 skipped, one re-run | the pull request's metadata, checks and triage comment via the GitHub API; the owner's session log for the flake and re-run |
| "read if P(read) ≥ 0.30" | the triage policy file's thresholds block |
| 49 tests in the change | the changed test file's collection count |
| Live acceptance: reject then approve on a throwaway object | the owner's acceptance run, 2026-10-12, as recorded for the agents-as-a-service deck |
| The audit's origin finding (14 calls, 1 call, 0 approval requests) | verbatim from the audit script's module docstring, which quotes the 2026-07-14 spike |
| "Gates stripped on every sync" | the roadmap document's spike result, 2026-07-14 |
| 26 pull requests merged in a day | merged-at timestamps, 2026-10-11, as recorded for the workflow deck |
| An in-cluster orchestrator hands off to specialists | the orchestrator template's description ("hand-off, not delegation") |
| The runtime supports sandbox templates; none defined | the kagent docs list the kind; no such manifest exists in the repository |
| The dispatcher: declared table, CI completeness check, hourly budget, own identity, no approvals; accepted live with a real alert and three replays | the dispatcher app and its tests; the ledger's acceptance entry, 2026-10-12 |
| Promotion: one class, policy read from main, a dedicated merge identity; first decisions on real pull requests | the promotion policy and workflow; the ledger entries closed 2026-10-13; the workflow's logged decisions |
| Callers are principals: two issuers, identity set by the mesh, session owners verified in the database | the kagent app's identity manifests and tests; the ledger's acceptance entry, 2026-10-13 |

## Do not claim

- That the reference implements the reference architecture *fully*: two paths are partial,
  evaluation is not a gate, and the runtime is alpha.
- Level 3 maturity for the platform. Orchestration happened at the owner's desk; one dispatcher
  routing alerts to one read-only agent is a first piece, not orchestration.
- That promotion merges anything but its one declared class, or that the dispatcher can change
  anything: it opens investigations and never answers an approval.
- That the webinar's authors reviewed or endorsed the reference; the frame is borrowed from
  their public material, the scoring is the owner's.
- That any pull request is blocked by triage (labels only) or reviewed by a second human
  (approvals are admin overrides).
- That the retry on the hybrid path is bounded by anything but the owner.
- That the 59 applications or the 22 checks are the right number for a company; they are the
  reference's.
- Any company, person, hostname or repository name: the reference is anonymous by design.

## If the infographic needs fewer screens

Keep, in this order: 03 (the frame with the reference on it), 05 (three kinds of path), 07
(governance), 09 (the eight paths, scored), 10 (the ladder), 11 (one change through every
layer, recorded), 13 (what gets built next). That is the argument in seven panels. If an eighth
fits, add 12 (the query): it is the deck's best single piece of evidence.
