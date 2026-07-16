# Infrastructure Atlas

> **For agents:** Start here. Traverse: this atlas → a directory `_INDEX.md` → an app's `docs.md`/`runbook.md` → the `sources:` files listed in that doc. This atlas is a **navigation/summary layer**; the `sources:` files are authoritative. If a summary here looks wrong, go read the source.

## 1. System context
- Kubernetes API: `https://192.168.0.100:6443`
- GitOps: Argo CD watches this repo; the app-of-apps "master-app" (defined in `terraform/modules/application-sets/`, watching `base-apps/`) creates an Application per `.yaml` in `base-apps/`.
- Secrets: HashiCorp Vault at `vault.vault.svc.cluster.local:8200` (KV v2, path `k8s-secrets`), surfaced via External Secrets Operator. Human login to Vault is via **Dex OIDC** (`dex.arigsela.com`) fronting GitHub; the UI is at `vault.arigsela.com`.
- S3 buckets: `asela-terraform-states` (Terraform state), `asela-chores-loki-logs-*` (Loki), `asela-agent-audit-record` (the durable agent action record — write-only IAM, provisioned via Crossplane at `base-apps/agent-audit-aws-infrastructure/`).

## 2. Platform topology
- **Argo CD** (`base-apps/argo-cd/`) — control plane; master-app pattern.
- **base-apps/** — one Application per app; each app directory holds its manifests and (in-scope) its agent-docs contract.
- **terraform/** — `roots/asela-cluster` is the active root; reusable `modules/`.
- **Crossplane** — declarative cloud resources.

## 3. GitOps data flow
`git commit` → Argo CD detects drift → syncs manifests to the cluster (`prune: true`, `selfHeal: true`). Secrets resolve at runtime: `SecretStore` + `ExternalSecret` → Vault.

## 4. Cross-cutting concerns
- **Secrets:** Vault + External Secrets Operator; per-namespace `SecretStore`. Agent credentials are **path-scoped per consumer** (dedicated ESO ServiceAccount + SecretStore + Vault role + key) — never the broad `vault-backend` store or a shared key. See the agent-identity contract.
- **Human authentication:** Dex OIDC (`base-apps/dex/`, `dex.arigsela.com`) fronting GitHub, used for Vault UI login. See `docs/superpowers/plans/` for the Dex/OIDC phase.
- **TLS/certs:** cert-manager (`base-apps/cert-manager/`) issuing from Let's Encrypt — `letsencrypt-prod`/`letsencrypt-staging` use HTTP-01 via nginx; a separate `letsencrypt-route53` issuer uses Route 53 DNS-01.
- **Ingress/mesh:** nginx-ingress and Istio ambient mesh.
- **Agent guardrails (L03):** kagent agents run under two admission-enforced contracts — **identity** (`ClusterPolicy/agent-identity`, scoped credentials) and **capability** (`ClusterPolicy/agent-capability`, per-agent `read`/`write`/`admin` classes, a fail-closed tool taxonomy, HITL approval on mutating tools, and no delegation escalation). Both are Kyverno `Enforce`, mirrored by CI validators (`scripts/validate-agent-*.py`). See `docs/adp-engineering-deep-dive.md`.
- **Observability:** Loki (logs, S3-backed) + Grafana + Coroot (eBPF traces/metrics). Plus the **agent action record** — kagent's tool-call history (who called what, with what args, approved or not) surfaced read-only, redacted, exported daily to S3, and checked by a scheduled job (`base-apps/postgresql/agent-audit-cronjob.yaml`, `scripts/agent-audit.py`). **Alerting:** Falco → Loki and Grafana alert rules → an n8n webhook. See `docs/adp-resources-and-observability.md` for where to look.
- **Agent evaluation:** a golden-answer corpus (`tests/eval-corpus/`) + scorer (`scripts/score-eval.py`) grade agent answers, including that they refuse to leak secrets.

## 5. Known gaps
| Gap | Recommendation | Source |
|---|---|---|
| Not every app carries the full agent-docs contract | Backfill stubs in `base-apps/_INDEX.md` under CI gating | `base-apps/_INDEX.md`, `scripts/agent-docs-scope.txt` |
| L02 golden paths absent | A declarative path registry humans + agents can invoke | `docs/superpowers/specs/2026-07-14-adp-remaining-pillars-roadmap.md` (P2–P4) |
| Evaluation is a tool, not yet a gate | Run the scorer on every agent/prompt change (E3) | same roadmap (E3) |
| kagent `Execution` pillar unassessed | Audit the runtime + openshell sandbox boundary | same roadmap |

## 6. Source registry
| Domain | Authoritative location |
|---|---|
| App manifests | `base-apps/<app>/` |
| Argo CD Applications | `base-apps/<app>.yaml`; app-of-apps master-app in `terraform/modules/application-sets/` |
| Infrastructure | `terraform/roots/asela-cluster/`, `terraform/modules/` |
| Secret wiring | per-app `secret-store.yaml` / `external-secret*.yaml` |
| Agent guardrails | `base-apps/kyverno-policies/agent-identity.yaml`, `agent-capability.yaml` (generated from `agent-capability-taxonomy.yaml`), `templates/agent-identity/README.md`, `scripts/validate-agent-*.py` |
| Agent audit & eval | `scripts/agent-audit.py`, `base-apps/postgresql/agent-audit-cronjob.yaml`, `tests/eval-corpus/`, `scripts/score-eval.py` |
| Doc contract & index | `templates/agent-docs/README.md`, `base-apps/_INDEX.md` |

## 7. App index
See `base-apps/_INDEX.md` for the per-app index, `terraform/_INDEX.md` and `docs/_INDEX.md` for those trees.
