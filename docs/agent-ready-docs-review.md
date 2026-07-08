# Agent-Ready Infrastructure Documentation — Research Review

*A shareable summary of our review into "docs-as-code for AI agents": how teams structure infrastructure knowledge in git so agents (kagent, Claude Code, SRE copilots) can triage, answer questions, and operate the stack.*

---

## TL;DR

Over the last year a pattern has crystallized: a **git-tracked, markdown-first knowledge base whose *structure* — not just its content — is designed for AI agents to navigate.** The `Golden-Ecosystem` repo you shared (with `INFRASTRUCTURE_ATLAS.md`, per-folder `_INDEX.md` files, dated audits, and `.claude/` commands) is a clean instance of it.

The durable insight: **your git repo is the versioned "ground truth" context layer; MCP servers are how that context — plus live cluster state — gets *delivered* to an agent at query time.** The repo doesn't replace live tool calls; it's the source of truth those tools reference. This is why **Backstage's MCP backend is such a strong fit** (details below).

---

## What the pattern looks like

The recognizable building blocks, all present in the example repo:

| Element | Purpose |
|---|---|
| `INFRASTRUCTURE_ATLAS.md` | Single top-level entry point an agent reads first: system context, topology, known gaps, and a **source registry** of where every fact came from. |
| `_INDEX.md` per folder | Per-directory table of contents so an agent can traverse breadth-first without reading every file. |
| `CLAUDE.md` / `AGENTS.md` + `.claude/` | Agent entry-point instructions and invocable commands. |
| `Runbooks/`, dated `Audit/` snapshots, `[WIP]` markers | Status and freshness encoded in filenames so the agent knows what's current. |

## The file-convention layer (the most settled part)

- **`CLAUDE.md`** is what Claude Code reads; **`AGENTS.md`** is the vendor-neutral equivalent (adopted across 2,500+ repos GitHub studied). They **don't auto-read each other** — Anthropic's guidance is one source of truth with `CLAUDE.md` importing `@AGENTS.md`.
- **Keep instruction files short** (~200 lines) — longer files measurably *reduce* how well the agent follows them.
- **Lazy, path-scoped loading**: rule files with `paths:` globs and nested per-directory files load *only when the agent touches matching files*, protecting the context budget.
- **`llms.txt`** is the analog for *published docs sites*; `AGENTS.md`/`CLAUDE.md` are for *repos*. Different jobs.
- **`SKILL.md` / command files** turn runbook procedures into *invocable* skills (`/triage-sync`, `/runbook`) instead of passive prose.

## How Kubernetes/GitOps agents actually consume it

Important verified nuance: **agents blend static repo docs with live tool calls — not one or the other.**

- **HolmesGPT** (CNCF Sandbox AI-SRE agent): pulls org knowledge from **doc integrations** (Confluence, Slab, GitHub/GitLab via MCP, Notion) *and* 50+ live toolsets (Kubernetes, Prometheus, Grafana, **ArgoCD** — reads app status, sync history, manifests during an investigation). Two ideas worth stealing: **read/write capability-scoping** (read-only by default, separate remediation server for writes) and **context-budgeting as a first-class concern**.
- **kagent** (CNCF): exposes tools to agents via **MCP servers configured as Kubernetes CRDs** — your agent tooling is itself GitOps-managed YAML. Reportedly loads markdown runbooks from git into agents at startup.

## Why Backstage as the backend MCP for agents

Backstage ships an **MCP Actions backend** that exposes the software catalog to agents — turning your catalog into agent-queryable context. This is compelling as *the* backend because:

- **The catalog already models your stack** — services, owners, dependencies, APIs, systems, and their relationships. That graph is the single highest-signal context you can hand an agent; you don't have to invent a new schema.
- **One backend, many agents.** Instead of each agent scraping repos independently, Backstage's MCP backend gives every agent (kagent, Claude Code, an SRE copilot) a *consistent, permissioned* view of the same catalog.
- **Ownership and relationships are built in.** "Who owns this service, what does it depend on, what's its runbook" is a native catalog query — exactly the questions triage agents ask.
- **Your Internal Developer Platform becomes an "AI goldmine."** The metadata you already maintain for humans (TechDocs, catalog entities, scorecards) becomes the retrieval layer for agents at near-zero extra cost.
- **GitOps-native.** Catalog entities are YAML in git, so the agent's context inherits the same review, versioning, and audit trail as the rest of the platform.

**The combined architecture:** git repo (atlas + runbooks + per-service docs, version-controlled ground truth) → surfaced through Backstage's catalog + MCP Actions backend → consumed by kagent/Claude Code agents alongside live cluster toolsets (ArgoCD, Prometheus, Kubernetes) for triage and Q&A.

## Retrieval-design principles (make the KB actually work)

- **Be explicit — agents don't infer.** Omitted context is invisible to retrieval; write out the "obvious."
- **Keep related facts physically close** — one service's config, dependencies, and runbook together beats the same facts scattered across five files.
- **Frontmatter metadata** (`owner`, `status`, `last-reviewed`, `tags`) so agents can filter and date-check.
- **Wikilinks + an index layer** for a navigable graph.
- **Encode freshness** — dated snapshots and `[WIP]` markers so the agent distrusts stale content.

## Sources

- Anthropic — *How Claude remembers your project* (`code.claude.com/docs/en/memory`) — CLAUDE.md conventions, sizing, path-scoping *(verified)*
- `github.com/herms14/homelab-agent` — homelab KB layout with service catalog / IP registry / runbook commands *(verified)*
- HolmesGPT — `holmesgpt.dev` + `github.com/HolmesGPT/holmesgpt` — doc + toolset + MCP triage pattern *(verified)*
- Backstage — `backstage.io/docs/ai/mcp-actions` (MCP Actions backend) + "Agentic Backstage: managing an AI software catalog"
- kagent — `kagent.dev` (MCP tools as CRDs, skills-from-git)
- Roadie — *Your IDP Is an AI Goldmine: context engineering*
- GitHub — *How to write a great agents.md: lessons from 2,500 repositories*
- Karpathy's LLM Wiki gist + `llm-wiki-compiler` — the "atlas points at sources; sources are truth" wiki-compilation pattern *(verified)*

*Note: a few claims (kagent skills-from-git specifics, some retrieval-design principles) come from single or vendor sources and are marked accordingly; the verified items were adversarially fact-checked against their primary sources.*
