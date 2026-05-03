# Phase 0 — Kickoff Checklist

> Companion to `2026-05-03-golden-poc-phase-0-foundation.md`. Use this to gate "am I actually ready to start Phase 0" and to sequence the work day-by-day. Not a replacement for the full plan — the full plan has the commands and file paths.

**Reference design:** `docs/superpowers/specs/2026-05-02-golden-ai-platform-poc-design.md`
**Full plan:** `docs/superpowers/plans/2026-05-03-golden-poc-phase-0-foundation.md`

---

## Before you start (decide / acquire)

- [ ] **Worktree branch** chosen for the Phase 0 PR (e.g. `phase-0-foundation`)
- [ ] **Anthropic API key** in hand
- [ ] **GitHub admin access** to `arigsela/kubernetes` (to create the App)
- [ ] **DNS / ingress hostnames** decided for: `langfuse.<base-domain>`, `agentgateway.<base-domain>`, `agentregistry.<base-domain>`, `github-webhook.<base-domain>` (last one is for Phase 2 but the webhook URL is set now)
- [ ] **GitHub webhook reachability path** chosen (Cloudflare Tunnel, existing ingress, or smee.io for dev) — only the URL needs to exist now; reachability is Phase 2's problem
- [ ] **Helm chart versions** noted for Langfuse / agentgateway / agentregistry (plan punts on these; pin them now)
- [ ] **Local tools verified:** `gh`, `vault` (authenticated), `helm`, `kubectl`, `crossplane` CLI, `argocd` CLI
- [ ] **Baseline check:** ArgoCD apps all green; CNPG running; existing kagent v0.8.6 healthy; Vault + ESO functioning per-namespace

---

## Day 1 — Resolve gating unknowns

These three unblock the rest of Phase 0 (and parts of Phase 1 and Phase 3). Do them first.

- [ ] **Preflight 1** (Task 0.1): clone kagent, inspect ToolServer CRD, classify Bucket A / B / C → record in `docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md`
- [ ] **Preflight 2** (Task 0.2): clone agentregistry, document the 4 HTTP endpoints (list artifacts, get artifact, list versions, list tags) → append to preflight-results doc
- [ ] **GitHub App** (Task 0.3): create `golden-poc-pr-reviewer`, install on repo, store App ID + install ID + webhook secret + private key in `vault kv put k8s-secrets/github-webhook-adapter ...`
- [ ] **Decision gate:** if Preflight 1 = Bucket B or C, flag Phase 1 — Composition design changes materially

---

## Day 2 — Stage credentials

- [ ] **Anthropic key** (Task 0.4): `vault kv put k8s-secrets/agentgateway anthropic_api_key=...`

---

## Day 2–3 — Install platform components (parallelizable)

The three installs are independent; the only ordering constraint is that the kagent reconfig (Day 4) waits on agentgateway and Langfuse being live.

- [ ] **Langfuse** (0.5 + 0.6): namespace + SecretStore + ESO (sync-wave 0) → Helm app with CNPG-backed Postgres + Ingress (sync-wave 1) → seed `golden-poc` project, capture `pk_*` / `sk_*` into Vault
- [ ] **agentgateway** (0.7 + 0.8): namespace + ESO with Anthropic key → Helm app fronting Anthropic; smoke test with `curl` from a debug pod
- [ ] **agentregistry** (0.9 + 0.10): namespace + admin token in Vault → Helm app with persistent volume + Ingress
- [ ] **Seed skills** (0.11): install `arctl`, `arctl push` four skills (`kubernetes-mcp`, `prometheus-mcp`, `github-mcp`, `k8s-yaml-lint` placeholder)

---

## Day 4 — Wire kagent (sequential edits to one app)

- [ ] **Route LLM via agentgateway** (0.12): single PR to `base-apps/kagent.yaml` pointing model config at `agentgateway.agentgateway.svc`
- [ ] **OTLP to Langfuse** (0.13): add second OTel exporter alongside Coroot
  - Decide here: kagent multi-exporter native (Pattern X) or OTel collector sidecar (Pattern Y) — risky/underspecified item from the design

---

## Day 5 — Verify + close out

- [ ] **E2E verification** (0.14): ArgoCD all green; agentgateway proxy returns Anthropic response; kagent emits trace visible in Langfuse `golden-poc` project; agentregistry lists all four skills via API
- [ ] **Preflight-results doc** finalized and committed (drives Phase 1 + Phase 3 design decisions)
- [ ] **PR** opened from worktree branch with all Phase 0 commits

---

## Exit criteria (Phase 0 is "done" when)

1. New ArgoCD apps for Langfuse, agentgateway, agentregistry — all Synced + Healthy
2. kagent routes through agentgateway and emits OTLP to Langfuse
3. GitHub App `golden-poc-pr-reviewer` exists, installed on repo, creds in Vault
4. Four skills queryable in agentregistry
5. Preflight results doc committed with Bucket A/B/C decision and HTTP API table
6. Demo-blocking risks called out: any chart values that surprised you, OTLP pattern chosen, webhook reachability mechanism

---

## Watch-outs flagged in the plan itself

- Helm chart values schemas for agentgateway and agentregistry are unverified — inspect chart values **before** committing the ArgoCD app
- `arctl push` flag stability (agentregistry pre-1.0)
- Langfuse OTLP auth header format may force the OTel collector sidecar route
- kagent Agent CR `spec` field names are assumed; verify against the v0.8.6 CRD while you're already in the kagent repo for Preflight 1

---

## Cross-references

- After Phase 0: Phase 1 plan branches on Preflight 1 result (`Bucket A` is the assumed-default path; `B`/`C` require Composition rewrites)
- After Phase 0: Phase 3 Backstage skill-catalog plugin uses Preflight 2 endpoint shapes
- Phase 4 heartbeat CronJob must start ≥1 week before demo day to seed Langfuse history
