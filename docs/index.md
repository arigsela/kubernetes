---
type: "Directory Index"
title: "Docs"
description: "Directory listing for cross-cutting guides, troubleshooting notes, design specs, and implementation plans."
tags: [docs, specs, plans]
---

# docs Index

Cross-cutting guides, troubleshooting notes, and the design history behind the
platform. Per-app knowledge lives beside the manifests in `base-apps/<app>/`
(`docs.md`, `runbook.md`), not here; start from `base-apps/index.md` for an app.

**Status** says how far to trust a doc: *living* docs are kept current;
*record* docs describe work that is finished and may name files that have since
changed; *active* plans describe work in progress.

## Agentic Development Platform (ADP)

The agent-platform hardening arc: Identity, Security/Capability, Observability,
and Evaluation.

| doc | status | purpose |
|---|---|---|
| `adp-engineering-deep-dive.md` | living | How the agent-identity & agent-capability admission policies and the corpus/scorer eval harness work, in engineering detail |
| `adp-resources-and-observability.md` | living | Review index: AWS resources created, dashboards to observe it in, and links to every tool used |
| `superpowers/specs/2026-07-14-adp-remaining-pillars-roadmap.md` | active | Roadmap for the remaining pillars + the O0 observability spike result |
| `superpowers/specs/2026-07-11-agent-identity-principal-design.md` | record | Identity: the "agent-principal" pattern (scoped credentials) |
| `superpowers/specs/2026-07-13-agent-capability-classes-design.md` | record | Security: per-agent capability classes + tool taxonomy |
| `superpowers/homelab-agent-vault-provisioning.md` | living | One-time Vault provisioning and rollout runbook for the BYO `homelab-agent` (see `scripts/provision-homelab-agent-vault.sh`) |
| `agent-ready-docs-review.md` | record | Research review: docs-as-code for agents (led to the agent-docs contract) |
| `okf-documentation-structure.md` | living | How this repo's docs map onto the Open Knowledge Format |

The enforced contracts themselves: `templates/agent-identity/README.md`, the
native admission policies in `base-apps/admission-policies/agent-*.yaml`, and the validators
in `scripts/validate-agent-*.py`.

## Platform guides

| doc | status | purpose |
|---|---|---|
| `managed-apps-appset.md` | living | The `managed-apps` ApplicationSet: config schema, adding/removing apps, rollback |
| `idp-migration-guide.md` | record | Onboarding apps via Backstage + the Crossplane XApplication (superseded by the New App template, `templates/new-app/`) |
| `kubernetes-networking-and-service-mesh.md` | record | Networking, mTLS, and service-mesh primer (its cluster-specific parts predate the Gateway migration) |
| `istio-ambient-mesh-implementation-plan.md` | record | How the Istio ambient dataplane was installed (Dec 2025) |
| `feed-aggregator-refactor-plan.md` | record | Feed-aggregator n8n workflows (`n8n-workflows/`, schema in `sql/feed_articles_schema.sql`) |

## Troubleshooting

| doc | purpose |
|---|---|
| `troubleshooting/kubectl-oidc.md` | kubectl login through Dex OIDC (kubelogin), and the break-glass path |
| `troubleshooting/in-place-resize.md` | In-place pod resize: which workloads support it, `scripts/resize-pod.sh`, the alerts |
| `troubleshooting/cnpg-managed-roles-inert.md` | CNPG `spec.managed.roles` reconciliation is inert on `postgresql-cluster` (open), and the manual workaround |

## Plans (`plans/`)

| doc | status | purpose |
|---|---|---|
| `plans/k3s-1.36-upgrade-plan.md` | record | Runbook for the k3s v1.35.6 → v1.36.4 hop; the procedure to reuse for the next hop (SPEC T89) |
| `plans/k3s-1.36-api-scan.md` | record | Deprecated-API scan for 1.36 (read by `tests/k3s-upgrade/`; don't move it) |
| `plans/k8s-136-features-implementation-plan.md` | record | Adopting five Kubernetes 1.34–1.36 features after the 1.36 hop |
| `plans/kagent-1-0-feasibility-spikes-implementation-plan.md` | active | Feasibility spikes before a kagent 1.0 upgrade |
| `plans/argocd-drift-diagnosis.md` | record | Why each `ignoreDifferences` exists (cited by several Applications) |
| `plans/vault-auto-unseal-implementation-plan.md` | record | Moving Vault from Shamir to AWS KMS auto-unseal |
| `plans/atlantis-infracost-implementation-plan.md` | record | Deploying Atlantis + Infracost |
| `plans/argo-workflows-implementation-plan.md` | record | Deploying Argo Workflows |
| `plans/ingress-nginx-replacement-handover.md` | record | Replacing ingress-nginx with the Istio Gateway (SPEC T43) |
| `plans/ingress-migration-baseline-2026-07-29.md` | record | Pre-migration ingress baseline (SPEC T50) |
| `plans/newsletter-digest-n8n-prd.md` | record | Newsletter digest: requirements |
| `plans/newsletter-digest-n8n-implementation-plan.md` | record | Newsletter digest: n8n implementation |
| `plans/newsletter-digest-cowork-skill-handoff.md` | record | Newsletter digest: one-time skill handoff |

## Specs & plans: where they go

There are two plan locations, one per tool:

- `plans/` — plans written by this repo's `.claude/skills/`
  (`creating-implementation-plans`, `executing-implementation-plans`).
- `superpowers/specs/` and `superpowers/plans/` — design specs and plans from the
  superpowers brainstorming and writing-plans skills, dated `YYYY-MM-DD-<slug>`.
  Browse the directories for the full set.

`SPEC.md` at the repo root is the current task and invariant ledger; manifests
cite its sections (`§T.n`, `§V.n`).
