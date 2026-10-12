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

## Tech-stack talk — 2026-10-11

| file | what it is |
|---|---|
| `tech-stack-presentation-brief.md` | A 20-slide walk through the stack in this repo (GitOps, secrets, edge, policy, data, observability, portal, agents), written for a presentation agent. No rendered deck is kept for it. |
