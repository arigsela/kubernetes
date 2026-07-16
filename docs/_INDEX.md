# docs Index

Top-level design docs, guides, and plans. Design specs and implementation plans
live under `superpowers/specs/` and `superpowers/plans/` (see the bottom of this
file).

## Agentic Development Platform (ADP)

The agent-platform hardening arc — Identity, Security/Capability, Observability,
and Evaluation.

| doc | purpose |
|---|---|
| `adp-engineering-deep-dive.md` | How the agent-identity & agent-capability admission policies and the corpus/scorer eval harness work, in engineering detail |
| `adp-resources-and-observability.md` | Review index: AWS resources created, dashboards to observe it in, and links to every tool used |
| `superpowers/specs/2026-07-14-adp-remaining-pillars-roadmap.md` | Roadmap for the remaining pillars + the O0 observability spike result |
| `superpowers/specs/2026-07-11-agent-identity-principal-design.md` | Identity: the "agent-principal" pattern (scoped credentials) |
| `superpowers/specs/2026-07-13-agent-capability-classes-design.md` | Security: per-agent capability classes + tool taxonomy |
| `agent-ready-docs-review.md` | Research review: docs-as-code for agents |

The enforced contracts themselves: `templates/agent-identity/README.md`, the
Kyverno policies in `base-apps/kyverno-policies/agent-*.yaml`, and the validators
in `scripts/validate-agent-*.py`.

## Platform guides & plans

| doc | purpose |
|---|---|
| `idp-migration-guide.md` | Onboarding existing apps via Backstage + Crossplane |
| `devops-maturity-implementation-plan.md` | DevOps maturity assessment + roadmap |
| `kubernetes-networking-and-service-mesh.md` | Networking, mTLS, and service-mesh notes |
| `istio-ambient-mesh-implementation-plan.md` | Istio ambient mesh deployment plan |
| `vault-auto-unseal-plan.md` | Vault KMS auto-unseal plan |
| `openclaw-setup-guide.md` | OpenClaw app setup |
| `feed-aggregator-refactor-plan.md` | Feed-aggregator refactor plan |

## Specs & plans

- `superpowers/specs/` — design specs (one per feature/increment).
- `superpowers/plans/` — implementation plans.

Both are dated `YYYY-MM-DD-<slug>`; browse the directories for the full set.
