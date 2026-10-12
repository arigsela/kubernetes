# One agent, every door — agents as a shared platform service, screen by screen

**Purpose:** a complete description of a 14-screen briefing on how this platform defines AI
agents once, centrally (prompt, model, tools, approvals, runtime, all as reviewed files), and
then lets everyone reach the same agent through whichever door suits them: a chat UI, a coding
assistant, a command line, the developer portal, or automation. It is written for an agent that
will turn it into infographics, in the same form as `dev-workflow-presentation-brief.md` and
`adp-concept-presentation-brief.md`. Every screen gives: the message, the exact copy, the visual
to draw, and speaker notes. Data provenance is at the end.

**Audience:** engineering leadership; first target is the company's AI pathfinders group. They are
technical but not Kubernetes specialists. Vocabulary rule: name the mechanism in plain words
first; the specific tool goes in a smaller "in the reference" line, never in the headline.

**Framing:** "an agent is a platform resource, not a chat window". Most teams meet agents as
features inside individual tools: one in the IDE, one in the chat app, one in a CI job, each with
its own prompt, its own API key and its own idea of what it may do. The reference defines each
agent once, in the same repository and with the same review path as everything else, and exposes
it through one gateway. The UI, the coding assistant and the scripts are all just clients. The
reference is the same anonymized platform as the other two decks. Call it "the reference". No
hostnames, repository name, personal or company names.

**Relationship to the other decks:** the ADP deck is about the governance that makes agents safe
to run; the workflow deck is about agents changing the platform. This one is about the
*consumption model*: how one definition serves many doors, and why that matters for adoption.
Screen 12 reuses the ADP deck's controls from the consumer's side.

**Tone:** confident, plain, evidence-first. Show the gaps as deliberately as the features (one
shared identity, an unauthenticated open-source gateway behind an allow-list, approvals that one
door can't decide). No "AI-powered", no "copilot for everything", no emoji.

**Format of the source:** guided (presented live by the owner), so each screen carries little
text and the speaker notes carry the argument. Three screens are `recorded`: real transcripts
from 2026-10-12.

## Visual identity

Identical to the other two decks so the three can be shown as a set. Carry over unless the
infographic agent has a house style to follow:

- **Background:** near-black with a subtle diagonal gradient (`#1a1a1a` → `#2d2d2d` → `#1a1a1a`).
- **Accent:** one orange, `#FF5722`, for the focal card, bullets, numbers, highlights; 14% opacity
  for hot panels. A single green, `#7ed957`, only for "done / ok" states. For this deck the
  orange also marks the **one gateway** every door passes through.
- **Text:** white; secondary text white at 62% opacity. Dark text on the orange card.
- **Type:** display face Archivo Black; body face Space Grotesk; a monospace for recorded output.
- **Frame:** screen number top-left in orange (01…14); a breadcrumb of the sections top-right
  (Concept · The model · Doors · In use · Governance · Reference · Discussion) with the current
  one lit; footer left (section) and right (a one-line takeaway).
- **Components:** orange focal card for title/close; thin-bordered translucent panels; stat tiles
  (big orange number, small grey label); a **door glyph** (a simple doorway outline) for each
  consumer, used consistently on screens 06 to 11; transcript panels with small-caps role labels
  (orange for the agent, grey for the human, green for system/ok).
- **A `recorded` pill** marks every screen whose content is a real transcript or screenshot.

## Section map

| # | Section | Screen |
|---|---|---|
| 01 | Concept | Title |
| 02 | Concept | Define once, consume anywhere |
| 03 | The model | What an agent is made of |
| 04 | The model | Tools are a catalog, not a prompt |
| 05 | The model | Prompts are shared and versioned |
| 06 | Doors | One gateway, five doors |
| 07 | Doors | Door 1 · The chat UI |
| 08 | Doors | Door 2 · The coding assistant |
| 09 | Doors | Doors 3 to 5 · Command line, portal, automation |
| 10 | In use | Recorded: one conversation, three doors |
| 11 | In use | Recorded: the approval, decided from the command line |
| 12 | Governance | Same gate, same record, whichever door |
| 13 | Reference | The reference in numbers, and what it took |
| 14 | Discussion | What maps, five questions, close |

---

## 01 — Title

**Message:** this is a consumption-model talk backed by a platform you can call.

**Copy (on the orange card):**
> **One agent, every door**
> Define an agent once, as a platform resource. Reach it from the chat UI, the coding
> assistant, the command line, the portal, and automation. Same prompt, same tools, same
> gate, same record.

**Copy (below the card, secondary):**
> Concept, with one anonymized working reference. Every transcript has a date.

**Footer:** left "Engineering leadership briefing", right "2026".

**Visual:** the orange card bottom-left of a dark gradient, nothing else.

**Speaker note:** Third of the set. The ADP deck said agents are principals; the workflow deck
said agents change the platform through the same path as people. This one says: an agent is
something the platform *provides*, the way it provides a database or a secret store, and the
tools people already use are just clients of it.

---

## 02 — Define once, consume anywhere

**Message:** agents embedded per tool multiply prompts, keys and permissions; an agent defined
as a platform resource is reviewed once and reused everywhere.

**Headline:** *Define once, consume anywhere*

**Two columns, "today" and "on the reference":**

- **Today, in most teams.** An agent in the IDE, another in the chat app, a third in a CI job.
  Each has its own prompt (pasted, drifting), its own API key (someone's), its own list of what
  it may touch (nobody's). Improving one improves one.
- **On the reference.** An agent is a set of files in the platform repository: the prompt, the
  model, the exact tools it may call, which of those need a human, and the runtime it runs on.
  It changes by pull request, like a deployment. One gateway serves it to every client. Improving
  it improves it everywhere, for everyone, at once.

**Stat tiles:**
- **7** agents defined centrally
- **5** doors into the same agents (UI · coding assistant · command line · portal · automation)
- **1** gateway, one identity header, one session store, one action record
- **0** API keys on any client: the model key is injected at the platform's egress

**Visual idea:** left, three separate boxes each with its own tiny key and scroll icon, not
connected; right, one agent card in the centre with five doorways around it, all arrows passing
through one orange ring labelled "gateway".

**Footer right:** "An agent is a platform resource, not a chat window".

**Speaker note:** The adoption argument is the point: a team that must install, configure and
trust an agent per tool will do it once and stop. A team that can point any tool at an agent
that already exists, already has permissions, and already shows up in the audit, keeps going.

---

## 03 — What an agent is made of

**Message:** an agent is three declarative objects plus shared parts; nothing lives only in a
chat window.

**Headline:** *What an agent is made of*

**Visual:** three stacked cards with a "shared parts" column beside them.

| Part | What it holds | In the reference |
|---|---|---|
| **Template** | The prompt, the model, the tool bindings with their approval flags, the memory setting | one file per agent; 7 to 108 lines of prompt |
| **Harness** | How it runs: the runtime image, the worker pool, where idle conversations are snapshotted, the embedding model for memory | three harnesses: with memory, stateless, chat |
| **Agent** | Binds a template to a harness; the thing clients address | one per agent; the portal catalogs it |
| *Shared: prompt snippets* | Safety rules, Kubernetes context, tool-usage rules, delegation rules | five snippets, included by reference, maintained once |
| *Shared: tool servers* | MCP servers each exposing a named list of tools | seven servers, 177 tools discovered |
| *Shared: model configs* | Which model, caching, token limits | two: a larger model for operators, a smaller one for readers |

**Standing caption:** Every part is a file in the repository, reviewed by pull request, checked
at admission. Each conversation runs its agent's revision at the time the conversation started.

**Footer right:** "Three files, shared parts, reviewed like code".

**Speaker note:** Walk one agent live: the operator agent's template shows the prompt, two
bindings to the same tool server (reads ungated, writes gated), the memory flag. Then show the
harness it runs on. That is the whole definition; there is no hidden configuration in a UI.

---

## 04 — Tools are a catalog, not a prompt

**Message:** what an agent can do is an explicit, classified list bound from a shared catalog,
not a paragraph in its prompt.

**Headline:** *Tools are a catalog, not a prompt*

**Body:**
> Tool servers publish their tools; the platform discovers them and keeps a taxonomy that
> classifies every tool as read, write or destructive. An agent's template binds the tools it
> needs by name. A binding that holds a write or destructive tool must carry an approval flag,
> or the platform refuses the agent at admission and in CI. A tool the taxonomy doesn't know
> is refused too.

**Table (per agent, from the reference):**

| Agent | Role | Tools bound | Gated | Reads from |
|---|---|---|---|---|
| Knowledge agent | answers "what runs where", reads docs and live state, searches the web | 13 from 4 servers | 0 | docs, catalog, cluster (read), web |
| Cluster operator | changes cluster objects | 18 | 10 | cluster (read + write) |
| Mesh operator | changes the service mesh | 20 | 7 | cluster (read + write) |
| Cluster reader | cheap read-only lookups | 8 | 0 | cluster (read) |
| Build orchestrator | routes a request to the right operator | 0 of its own; two sub-agents | inherits | the operators |
| Skill suggester | proposes skills for a new agent | 0 | 0 | nothing |
| A hobby agent | book discussion with web search, spoilers withheld | 1 | 0 | web |

**Callout (orange):** *Adding a tool is a pull request:* classify it in the taxonomy, bind it
in the template, let the checks confirm the class matches the binding. The web-search server was
added that way on 2026-10-12 and reached two agents the same hour.

**Footer right:** "Capability is a list with a class, not a sentence".

**Speaker note:** This is why "consume anywhere" is safe: the client cannot widen what the agent
does. The coding assistant, the UI and the script all get the same bound list, and the taxonomy
is the one place where "destructive" is defined.

---

## 05 — Prompts are shared and versioned

**Message:** prompts are files with shared includes; improving a shared rule improves every
agent at once, and every conversation knows which revision it runs.

**Headline:** *Prompts are shared and versioned*

**Three panels:**
- **Shared snippets.** Five prompt fragments (safety guardrails, cluster context, tool-usage
  rules, delegation rules, skills usage) live in one place and are included by reference. A
  fix to the safety rules reaches every agent that includes them on the next revision.
- **Per-agent prompt.** The agent's own instructions sit in its template, under review. The
  knowledge agent's prompt, for example, tells it never to invent a path, never to quote a
  secret, to say when docs and live state disagree, and that it holds no mutating tools.
- **Revision pinning.** A conversation runs the revision it was created from for its whole life.
  A prompt change never alters a conversation mid-way; a new conversation picks it up.

**Recorded (small, monospace): a prompt fix that reached six agents in one PR, 2026-10-11:**
```
## Memory tools
`load_memory` requires a `query` argument: a short phrase describing what to recall
(for example "kagent cutover date"). Never call it with no arguments; that call fails.
```

**Visual idea:** one "shared" card feeding five arrows into five agent cards, with a small
version tag on each conversation bubble below them.

**Footer right:** "Fix a rule once; every agent gets it on its next revision".

**Speaker note:** The recorded snippet is a real workaround for a runtime bug: the runtime sent
one tool with an empty schema, so the model called it without its required argument. Rather than
wait for upstream, one PR added three lines to six prompts. That is what "prompts are code"
buys: a fix with a diff, a review, and a date.

---

## 06 — One gateway, five doors

**Message:** every client reaches an agent through the same gateway; a conversation is a
first-class object that any door can continue.

**Headline:** *One gateway, five doors*

**Visual:** the agent in the centre, an orange ring labelled "gateway" around it, five doorway
glyphs outside the ring, each with an arrow in. Under each door a one-line "who uses it".

| Door | Who | How it talks to the gateway | Starts conversations | Decides approvals |
|---|---|---|---|---|
| **Chat UI** | anyone on the network | the UI's own calls | yes | yes (a card with Approve / Reject) |
| **Coding assistant** | engineers at their editor | the gateway's MCP endpoint, registered once as a server | no (continues one) | no (the client lacks the tasks extension) |
| **Command line** | engineers, the coding assistant, scripts | the agent protocol (A2A) over HTTPS | yes | yes (`--approve` / `--reject`) |
| **Developer portal** | anyone browsing the catalog; the new-app wizard | the agent protocol from inside the cluster | yes | n/a (read-only agents) |
| **Automation** | the evaluation harness, scheduled jobs | the agent protocol from inside the cluster | yes | n/a |

**Standing caption:** A conversation is a session with an id. Start it at one door, continue it
at another: same memory, same agent revision, same audit trail.

**Footer right:** "Clients differ; the agent, the gate and the record don't".

**Speaker note:** The table's last two columns are the honest part. Not every door can do
everything: the coding assistant can continue a conversation but can't open one or decide an
approval through its protocol. The reference solves it by pairing doors (start on the command
line, continue in the assistant, approve in the UI or on the command line). Screen 10 shows that
pairing for real.

---

## 07 — Door 1 · The chat UI

**Message:** the UI is the door for everyone, and the only one that shows every conversation
and every pending approval in one place.

**Headline:** *Door 1 · The chat UI*

**Three panels:**
- **For anyone.** A dashboard of agents, models, tool servers and recent conversations; a chat
  per agent; no installation. Sessions started from any other door appear here too, titled by
  their first message.
- **Approvals.** A paused tool call renders as an amber card, "The agent is asking permission to
  run a tool", with the exact call and arguments above it and three buttons: Approve, Reject,
  Cancel request.
- **Operations.** The runtime's worker pools, snapshots and tool-server status are visible on
  the same UI, so "is the agent slow because the pool is full" is one click away.

**Recorded (UI text, 2026-10-12):**
```
k8s_delete_resource                                              called
{ "namespace": "default", "resource_name": "approval-gate-probe",
  "resource_type": "configmap" }

The agent is asking permission to run a tool
Please approve or reject the tool call k8s_delete_resource() ...
[ Approve ]  [ Reject ]  [ Cancel request ]
```

**Visual idea:** a simplified UI frame: left rail with agents and chats, centre a chat with the
amber approval card, right a panel listing the agent's bound tools.

**Footer right:** "Every conversation, every pause, one place".

**Speaker note:** The screenshot is from the session the owner approved during the test. Point
at the tool panel on the right: the UI shows the bound tool list straight from the template, so a
reviewer can see what the agent *could* do, not only what it did.

---

## 08 — Door 2 · The coding assistant

**Message:** the engineer's coding assistant reaches the same agents through a standard server
registration, with no API key on the laptop.

**Headline:** *Door 2 · The coding assistant*

**Body:**
> The gateway exposes a standard MCP endpoint. An engineer registers it once in their coding
> assistant, with a URL and an identity header. From then on, the assistant can list the
> engineer's conversations with any agent and continue them as a tool call: it gets back the
> state and the agent's text, in the same conversation, with the same memory.

**Recorded (the assistant's tool call and result, 2026-10-12):**
```
invoke_session
  session_id: 01a130c3-3187-7846-86e9-7615836077dc
  message:    One-line answer: which namespace is the brave-search-mcp MCPServer in?

→ state: TASK_STATE_COMPLETED
  text:  `kagent`. The file `base-apps/kagent/brave-search-mcp.yaml` sets
         `metadata.namespace: kagent`, and the live object is in the same namespace.
```

**Three callouts:**
- *No credentials on the laptop.* The assistant holds the gateway URL and an identity header.
  The model key stays in the platform's secret store and is injected at egress.
- *What it can't do.* This door cannot open a new conversation (the endpoint offers list,
  continue, checkpoint and fork, not create) and cannot decide an approval (the assistant
  doesn't speak the protocol's tasks extension). It pairs with door 3 for both.
- *A written rule for the assistant.* The engineer's assistant has a one-paragraph standing
  instruction: open a conversation on the command line, continue it here, decide approvals on
  the command line or in the UI, start fresh after an agent change or seven idle days.

**Visual idea:** an editor window on the left with a tool-call block; an arrow through the
orange gateway ring to the agent; a small crossed-out key icon on the laptop.

**Footer right:** "Register once; the agent is a tool in the editor".

**Speaker note:** The standing instruction is the adoption trick. Engineers don't learn an API;
they type "ask the knowledge agent …" and their assistant does the right thing. The rule lives
in a file the assistant reads at startup, so it is versioned like everything else.

---

## 09 — Doors 3 to 5 · Command line, portal, automation

**Message:** the same protocol serves people at a terminal, the developer portal and
unattended jobs; none of them needs special access.

**Headline:** *Doors 3 to 5 · Command line, portal, automation*

**Three panels:**

- **Door 3 · Command line.** A small script (standard library only) speaks the agent protocol
  to the gateway: ask, continue with an id, approve or reject a pending call, answer a question
  the agent asked. Exit codes say "answered", "waiting for you", "failed". Humans use it; the
  coding assistant uses it; CI could. *In the reference: `ask`, `--context`, `--approve`,
  `--reject "why"`.*
- **Door 4 · Developer portal.** Every agent appears in the service catalog as an entity built
  from its definition: model, tools, approvals, sub-agents, on one card. The new-service wizard
  calls the skill-suggester agent to propose what a new agent should do. *In the reference: the
  portal calls the same gateway from inside the cluster; no extra access.*
- **Door 5 · Automation.** The evaluation harness asks the knowledge agent the corpus
  questions and scores the answers; the daily audit reads the action record; a scheduled job
  could do the same. *In the reference: the eval harness is the same client library as the
  command line.*

**Standing caption:** Five doors, one client library, one protocol. The expensive part (the
agent) is built once; a door is a thin client.

**Footer right:** "Thin clients, one agent".

**Speaker note:** Stress the cost asymmetry. The command-line client is a few hundred lines
and has tests; the portal integration is a catalog card and one action. Nothing in a door
re-implements prompts, tools or permissions.

---

## 10 — In use: one conversation, three doors `recorded`

**Message:** a conversation started at one door was continued at a second and decided at a
third, with no hand-off step; the session id is the only thing that moved.

**Headline:** *In use: one conversation, three doors*

**Timeline (three nodes on a track, each with its door glyph), 2026-10-12:**

1. **Command line, 15:37 UTC.** The engineer's assistant opens a conversation with the knowledge
   agent: *"Which file defines the Brave Search MCP server and what tools does it enable?"* The
   agent answers from live cluster objects, notes it can't read the repository right now (a
   token had expired), and the script prints the session id.
2. **Coding assistant, 16:12 UTC.** Same session, through the MCP door: *"One-line answer:
   which namespace is the brave-search-mcp MCPServer in?"* Reply: `kagent`, with the file path,
   now that the repository token was fixed. The agent remembered the earlier context.
3. **Chat UI, 17:20 UTC.** A different conversation, started on the command line with the
   cluster operator (*"Delete the ConfigMap approval-gate-probe …"*), paused for approval. The
   owner opened the dashboard, found the session in "Recent agent chat sessions", and pressed
   Approve. The object was deleted; the command-line client, asked later, saw the conversation
   continue.

**Transcript (node 2, verbatim):**
```
human (via assistant): One-line answer: which namespace is the brave-search-mcp
                       MCPServer in?
agent:                 `kagent`. The file `base-apps/kagent/brave-search-mcp.yaml`
                       sets `metadata.namespace: kagent`, and the live object is in
                       the same namespace.
```

**Infographic idea:** one continuous line with three doorway glyphs on it; the session id
written once, above the line, spanning all three.

**Footer right:** "The session id is the hand-off; nothing else moves".

**Speaker note:** Say that the three doors ran as the same identity, which is why the UI showed
the script's session. In a company that identity is per person or per team, and the same
property means "my conversations follow me across tools", not "everyone sees everything".

---

## 11 — In use: the approval, decided from the command line `recorded`

**Message:** a gated call pauses the same way whichever door started it, and the command line
can decide it; reject is terminal, approve runs it once, both are recorded.

**Headline:** *In use: the approval, decided from the command line*

**Transcript (verbatim, trimmed; 2026-10-12):**
```
$ ask --agent operator "Delete the ConfigMap approval-gate-probe in the default
  namespace. First read it and confirm it carries the label throwaway=true."
agent:  The label `test.homelab/throwaway: "true"` is present. I'm deleting the
        ConfigMap now, as you asked.
ask:    waiting for approval of:
          k8s_delete_resource {"namespace": "default", "resource_name":
          "approval-gate-probe", "resource_type": "configmap"}
          ask --context 01a130d5-… --approve | --reject REASON
exit 2

$ ask --context 01a130d5-… --reject "CLI acceptance test: reject path"
agent:  The ConfigMap was not deleted. The delete call was rejected at the
        approval step, so I didn't retry. The ConfigMap still exists and I
        haven't changed anything.

$ ask --context 01a130d5-… "Please try the delete again now."
ask:    waiting for approval of: k8s_delete_resource {...}        exit 2

$ ask --context 01a130d5-… --approve
agent:  The ConfigMap `approval-gate-probe` in the `default` namespace is deleted.
        A `get` on the ConfigMap failed afterwards, as expected for a resource
        that no longer exists.
```

**Three callouts:**
- *The pause is protocol-level.* The agent's "I'm deleting it now" did not run anything; the
  runtime held the call until a decision arrived.
- *Reject is terminal for that call.* The agent received the reason and reported, it did not
  retry on its own.
- *Same pause, two doors.* The owner decided an identical pause in the UI an hour later
  (screen 10, node 3). Both decisions landed in the same action record.

**Infographic idea:** a fork: one request, the gate in the middle drawn as the orange ring, two
outcomes below (object kept · object deleted), each with the door glyph that decided it.

**Footer right:** "Same request, run twice: rejected, then approved".

**Speaker note:** Before 2026-10-12 the command-line client didn't request the approval
extension, so a pause started from it could only be cancelled or decided in the UI. The fix was
small (ask for the extension on every message, remember the pause per conversation) and it is
why the doors are now interchangeable for approvals. Mention it: the reference is honest about
the day it became true.

---

## 12 — Same gate, same record, whichever door

**Message:** the controls sit behind the gateway, so no client can bypass them; the open gaps
are about identity, and they are named.

**Headline:** *Same gate, same record, whichever door*

**Five panels (controls, from the consumer's side):**
- **Identity.** The caller names itself in a header; conversations are partitioned by it; the
  UI and the command line list only that identity's sessions. *Reference gap: one shared
  identity today, and the open-source gateway doesn't authenticate the header. The platform's
  IP allow-list and TLS are the mitigation, not a substitute.*
- **Capability.** Bound tool lists and classes are enforced at admission; a client sends text,
  never a tool. *Holds for every door.*
- **Approval.** A gated binding pauses the call; the UI and the command line can decide; the
  decision carries who and why. *Reference gap: the coding-assistant door can't decide; it
  pairs with another door.*
- **Record.** Every tool call, with arguments and decision, lands in one store regardless of
  the door; a daily job checks that no gated call ran ungated. *Holds for every door.*
- **Isolation and cost.** Each conversation runs in its own sandboxed actor, snapshotted when
  idle; model keys are injected at egress; prompt caching cuts input cost. *Holds for every
  door, because the door never touches the model.*

**Standing caption:** A door is a thin client. If a control depended on the client, it would
not be a control.

**Footer right:** "Controls behind the gateway, not in the client".

**Speaker note:** The identity gap is the first thing a company must close: put an
authenticating proxy in front of the gateway (SSO), map the person to the identity header, and
the per-identity session partitioning that already exists does the rest. Say it plainly; the
audience will ask.

---

## 13 — The reference in numbers, and what it took

**Message:** the model is small enough to count, and the doors were added in days once the
agents were central.

**Headline:** *The reference in numbers, and what it took*

**Stat tiles (eight):**
- **7** agents · **3** harnesses · **2** model configs
- **7** tool servers · **177** tools discovered · **60** bound across agents · **17** gated
- **5** shared prompt snippets
- **5** doors · **20** conversations ready at the time of writing
- **48%** of model input served from cache (86 to 98% inside tool loops)
- **0** API keys on clients

**Timeline:**
| When | Added | Because |
|---|---|---|
| Jul 2026 | Agents defined declaratively with scoped credentials and explicit tool lists; the docs contract they read | agents could not be reviewed, or answer "what runs where" |
| Jul 2026 | The capability taxonomy and per-binding approvals, enforced at admission and in CI | a gate that prompts could talk around |
| 11 Oct 2026 | New runtime: sandboxed per-conversation actors, snapshots, keys injected at egress | execution isolation was unassessed |
| 11 Oct 2026 | The gateway's A2A door: the command-line client and the eval harness | the old per-agent endpoints went away |
| 12 Oct 2026 | The coding-assistant door registered against the gateway's MCP endpoint; shared identity header | engineers wanted the agent inside the editor |
| 12 Oct 2026 | Approvals and questions answerable from the command line | a pause started from a script could only be decided in the UI |
| 12 Oct 2026 | Web search added to two agents by one PR | the knowledge agent could not reach outside |

**Footer right:** "Central first; then every door was a thin client".

**Speaker note:** The dates matter: three doors in two days, because the hard part (agents as
reviewed resources with a gateway) existed. A team that starts with doors and no centre spends
those two days per tool, forever.

---

## 14 — What maps, five questions, close

**Message:** the model transfers; identity and ownership are the decisions; the reference
has gaps a company must close first.

**Headline:** *What maps to a company, and what doesn't yet*

**Three columns:**

- **Maps directly.**
  - Agents as reviewed files (prompt, model, tool bindings, approvals, runtime).
  - A tool catalog with a class per tool, and approval per binding.
  - Shared prompt snippets included by reference.
  - One gateway with a standard MCP endpoint and the agent protocol; thin clients.
  - Conversations as sessions with an id that any door can continue.
  - One action record for every door.
- **Needs a decision.**
  - Identity: SSO in front of the gateway, and whether sessions are per person or per team.
  - Who owns an agent's prompt, and who may add a tool to the catalog.
  - Which doors are offered first (the chat UI and the coding assistant are the cheap ones).
  - Whether approvals may be decided from a script at all, or only in the UI.
- **Still early.**
  - One shared identity on the reference; the open-source gateway doesn't authenticate.
  - The coding-assistant door can't open conversations or decide approvals; it is paired
    with the command line.
  - The runtime is alpha; conversations idle seven days are deleted.

**Five questions for the group:**
1. **Centre.** Which agents do we already have, scattered across tools, that should become one
   definition?
2. **Doors.** Which two doors would reach the most engineers on day one?
3. **Identity.** What maps a person to an agent identity in our environment, and who sees
   whose conversations?
4. **Catalog.** Who classifies a tool as destructive, and who approves adding it to an agent?
5. **Record.** Where does the action record go, and who reads it when an approval was wrong?

**Close (orange card):**
> Build the agent once, behind a gate, with a record.
> Then let every tool you already use be a door.

**Footer right:** "The agent is the service. The tools are the clients."

**Speaker note:** End on the questions. If there is time, do the three-door demo live: start
on the command line, continue from the editor, approve in the UI. It takes four minutes and
shows the whole model.

---

## Data provenance (for the numbers and quotes)

All measured on the reference platform on 2026-10-12 unless noted.

| Claim | Source |
|---|---|
| 7 agents, 3 harnesses, 2 model configs, 5 shared prompt snippets | the agent-template, harness and model-config files; the built-in prompts ConfigMap |
| 7 tool servers, 177 tools discovered, 20 sessions ready | the UI dashboard at the time of writing |
| Tools bound per agent (13 / 18 / 20 / 8 / 1 / 0 / 0) and gated (10 / 7) | the templates' MCP bindings, parsed |
| 60 bound, 17 gated | the sums of the previous row |
| 48% of model input from cache; 86–98% in tool loops | token usage stored per model call, measured 2026-10-11 |
| 0 API keys on clients; keys injected at egress | the runtime's credential-provider design and the coding-assistant server entry (URL + identity header only) |
| UI approval card text and buttons | the UI, session `01a130fd…`, 2026-10-12, verified by the owner |
| The MCP tool call and its result | the coding assistant's tool log, session `01a130c3…`, 2026-10-12 |
| The command-line approval transcript | the acceptance run on 2026-10-12, session `01a130d5…` (object created for the test, deleted by the approved call) |
| "Three doors" timeline times | the same three sessions' timestamps |
| Prompt fix reaching six agents | the memory-tools prompt PR, 2026-10-11 |
| Web search added to two agents in one PR | the web-search PR, 2026-10-12 |
| Revision pinning, seven-day idle deletion, MCP endpoint tool list, tasks extension requirement | the runtime's 1.x documentation (core concepts; agents via MCP; A2A) |
| Timeline dates | first commits of the relevant files and the ledger's dated entries |

## Do not claim

- That the coding-assistant door can open a conversation or decide an approval (it can't;
  it pairs with the command line or the UI).
- That the gateway authenticates callers (the open-source build doesn't; the reference relies
  on an IP allow-list and TLS, with one shared identity).
- That the three doors in screen 10 ran as different people (they ran as the same identity).
- Production readiness of the runtime (it is alpha) or any uptime figure.
- That "177 tools" are all bound to agents (60 are; the rest are discovered but unbound).
- Any company, person, hostname or repository name: the reference is anonymous by design.

## If the infographic needs fewer screens

Keep, in this order: 02 (define once), 03 (what an agent is made of), 04 (tools are a
catalog), 06 (one gateway, five doors), 10 (three doors, recorded), 12 (same gate, same
record), 14 (what maps). That is the argument in seven panels.
