> **Superseded** by `2026-10-25-hk-demo-platform-tab-design.md` (hk-demo v0.6.0): the picture
> moved to its own tab, "The platform", without any run data; Ask hk shows its eight boxes again.

# hk demo: the agent-platform picture on the Ask hk tab

Date: 2026-10-24. Owner-approved in conversation (mockups at `.superpowers/brainstorm/`, the last one
`agent-platform.html`). App: `arigsela/hk-demo`. Related designs: `2026-10-22-hk-demo-design.md`,
`2026-10-23-hk-demo-follow-ups-design.md`, `2026-10-24-hk-demo-on-its-own-design.md`.

## Intent

The owner demos hk-demo to a viewer who knows Kubernetes basics (ingress, Deployment, pod,
namespace, service account) but not the products underneath (Istio, Dex, gVisor). Today the Ask hk
tab opens on eight boxes that follow one HTTP request (Gateway, Login, hk demo, Identity check,
kagent, Sandbox, Model API, Tools). That explains how the demo page is served, not what the owner
wants to show: **the infrastructure for agents** (layer 3 of the Weave ADP model the owner presents
from, `docs/presentations/whole-picture-presentation-brief.md` screens 06 and 07).

So the tab opens on a picture of the agent platform. The presenter walks through it piece by piece,
then asks a real question and the same pieces light up as that run reports them; after the answer,
each piece carries the ✓ / ✗ of the evidence behind it and opens that evidence when clicked.

## Goals

1. The Ask hk tab opens on the agent-platform picture (below), before any question.
2. Walkthrough: every piece is clickable; **All**, **◀** and **Next ▶** step through the pieces in
   order; a caption under the picture explains the selected piece in one or two sentences.
3. During a real run, pieces light up from that run's own events (table below).
4. After the run, each piece shows a mark derived from its evidence boxes, and clicking it opens the
   matching box's panel (the existing panel: checks, numbers, Evidence / Trace spans / Config).
5. **Show technical names** (off by default) adds the product names under each piece.

## Non-goals

- The On its own tab's picture (alert → dispatcher → implementer → PR). It comes next, as its own
  design, reusing what this one builds.
- A draw.io source. The owner chose to keep the picture as code in hk-demo (inline SVG).
- Any change to the evidence boxes, their checks or the API. This is page-side only (template,
  JS, CSS) plus the version bump.

## Anti-goals (a failure even if it works)

- A timed animation. A piece lights only when the run's own events say so; nothing moves on a clock.
- A ✓ the evidence does not support. Marks come only from the boxes (§V.107: ✓ only from the run's
  own spans, logs or metrics; otherwise ⚙ "configured").
- Product names in the default view (Istio, Dex, oauth2-proxy, gVisor, agentgateway, …).
- Losing the eight-box view. It stays one click away, unchanged.

## Constraints

- hk-demo's rules (its `CLAUDE.md`): text from the server is inserted with `textContent` only; the
  strict CSP (`script-src 'self'; style-src 'self'`) means no inline `<style>`, no `style=`
  attributes and no inline script: the SVG is inline markup in `templates/index.html`, its look is
  classes in `static/app.css`, its behaviour in a static JS file.
- Every PR bumps the version (`CLAUDE.md` rule 8). This is a feature: **0.5.0**
  (`pyproject.toml`, `openapi.yaml` `info.version`, `uv lock`). Merging releases it and opens the pin
  PR in arigsela/kubernetes; merge that to deploy.
- Readable at the presenter's laptop width; at phone width the picture scales down without a
  horizontal page scroll (the SVG has a viewBox and `width: 100%`).
- Keyboard: pieces are focusable (`role="button"`, `tabindex="0"`, an `aria-label` with the title);
  Enter or Space selects; the step buttons are real buttons.

## The picture

Inline SVG, viewBox about 1240 × 650, laid out as the approved mockup: callers on the left, the
identity check, then the platform area (definition and context on top, the sandbox in the middle
with the agent loop inside it, guardrails and evaluation below), model gateway / tools / "a person
decides" on the right, observability across the bottom. Each piece is a `<g data-piece="…">` with a
pillar tag where the mockup had one. Copy, as approved:

| piece id | pillar tag | title | description (default view) | technical names (toggle) |
|---|---|---|---|---|
| `callers` | — | Callers | this demo app · Slack · the dispatcher (alerts) · a terminal (ask-hk); *each with its own identity* | — |
| `identity` | IDENTITY | Identity check | the mesh verifies the caller's token: a principal, not a password | Istio waypoint · JWT |
| `definition` | CAPABILITY | Agent definition | a Kubernetes resource in Git: prompt, model, tools, and a class read · propose · admin | AgentTemplate · capability taxonomy |
| `context` | CONTEXT | What it knows | every app's docs and runbook, the service catalog, memory per user | agent-docs · Backstage · pgvector |
| `sandbox` | EXECUTION | A sandbox per conversation | the agent loop: think → call a tool → think → answer; kernel-isolated pod, egress allow-listed, no keys inside; suspended to a snapshot between turns, resumed anywhere | Agent Substrate · gVisor actor · atenet-egress |
| `security` | SECURITY | Guardrails | admission refuses an unsafe agent; write tools need approval | admission policies · Falco |
| `evaluation` | EVALUATION | Was it right? | golden questions re-run after every agent change; alerts | eval-gate PostSync Job |
| `model` | SECURITY | Model gateway → Claude | the API key is added on the way out; each agent has a daily budget; every call is priced | agentgateway · Anthropic API |
| `tools` | CAPABILITY | Tools (MCP servers) | an allow-list per agent; every tool classed read / write / destructive; reach: the Git repo (docs, PRs), the Kubernetes API (read-only), the catalog, web search | agentgateway · github-mcp · kagent-tools |
| `approval` | — | A person decides | writes wait for approve / reject; pull requests are merged by a human | A2A input-required · approvals page |
| `observability` | OBSERVABILITY | Everything is recorded | every model call, tool call and approval is traced and logged; a daily audit flags anything unapproved. This record is what the page shows as evidence. | OpenTelemetry · Tempo · Loki · agent-audit |

Captions (one per piece, plus **All**), as in the mockup:

- **All**: Several callers use the same agents. Each agent is defined in Git, runs in its own
  sandbox, reaches a model and tools only through gateways, and everything it does is recorded.
- **identity**: Every caller is a principal: the mesh verifies a token (a person's sign-on, or a
  workload's service account) before the platform sees the request. The agent sees who called;
  nobody can claim to be someone else.
- **definition**: An agent is a Kubernetes resource in Git: its prompt, model, tools and a class.
  hk is read-only; the implementer may propose (branches and pull requests) but never merge.
  Admission refuses a definition that breaks its class.
- **context**: What the agent knows going in: every app's architecture page and runbook, the
  service catalog, and a memory per user. The same docs people read.
- **sandbox**: Each conversation is its own kernel-isolated sandbox. It can only reach allow-listed
  destinations, holds no keys, and is snapshotted between turns so it can resume on any node.
- **model**: Calls to Claude leave the sandbox without a key; the key is added on the way out. The
  gateway enforces each agent's daily budget and prices every call.
- **tools**: MCP tool servers behind an allow-list per agent. Every tool is classed read, write or
  destructive; a tool not on the list is refused, and each allowed call is logged.
- **approval**: Anything that changes something waits for a human: a restart waits for approve or
  reject, and the implementer's pull requests are merged by a person, never by the agent.
- **security**: Rules that do not depend on the model behaving: admission policies refuse unsafe
  agent definitions, write tools need an approval, and secrets never enter the sandbox.
- **evaluation**: After every change to an agent's definition, a set of golden questions is re-run
  and scored; a regression posts a failing status and an alert.
- **observability**: Every model call, tool call and approval is traced and logged, and a daily
  audit flags anything that ran without the approval it needed. The evidence on this page comes
  from that record.
- **callers** (not a step button; reached by clicking the piece): The same agents serve this demo,
  Slack, the dispatcher and a terminal. Each caller proves who it is before anything runs.

Step order for ◀ / Next ▶: All → identity → definition → context → sandbox → model → tools →
approval → security → evaluation → observability → All.

## How it behaves

### Where it sits

The map area of the Ask hk view (`#map`) gets a two-way switch, **Platform** | **Boxes**. Platform
(the picture) is the default; Boxes is today's eight-box map, unchanged. The panel (`#panel`) and
the conversation stay where they are. The choice is remembered per browser (`localStorage`,
wrapped in try/catch, as `hk-demo-view` is today).

### Live lighting (from the run's own events)

The page already lights boxes while a run moves (`light()` in `app.js`: an "active" class, not a
check); the Boxes view keeps that unchanged. The picture keeps its own per-turn state, fed by the
same events:

| event | pieces it makes "now" |
|---|---|
| `ids` (the platform opened the task) | callers, identity, definition, sandbox |
| `progress`, kind `status` (the model is working) | model |
| `progress`, kind `tool` | tools; also context when the tool is a context tool: `search_code`, `get_file_contents`, `get-catalog-entity`, `load_memory`, `save_memory` |
| `progress`, kind `tool`, tool `create_pull_request` (an `implementer:` turn) | tools, approval |
| `boxes` (evidence arriving) | observability |

The light **moves**: each event replaces the "now" set (a strong colour), and every piece it has
touched keeps a soft "visited" tint. `answer` clears "now" except observability, which stays "now"
until `done`. A piece's visited tint gives way to its mark when its evidence arrives (a box state
other than "waiting"), so nothing goes blank between the answer and the evidence. Lit is never a
✓. security and evaluation never light: they are standing rules, not steps of a run. (Ruling
2026-10-24, builder check: a moving light reads better live, and the visited tint covers the 20+ s
the trace takes.)

### Marks and clicks after the run

Each piece maps to evidence boxes of the selected turn:

| piece | boxes (the first opens on click) | mark |
|---|---|---|
| callers | app, gateway, login | worst of the three |
| identity | identity | that box's |
| definition | kagent | that box's |
| context | tools | from the tools box's spans: ✓ if a context tool (list above) ran without error, ✗ if one failed, ⚙ if none ran ("this answer used no docs, catalog or memory") |
| sandbox | sandbox | that box's |
| model | model | that box's |
| tools | tools | that box's |
| approval | tools on an implementer turn; kagent on an hk turn | implementer turn: ● "handed to a person" when a `create_pull_request` span succeeded, ✗ when it failed, blank with "no pull request in this turn" when there was none; hk turn: ⚙ "hk is read-only: nothing to approve" |
| security | kagent | ⚙ always |
| evaluation | kagent | ⚙ always (the kagent box already carries the last eval result as ⚙) |
| observability | — (clicking switches to Boxes) | blank until `done`; then ✓ if none of the trace-based boxes (identity, kagent, sandbox, model, tools) is still "waiting", else … ("the run's trace was not found") |

"Worst" ranks ✗ (failed) over … (partial) over blank (waiting) over ✓ (done), so a piece shows ✓
only when all its boxes are done. A piece with more than one box shows the others as small buttons
in the panel header ("Also: Gateway · Login"). Before any question, clicking a piece only selects
it and shows its caption; the panel keeps its "Ask a question or pick a recent run" hint.

The approval mark never claims the pull request's state today: a turn replayed from Recent runs may
be days old and this tab does not ask GitHub (the On its own tab does). ● says the agent stopped at
a pull request and handed it to a person, which the run's own span shows.

(Rulings 2026-10-24, builder check: observability, context and approval marks above, each derived
only from evidence the page already receives; a lost trace is …, not ✗, because the record may
exist and only the lookup failed.)

## Calls made after the builder check (2026-10-24)

- The platform area's label is "Agent platform (in the cluster)"; "kagent" moves to the technical
  names.
- Italic text uses a CSS class: the template must not carry `style=` or `font-style=` (the page test
  forbids `style=`).
- The README has no version number: it gets a short description of the picture.
- In arigsela/kubernetes, a SPEC.md task row and a line in `base-apps/hk-demo/docs.md`, as for the
  earlier hk-demo features.

## What is left to the builder

- Exact coordinates, colours (from `app.css`'s palette), and how the lit state and the marks look.
- Which JS file holds the picture's code (a new `static/platform.js` loaded after `common.js` is the
  expected shape) and how it hooks into `app.js`'s existing `light()` and `renderMap()`.
- The tests: hk-demo checks its page with pytest (`tests/test_page.py` reads the template and the
  JS). At minimum: every piece id in the template; every caption, piece→box and event→piece mapping
  covers exactly the piece ids; no inline `style`/`<style>`/script in the template; the CSP header
  unchanged; the default view shows no product name (the technical names are hidden until the
  toggle); the README and `openapi.yaml` version bump.
