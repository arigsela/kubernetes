# hk demo: "The platform" tab

Date: 2026-10-25. Owner-approved in conversation (mockup `.superpowers/brainstorm/…/platform-tab.html`).
App: `arigsela/hk-demo`. **Supersedes** `2026-10-24-hk-demo-agent-platform-picture-design.md` (v0.5.0)
for where the picture lives and what it does: the picture leaves the Ask hk tab and loses everything
tied to a run.

## Intent

The owner demos hk-demo to a viewer who knows Kubernetes basics (ingress, Deployment, pod, service
account) but not the products underneath. v0.5.0 put an agent-platform picture on the Ask hk tab and
tied it to the run (live lights, marks). The owner's call: keep the two jobs apart.

- A **new first tab, "The platform"**, holds the whole picture and is the demo's starting point.
  The presenter walks the viewer through it one component at a time. It is **not tied to any run**.
- **Ask hk** and **On its own** show the evidence boxes for one run each, as they did before v0.5.0.

## Goals

1. Tabs: **The platform** | **Ask hk** | **On its own**. A first visit opens on The platform; after
   that the page reopens on the last tab used (the existing per-browser memory, `hk-demo-view`).
2. The picture shows the whole platform: two ways in, the shared middle, two ways out (below).
3. Walkthrough: **All** plus one step button per component, **◀ / Next ▶**, and a click on a
   component, all select it. The others dim while one is selected (as in the mockup); All clears
   the selection.
4. A three-part panel under the picture for the selected component:
   - **What it is**: one or two plain sentences.
   - **How it is set up here**: the real configuration excerpts, each with its source path in
     arigsela/kubernetes. These are the same excerpts the evidence boxes' Config tab shows, from
     `src/hk_demo/config_excerpts.yaml`.
   - **Where you'll see it in a run**: short chips naming the box on Ask hk and/or On its own.
5. **Show technical names** (off by default) adds the product names under each component.
6. Ask hk goes back to its eight boxes, as before v0.5.0. On its own is unchanged.

## Non-goals

- Any run data on The platform tab: no live lighting, no ✓ / ✗, no API calls.
- Changing On its own, the evidence boxes, their checks or the API.
- A draw.io source (owner: the picture stays as code in hk-demo).

## Anti-goals

- The platform tab implying anything about a particular run.
- Product names in the default view.
- Ask hk losing anything it had before v0.5.0.

## Constraints

- hk-demo's rules (`CLAUDE.md`):
  - text from the server goes in with `textContent`;
  - strict CSP: no inline `<style>`, `style=`, `font-style=` or script;
  - looks live in `app.css`, behaviour in static JS.
- The excerpts are rendered into the page by the server (Jinja, autoescaped) from
  `config_excerpts.yaml`. No new endpoint, no API change. They are already public on the page
  through the Config tabs.
- Every PR bumps the version (`CLAUDE.md` rule 8). This is a feature and a visible change: **0.6.0**.
- Readable on the presenter's laptop. At phone width the picture scales without horizontal page
  scroll, and the three panel parts stack.
- Keyboard: components are focusable (`role="button"`, `tabindex="0"`, `aria-label`), and Enter or
  Space selects one.

## The picture

As in the approved mockup `platform-tab.html` (viewBox about 1240 × 660). Components, in step order
after **All**:

| id | where | title | description (default view) | technical names |
|---|---|---|---|---|
| `people` | ways in | People | this demo · Slack · a terminal (ask-hk); each signs in as themselves | Dex SSO · kubelogin |
| `alert` | ways in | An alert fires | a container near its memory limit, 15 min | Grafana alerting |
| `dispatcher` | ways in | The dispatcher | plain code, not an AI: a rule picks the agent; limits: 4 an hour, once per 6 h, at most 3 open pull requests | dispatch-table.yaml |
| `identity` | middle | Identity check | every caller shows a token the mesh verifies: a person or a service account, never a password | Istio waypoint · JWT |
| `definition` | middle | Agent definition | a Kubernetes resource in Git: prompt, model, tools, a class: hk reads · implementer proposes | AgentTemplate · capability taxonomy |
| `context` | middle | What it knows | every app's docs and runbook, the service catalog, memory per user | agent-docs · Backstage · pgvector |
| `sandbox` | middle | A sandbox per conversation | the agent loop: think → call a tool → think → answer; kernel-isolated pod, egress allow-listed, no keys inside; suspended to a snapshot between turns, resumed anywhere | Agent Substrate · gVisor actor · atenet-egress |
| `model` | calls out | Model gateway → Claude | the key is added on the way out; a daily budget per agent; every call priced | agentgateway · Anthropic API |
| `tools` | calls out | Tools (MCP servers) | an allow-list per agent; each tool classed read / propose / write / destructive; it reaches the repo, the Kubernetes API (read-only), the catalog and the web | agentgateway · github-mcp · kagent-tools |
| `results` | ways out | An answer, or a pull request | people get an answer; an alert ends in a pull request and a finding in Slack | — |
| `person` | ways out | A person decides | merges or closes the pull request; approves or rejects a restart | — |
| `security` | middle | Guardrails | admission refuses an unsafe agent; write tools need approval | admission policies · Falco |
| `evaluation` | middle | Was it right? | golden questions re-run after every agent change; alerts | eval-gate PostSync Job |
| `observability` | bottom | Everything is recorded | every model call, tool call, approval and alert is traced and logged; a daily audit flags anything unapproved. The run tabs show this record as their evidence. | OpenTelemetry · Tempo · Loki · agent-audit |

The arrows run from people to identity; from alert to dispatcher to identity (the alert path in its
own colour); from identity into the platform; from definition and context into the sandbox; and from
the sandbox out to the model, the tools and the ways out. Group labels: "WAYS IN", "Agent platform
(in the cluster)", "WAYS OUT".

### Panel copy

**What it is** (All first):

- **All**: Two ways in, one platform, two ways out. People ask an agent directly; alerts reach it
  through the dispatcher. Either way the same identity check, sandbox, gateways and record apply.
- **people**: People ask agents directly: in this demo, in Slack, or from a terminal. Each signs in
  as themselves, and that identity travels with the request.
- **alert**: Grafana watches every container. When one stays above 90% of its memory limit for 15
  minutes, the alert goes to Slack and, separately, to the dispatcher.
- **dispatcher**: The dispatcher is plain code, not an AI. It matches the alert to a rule, opens a
  new session with the right agent under its own service account, and enforces limits.
- **identity**: Every caller is a principal: the mesh verifies a token (a person's sign-on, or a
  workload's service account) before the platform sees the request.
- **definition**: An agent is a Kubernetes resource in Git: its prompt, model, tools and a class. hk
  may only read; the implementer may propose pull requests but never merge.
- **context**: What the agent knows going in: every app's architecture page and runbook, the service
  catalog, and a memory per user.
- **sandbox**: Each conversation is its own kernel-isolated sandbox: allow-listed egress, no keys,
  snapshotted between turns.
- **model**: Calls to Claude leave the sandbox without a key; the key is added on the way out. The
  gateway enforces each agent's daily budget and prices every call.
- **tools**: MCP tool servers behind an allow-list per agent. Every tool is classed read, propose,
  write or destructive; a tool not on the list is refused, and each allowed call is logged.
- **results**: People get an answer in the conversation. An alert ends in a pull request that fixes
  the cause, and the finding goes to Slack.
- **person**: Nothing an agent proposes happens on its own: a person merges or closes the pull
  request, and approves or rejects a restart.
- **security**: Rules that do not depend on the model behaving: admission refuses unsafe agent
  definitions, write tools need an approval, and secrets never enter the sandbox.
- **evaluation**: After every change to an agent's definition, golden questions are re-run and
  scored; a regression posts a failing status and an alert.
- **observability**: Every model call, tool call, approval and alert is traced and logged, and a
  daily audit flags anything unapproved. The run tabs read this record as their evidence.

**How it is set up here**: excerpts from `config_excerpts.yaml`, by key and, where a key holds
excerpts for several components, by source file:

| component | excerpts |
|---|---|
| people | `login` |
| alert | `alert` |
| dispatcher | `dispatcher` |
| identity | `identity` |
| definition | `kagent` and `kagent@implementer`, except entries from `base-apps/admission-policies/agent-capability.yaml` |
| context | entries from `base-apps/kagent/agent-docs-mcp.yaml` (under `tools`) |
| sandbox | `sandbox` |
| model | `model` and `model@implementer`, except entries from `base-apps/tracing/collector.yaml` |
| tools | `tools` and `tools@implementer`, except entries from `base-apps/kagent/agent-docs-mcp.yaml` |
| results | `pr`, `slack` |
| person | `human` |
| security | entries from `base-apps/admission-policies/agent-capability.yaml` |
| evaluation | a new key `evaluation`: excerpt(s) from the eval gate's Job in `base-apps/kagent/` (copied as the README's Releasing section says) |
| observability | entries from `base-apps/tracing/collector.yaml` |
| All | none: a line saying to pick a component |

A component with no excerpt shows "No excerpt for this one yet." Each excerpt shows its source path
above the text.

**Where you'll see it in a run** (chips):

| component | chips |
|---|---|
| All | "Ask hk shows one question's evidence; On its own shows one alert's." (text, not chips) |
| people | Ask hk: Gateway, Login, hk demo |
| alert | On its own: Grafana alert |
| dispatcher | On its own: Dispatcher |
| identity | Ask hk: Identity check · On its own: Identity check |
| definition | Ask hk: kagent · On its own: kagent · implementer |
| context | Ask hk: Tools · On its own: Tools · GitHub |
| sandbox | Ask hk: Sandbox · On its own: Sandbox |
| model | Ask hk: Model API · On its own: Model gateway |
| tools | Ask hk: Tools · On its own: Tools · GitHub |
| results | Ask hk: the answer · On its own: Pull request, Slack |
| person | On its own: Human decision |
| security | Ask hk: kagent · On its own: kagent |
| evaluation | Ask hk: kagent · On its own: kagent |
| observability | every box's Evidence and Trace spans tabs |

The chips are text; making them jump to the run tabs is not part of this design.

## Undoing v0.5.0 on Ask hk

The Ask hk tab returns to its pre-v0.5.0 behaviour. These come out:

- the Platform | Boxes switch and the picture (`#map` is visible again);
- `platformEvent` / `renderPlatform` calls in `app.js`;
- `state.also` and the panel's "Also:" line;
- `turn.dropped`;
- the picture's run-related CSS (`now`, `visited`, marks);
- the `hk-demo-ask-map` storage key.

The Ask view's subtitle goes back to telling the viewer to click a box. `PLATFORM`'s run logic
(`piecesForEvent`, `markFor`, `boxesFor`, `worst`, `CONTEXT_TOOLS`, `TRACE_BOXES`) and its tests go.
What stays and moves to the new tab: the component list, step order, captions, `stepAfter`, the
technical-names toggle, and the Node-from-pytest test harness.

## Calls made in the build (2026-10-25)

- **Panel placement:** from 1000 px wide the panel sits beside the picture, and the picture's height
  is capped to the window. Below that, it sits under the picture. The whole-branch review measured
  the panel below the fold on every laptop size when it was always under.
- **Tools copy:** names the capability taxonomy's four classes. The approved copy said "read / write
  / destructive"; the taxonomy also has propose.
- **Legend and masking:** the run marks' legend is hidden on The platform. The excerpts go through
  the page's masking, like every rendered string (§V.107).

## What is left to the builder

- Exact coordinates and colours (start from the mockup, keep `app.css`'s palette), and the panel's
  layout at narrow widths.
- How the server hands the excerpts to the template (a small function next to `_excerpts()` in
  `evidence.py`, or a new module) and how the page holds them: for example one hidden block per
  component that JS shows.
- The tests:
  - every component in the template and in `PLATFORM`;
  - each component's excerpts rendered from the YAML, with the right sources (and none from a
    filtered-out source);
  - the chips;
  - no product names in the default view;
  - CSP unchanged;
  - the tab order and the first-visit default;
  - Ask hk back to eight visible boxes, with none of the removed pieces left;
  - the version bump.
