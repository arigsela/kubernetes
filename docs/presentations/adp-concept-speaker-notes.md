# An Agentic Developer Platform

Seven-panel concept briefing for engineering leadership and AI pathfinders. Created 2026-10-12 from the supplied concept brief. This follows the brief's explicitly permitted condensed sequence: original screens 02, 03, 05, 07, 09, 13, 14.

The reference remains anonymous. The illustrated notebook palette follows the requested tech-infographics house style. Images are explanatory illustrations, not screenshots. Recorded quotations are attributed to the supplied transcript dated 2026-10-11. Configuration was checked against repository documentation; no live cluster checks or demo commands were executed for this artifact.

## 01 — Agents are new users of the platform

**Visible copy / text alternative:** The platform is reused. The agent becomes a principal. A developer and an agent each have four matching badges: Identity, Permissions, Audit trail, Paved roads. Both use one existing developer platform. The agent also has human approval for risky actions. Governance decides what agents may do. In the reference: own credential, scoped tools, recorded actions.

**Say:** This is a concept backed by a working reference. An agent becomes a governed principal, like a developer or CI job: its own identity, explicit permissions and an action record. Most of the work is platform work: identity, policy, audit, docs and a runtime. The model can be swapped without redesigning these controls.

**Qualification:** An assigned credential is a logical identity relationship; it does not mean the agent process holds the secret key. The reference injects model credentials at the egress boundary. Do not claim that a compromised agent can cause no harm: scope, enforcement and operations still matter.

**Sources:** supplied brief, screen 02; reference runtime documentation, credential and tool-binding sections. Status: concept plus documented configuration.

## 02 — Three layers

**Visible copy / text alternative:** Build bottom-up. Reuse what already works. Bottom layer: Developer platform — GitOps, Infrastructure as code, Secrets, Edge, Policy, Data & backups, Observability, Developer portal. Middle layer: Paved roads — Scaffolder, Dependency updates, PR triage, Scheduled work. Top layer: Agent infrastructure — Runtime (alpha), Identity, Capability, Approval, Tools, Memory, Audit, Evaluation. Layer 3 reuses the secrets, deployment and docs systems below.

**Say:** Start at the bottom. The developer platform provides the foundations; paved roads make repeatable work routine; the agent layer adds governed access to those existing capabilities. It adds no separate secrets system, deployment path or documentation system.

**Mechanisms to explain if asked:** GitOps means reviewed commits express desired state and a controller reconciles it. Secrets come from a central vault and are scoped per consumer. A tool taxonomy classifies named capabilities; approval is a protocol-level pause. Docs are schema-checked architecture pages and runbooks, rendered for people and retrieved by agents from the same source. Evaluation compares answers with an expected corpus.

**In the reference:** Argo CD; Vault and External Secrets; native admission policies; Backstage; tool servers using MCP; kagent on Agent Substrate. The runtime is alpha. The slide lists components rather than asserting service counts or live health.

**Sources:** supplied brief, screen 03; reference runtime docs; substrate docs. Status: documented architecture/configuration, not current runtime observations.

## 03 — A change stops at the gate

**Visible copy / text alternative:** Delete the ConfigMap approval-gate-probe. Gateway → Operator agent → Checks first → Human approval → Action + record. Read the object and confirm the throwaway label. A human sees the exact call and arguments. Approve permits the next step. Reject means no action and no automatic retry. Three nested enforcement layers: runtime binding, admission policy, CI validator. In the reference: requireApproval: true. Three enforcement layers. No prompt can grant approval.

**Say:** The agent reads before proposing the deletion. The runtime pauses a call through an approval-required binding. Admission policy refuses a definition that exposes mutating tools without approval; CI checks the same condition before merge. These are separate enforcement points, not three human approvals.

**Scope:** Approval is per binding in this reference API. Read tools and mutating tools can use separate bindings to the same tool server. The statement about prompts applies to this configured gate, not a proof against every possible implementation vulnerability. Operational changes to the platform still go through Git. This deletion is a disposable approval probe, not a recommended deployment workflow.

**Sources:** supplied brief, screen 05; engineering deep-dive, capability rules 1–5; operator template and generated admission policy. Status: configuration corroborated locally. This artifact does not execute the probe.

## 04 — The approval gate, both ways

**Visible copy / text alternative:** Recorded 2026-10-11. Same request, run twice: rejected, then approved. Delete approval-gate-probe; read object and confirm throwaway label. k8s_delete_resource requires approval. Waiting for a human. Run 1 — Reject: object remains. Run 2 — Approve: object deleted; call and decision recorded. A protocol-level pause. Rejection ends that call.

**Recorded wording from the supplied brief:**

> Operator agent: The ConfigMap exists and matches your description. It has the label `test.homelab/throwaway: "true"`. I'm deleting it now.

> System: Tool `k8s_delete_resource` requires approval before execution. Waiting for a human.

> Human · reject → "The delete didn't go through. The approval step rejected the call, so the ConfigMap is still in the default namespace. I haven't retried."

> Human · approve → "The ConfigMap approval-gate-probe is deleted from the default namespace. A follow-up get on it failed, confirming it is gone."

**Say:** Notice that the agent's claim that it is deleting the object does not execute the call. The runtime still waits. These are two runs of the same request, not one rejected call silently retried. The approved run executed once in this recorded example; that is not a distributed-systems guarantee of exactly-once behavior under all failures.

**Source:** supplied brief, screen 07 and Data provenance. Status: supplied historical transcript, not a fresh execution. The two outcome quotations are preserved on the image; the full preamble is retained here.

## 05 — Six controls around the agent

**Visible copy / text alternative:** Enforce permissions outside the model. Identity — scoped credentials, keys injected at egress. Capability — read, write, destructive; mutating bindings require approval. Audit — record calls and decisions; alert when reporting stops. Evaluation — known-good answers; rules, then classifier, then judge. Isolation — sandbox per conversation; default-deny egress. Change control — tools, models and permissions change through reviewed Git. The model cannot grant itself permissions. Reference runtime: alpha.

**Say:** Identity limits credentials; capability limits tools; approval pauses risky actions; isolation limits the execution environment. Audit and evaluation supply evidence and detect failures. Evaluation is a quality measurement layer, not an authorization boundary. The controls operate together and do not establish absolute security.

**Historical failure, deliberately retained:** On 2026-10-11 a runtime change altered the event format and blinded the audit for about 90 minutes while it reported zero findings. The repository incident ledger records 18 missed calls and no actually ungated calls. The lesson is to replay a known gated call after runtime changes and verify that audit sees it. Zero findings alone is insufficient evidence.

**Examples supplied by the brief:**

```text
denied: binds write/destructive tools in an MCP binding without requireApproval: true
{"check":"agent-audit-ungated","severity":"ok","ungated_invocations":0}
```

These are supplied historical outputs, not generated logs or new measurements. The runtime and its substrate remain alpha; the reference has a single control plane. No availability or production-readiness claim is made.

**Sources:** supplied brief, screens 09 and 12; engineering deep-dive; runtime/substrate docs; specification ledger incident B28 dated 2026-10-11. Status: configured controls plus a dated incident.

## 06 — What maps to a company

**Visible copy / text alternative:** Reuse the governance. Decide the ownership. Maps directly: agents as principals, team-owned tool taxonomy, audit feed to your SIEM, shared docs contract. Needs a decision: who approves each write, routing by team and risk, accountable owner. Still early: sandboxed runtime is alpha; domain evaluation needs work. Start here: one team → one read-only agent → every call recorded. Answers from the team's docs and systems; cannot change them. Start read-only. Earn write access with evidence.

**Say:** These are transferable design patterns, not a claim that one toolset fits every company. The reference has one human approver; a company must choose approval ownership and escalation by team and risk. Start with a read-only agent for one team, reading its docs and systems, with every call recorded. The governance created for that use case supports later work.

**Clarify:** SIEM means the organization's security event and monitoring system. The proposed SIEM integration is a company adoption pattern; the slide does not claim the reference already has that integration. Domain-specific evaluation remains work to do.

**Source:** supplied brief, screen 13. Status: adoption proposal and stated limitations.

## 07 — Five questions for the group

**Visible copy / text alternative:** Decide the operating model before expanding access.

1. Reuse: Which platform pieces already exist, and which are missing?
2. Authority: Who classifies tools, and who approves a write?
3. First agent: What is the first read-only agent, and whose docs would it read?
4. Accountability: What does the audit record feed, and who reads it?
5. Evaluation: How do we evaluate before and after a model change?

The agents are visible. The platform makes them safe to run. The reference can demonstrate a question, an approval and a refusal.

**Say:** The group owns the strategy. Use the working reference as something concrete to challenge. For the first question, consider non-human identity, scoped secrets and policy as code. For authority, decide team, risk-class or central ownership. End on the questions. A live demo can show a question, an approval and a policy refusal, but preparing these graphics did not run any of them.

**Sources:** supplied brief, screens 14 and 15. Status: discussion prompts and supplied demo offer.

## Evidence and editorial choices

- This set uses the seven-screen sequence authorized by the brief. It is not a rendering of all 15 screens.
- No current service counts, cache percentages, uptime or cloud costs are asserted. The brief's budget target is not an invoice, and its metrics describe a dated sample.
- No hostnames, repository name, personal names, company names or creator branding appear in the slides. Generic relative technical paths below support local verification.
- The PDF consists of raster image pages. It is not a tagged accessible or selectable-text PDF. The text alternatives and notes here provide reusable text.
- The slides were generated as images from the brief (one prompt per slide) and assembled into `adp-concept-infographics.pdf`, seven 16:9 pages at 1672×941 px. The generation prompts and per-page checksums stayed with the generation output outside this repo.

## Local source map

Paths are relative to the project root. No external research or live queries were used.

| Source | Evidence used |
|---|---|
| `docs/presentations/adp-concept-presentation-brief.md` | Intended audience, anonymity, sequence, quotations and dated provenance |
| `docs/adp-engineering-deep-dive.md` | Capability rules, taxonomy and per-binding approval semantics |
| `base-apps/kagent/docs.md` | Runtime alpha status and approval behavior |
| `base-apps/kagent/agent-templates/k8s-agent.yaml` | Read and mutating tool bindings |
| `base-apps/admission-policies/agent-capability.yaml` | Generated enforcement of mutating bindings |
| `base-apps/substrate/docs.md` | Sandbox, snapshots and egress credential injection |
| `SPEC.md` | Historical runtime migration and incident B28 |
