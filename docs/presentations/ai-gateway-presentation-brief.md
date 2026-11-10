# AI gateway infographic: handover brief for Codex

**Result:** `ai-gateway-infographics.pdf` beside this file (three landscape panels, the
readability fallback below), with `ai-gateway-speaker-notes.md`. The owner chose to put their
name in the footer.

**Use the `tech-infographics` skill.** This brief gives you the audience, the exact visible
copy, the diagram topology, the evidence for every claim, and the claims you must not make.
Written 2026-11-10 from repository revision `02dd5725` (3 November 2026). It describes
configuration in git; nothing here was observed on the live cluster unless a date says so.

## What this is for

The owner read an article, "AI Routers Are Changing AI Governance" (Human Driven AI, POV,
August 2026, https://humandrivenai.com/ai-routers-are-changing-ai-governance-human-driven-ai/).
Its argument: governing AI is moving from lists of approved tools to designing the pathway a
request takes (employee, application, agent, gateway, model provider, tool), and a gateway is
where policy becomes a technical control (access, spending limits, approved models, human
approval for high-risk actions).

This platform already runs such a gateway. The owner wants to send the article's author one
infographic that shows how it works here, so she can see her ideas in a working system. A
written note will follow separately; the infographic has to stand on its own.

## The reader

One person: the author, a consultant who writes about AI governance for business leaders. She
knows the concepts (routing, gateways, pathways, spending limits, human approval) and uses
those words herself. She is **not** an engineer.

What that means for you:

- **Plain words on the image, product names off it.** No Kubernetes, agentgateway, kagent,
  Substrate, Istio, Vault, Argo CD, HTTP status codes, file paths, YAML or command lines in any
  visible copy. Those belong in the companion notes, where an engineer can check them. This
  overrides the skill's default of using real paths and tool names on slides.
- **Use her vocabulary where it fits**: "pathway", "gateway", "approved models", "spending
  limit", "human approval". The glossary below maps each plain term to what it is in the
  repository.
- **One idea per region.** She should be able to follow the picture top to bottom without
  anyone explaining it.

## Format

One **portrait poster** in the skill's illustrated-notebook mode (the `learning-roadmap.png`
treatment): a single PNG plus a one-page raster PDF, and a companion notes file. A poster,
not a deck, because she will read it on her own rather than watch it presented.

If the poster cannot hold the copy below at a readable size, split it into three landscape
16:9 panels along the region boundaries (regions 1–2, regions 3–4, regions 5–7) rather than
shrinking the lettering. Say which you did.

Footer, small: `A homelab reference platform · configuration as of 3 November 2026 ·
drawn from the repository, not a live measurement`. Ask the owner whether to add their name;
do not add it unprompted. Do not use Human Driven AI's name, logo or colors as branding, and do
not name the author on the image. The article is mentioned once, in the subtitle.

## Visible copy and layout

Seven regions, read top to bottom. The copy is exact; keep it unless a line will not fit, and
then shorten rather than reword the meaning.

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

## Glossary: plain term → what it is in the repository

For the companion notes only. Never on the image.

| On the image | In the repository |
|---|---|
| the gateway | A standalone agentgateway (v1.7.0) in namespace `model-gateway`, in front of the Anthropic API. `base-apps/model-gateway/` |
| a group of agents | One entry under `groups:` in `base-apps/model-gateway/budgets.yaml`: the agents that share one virtual key and one budget |
| its own pass | The group's virtual key, injected into the request by the sandbox's egress proxy, so the agent's sandbox never holds it |
| the real key | The Anthropic API key, mounted only in the gateway's namespace |
| sealed workspace | A gVisor sandbox per agent session (Agent Substrate) |
| approved models | `allowedModels` per key in `base-apps/model-gateway/configmap.yaml` |
| daily limit | `daily_usd` per group; a 24 h window from 00:00 UTC; `onBudgetExceeded: Block` |
| refused | HTTP 429 `budget_exceeded` from the gateway |
| a warning goes to the owner | Grafana alerts to Slack at `warn_at: 0.8`, on block, and when the gateway is down |
| smaller / more capable model | Claude Haiku 5.5 / Claude Sonnet 5.5 |
| read / propose / change or delete | The capability classes in `base-apps/admission-policies/agent-capability-taxonomy.yaml` |
| a person says yes | `requireApproval: true` on the tool binding in an AgentTemplate, e.g. `base-apps/kagent/agent-templates/restarter.yaml` |
| one reviewed file | `budgets.yaml`; `scripts/gen-model-budgets.py` generates the gateway config from it and CI fails if it is stale |

## Evidence

Record each claim's status in the companion notes, as the skill's evidence checklist asks.

| Claim on the image | Status | Source |
|---|---|---|
| On 14 October 2026 one shared spending limit ran out and every agent stopped | Dated recorded observation | `SPEC.md` §T.119; `docs/superpowers/specs/2026-10-23-model-gateway-budgets-design.md`, "Why" |
| Each group has its own daily limit; over it, only that group is refused until 00:00 UTC | Confirmed configuration | `base-apps/model-gateway/budgets.yaml`, `configmap.yaml`, `docs/index.md` |
| Ten groups, limits from $0.50 to $10 a day | Confirmed configuration (count the `groups:` entries again before rendering) | `base-apps/model-gateway/budgets.yaml` |
| Warning at 80%, alert on block | Confirmed configuration | `budgets.yaml` (`warn_at`), `base-apps/model-gateway/docs/index.md`, "Alerts" |
| The agent's workspace holds no real provider key; the gateway adds it | Confirmed configuration | `base-apps/model-gateway/docs/index.md`, "How a model call flows" |
| Each group may use only its listed models | Confirmed configuration | `configmap.yaml` (`allowedModels`) |
| The record holds group, model and cost, not the question or answer; kept 30 days | Confirmed configuration | `base-apps/model-gateway/docs/index.md`, "What it keeps"; `request-log-retention.yaml` |
| Smaller model for lookups and suggestions, larger for investigation and fixes | Confirmed configuration | `budgets.yaml` (`models:` per group) |
| Change-or-delete actions wait for a person's approval; the decision is recorded | Confirmed configuration | `base-apps/admission-policies/agent-capability.yaml`; `base-apps/kagent/agent-templates/restarter.yaml`, `k8s-agent.yaml`; `docs/presentations/safe-agent-deployment-speaker-notes.md` panels 2–3 |
| Proposing a change needs no approval, but a person must accept it | Confirmed configuration | `base-apps/kagent/agent-templates/implementer.yaml`; same speaker notes, panel 2 |
| Limits change only through a reviewed change request; no override | Confirmed configuration | `budgets.yaml` header; design spec, Goals 4 |
| Routing, sensitive-data scanning and failover were not built | Confirmed (stated non-goals) | Design spec, "Non-goals" |

Read `docs/presentations/safe-agent-deployment-speaker-notes.md` before you start: it is the
most recent deck on the same platform, made with this same skill, and its scope lines are the
caveats to carry into your notes.

## Do not claim

- **"There is no way around the gateway."** Two model configurations in `base-apps/kagent/`
  still reference the direct provider key (`kagent-anthropic`), and other projects outside this
  platform call the provider directly. The caption says every model request *takes this path*
  for the ten groups; do not add "cannot bypass", "the only route" or a locked-door image.
- **An exact spending cap.** A request is checked before it runs and charged after, so a group
  can overshoot by the requests already in flight. "Daily limit" is right; "never spends more
  than" is not. A limit is not an invoice.
- **That every action needs approval.** Reading and proposing do not.
- **That the gateway inspects or filters what is sent.** It passes requests through unchanged.
- **That the gateway chooses the model.** People do, per agent, in advance.
- **Live health, uptime, total spend, savings, or request counts.** None are in the sources.
- **Dollar figures beyond the $0.50–$10 range**, or a total across groups.
- **"Enterprise", "production-grade", "secure"** or any claim of completeness. This is one
  person's homelab; the agent runtime is alpha software. Say so in the notes.
- **Agreement by the author.** Region 7 says what this platform shares with the article, not
  that she endorses it.

## Deliverables

Write to `output/presentations/ai-gateway/` (untracked working output; do not commit, and do
not copy anything into `docs/presentations/` until the owner approves the image):

- `ai-gateway-infographic.png` (report its pixel dimensions) and `ai-gateway-infographic.pdf`
  (one raster page), or `slide-01.png` to `slide-03.png` plus the PDF if you split it.
- `ai-gateway-infographic-notes.md`: the visible copy as text, the glossary, the evidence
  table with each claim's status, and the caveats from "Do not claim" phrased for a reader.
- The generation prompt(s) you used.

Before you hand back: read every label in the rendered image at the size she will see it,
follow every arrow against region 3's directions, count the ten meters, confirm no product
name, path or status code appears on the image, and confirm nothing from "Do not claim" crept
in. Regenerate if any check fails; do not fix an image error only in the notes. Do not run any
cluster, agent or demo command.
