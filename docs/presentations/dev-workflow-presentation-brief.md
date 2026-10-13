# From intent to running system — the development workflow, screen by screen

**Purpose:** a complete description of a 15-screen briefing on how changes reach this platform
when an AI agent writes most of them: spec-driven design, an agent executing, an automated
triage of every pull request, a wall of checks, and a merge that is also the deployment. It is
written for an agent that will turn it into infographics, in the same form as
`adp-concept-presentation-brief.md` (the Agentic Developer Platform concept deck). The rendering
built from it is `dev-workflow-infographics.pdf` beside it, with `dev-workflow-speaker-notes.md`. Every screen
gives: the message, the exact copy, the visual to draw, and speaker notes. Data provenance is at
the end.

**As of:** this brief describes the platform before 2026-10-13. Since then two pieces exist that it does not mention: `review:skip` Renovate PRs are merged by a promotion workflow under `.github/promotion-policy.yaml` (so "Renovate never merges" and "the owner merges everything" now read "Renovate never merges; promotion merges its skip class, the owner merges the rest"), and a dispatcher routes Grafana alerts to read-only agent investigations (`base-apps/dispatcher/`). The current map is `docs/presentations/whole-picture-presentation-brief.md`.

**Audience:** engineering leadership; first target is the company's AI pathfinders group. They are
technical but not Kubernetes specialists. Vocabulary rule: name the mechanism in plain words
first; the specific tool goes in a smaller "in the reference" line, never in the headline.

**Framing:** "the agent moved the bottleneck from writing to deciding, so the workflow is built
around deciding". The reference is the same anonymized platform as the ADP deck: a small
Kubernetes platform run as a GitOps repository by one owner, where an agent has written most
of the commits since mid-2026 and the owner merges them. Call it "the reference" or "the
reference repository". No hostnames, repository name, personal or company names.

**Relationship to the ADP deck:** that deck is about agents *running on* the platform as governed
principals. This one is about agents *changing* the platform, and what sits between an agent's
pull request and a running system. Screen 05 is the hinge between the two: the in-cluster agents
read the same docs this workflow produces.

**Tone:** confident, plain, evidence-first. Show the failure modes as deliberately as the
features (the mis-triaged PR, the follow-up-fix rate, the solo-owner gap). No "AI-powered", no
"revolutionary", no emoji.

**Format of the source:** guided (presented live by the owner), so each screen carries little
text and the speaker notes carry the argument. For an infographic, promote the speaker note's key
sentence onto the visual where it helps.

## Visual identity

Identical to the ADP deck so the two can be shown together. Carry over unless the infographic
agent has a house style to follow:

- **Background:** near-black with a subtle diagonal gradient (`#1a1a1a` → `#2d2d2d` → `#1a1a1a`).
- **Accent:** one orange, `#FF5722`, for the focal card, bullets, numbers, highlights; 14% opacity
  for hot panels. A single green, `#7ed957`, only for "done / ok" states. For this deck add one
  reserved use of orange: the **stop glyph** of a check that failed or a gate that held.
- **Text:** white; secondary text white at 62% opacity. Dark text on the orange card.
- **Type:** display face Archivo Black; body face Space Grotesk; a monospace for recorded output.
- **Frame:** screen number top-left in orange (01…15); a breadcrumb of the sections top-right
  (Concept · The loop · Stages · In use · Governance · Reference · Discussion) with the current
  one lit; footer left (section) and right (a one-line takeaway).
- **Components:** orange focal card for title/close; thin-bordered translucent panels; stat tiles
  (big orange number, small grey label); nodes on a track for the loop; a **"who" badge** on each
  stage (HUMAN / AGENT / MACHINE, small caps) which this deck uses on every stage screen.
- **A `recorded` pill** marks every screen whose content is a real transcript, comment or log.

## Section map

| # | Section | Screen |
|---|---|---|
| 01 | Concept | Title |
| 02 | Concept | The bottleneck moved from writing to deciding |
| 03 | The loop | One change, start to finish (the loop) |
| 04 | Stages | 1 · Decide first: spec-driven design |
| 05 | Stages | 2 · The agent does the work, under a written contract |
| 06 | Stages | 3 · Every pull request is triaged, none is blocked |
| 07 | Stages | 4 · The wall of checks |
| 08 | Stages | 4b · Docs are part of the change |
| 09 | Stages | 5 · Merge is the deployment |
| 10 | Stages | 6 · Close the loop |
| 11 | In use | Recorded: one triage comment, including its own mistake |
| 12 | In use | Recorded: one day, one plan, twenty-six pull requests |
| 13 | Reference | What it took, in what order |
| 14 | Reference | What maps to a company, and what doesn't yet |
| 15 | Discussion | Five questions, and close |

---

## 01 — Title

**Message:** this is a workflow talk backed by a repository you can read.

**Copy (on the orange card):**
> **From intent to running system**
> A development workflow where an agent writes the change and the platform decides what a
> human must look at.

**Copy (below the card, secondary):**
> Concept, with one anonymized working reference. Every number has a date.

**Footer:** left "Engineering leadership briefing", right "2026".

**Visual:** the orange card bottom-left of a dark gradient, nothing else.

**Speaker note:** This is the companion to the ADP briefing. That one asked "how do agents run
safely on a platform". This one asks "how do agents change a platform safely", which is the
question every team hits first.

---

## 02 — The bottleneck moved from writing to deciding

**Message:** when an agent writes most changes, the scarce resource is human attention, so
the workflow must allocate it.

**Headline:** *The bottleneck moved from writing to deciding*

**Stat tiles (four):**
- **80%** — of commits since July 2026 carry an agent co-author trailer (562 of 701)
- **0** — pull requests with a GitHub review, measured 2026-09-30 over 100 merged
- **32%** — of merged PRs were followed by a fix within 72 hours (63 of 200)
- **1** — human. The owner merges everything.

**Body:**
> Review effort was spread evenly, which meant mostly not at all. The size of a change
> predicted its follow-up fixes (5% for tiny changes, about 50% for medium and large). The fix
> was not to review harder. It was to build a workflow that says, per change, *how much* a
> human must read and *which lines*.

**Visual idea:** a thin horizontal bar of 100 PR cards, all the same grey, labelled "attention,
before"; below it the same 100 cards coloured in three bands (skip · skim · read) labelled
"attention, after". The sameness of the first row is the picture.

**Footer right:** "Decide where attention goes, per change".

**Speaker note:** These numbers are from the triage design, measured before it was built. The
first instinct is "add required reviewers". With one owner that only creates a rubber stamp.
The workflow instead makes the platform do the first read.

---

## 03 — One change, start to finish

**Message:** six stages; each is owned by a human, an agent, or a machine; the output of
the last stage feeds the first.

**Headline:** *One change, start to finish*

**Visual:** six nodes on a track that bends back into a loop. Each node carries a "who" badge.

| # | Stage | Who | One line |
|---|---|---|---|
| 1 | Decide | HUMAN (with the agent asking) | Intent becomes a dated spec and a plan; the open decisions are asked, not assumed |
| 2 | Build | AGENT | The agent executes the plan under a written contract, in a branch, as pull requests |
| 3 | Triage | MACHINE | Every PR gets `skip`, `skim` or `read` and a reason; nothing is blocked |
| 4 | Check | MACHINE | Twenty-two validation jobs: lint, schema, contracts, generated-file drift, and drills against a real cluster |
| 5 | Merge & apply | HUMAN decides, MACHINE applies | Merge is the deployment; infrastructure changes apply before merge |
| 6 | Observe & record | MACHINE + AGENT | The ledger, the docs and the agent's memory are updated; the next PRs are opened automatically |

**Standing caption:** The loop is the product. Each stage was added when the previous one
produced more than a human could absorb.

**Footer right:** "Six stages, one loop".

**Speaker note:** Walk it once quickly, then spend the next six screens on the stages. Point
out that only two of the six have a human in them, and both are decisions, not typing.

---

## 04 — Stage 1 · Decide first: spec-driven design

**Message:** the human's decisions are collected up front, in writing, before the agent
touches anything; the specification is a ledger that outlives the task.

**Headline:** *Decide first*
**Sub:** *The agent asks; the human decides; the answers go into a dated file.*

**Three panels:**

- **Brainstorm → design spec.** A conversation that ends in a dated design document in the
  repository (`YYYY-MM-DD-<topic>-design.md`). The agent is required to ask about anything
  it cannot resolve from the code. *In the reference: 11 design specs since May 2026.*
- **Spec → implementation plan.** Phases, tasks, checkpoints, and the validation to run
  after each. Executed later, possibly unattended. *In the reference: 13 plans, the latest a
  cluster and agent-runtime upgrade executed in one day while the owner was away.*
- **The ledger.** One file holds the goal, constraints, interfaces, research findings,
  invariants, tasks and bugs, each numbered. Manifests and scripts cite the invariant they
  satisfy. A bug entry records cause and fix, not blame. *In the reference: 68 tasks, 55
  invariants, 25 recorded bugs; 58 files cite a ledger section.*

**Recorded (small, monospace): the seven decisions taken before the upgrade plan ran:**
```
In-place on production, no scratch-VM preflight
Retire the hand-built agent
Model split: larger model for operators, smaller for readers
Include the interim runtime step
Take the newest minor from the "latest" channel now
Bind read tools directly, not through a sub-agent
Port the audit as part of this plan
```

**Visual idea:** a funnel: many questions at the top, seven answers in the middle, one plan
file at the bottom. The ledger sits beside the funnel as a tall ruled page with numbered
sections.

**Footer right:** "Every decision has a date and a file".

**Speaker note:** The point of the ledger is not ceremony. When the agent later works alone,
the constraints ("no direct cluster edits", "a single control plane means every hop is an
outage") are already written, and a bug it causes is recorded with its cause next to the
invariant it broke. The ledger is written in a compressed notation to keep it short; it is
for the agent and the owner, not for presentation.

---

## 05 — Stage 2 · The agent does the work, under a written contract

**Message:** the agent is an executor with a contract: rules in the repository, provenance in
every commit, and memory across sessions.

**Headline:** *The agent does the work, under a written contract*

**Body:**
> The agent reads a short contract at the top of the repository: everything goes through
> Git, infrastructure applies before merge, never commit a secret, write the PR title so a
> classifier can read it. It works in a branch and opens pull requests. Each commit carries an
> agent co-author trailer and a link to the session that produced it, so the provenance of a
> change is in git, not in a chat log.

**Three callouts:**
- *Two kinds of agent.* The coding agent (a developer tool, run by the owner) changes the
  repository. The platform agents (running in the cluster, from the ADP deck) answer questions
  and act under approval. Both read the same documentation.
- *Memory.* The coding agent keeps a file-based memory across sessions: the platform's
  roadmap, standing authorizations, past mistakes ("merge before apply strands the change;
  happened twice"). Corrections become durable rules.
- *Authorization is explicit and scoped.* The owner grants the agent permission to merge its
  own green PRs for one named plan, in writing, with a date. Anything else, it asks.

**Visual idea:** a commit card with its trailer lines rendered large (`Co-Authored-By:` and a
session link), the rest of the commit greyed. Beside it a small stack labelled "memory" with
three example notes.

**Footer right:** "Provenance in git, not in chat".

**Speaker note:** The contract is a markdown file. Nothing enforces it at this stage; the
next three stages do. That is deliberate: the agent should be fast here and caught later by
machines, not slowed by ceremony.

---

## 06 — Stage 3 · Every pull request is triaged, none is blocked

**Message:** a risk policy in the repository decides how closely a human must read each PR;
it labels, explains, and never blocks.

**Headline:** *Every pull request is triaged. None is blocked.*

**Visual:** a three-step cascade, left to right, with a stop glyph where each step can decide:

1. **Rules** — paths and resource kinds that are always "read" (access control, admission
   policies, networking, storage, infrastructure code, CI itself); changes that are always
   "skip" (docs only; an image tag bump of four lines or fewer). *Decides about half.*
2. **A small classifier** — a cheap model gives a probability for skip / skim / read, with
   "hot components" weighted by their measured follow-up-fix rate. *Decides most of the rest.*
3. **A frontier model** — called only when the classifier is unsure. *The exception.*

**Output (one card):** a label `review:skip | skim | read`, and one sticky comment: why, what
to look at (three or four bullets naming files and hunks), and the next commands.

**Four rules worth the slide:**
- The policy is read from the **base** commit, so a PR cannot loosen its own triage.
- Changes to the CI workflows are always "read".
- Failures fall back to "read", never to "skip".
- Nothing blocks. The owner can merge a "read" PR unread; the label is a recommendation.

**Stat tiles:** last 100 merged PRs on the reference: **46 read · 26 skim · 24 skip**; 30 of
the 100 were opened by the dependency bot.

**Footer right:** "Policy as a file, decisions as labels".

**Speaker note:** Calibration is a command, not a feeling: it replays the policy over the last
200 merged PRs and computes the follow-up-fix rate per component. The "hot components" table in
the policy came out of that run and gets refreshed when thresholds change. The retro command
closes the loop in the other direction: it compares a past review session with the fix PRs
that followed and proposes edits to the reviewer.

---

## 07 — Stage 4 · The wall of checks

**Message:** the checks are the real review; they are of four kinds, and the fourth kind runs
against a real cluster.

**Headline:** *The wall of checks*
**Sub:** *Twenty-two jobs. Four kinds. The last kind boots a real cluster.*

**Visual:** a wall of 22 small tiles grouped into four rows, each row labelled; a few tiles
show a "skipped" state in grey (jobs that only run when their paths changed).

| Kind | What it catches | In the reference |
|---|---|---|
| **Lint and schema** | Malformed YAML; manifests that don't match the Kubernetes API schema; infrastructure code that doesn't parse | yamllint, kubeconform with CRD schemas, terraform fmt/validate/tflint, a security scan and a cost estimate on infrastructure PRs |
| **Contracts** | An app without its docs; a catalog entry pointing at nothing; an agent with an unscoped credential or an unclassified tool; a WAF exception without a reason; a secret in the eval corpus | seven validators, one per contract, all fail-closed |
| **Generated-file drift** | A hand edit to a file that a generator owns | five generators run with `--check`; a bot commit regenerates the docs index on the PR |
| **Drills against a real system** | A policy that reads correctly but behaves differently in the API server; a backup script that produces an unrestorable artifact | admission policies are tested by booting a real single-node cluster of the same version and server-side dry-running fixtures; backup and restore scripts are drilled against a real cluster, database and vault in containers |

**Callout (orange):** *Static tools lied twice.* The schema checker skipped a whole policy
kind; the policy CLI mis-evaluated parameterised policies. Hence the real cluster in CI.

**Footer right:** "The checks are the review".

**Speaker note:** The order matters: cheap checks fail fast; the drills take minutes and only
run when their paths changed. Four of the 22 jobs skipped on a docs-only PR. The honest
framing: these catch the classes of mistake the owner has already made once. The bug ledger
is where the next check comes from.

---

## 08 — Stage 4b · Docs are part of the change

**Message:** documentation is an artifact with a test, written beside the thing it
describes, with provenance that says whether a human or an agent last checked it.

**Headline:** *Docs are part of the change*

**Body:**
> Every in-scope component carries three hand-written files next to its manifests: a
> catalog entry (owner, dependencies), an architecture page, and a runbook (symptom → check →
> fix). A validator fails the PR if one is missing, if a referenced file doesn't exist, or if
> the page is stale. The same files are rendered for people in the developer portal and read by
> the in-cluster knowledge agent. One source, two readers.

**Three callouts:**
- *An open format.* The pages follow a published knowledge format (markdown with frontmatter,
  in git, beside the subject), so any tool or agent that speaks it can read the repository
  without a bespoke parser.
- *Provenance is derived, not typed.* Who generated a page (agent or human) comes from the
  commit trailers; "verified by a human" requires a human-signed review line; pages go stale
  after 180 days unless re-reviewed.
- *Per-claim citations.* A sentence that is easy to get wrong can cite the exact file it comes
  from, and the validator checks the citation resolves.

**Visual idea:** one folder drawn as three cards (catalog · docs · runbook) with two arrows out:
one to a portal window, one to the agent from the ADP deck. A small stamp on the docs card:
"generated by: agent · verified by: human · stale after: 180 d".

**Footer right:** "One source, two readers".

**Speaker note:** This is the hinge with the ADP deck. The reason the knowledge agent can answer
"what is deployed and where" is that this stage makes the docs exist and keeps them honest.
Say plainly: docs written by an agent are marked as such, and only a human-signed review
changes that mark.

---

## 09 — Stage 5 · Merge is the deployment

**Message:** the merge is the deployment, so the branch rules and the apply order are the
real change control; infrastructure applies before merge, not after.

**Headline:** *Merge is the deployment*

**Visual:** a track with three nodes after "merge": **sync** (the GitOps controller reconciles
the cluster to the commit, pruning what was removed and healing drift), **admit** (the API
server's admission policies evaluate the result, failing closed), **run**. A side branch before
"merge" labelled **infrastructure applies first**.

**Four panels:**
- *Branch rules.* Rebase merges only; no force-push; a required approval. With one owner the
  approval is an admin override, recorded as such.
- *Infrastructure before merge.* Infrastructure code is planned on the PR and applied by an
  in-cluster runner **while the PR is open**, from a comment or a gated Action with a required
  reviewer. Merging first strands the change silently. This was learned twice and is now a
  memory rule and a ledger bug.
- *Cluster after merge.* The GitOps controller syncs `main` with prune and self-heal on. There
  is no manual apply step; a direct edit to the cluster is reverted.
- *The last gate.* Native admission policies in the API server evaluate every agent definition
  and every pod at apply time. They fail closed: an evaluation error is a denial.

**Footer right:** "Apply order is the change control".

**Speaker note:** The counter-intuitive part is "infrastructure before merge". It exists because
the apply needs credentials and reachability that the hosted CI cannot have, so it runs in the
cluster, and the only safe moment is while the PR can still be fixed. The dependency bot's PRs
go through exactly the same path; it never merges.

---

## 10 — Stage 6 · Close the loop

**Message:** what the system learns after a merge is written down somewhere the next change
will read.

**Headline:** *Close the loop*

**Four panels (each with a "feeds" arrow back to a stage number):**
- *The ledger.* Tasks are closed with a timestamp; bugs are recorded with cause, fix and the
  invariant they broke. → feeds stage 1.
- *The agent's memory.* Corrections become rules ("one push per PR", "merge only after
  apply is green"). → feeds stage 2.
- *Calibration and retro.* The triage policy is recalibrated against the last 200 PRs; the
  review retro compares a review session with the fix PRs that followed. → feeds stage 3.
- *Automatic next PRs.* The dependency bot opens the week's version bumps on Monday after
  the Sunday vulnerability scan; the daily agent audit and the eval corpus report on the
  platform agents. → feeds stage 2 and 3.

**Standing caption:** Nothing here is a meeting. Each is a file, a job, or a command.

**Footer right:** "Lessons go where the next change reads them".

**Speaker note:** This is why the ledger is in the repository and not in a tracker: the agent
that executes the next plan reads it, and so does the agent that triages the next PR (through
the policy that calibration rewrote).

---

## 11 — In use: one triage comment, including its own mistake `recorded`

**Message:** the triage explains itself, and when a rule fires wrongly it says so; that is
why it labels and does not block.

**Headline:** *In use: one triage comment, including its own mistake*

**Bullets:**
- The PR added a presentation deck and documentation, nothing else.
- A rule-level signal ("a cluster version string changed") matched a version mentioned
  inside a document, so the rules forced `read` without consulting the classifier.
- The model that writes the comment noticed, said so in the first bullet, and still gave the
  owner three useful things to look at.

**Transcript (verbatim, trimmed; keep the wording):**
```
review:read
Why: always_read.signals k3s_version_change
What to look at:
- No k3s version change detected — The signal appears to be a false positive; this PR
  adds documentation and presentation files only, with no changes to infrastructure
  code, manifests, or Kubernetes configuration
- docs/presentations/README.md is the key file — Contains instructions for presenting
  the deck and explains the anonymization, file structure, and regeneration process
- Verify anonymization in presentation files — The large PDF and markdown briefs
  should contain no hostnames, repo names, or personal identifiers
Signals: decided_by: rules · size: large (1082 lines)
```

**Infographic idea:** the comment drawn as a card; the "Why" line struck through in orange with
the first bullet's "false positive" highlighted; a small label "rules fired, model corrected".

**Footer right:** "Reference repository, 2026-10-12".

**Speaker note:** Say that this happened the day before the talk. The signal is a regex over
changed lines; a doc that quotes a version string trips it. The fix is a one-line policy change,
and because the policy is read from the base branch, that fix only takes effect after it merges.
A blocking check would have made this a nuisance; a label made it a footnote.

---

## 12 — In use: one day, one plan, twenty-six pull requests `recorded`

**Message:** with decisions taken up front, an agent executed a cluster upgrade and an agent
runtime migration in one day; the workflow caught and recorded its mistakes.

**Headline:** *In use: one day, one plan, twenty-six pull requests*

**Timeline (left to right, one day, 2026-10-11):**
- **Morning.** The plan is written; seven decisions are asked and answered; the ledger gets
  the new tasks and invariants; the owner leaves for the day.
- **Midday.** Backups are drilled, then taken. A backup script is found to record a truncated
  dump when its transport drops: fixed, drilled, recorded as a bug with a new invariant.
- **Afternoon.** The three nodes are upgraded one minor version; the API was unavailable for
  42 seconds. The agent runtime is replaced in place; seven platform agents come up on new
  models.
- **Evening.** The audit job that watches agent tool calls is found to have been blind for
  about 90 minutes after the runtime swap (the event format changed under it). Fixed, recorded,
  and a rule added: replay a known gated call after every runtime change.
- **Count:** 26 pull requests merged that day, PR numbers 725 to 750. Every one labelled and
  checked; the owner's explicit, dated grant allowed the agent to merge its own green PRs for
  this plan only.

**Stat tiles:** **26** PRs · **42 s** API outage · **4** bugs recorded (B25–B28) · **0** data loss.

**Infographic idea:** a single horizontal day-line with the four events as nodes; the two
"found a bug" nodes drawn as orange stop glyphs that turn into green "recorded" ticks.

**Footer right:** "Decisions first, then unattended execution".

**Speaker note:** The unattended part is only possible because of stages 1, 4 and 6. The two
bugs are the point of the slide, not an embarrassment: both were found by the workflow (a drill,
a replay), both are in the ledger, and both produced a new check.

---

## 13 — What it took, in what order

**Message:** the workflow was added in layers over eight months, each after the previous one
produced a problem.

**Headline:** *What it took, in what order*

**Timeline:**
| When | Added | Because |
|---|---|---|
| Feb 2026 | CI validation on every PR; GitOps controller as the only deploy path | manual applies drifted |
| Jul 2026 | The docs contract and its validator; the open knowledge format | agents could not answer "what runs where" |
| Jul 2026 | The specification ledger (goal, constraints, invariants, tasks, bugs) | a multi-week upgrade walk needed decisions that outlived a chat |
| Sep 2026 | Native admission policies tested against a real cluster in CI | static tools mis-evaluated them |
| 30 Sep 2026 | PR triage with a risk policy in the repository | 100 merged PRs, zero reviews, 32% follow-up fixes |
| Oct 2026 | Weekly dependency PRs, never auto-merged | upstream fixes were the main CVE route |
| 11 Oct 2026 | A full upgrade executed by the agent from a plan, unattended | the previous layers made it safe to try |

**Footer right:** "Each layer answered the last layer's problem".

**Speaker note:** Nothing here was built speculatively. The order is a reasonable default for a
team starting now: deploy path first, docs contract second, ledger third, triage when volume
makes it necessary.

---

## 14 — What maps to a company, and what doesn't yet

**Message:** the mechanisms transfer; the ownership decisions do not come with them; and the
reference has a solo-owner gap that a company must close differently.

**Headline:** *What maps to a company, and what doesn't yet*

**Three columns:**

- **Maps directly.**
  - A risk policy as a file in the repository, read from the base branch.
  - Triage that labels and explains, never blocks.
  - A docs contract with a validator and derived provenance (agent-written vs human-verified).
  - Generated files checked for drift in CI.
  - Drills against a real system for the checks that static tools get wrong.
  - Agent provenance in commit trailers.
- **Needs a decision.**
  - Who owns the risk policy, and who may change the thresholds.
  - What "read" means with more than one reviewer: a quota, a rota, a required team.
  - Whether an agent may ever merge its own PR, and under what written grant.
  - Which checks become required (blocking) once a team depends on them.
- **Still early.**
  - The reference has one owner, who approves their own PRs with an admin override. A company
    needs a second principal, human or machine, before that pattern is acceptable.
  - The classifier's calibration is from one repository's history; a new repository starts
    with rules only.
  - The retro and explainer are developer-tool skills, not a service.

**Start here (orange card):** one repository → a risk policy with rules only → triage labels on
every PR for a month → calibrate → then add the classifier.

**Footer right:** "Reuse the mechanisms. Decide the ownership."

**Speaker note:** Be direct about the solo-owner gap; the audience will see it. The mechanisms
were designed for a team (the policy has an owner field in spirit, the triage never
blocks) but have only been run by one person.

---

## 15 — Five questions, and close

**Message:** the group owns the decisions; the reference is something concrete to argue with.

**Headline:** *Five questions for the group*

**Numbered list:**
1. **Decisions.** Where do our design decisions live today, and could an agent read them
   before starting work?
2. **Attention.** If an agent opened most of our PRs, what would decide which ones a human
   reads?
3. **Checks.** Which of our review comments could be a check instead? Which checks need a real
   system to be trusted?
4. **Docs.** What would a docs contract with a validator look like for one of our services, and
   who signs "human-verified"?
5. **Ownership.** Who owns the risk policy, and under what written grant may an agent merge?

**Close (orange card):**
> The agent writes the change. The platform decides what you must read.
> The reference can show a triage, a failed check, and a blocked apply.

**Footer right:** "Decide the operating model before the agent opens its first PR".

**Speaker note:** End on the questions. If there is time, show one PR end to end: the triage
comment, the checks wall, the sync. The ADP deck is the natural follow-on if the group wants
the "agents running on the platform" half.

---

## Data provenance (for the numbers and quotes)

All measured on the reference repository on 2026-10-12 unless noted.

| Claim | Source |
|---|---|
| 80% of commits since July 2026 carry an agent co-author (562 of 701) | `git log --since=2026-07-01`, counting the `Co-Authored-By: Claude` trailer |
| 0 PRs with a GitHub review; 32% follow-up-fix rate (63 of 200); size buckets 5% / 28% / 52% / 48% | the PR-review toolkit design document, measured 2026-09-30 |
| 11 design specs, 13 implementation plans | the two plan directories in the repository |
| 68 tasks, 55 invariants, 25 bugs in the ledger; 58 files cite a section | grep over the ledger and over manifests, scripts and tests |
| Seven decisions before the upgrade plan | the plan's decision log, 2026-10-11 |
| 22 validation jobs; 4 skipped on a docs-only PR | the CI workflow file; the checks on the deck PR, 2026-10-12 |
| Triage cascade (rules → classifier → frontier model), policy read at base commit, fallback to read | the triage workflow and policy file; the toolkit design |
| Last 100 merged PRs: 46 read, 26 skim, 24 skip; 30 by the dependency bot | PR labels and authors via the GitHub API |
| The mis-triaged PR and its comment | the triage comment on the deck PR, 2026-10-12 (quoted trimmed) |
| 26 PRs merged on 2026-10-11, numbers 725–750 | merged-at timestamps via the GitHub API |
| 42 s API outage; 4 bugs B25–B28; 0 data loss | the ledger's goal line and bug table, 2026-10-11 |
| Audit blind for about 90 minutes | ledger bug B28 |
| Branch rules: rebase only, no force-push, one approval, admin override | the repository ruleset via the GitHub API |
| Timeline dates | first commit of each file (`git log --diff-filter=A`) |
| "Static tools lied twice" | the admission-policies test harness docstring |

## Do not claim

- That any PR is blocked by triage or by the classifier (nothing blocks; labels only).
- That a second human reviews PRs (there is one owner; approvals are admin overrides).
- That the classifier's calibration transfers to another repository.
- That the checks catch everything: the 32% follow-up-fix rate predates triage and has not
  been re-measured since.
- Any company, person, hostname or repository name: the reference is anonymous by design.
- That the local judge hook (a different model family grading the agent's diff before it
  stops) is in use on the reference repository; it exists in the owner's tooling and is not
  active there.

## If the infographic needs fewer screens

Keep, in this order: 02 (the bottleneck), 03 (the loop), 06 (triage), 07 (the checks), 09
(merge is the deployment), 11 (the mis-triage, recorded), 14 (what maps). That is the
argument in seven panels.
