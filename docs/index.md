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
| `agent-ready-docs-review.md` | record | Research review: docs-as-code for agents (led to the agent-docs contract) |
| `okf-documentation-structure.md` | living | How this repo's docs map onto the Open Knowledge Format |

The enforced contracts themselves: `templates/agent-identity/README.md`, the
native admission policies in `base-apps/admission-policies/agent-*.yaml`, and the validators
in `scripts/validate-agent-*.py`.

## Platform components (`platform/`)

Living docs for shared components that have no per-app `docs.md` (Helm-only or
ApplicationSet-generated Applications, which the agent-docs validator can't
onboard yet).

| doc | purpose |
|---|---|
| `platform/external-secrets.md` | External Secrets Operator: store patterns, refresh, failure modes, upgrade gates |
| `platform/crossplane.md` | Crossplane core, AWS providers, functions, the dormant XApplication, the `*-aws-infrastructure` buckets, upgrades |
| `platform/falco.md` | Falco runtime detection: rules, the alert path, how a silent failure looks |
| `platform/argo-workflows.md` | Argo Workflows: access model, the weekly image-scan and CVE report, artifacts |
| `platform/vulnerability-management.md` | CVE policy: actionable vs actively exploited (KEV) vs EPSS, fix deadlines, how findings get fixed, accepted risks |

Also cross-cutting, elsewhere in the repo: backup & recovery in
`recovery/CLUSTER-RECOVERY.md`, k3s upgrades in
`base-apps/system-upgrade-controller/runbook.md`, and the New App template in
`templates/new-app/README.md`.

## Platform guides

| doc | status | purpose |
|---|---|---|
| `managed-apps-appset.md` | living | The `managed-apps` ApplicationSet: config schema, adding/removing apps, rollback |
| `idp-migration-guide.md` | record | Onboarding apps via Backstage + the Crossplane XApplication (superseded by the New App template, `templates/new-app/`) |
| `kubernetes-networking-and-service-mesh.md` | living | Networking, mTLS, and service-mesh primer, plus how this cluster uses it |
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
| `plans/k3s-1.36-upgrade-plan.md` | record | The full record of the k3s v1.35.6 → v1.36.4 hop; the reusable procedure is in `base-apps/system-upgrade-controller/runbook.md` |
| `plans/k3s-1.36-api-scan.md` | record | Deprecated-API scan for 1.36 (read by `tests/k3s-upgrade/`; don't move it) |
| `plans/k8s-136-features-implementation-plan.md` | record | Adopting five Kubernetes 1.34–1.36 features after the 1.36 hop |
| `plans/kagent-1-0-feasibility-spikes-implementation-plan.md` | active | Feasibility spikes before a kagent 1.0 upgrade |
| `plans/cve-remediation-automation-implementation-plan.md` | active | Proactive CVE remediation: KEV/EPSS in the weekly scan, and Renovate for this repo |
| `plans/argocd-drift-diagnosis.md` | record | Why each `ignoreDifferences` exists (cited by several Applications) |
| `plans/vault-auto-unseal-implementation-plan.md` | record | Moving Vault from Shamir to AWS KMS auto-unseal |
| `plans/atlantis-infracost-implementation-plan.md` | record | Deploying Atlantis + Infracost |
| `plans/argo-workflows-implementation-plan.md` | record | Deploying Argo Workflows (partly built; living doc `platform/argo-workflows.md`) |
| `plans/ingress-nginx-replacement-handover.md` | record | Replacing ingress-nginx with the Istio Gateway (SPEC T43) |
| `plans/ingress-migration-baseline-2026-07-29.md` | record | Pre-migration ingress baseline (SPEC T50) |
| `plans/newsletter-digest-n8n-prd.md` | record | Newsletter digest requirements (operations: `base-apps/n8n/runbook.md`) |

## Specs & plans: where they go

There are two plan locations, one per tool:

- `plans/` — plans written by this repo's `.claude/skills/`
  (`creating-implementation-plans`, `executing-implementation-plans`).
- `superpowers/specs/` and `superpowers/plans/` — design specs and plans from the
  superpowers brainstorming and writing-plans skills, dated `YYYY-MM-DD-<slug>`.

Specs and plans are kept only while they are active or cited by a living doc
(a `sources:` entry, a manifest comment, or this index). Once the work ships,
fold anything durable (the *why*, gotchas, runbook steps) into the app's
`docs.md`/`runbook.md` and delete the spec; git history keeps the original.
The ones still here and why:

| spec / plan | kept because |
|---|---|
| `superpowers/specs/2026-07-14-adp-remaining-pillars-roadmap.md` | active roadmap (above) |
| `superpowers/specs/2026-07-11-agent-identity-principal-design.md`, `2026-07-13-agent-capability-classes-design.md` | design records for the agent contracts (above; the latter is cited by `.github/workflows/validate.yaml`) |
| `superpowers/specs/2026-05-02-golden-ai-platform-poc-design.md` | prior art the roadmap mines for golden-path design |
| `superpowers/specs/2026-07-17-backstage-catalog-enrichment-design.md` | backlog: phases P2, P3 and P5 are not done |
| `superpowers/specs/2026-08-11-coraza-waf-design.md` | a `sources:` entry of `base-apps/istio-waf/docs.md` |
| `superpowers/specs/2026-08-13-managed-apps-appset-design.md` | the design section of `managed-apps-appset.md` |
| `superpowers/specs/2026-08-17-jupyter-notebooks-design.md` | cited by `base-apps/jupyter/docs.md` for its egress exceptions |
| `superpowers/specs/2026-08-17-image-vulnerability-scanning-design.md` | cited by `base-apps/argo-workflow-tasks/image-scan.yaml` |
| `superpowers/specs/2026-09-29-ansible-host-management-design.md` + plan | active: the operator rollout isn't confirmed yet (`ansible/README.md`) |
| `superpowers/plans/2026-09-27-agent-audit-web-gitops.md` | cited inside Dex's hashed config (`base-apps/dex/configmap.yaml`); remove with the next Dex config change |

`SPEC.md` at the repo root is the current task and invariant ledger; manifests
cite its sections (`§T.n`, `§V.n`).
