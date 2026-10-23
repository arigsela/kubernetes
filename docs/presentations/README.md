# Presentations

Decks and briefs about this platform, kept with the docs they draw on. Each deck is
anonymized for an outside audience: no hostnames, repository name, personal or company
names. The numbers and quotes trace back to dated repo state (each brief has a provenance
section), so a deck is a *record* of the platform at its date, not a living doc.

## Agentic Developer Platform (ADP) concept briefing — 2026-10-12

The initiative: build this homelab out as an Agentic Development Platform (the three-layer
model: developer platform, paved roads, agent infrastructure) and use it as a working,
anonymized reference when proposing the concept to engineering leadership and the AI
pathfinders group. Roadmap and status: `../superpowers/specs/2026-07-14-adp-remaining-pillars-roadmap.md`;
engineering detail: `../adp-engineering-deep-dive.md`.

| file | what it is |
|---|---|
| `adp-concept-infographics.pdf` | The deck to present: seven 16:9 infographic panels (agents as principals; three layers; a change stops at the gate; the approval gate both ways; six controls; what maps to a company; five questions for the group). Raster pages, no selectable text; the notes below carry the text. |
| `adp-concept-speaker-notes.md` | Per-panel visible copy, what to say, qualifications and sources. Read it before presenting; it also states what the deck does *not* claim. |
| `adp-concept-presentation-brief.md` | The source brief: the full 15-screen version (message, copy, visual, interaction, speaker note per screen), the layer-map component data, both flow tables, data provenance and the "do not claim" list. The seven-panel deck is its permitted short cut (screens 02, 03, 05, 07, 09, 13, 14). |

How to present: open the PDF full-screen, keep the speaker notes on a second screen. The
recorded transcripts (panel 04) are from 2026-10-11 on kagent 1.0.0-alpha11; say "alpha"
about the runtime. A live demo can add a question (`scripts/ask-hk`), an approval and a
policy refusal, but the deck does not depend on one.

Regenerating: hand the brief to an infographic-capable agent; the brief specifies audience,
tone, visual tokens and exact copy. Update the brief first when the platform changes, then
the deck.

## Development workflow briefing — 2026-10-12

The companion to the ADP deck: how changes reach the platform when an agent writes most of
them. Spec-driven design and the ledger, the agent's written contract, PR triage that labels
and never blocks, the 22-job check wall, merge-as-deployment, and the loop back into the
ledger and memory. Same audience, same anonymization, same visual identity.

| file | what it is |
|---|---|
| `dev-workflow-infographics.pdf` | The deck to present: seven 16:9 infographic panels (the bottleneck moved to deciding; one change start to finish; triage guides attention; the wall of checks; merge is the deployment; a triage comment caught its own mistake; reuse the mechanisms, decide the ownership). Raster pages, no selectable text. |
| `dev-workflow-speaker-notes.md` | Per-panel text alternative, what to say, qualifications and sources. Read it before presenting. |
| `dev-workflow-presentation-brief.md` | The source brief: the full 15-screen version with two recorded screens (a mis-triaged PR's own comment; the 26-PR upgrade day), data provenance and the "do not claim" list. The seven-panel deck is its permitted short cut (screens 02, 03, 06, 07, 09, 11, 14). |

## Agents as a shared service briefing — 2026-10-12

The third of the set: agents defined once as platform resources (prompt, model, tools,
approvals, runtime, all reviewed files) and consumed through five doors (the chat UI, the
coding assistant over MCP, the command line and automation over A2A, the developer portal).
Three recorded screens from 2026-10-12: one conversation across three doors, the approval
decided from the command line, and the UI's approval card. Same audience, anonymization and
visual identity as the other two.

| file | what it is |
|---|---|
| `agents-as-a-service-infographics.pdf` | The deck to present: seven 16:9 infographic panels (define once, consume anywhere; what an agent is made of; tools are a catalog; one gateway, five doors; three doors, two conversations; same gate, same record; the agent is the service). Raster pages, no selectable text. |
| `agents-as-a-service-speaker-notes.md` | Per-panel text alternative, what to say, qualifications and sources. Its "evidence corrections" (two conversations not one; 30-day retention; memory on the Harness; shared identity is not authenticated identity) are folded into the brief. |
| `agents-as-a-service-presentation-brief.md` | The source brief: the full 14-screen version with the per-agent tool table, the five-door table, three recorded screens, data provenance and the "do not claim" list. The seven-panel deck is its permitted short cut (screens 02, 03, 04, 06, 10, 12, 14). |

## The whole picture — 2026-10-14

The fourth of the set, and the map the other three sit on: the reference platform placed on
the Weave Intelligence ADP reference architecture (tooling, path definitions, agent
infrastructure, everything as code), with every piece scored built / partial / not yet, the
eight core paths scored the same way, an honest maturity-ladder placement (level 2 daily,
level 3 in places, level 4 for one declared class), and the next pieces in dependency order.
Two recorded screens: one change through every layer in 23 minutes (the CLI-approvals PR,
2026-10-12) and the audit's origin query. Same audience, anonymization and visual identity.
The brief was written 2026-10-12, rescored on 2026-10-13 after the dispatcher (path 1) and
policy promotion (path 5) went live, and rescored again on 2026-10-14 when the implementer,
the restarter and the approvals page closed paths 3 and 8 (eight paths built, all five "next"
nodes landed). The deck was regenerated from the brief the same day: panels 01 to 05 and 08
carry the 14 October state, panels 06 and 07 are recorded events and keep their 12 October
artwork.

| file | what it is |
|---|---|
| `whole-picture-infographics.pdf` | The deck to present: eight 16:9 infographic panels (the whole picture; three ways work moves; governance surrounds the agent; eight paths, one honest scorecard; how much autonomy exists; one recorded change; the audit that changed the controls; close the loop, in order). Raster pages, no selectable text; the notes below carry the text. Regenerated 2026-10-14; panels 06 and 07 keep their 2026-10-12 artwork and dated evidence. |
| `whole-picture-speaker-notes.md` | Per-panel text alternative, what to say, qualifications and sources, as of 2026-10-14. Its precision corrections (the exact promotion predicate, not "every check green"; a `review:skip` label alone cannot promote; an unattributed commit is refused; the human approving a restart is recorded beside, not inside, the runtime's record) are the ones to keep in mind when presenting. |
| `whole-picture-presentation-brief.md` | The source brief: the full 14-screen version with the layer-by-layer mapping tables, the eight-path scorecard, the ladder, the "what next" track, data provenance and the "do not claim" list. The eight-panel deck is its permitted short cut (screens 03, 05, 07, 09, 10, 11, 13, plus 12). |

## The implementer and the leak restarter — 2026-10-14

Two technical walkthroughs of the remediation agents, made the day both went live. The
implementer turns a resource alert or a person's request into a pull request under its own
GitHub App identity, and stops there: a human merges. The leak restarter is the one
remediation that changes the cluster directly. When the implementer's verdict on a
memory-limit alert is `leak`, the dispatcher opens a second session with the restarter, whose
only mutating tool (delete the named pod so its controller recreates it) waits for a human to
approve it. Both decks are drawn from the deployed manifests, the dispatcher code and the
SPEC acceptance records (T116 to T118); there is no separate source brief. They keep tool and
file names, so they read as engineering walkthroughs rather than leadership briefings. Same
anonymization as the decks above.

Both were finished on the afternoon of 2026-10-14, before three later changes. The
implementer deck's "not yet: gated operator action for leaks" is the leak restarter, built
the same day. Restart approvals are now decided on the dispatcher's approvals page (live later
that afternoon, and it logs the approver's email) or with Slack buttons (that evening), so the
command-line approval on the restarter deck's panel 05 is the fallback. And the runtime moved
from kagent 1.0.0-alpha11 to alpha14 on 2026-10-19; the implementer notes' sandbox limitation
was written against alpha11.

| file | what it is |
|---|---|
| `implementer-infographics.pdf` | Eight 16:9 infographic panels (from alert to proposed fix; what was deployed; how an alert reaches the agent; diagnose before changing a number; the change contract; where the hard gates sit; two recorded acceptance runs; operate it with its limits visible). Raster pages, no selectable text; the notes below carry the text. |
| `implementer-speaker-notes.md` | Per-panel text, qualifications and sources. Keep its corrections in mind when presenting: four proposal tools, not five; the three-open-PR cap is soft backpressure, not a ceiling; branch, file and injection rules are prompt constraints, not GitHub permission boundaries. |
| `leak-restarter-infographics.pdf` | Eight 16:9 infographic panels (a leak becomes a held action; one verdict line chooses the path; the restarter has one mutating tool; check the target before offering approval; approve the task your laptop never saw; two budgets prevent restart loops; what the acceptance actually proved; a restart is relief, not a repair). Raster pages, no selectable text. |
| `leak-restarter-speaker-notes.md` | Per-panel text, qualifications and sources. No leaking pod was available, so the full leak-to-restart chain is covered by unit tests and its parts were proven live separately; the restart budgets live in memory; the runtime's record names the dispatcher, not the human, as the approver. |

The generation prompts and per-panel PNGs the notes mention are not kept in the repo.

## hk demo architecture — 2026-10-23

A one-page diagram to show beside the hk demo page (`base-apps/hk-demo/`): where each of the
page's eight boxes runs in the cluster, and how a question moves between them. The numbered
circles match the page's boxes. It was drawn from the manifests on 2026-10-23 (hk-demo v0.2.3,
kagent 1.0.0-alpha14). Unlike the decks above it is **not anonymized**: it names the hosts, the
repository and the GitHub App, as the page itself does. Anonymize it before it goes into a deck
for an outside audience.

| file | what it is |
|---|---|
| `hk-demo-architecture.drawio` | The source. Edit it in draw.io. |
| `hk-demo-architecture.png` | The same diagram as a PNG with the drawing embedded, so draw.io can open and edit it too. Re-export it after an edit: `drawio -x -f png -e -s 1.5 -o hk-demo-architecture.png hk-demo-architecture.drawio`. |

On that date both agents called Anthropic directly through Substrate's egress. The model
gateway (§T.137) is not drawn, because it was not yet in their path.

## Tech-stack talk — 2026-10-11

| file | what it is |
|---|---|
| `tech-stack-presentation-brief.md` | A 20-slide walk through the stack in this repo (GitOps, secrets, edge, policy, data, observability, portal, agents), written for a presentation agent. No rendered deck is kept for it. |
