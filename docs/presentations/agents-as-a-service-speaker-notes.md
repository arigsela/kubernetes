# One agent, every door

Seven-panel briefing for engineering leadership and AI pathfinders, dated 2026-10-12. This is the consumption-model companion to the ADP concept and development workflow decks. It follows the source brief's permitted condensed sequence: screens 02, 03, 04, 06, 10, 12 and 14.

House style: illustrated notebook, matching both previous decks. Diagrams are explanatory art, not screenshots. Recorded material is attributed to the supplied brief; no agent conversations, live approval probes, deployment changes or account operations were performed while preparing this artifact.

## Important evidence corrections

1. **Three doors does not mean one conversation.** The source's screen 10 title and suggested continuous timeline contradict its event descriptions: the CLI-to-coding-assistant exchange is a knowledge session, while the CLI-to-UI approval belongs to a different operator session. Slide 05 uses two separate lanes and the title “Three doors, two conversations.”
2. **Configured idle retention is now 30 days.** The brief and a runbook passage say seven days. The current deployment manifest sets `controller.sessionIdleTTL: "720h"`, and the architecture page says 30 days. Slide 07 calls this configured retention, not observed deletion behavior. Running turns and turns awaiting approval are excluded from expiry according to the manifest comments and architecture page. Seven days is the chart default cited by those docs.
3. **Memory configuration belongs to the Harness in the current reference.** The source's object table assigns a memory setting to the Template. The checked manifests put memory configuration under the Harness; the Template holds prompt, model and tool bindings. Slide 02 reflects the actual files.
4. **Shared identity is not authenticated identity.** The recorded CLI/editor/UI demo used a shared identity. Local documentation also describes a different portal identity header, so the deck does not claim every integration literally uses one identical header value. Caller-supplied partitioning is not an authentication boundary.
5. **Definition changes do not alter existing sessions mid-conversation.** Slide 01 says new conversations use the improved definition. Existing sessions retain their starting revision. This qualifies the source's “everywhere, at once” language.

## 01 — Define once, consume anywhere

**Text alternative:** An agent is a platform resource, not a chat window. Left: separate editor, chat and job agents each have their own prompt and key; prompts, keys and permissions drift. Right: one reviewed agent definition holds prompt, model, tools plus approvals, and runtime. Five doors—chat UI, coding assistant, command line, portal and automation—reach it through one logical gateway. Improve the shared definition; new conversations use it. Existing conversations keep their starting revision.

**Say:** Centralize the agent definition rather than recreating its behavior inside every client. Teams can use the tools they already know while prompt ownership, permissions and the action record stay with the platform. The left-hand side is a fragmentation pattern, not a measured claim about most companies.

**Qualification:** One gateway here means a shared logical access/service layer, not necessarily one hostname, process, replica or network endpoint. The UI and API can use different routes. Zero model API keys on clients does not mean clients need no identity credentials in a properly authenticated company deployment.

**Source/status:** brief screens 01–02 and 05; revision behavior corroborated in the runtime runbook. Concept and documented behavior, not a live rollout verification.

## 02 — What an agent is made of

**Text alternative:** Template: prompt, model, named tools and approval flags. Harness: runtime, worker pool, snapshots and memory. Both feed an Agent, which binds the template to the harness. A Conversation is pinned to its starting revision. Shared parts include prompt snippets, tool servers and model configs. Nothing is configured only in a chat window. Reference runtime: alpha.

**Say:** These are declarative resources with clear responsibilities. The Template describes what the agent does and may call. The Harness describes how it executes. The Agent binds those references and is what clients address. Shared prompt includes and model configurations keep common changes reviewable.

**Current configuration correction:** Memory lives in the Harness in the checked reference files. The Template has memory-related prompt instructions, but the runtime memory model configuration is not the same thing as a prompt instruction. Snapshot storage persists execution state; long-term memory is a distinct mechanism.

**Revision lesson:** Existing conversations do not automatically adopt a changed prompt. Open a new conversation to exercise a changed definition. The source's memory-tool workaround was a reviewed update to six per-agent prompts; do not misrepresent that particular patch as proof that one shared include alone propagated the fix.

**Sources/status:** brief screens 03 and 05; `base-apps/kagent/agent-templates/k8s-agent.yaml`; `base-apps/kagent/harnesses/kagent-memory.yaml`; `base-apps/kagent/agents/k8s-agent.yaml`; `base-apps/kagent/runbook.md`. Locally checked configuration and documented revision behavior. The runtime remains alpha.

## 03 — Tools are a catalog, not a prompt

**Text alternative:** Capability is a named list with a class. A tool catalog separates read, write and destructive capabilities. Agent templates bind tools by name into read and mutating bindings. Mutating bindings require `requireApproval: true`; execution waits for a human decision. Admission and CI refuse unknown tools and ungated mutations. The reference cluster operator has eight read tools and ten gated tools. Adding a tool is a reviewed change. Discovered tools are not automatically available to an agent.

**Say:** A client sends a request to an existing agent; it does not rewrite the agent's tool bindings. The platform checks the definition against a taxonomy. Approval applies to the binding containing a mutating tool, while runtime execution pauses when a call uses that binding. Admission and CI are definition checks; they do not wait for a human decision on every runtime call.

**Evidence:** The operator template has two explicit bindings to the same server. The first lists eight read tools and sets approval false. The second lists ten tools and sets approval true. Omitting or emptying the tool list would expose broad server capabilities and is denied by the reference policy.

**Counts intentionally not generalized:** The brief's discovered catalog count of 177 is a dated UI observation, not the number of capabilities available to every agent. Its 60 bound entries and 17 gated entries aggregate bindings across agents and should not be called 60 unique tools. These aggregate dashboard metrics are omitted from the slide.

**Sources/status:** brief screen 04; operator template; capability description in `base-apps/kagent/docs.md`; `base-apps/admission-policies/agent-capability.yaml`. Binding counts checked directly in the local template. No tool execution was performed.

## 04 — One gateway, five doors

**Text alternative:** Five doors feed a shared gateway and agent/session/action-record service: Chat UI, Coding assistant, Command line, Developer portal, Automation. Chat UI starts conversations and decides approvals. Coding assistant continues existing sessions but does not start a fresh conversation or decide approvals in this reference integration. Command line does both. Portal starts sessions for read-only uses. Automation starts sessions; approval decisions are not used in the described reference automation. Start in one client, continue with the same session id. MCP and the agent protocol are the interfaces; model API keys stay off clients.

**Say:** The clients are thin, but they are not interchangeable in every operation. The coding assistant is useful because it reaches the agent from the editor; its current MCP integration still needs the CLI or UI for fresh sessions and approval decisions. These limitations belong to the reference integration, not every coding assistant or every MCP implementation.

**Capability table:**

| Door | Starts a fresh conversation | Decides an approval in the reference |
|---|---|---|
| Chat UI | Yes | Yes |
| Coding assistant | No; continues a session | No; pair with UI or CLI |
| Command line | Yes | Yes |
| Developer portal | Yes | Read-only uses; not applicable |
| Automation | Yes | Not used in the described reference |

**Mechanisms:** The gateway MCP endpoint exposes list, continue, checkpoint and fork operations. A fork is not the fresh-by-agent start operation discussed here. The assistant lacks the protocol tasks extension needed for approvals. The CLI requests the agent protocol's approval extension. The CLI and evaluation harness share a client library; the UI and MCP integration use different interfaces. Do not repeat the source's overly broad “five doors, one client library, one protocol” caption literally.

**Sources/status:** brief screens 06–09; endpoint and identity sections in `base-apps/kagent/docs.md`; `scripts/ask-hk` and `scripts/kagent_a2a.py` are the documented CLI/client library. The Python files were identified, not executed. Portal and automation usage is described by the supplied brief.

## 05 — Three doors, two conversations

**Text alternative:** RECORDED, 2026-10-12. One shared identity; two separate session histories. Lane A: knowledge conversation, command line at 15:37 UTC asks about the search server, then coding assistant at 16:12 UTC asks its namespace using the same session id. Answer: kagent. A repository token expired and was later fixed. Lane B: a different operator conversation starts a deletion request from the command line; the call pauses, and at 17:20 UTC a human approves in the chat UI. Object: approval-gate-probe. These were not different authenticated users. Session continuity crosses clients; separate conversations stay separate.

**Say:** The first example demonstrates continuing the same knowledge session across two clients. The second demonstrates deciding a paused operator session from another client. Together these exercise three distinct doors. They do not establish that a single session changed agent identity or crossed from knowledge to operator.

**Supplied event descriptions:**

- **15:37 UTC, knowledge session:** The command line asked, “Which file defines the Brave Search MCP server and what tools does it enable?” The answer used live cluster objects and disclosed that repository access was unavailable because a token had expired.
- **16:12 UTC, same knowledge session:** The coding assistant continued through MCP after the repository token was fixed. It asked the namespace question below.
- **17:20 UTC, separate operator session:** A deletion request started from the command line paused for approval. The owner found that session in the UI and approved. A later command-line query saw the continued session.

**Verbatim supplied follow-up, with line wrapping normalized:**

```text
human (via assistant): One-line answer: which namespace is the brave-search-mcp
                       MCPServer in?
agent:                 `kagent`. The file `base-apps/kagent/brave-search-mcp.yaml`
                       sets `metadata.namespace: kagent`, and the live object is in
                       the same namespace.
```

**Approval demonstration retained from source screen 11:** A CLI deletion request paused and returned exit 2. The human rejected it with the reason “CLI acceptance test: reject path.” The agent reported that it did not retry and that the object remained. A subsequent explicit message, “Please try the delete again now,” created a new pending call. The human then approved. The agent reported deletion and a failed follow-up get. Rejection terminated the first call; the agent did not retry autonomously. The same test conversation can contain a later, separately requested call.

**Scope:** The recorded approval happened once in that acceptance example; this is not a guarantee of exactly-once execution across every possible failure. The source says the CLI approval extension support was added on 2026-10-12. The UI decided a different pause later. No acceptance test was rerun for this artifact.

**Sources/status:** supplied brief screens 08, 10 and 11, plus Data provenance. Historical user-supplied transcripts and timestamps, not independently fetched logs. Session identifiers are unnecessary to the visual explanation and omitted.

## 06 — Same gate, same record

**Text alternative:** Controls behind the gateway, whichever client is used. Capability: named tools and enforced classes. Approval: gated calls pause for a decision. Action record: calls, arguments and decisions. Isolation and credentials: sandboxed conversation, model keys injected at egress. Identity gap: caller-supplied identity header, shared identity in the recorded demo, gateway does not authenticate callers, IP allow-list and TLS are not authentication. UI or command line decides approvals; coding assistant pairs with either. Shared controls do not solve caller identity.

**Say:** Centralized control placement is the architectural benefit. A new client does not add permissions to the agent definition. The audit records calls through the shared runtime regardless of which client sent the request. But these controls do not fix the reference's unauthenticated identity header.

**Identity gap, explicitly:** In the described insecure mode, the caller supplies the identity header. Session partitioning uses that identity, but an unverified value is not proof of who the caller is. The IP allow-list restricts network access and TLS protects transport; neither authenticates the claimed user. Local docs mention a portal-specific header as well as the shared identity used by the CLI/editor/UI demo. Neither is an authenticated person by itself.

**Company design implication:** Put a trusted authenticating layer in front, map the verified identity to the upstream header, strip or overwrite caller-supplied identity values, and prevent bypass access to the unauthenticated upstream. Then decide whether sessions and approval authority are per person or per team, and test that policy. A proxy alone does not establish correct authorization without these choices. This is a proposed next step, not a deployed reference feature.

**Audit and isolation caveat:** An action record is only useful if collection is working; the companion decks describe the historical audit-format failure. A sandbox and an approval binding constrain behavior within their configured scope; they do not establish absolute security or production readiness. Model-provider API keys remain at the platform egress boundary, but future client SSO credentials are a different category.

**Sources/status:** brief screen 12; identity, approval and observability sections of `base-apps/kagent/docs.md`; sandbox egress description in `base-apps/substrate/docs.md`. Configured/documented controls, supplied recorded behavior, and a clearly identified future authentication proposal.

## 07 — The agent is the service

**Text alternative:** The tools are clients. Identity and ownership come first. Reuse reviewed definitions, shared prompts and tool catalog, one gateway with thin clients, and one action record. Decide person/team SSO sessions, prompt/tool ownership, the first two doors, and approval authority. Close the gaps: authenticate callers, pair the assistant with another client for start/approve, retain the alpha caveat and set session retention. Reference: 30-day idle TTL configured. Authenticate identity → choose one agent → offer two doors → review the action record. Build the agent once, behind a gate, with a record.

**Say:** The model transfers; identity and ownership do not arrive solved. Start with a concrete agent and two useful clients after establishing an authenticated identity boundary. Make prompt ownership, tool classification and approval authority explicit. Thin clients make adoption easier because the agent's definition and controls stay centralized.

**Retention correction:** The deployment currently sets `sessionIdleTTL: "720h"`, or 30 days. The architecture doc agrees; the supplied brief and a runbook sentence still say seven days. This artifact follows the configuration and labels it as configured, without claiming a deletion was observed. Running/approval-pending turns are excluded according to the source comments. Long-term memory and audit records have different storage/retention concerns from disposable sessions.

**Five discussion questions retained from the full brief:**

1. Which agents scattered across tools should become one centrally owned definition?
2. Which two doors reach the most engineers initially?
3. What maps a verified person to session identity, and who sees whose conversations?
4. Who classifies destructive tools and approves adding them to an agent?
5. Where does the action record go, and who reviews a mistaken approval?

**Source/status:** brief screen 14; `base-apps/kagent.yaml`, controller configuration; `base-apps/kagent/docs.md`, deployment settings. Company path is a proposal. Runtime alpha status remains material.

## Sources, boundaries and delivery

Primary source: `docs/presentations/agents-as-a-service-presentation-brief.md`, including Data provenance and Do not claim. Relative paths above identify locally checked evidence without hostnames or repository/owner identifiers.

The core narrative is the source brief's seven-panel alternative, not all fourteen screens. Detailed prompt-sharing, client behavior, approval outcomes and discussion questions are retained in these notes. UI dashboard counts, current health, cache metrics and total session counts were not independently refreshed and are omitted from the visuals. No claim is made that all discovered tools are bound, that all clients have the same abilities, that the gateway authenticates identity, or that the alpha runtime is production-ready.

The slides were generated as images from the brief (one prompt per slide) and assembled into `agents-as-a-service-infographics.pdf`, seven 16:9 pages. The generation prompts and per-page checksums stayed with the generation output outside this repo. The PDF consists of raster image pages; it is not selectable text or a tagged accessible PDF. These notes provide reusable text and text alternatives. The image archive contains seven ordered PNGs plus these notes, prompts and verification metadata.
