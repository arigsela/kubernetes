# Kubernetes GitOps Infrastructure

GitOps infrastructure for a homelab k3s cluster: automated deployment, secret management, infrastructure provisioning, and guardrails for AI agents.

> Looking for how something works? Start at [`index.md`](index.md), the navigation front door. Every app has a row in [`base-apps/index.md`](base-apps/index.md), and most link to a `docs.md` and `runbook.md`. Build and test commands are in [`AGENTS.md`](AGENTS.md).

## Architecture Overview

```
                                    ┌─────────────────────────────────────────────────────────────┐
                                    │                    GitOps Pipeline                          │
                                    └─────────────────────────────────────────────────────────────┘
                                                              │
    ┌──────────────┐          ┌──────────────┐          ┌─────▼─────┐          ┌──────────────┐
    │   Developer  │ ──────── │    GitHub    │ ──────── │  ArgoCD   │ ──────── │  Kubernetes  │
    │   Pull Req.  │          │  Repository  │          │   Sync    │          │ Cluster (k3s)│
    └──────────────┘          └──────────────┘          └───────────┘          └──────────────┘
                                                              │
                              ┌────────────────────────────────┼────────────────────────────────┐
                              │                                │                                │
                        ┌─────▼─────┐                    ┌─────▼─────┐                    ┌─────▼─────┐
                        │   Vault   │                    │ Crossplane│                    │   Cert    │
                        │  Secrets  │                    │ AWS S3/IAM│                    │  Manager  │
                        └───────────┘                    └───────────┘                    └───────────┘
```

## Skills Demonstrated

### GitOps & Continuous Deployment
- **Argo CD** with a self-managing master app (`base-apps/master-app.yaml`) that discovers every top-level `base-apps/*.yaml`
- **ApplicationSet** (`managed-apps`) generating 12 simpler apps from small config files in `appsets/managed-apps/`
- **Self-healing infrastructure** with `prune: true` and `selfHeal: true`
- **Declarative configuration** - Git as single source of truth; every change lands through a triaged PR

### Infrastructure as Code

| Layer | Technology | Implementation |
|-------|------------|----------------|
| **Hosts** | Ansible | Hypervisor and k3s VM baseline, patching with drained reboots, API server authn config |
| **Cluster Bootstrap** | Terraform (OpenTofu via Atlantis) | Argo CD installation, IAM users/policies, Vault KMS auto-unseal key |
| **Cloud Resources** | Crossplane | Per-app S3 buckets, IAM users, policies and access keys |
| **State Management** | S3 | Remote Terraform state in `asela-terraform-states` |

### Secret Management Architecture

```
┌─────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│   HashiCorp │     │  External Secrets   │     │   Kubernetes    │
│    Vault    │ ──▶ │     Operator        │ ──▶ │    Secrets      │
│  (KV v2)    │     │  (periodic sync)    │     │  (per-namespace)│
└─────────────┘     └─────────────────────┘     └─────────────────┘
       │
       └── Kubernetes Auth (no tokens in Git), AWS KMS auto-unseal
```

- **Per-namespace SecretStores** with role-based Vault access
- **Per-consumer credentials for agents and databases** - a dedicated ServiceAccount, SecretStore and Vault role each ([contract](templates/agent-identity/README.md))
- **Zero secrets in Git** - all credentials in Vault; human Vault login via Dex OIDC

### Cloud Infrastructure (AWS)

| Service | Purpose | Configuration |
|---------|---------|---------------|
| **ECR** | Container registry | Pull secret synced by a CronJob, injected by a native MutatingAdmissionPolicy |
| **S3** | Terraform state, Loki logs, Postgres backups, agent action record | Lifecycle policies, encryption |
| **KMS** | Vault auto-unseal | Terraform-managed key and credentials |
| **IAM** | Service accounts | Least-privilege policies per service |
| **Route 53** | DNS management | Cert-manager DNS-01 challenges; A records kept current by `wan-ip-monitor` |

### Ingress & Service Mesh (Istio)

```
┌─────────────────────────────────────────────────────────────┐
│  Istio (1.30)                                               │
├─────────────────────────────────────────────────────────────┤
│  Gateway API    - One Gateway, per-host HTTPRoutes + certs  │
│  AuthZ policy   - IP allow-list per host                    │
│  Coraza WAF     - Wasm plugin on the gateway (OWASP CRS)    │
│  ambient        - istiod, istio-cni, ztunnel installed      │
└─────────────────────────────────────────────────────────────┘
```

- **Gateway API ingress** - every public host is an `HTTPRoute` on the shared `main` Gateway with its own `Certificate`
- **Default-deny by source IP** - an `AuthorizationPolicy` allow-lists each host; only a few paths are public by design
- **Web application firewall** - Coraza runs inside the gateway's Envoy
- **Ambient dataplane** - installed and ready; no namespace is enrolled today

### Terraform CI/CD (Atlantis + Infracost)

```
┌─────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│  Developer   │     │    GitHub PR        │     │    Atlantis     │
│  Opens PR    │ ──▶ │  (terraform/** )    │ ──▶ │  Plan + Comment │
└─────────────┘     └─────────────────────┘     └─────────────────┘
                              │                          │
                     ┌────────▼────────┐        ┌────────▼────────┐
                     │   Infracost     │        │ Gated apply on  │
                     │  Cost Estimate  │        │ the open PR     │
                     └─────────────────┘        └─────────────────┘
```

- **Atlantis** - PR-based `plan/apply` automation (OpenTofu)
  - Auto-plans when `.tf` files change
  - Apply runs while the PR is still open, then the PR merges; nothing applies on merge
  - The "Terraform Apply (gated)" Action posts `atlantis apply` behind a required-reviewer GitHub Environment
  - Project locking prevents concurrent state modifications
- **Infracost** - Cost estimation on every PR
  - Posts cost diff as PR comment (free Cloud Pricing API)
  - Infracost CLI baked into Atlantis image (`infracost-atlantis`)
- **Terraform Validate** - CI checks on every PR
  - `terraform fmt` formatting check
  - `terraform validate` syntax validation
  - TFLint linting rules (provider version constraints)
  - tfsec security scanning

### PR Triage (AI review labels)

```
┌──────────────┐     ┌──────────────────────┐     ┌──────────────────────┐
│  PR opened   │ ──▶ │ review-policy.yaml   │ ──▶ │ Jev (TypeSafe)       │
│  or pushed   │     │ rules (base commit)  │     │ risk questions       │
└──────────────┘     └──────────────────────┘     └──────────────────────┘
                                                             │ unsure?
                     ┌──────────────────────┐     ┌──────────▼───────────┐
                     │ review:skip|skim|read│ ◀── │ Claude Haiku decides │
                     │ + sticky PR comment  │     │ + "what to look at"  │
                     └──────────────────────┘     └──────────────────────┘
```

- **PR Triage workflow** (`.github/workflows/pr-triage.yaml`) runs the [`pr-triage`](https://github.com/arigsela/claude-agents/tree/main/pr-triage) action, pinned by SHA, on every non-draft PR from this repo to `main`
  - Adds exactly one label, `review:skip`, `review:skim` or `review:read`, plus one sticky comment with the reason and what to look at
  - Labels only: it never blocks, approves or merges
  - If Jev or Claude is unavailable, it falls back to the rules, then to `review:read`
- **Review policy** (`.github/review-policy.yaml`) - read at the PR's *base* commit, so a PR can't loosen its own triage
  - Always read: auth/RBAC/admission apps, `terraform/**`, `ansible/**`, `appsets/**`, `.github/**`, `atlantis.yaml`, RBAC/CRD/admission/network-policy/PVC kinds, deleted Applications, k3s version changes
  - Always skip: docs-only changes and image-tag bumps (a chart `targetRevision` bump never qualifies)
  - Thresholds and 26 "hot components" come from calibration on the last 200 PRs: none of the 43 PRs that later needed a fix was labelled skip ([report](https://github.com/arigsela/claude-agents/blob/main/pr-triage/calibration/kubernetes-2026-09.md))
- **Explain and learn** - the [`pr-explainer`](https://github.com/arigsela/claude-agents/tree/main/skills/pr-explainer) and [`review-retro`](https://github.com/arigsela/claude-agents/tree/main/skills/review-retro) Claude Code skills: `/pr-explainer <n>` turns a GitOps PR into a rendered-manifest resource map with must-read hunks; `/review-retro <n>` compares a `/code-review` session with the fixes that followed

### Agent Guardrails (kagent)

- **Identity contract** - every agent's credentials are path-scoped to it; enforced at admission by native ValidatingAdmissionPolicies and mirrored in CI
- **Capability contract** - per-agent `read`/`write`/`admin` classes, a fail-closed tool taxonomy, human approval on mutating tools, and no escalation through delegation
- **Action record** - every agent tool call, redacted, exported daily to S3 and browsable at `agent-audit.arigsela.com`
- **Evaluation** - a golden-answer corpus and scorer (`tests/eval-corpus/`, `scripts/score-eval.py`)
- Details: [`docs/adp-engineering-deep-dive.md`](docs/adp-engineering-deep-dive.md)

### Observability Stack

- **Loki** - Log aggregation with S3 backend (30-day retention)
- **Grafana Alloy** - Log collection agent
- **Prometheus** - Metrics for the cluster, nodes, Istio and applications
- **Grafana** - Dashboards and alert rules, with alerts delivered to an n8n webhook; GitHub OAuth login
- **Coroot** - eBPF-based service map, traces and metrics
- **Falco** - Runtime security detection, alerts routed through Loki

### ECR Image Pull Automation

```
Every 15 minutes  ──▶  ecr-credentials-sync CronJob writes a fresh ecr-registry
                       secret into every non-system namespace (new ones included)
Pod with ECR image ──▶ API server (native MutatingAdmissionPolicy
                       inject-ecr-pull-secret) adds imagePullSecrets
```

- **Zero-touch ECR access** - No manual `imagePullSecrets` or namespace configuration
- **Secret provisioning** - a new namespace gets the ECR secret within 15 minutes (or at once via `kubectl create job --from=cronjob/ecr-credentials-sync`)
- **Automatic injection** - Pods referencing ECR images get `imagePullSecrets` at admission
- **Dynamic namespace discovery** - the CronJob refreshes tokens across all namespaces every 15 minutes

### Security Implementation

- **TLS Everywhere** - Cert-manager with Let's Encrypt certificates via Route 53 DNS-01
- **Single sign-on** - Dex OIDC (fronting GitHub) for Vault, Argo CD, kubectl and agent-audit-web
- **Admission control** - native ValidatingAdmissionPolicies / MutatingAdmissionPolicies, no webhook in the path
- **Edge protection** - per-host IP allow-lists and the Coraza WAF on the Istio gateway
- **Least Privilege** - Scoped IAM policies per Crossplane resource; scoped Vault roles per agent

## Repository Structure

```
├── base-apps/                      # Argo CD Applications (auto-discovered)
│   ├── master-app.yaml             # Root app-of-apps (manages itself)
│   ├── managed-apps.yaml           # ApplicationSet over appsets/managed-apps/
│   ├── {app}.yaml                  # Argo CD Application manifest
│   ├── index.md                    # Generated per-app index
│   └── {app}/                      # Kubernetes resources
│       ├── deployments.yaml
│       ├── services.yaml
│       ├── httproute.yaml          # Route on the Istio Gateway
│       ├── certificate.yaml        # TLS certificate for the host
│       ├── secret-store.yaml       # Vault SecretStore config
│       ├── external-secrets.yaml   # Secret mappings
│       ├── docs.md, runbook.md     # Agent-docs contract
│       └── catalog-info.yaml       # Backstage entity
│
├── appsets/managed-apps/           # One config per ApplicationSet-generated app
│
├── terraform/
│   ├── modules/argocd/             # Argo CD Helm deployment
│   └── roots/asela-cluster/        # The only active root
│       ├── providers.tf            # AWS, Kubernetes, Helm
│       ├── argocd.tf               # Argo CD installation
│       ├── atlantis-iam.tf         # Atlantis IAM user + policy
│       ├── vault-kms.tf            # Vault auto-unseal key
│       └── iam.tf                  # Service account roles
│
├── ansible/                        # Hypervisor and k3s VM host layer
├── templates/                      # Doc and identity contracts, New App template
├── catalog/                        # Backstage platform and API entities
├── scripts/                        # Generators, validators, backup/restore
├── tests/                          # pytest suites (see AGENTS.md)
├── atlantis.yaml                   # Atlantis project configuration
│
├── .github/
│   ├── review-policy.yaml          # PR triage risk policy
│   └── workflows/
│       ├── validate.yaml           # Lint, schema, validators, tests
│       ├── okf-autosync.yaml       # Regenerates base-apps/index.md on PRs
│       ├── pr-triage.yaml          # review:skip|skim|read labels
│       ├── terraform-validate.yaml # fmt, validate, tflint, tfsec
│       ├── terraform-apply.yaml    # Gated `atlantis apply` trigger
│       └── infracost.yaml          # Cost estimation on PRs
│
└── docs/                           # Guides, troubleshooting, specs and plans
```

## Deployed Applications

The full list, with a description and namespace per app, is generated in [`base-apps/index.md`](base-apps/index.md). Highlights:

| Area | Components |
|------|------------|
| **Platform** | Argo CD, Vault, External Secrets, cert-manager, Crossplane, CloudNativePG, system-upgrade-controller |
| **Edge** | Istio Gateway, Coraza WAF, wan-ip-monitor |
| **Identity & policy** | Dex, native admission policies, Kyverno (reports only) |
| **Developer platform** | Backstage, Atlantis, Argo Workflows, Jupyter |
| **AI agents** | kagent, Ollama (embeddings), oncall-agent, agent-audit-web |
| **Observability** | Loki, Grafana, Prometheus, Alloy, Coroot, Falco |
| **Apps** | n8n, Donetick, Homepage, Weather Kitchen |

## Key Patterns Implemented

### 1. Master App Pattern
An Application that creates an Application for every top-level `base-apps/*.yaml`, including its own:
```yaml
syncPolicy:
  automated:
    prune: true      # Remove orphaned resources
    selfHeal: true   # Revert manual drift
```

### 2. External Secrets Pattern
Vault secrets synced to Kubernetes without exposing credentials:
```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
spec:
  refreshInterval: "1h"
  secretStoreRef:
    name: vault-backend
  data:
  - secretKey: DATABASE_URL
    remoteRef:
      key: app-name
      property: database-url
```

### 3. Crossplane Resource Provisioning
Declarative cloud resources managed via Kubernetes:
```yaml
apiVersion: s3.aws.upbound.io/v1beta1
kind: Bucket
metadata:
  name: asela-jupyter-scratch
spec:
  forProvider:
    region: us-east-1
```

### 4. Gateway API Routing
Each host is an `HTTPRoute` on the shared Gateway; more specific matches win:
```yaml
parentRefs:
  - name: main
    namespace: istio-ingress
    sectionName: https-weather-kitchen
rules:
  - matches:
      - path: {type: PathPrefix, value: /api}
```

## Technology Stack

| Category | Technologies |
|----------|--------------|
| **GitOps** | Argo CD, GitHub |
| **IaC** | Terraform/OpenTofu, Atlantis, Crossplane, Ansible |
| **Cost Management** | Infracost (free Cloud Pricing API) |
| **Containers** | Kubernetes (k3s), Docker, AWS ECR |
| **Ingress & Mesh** | Istio Gateway API, Coraza WAF, Istio ambient |
| **Secrets** | HashiCorp Vault, External Secrets Operator |
| **Identity** | Dex (OIDC), native admission policies |
| **Certificates** | Cert-Manager, Let's Encrypt |
| **Observability** | Prometheus, Loki, Grafana, Alloy, Coroot, Falco |
| **Database** | PostgreSQL (CloudNativePG) |
| **Cloud** | AWS (S3, IAM, ECR, KMS, Route 53) |

## Deployment Workflow

```bash
# All deployments are Git-driven, through a pull request
git checkout -b feature/deploy-my-app
git add base-apps/my-app.yaml base-apps/my-app/
git commit -m "feat(my-app): deploy my-app"
git push -u origin feature/deploy-my-app
gh pr create

# After merge, Argo CD automatically:
# 1. Detects change in repository
# 2. Compares desired vs actual state
# 3. Syncs resources to cluster
# 4. Reports status in the Argo CD UI
```

---

**Author:** Ari Sela
**Repository:** [github.com/arigsela/kubernetes](https://github.com/arigsela/kubernetes)
