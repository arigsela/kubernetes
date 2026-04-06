# Kubernetes GitOps Infrastructure

Production-grade GitOps infrastructure managing containerized applications with automated deployment, secret management, and infrastructure provisioning.

## Architecture Overview

```
                                    ┌─────────────────────────────────────────────────────────────┐
                                    │                    GitOps Pipeline                          │
                                    └─────────────────────────────────────────────────────────────┘
                                                              │
    ┌──────────────┐          ┌──────────────┐          ┌─────▼─────┐          ┌──────────────┐
    │   Developer  │ ──────── │    GitHub    │ ──────── │  ArgoCD   │ ──────── │  Kubernetes  │
    │  Git Push    │          │  Repository  │          │   Sync    │          │   Cluster    │
    └──────────────┘          └──────────────┘          └───────────┘          └──────────────┘
                                                              │
                              ┌────────────────────────────────┼────────────────────────────────┐
                              │                                │                                │
                        ┌─────▼─────┐                    ┌─────▼─────┐                    ┌─────▼─────┐
                        │   Vault   │                    │ Crossplane│                    │   Cert    │
                        │  Secrets  │                    │ AWS/MySQL │                    │  Manager  │
                        └───────────┘                    └───────────┘                    └───────────┘
```

## Skills Demonstrated

### GitOps & Continuous Deployment
- **ArgoCD** with Master App pattern for automatic application discovery
- **Self-healing infrastructure** with `prune: true` and `selfHeal: true`
- **Declarative configuration** - Git as single source of truth
- **Zero-touch deployments** - push to main, automatically deployed

### Infrastructure as Code

| Layer | Technology | Implementation |
|-------|------------|----------------|
| **Cluster Bootstrap** | Terraform | ArgoCD installation, ApplicationSets, IAM roles |
| **Cloud Resources** | Crossplane | S3 buckets, IAM policies, MySQL users/databases |
| **State Management** | S3 + DynamoDB | Remote state with locking and versioning |

### Secret Management Architecture

```
┌─────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│   HashiCorp │     │  External Secrets   │     │   Kubernetes    │
│    Vault    │ ──▶ │     Operator        │ ──▶ │    Secrets      │
│  (KV v2)    │     │   (hourly sync)     │     │  (per-namespace)│
└─────────────┘     └─────────────────────┘     └─────────────────┘
       │
       └── Kubernetes Auth (no tokens in Git)
```

- **Per-namespace SecretStores** with role-based Vault access
- **Automatic rotation** via External Secrets Operator
- **Zero secrets in Git** - all credentials in Vault

### Cloud Infrastructure (AWS)

| Service | Purpose | Configuration |
|---------|---------|---------------|
| **ECR** | Container registry | Automated credential sync via CronJob + Kyverno |
| **RDS MySQL** | Production database | Crossplane-managed users/grants |
| **S3** | Terraform state, Loki logs, backups | Lifecycle policies, encryption |
| **IAM** | Service accounts | Least-privilege policies per service |
| **Route 53** | DNS management | Cert-manager DNS-01 challenges |

### Service Mesh (Istio Ambient)

```
┌─────────────────────────────────────────────────────────────┐
│  Istio Ambient Mesh Architecture                            │
├─────────────────────────────────────────────────────────────┤
│  ztunnel (L4)     - Per-node DaemonSet, mTLS, TCP metrics   │
│  Waypoint (L7)    - Per-namespace, HTTP metrics & policies  │
│  istiod           - Control plane (ambient profile)         │
│  istio-cni        - Traffic interception for K3s            │
└─────────────────────────────────────────────────────────────┘
```

- **Sidecar-less architecture** - No per-pod proxies, reduced resource overhead
- **Automatic mTLS** - Zero-config encryption between mesh workloads
- **L4/L7 metrics** - TCP and HTTP telemetry via Prometheus/Grafana
- **Waypoint proxies** - Optional L7 processing for HTTP features

### Terraform CI/CD (Atlantis + Infracost)

```
┌─────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│  Developer   │     │    GitHub PR        │     │    Atlantis     │
│  Opens PR    │ ──▶ │  (terraform/** )    │ ──▶ │  Plan + Comment │
└─────────────┘     └─────────────────────┘     └─────────────────┘
                              │                          │
                     ┌────────▼────────┐        ┌────────▼────────┐
                     │   Infracost     │        │  Apply on       │
                     │  Cost Estimate  │        │  PR Approval    │
                     └─────────────────┘        └─────────────────┘
```

- **Atlantis** - PR-based `terraform plan/apply` automation
  - Auto-plans when `terraform/**` files change
  - Apply gated behind PR approval + mergeable status
  - Project locking prevents concurrent state modifications
  - Runs on infrastructure nodes with 5Gi persistent storage
- **Infracost** - Cost estimation on every PR
  - GitHub Actions workflow scans all terraform roots
  - Posts cost diff as PR comment (free Cloud Pricing API)
  - Infracost CLI baked into Atlantis image (`infracost-atlantis`)
- **Terraform Validate** - CI checks on every PR
  - `terraform fmt` formatting check
  - `terraform validate` syntax validation
  - TFLint linting rules (provider version constraints)
  - tfsec security scanning

### Observability Stack

- **Loki** - Log aggregation with S3 backend (30-day retention)
- **Grafana Alloy** - Log collection agent
- **Prometheus** - Metrics collection for Istio and applications
- **Grafana Dashboards** - Istio Ambient Mesh L4/L7 visualization
- **Structured logging** - JSON format for queryability

### ECR Image Pull Automation (Kyverno)

```
┌─────────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│   New Namespace  │     │      Kyverno        │     │   ECR CronJob   │
│   Created       │ ──▶ │  generate-ecr-secret │     │  (hourly token  │
└─────────────────┘     │  (instant clone)     │     │   refresh)      │
                        └─────────────────────┘     └─────────────────┘
                                                            │
┌─────────────────┐     ┌─────────────────────┐             │
│   Pod with ECR  │     │      Kyverno        │     ┌───────▼─────────┐
│   Image Created │ ──▶ │ inject-ecr-pull-secret│     │  All Namespaces │
└─────────────────┘     │ (auto imagePullSecrets)│    │  Secrets Fresh  │
                        └─────────────────────┘     └─────────────────┘
```

- **Zero-touch ECR access** - No manual `imagePullSecrets` or namespace configuration
- **Instant secret provisioning** - Kyverno clones ECR credentials on namespace creation
- **Automatic injection** - Pods referencing ECR images get `imagePullSecrets` at admission
- **Dynamic namespace discovery** - CronJob refreshes tokens across all namespaces hourly

### Security Implementation

- **TLS Everywhere** - Cert-manager with LetsEncrypt production certificates
- **RBAC** - Namespace-isolated permissions
- **Network Policies** - Service mesh ingress control
- **Least Privilege** - Scoped IAM policies per Crossplane resource

## Repository Structure

```
├── base-apps/                      # ArgoCD Applications (auto-discovered)
│   ├── {app}.yaml                  # ArgoCD Application manifest
│   └── {app}/                      # Kubernetes resources
│       ├── deployments.yaml
│       ├── services.yaml
│       ├── nginx-ingress.yaml
│       ├── secret-store.yaml       # Vault SecretStore config
│       ├── external-secrets.yaml   # Secret mappings
│       └── crossplane_resources.yaml
│
├── terraform/
│   ├── modules/
│   │   ├── argocd/                 # ArgoCD Helm deployment
│   │   ├── application-sets/       # Master app pattern
│   │   ├── kube-secrets/           # Bootstrap secrets
│   │   ├── networking/             # VPC, subnets, IGW (reusable)
│   │   └── alb/                    # ALB, target group (reusable)
│   └── roots/
│       ├── asela-cluster/          # Main cluster configuration
│       │   ├── providers.tf        # AWS, Kubernetes, Helm
│       │   ├── argocd.tf           # ArgoCD installation
│       │   ├── atlantis-iam.tf     # Atlantis IAM user + policy
│       │   └── iam.tf              # Service account roles
│       └── swarm-cluster/          # Docker Swarm test cluster (us-east-1)
│
├── atlantis.yaml                   # Atlantis project configuration
│
├── .github/workflows/
│   ├── terraform-validate.yaml     # fmt, validate, tflint, tfsec
│   └── infracost.yaml              # Cost estimation on PRs
│
└── docs/                           # Architecture documentation
```

## Deployed Applications

### Production Applications
| Application | Stack | Features |
|-------------|-------|----------|
| **chores-tracker-backend** | FastAPI, MySQL RDS | REST API, JWT auth, health checks |
| **chores-tracker-frontend** | Node.js | HTMX frontend, CORS configured |
| **n8n** | Workflow automation | Secure ingress, PostgreSQL backend |

### Infrastructure Components
| Component | Purpose |
|-----------|---------|
| **vault** | Secret management with K8s auth |
| **external-secrets** | Vault → K8s secret sync |
| **crossplane-aws-provider** | S3, IAM provisioning |
| **crossplane-mysql-provider** | Database user/grant management |
| **cert-manager** | Automated TLS certificates |
| **nginx-ingress** | HTTP/HTTPS ingress controller |
| **logging (Loki)** | Centralized log aggregation |
| **mysql-rds-backup** | Automated daily S3 backups |
| **atlantis** | PR-based Terraform plan/apply with Infracost |

### Service Mesh Components
| Component | Purpose |
|-----------|---------|
| **istio-base** | Istio CRDs and cluster resources |
| **istiod** | Control plane (ambient profile) |
| **istio-cni** | CNI plugin for K3s traffic interception |
| **ztunnel** | L4 proxy DaemonSet (mTLS, TCP metrics) |
| **waypoint** | L7 proxy for HTTP metrics and policies |
| **istio-gateway-api** | Kubernetes Gateway API CRDs |

## Key Patterns Implemented

### 1. Master App Pattern
Single ApplicationSet discovers and deploys all applications:
```yaml
syncPolicy:
  automated:
    prune: true      # Remove orphaned resources
    selfHeal: true   # Revert manual drift
```

### 2. External Secrets Pattern
Vault secrets synced to Kubernetes without exposing credentials:
```yaml
apiVersion: external-secrets.io/v1beta1
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
apiVersion: mysql.crossplane.io/v1alpha1
kind: User
metadata:
  name: app-user
spec:
  forProvider:
    resourceOptions:
      grantOption: true
```

### 4. Ingress Priority Routing
Backend APIs (priority 100) take precedence over frontend (priority 50):
```yaml
nginx.ingress.kubernetes.io/priority: "100"
path: /api/(.*)
```

## Technology Stack

| Category | Technologies |
|----------|--------------|
| **GitOps** | ArgoCD, GitHub |
| **IaC** | Terraform, Crossplane, Atlantis |
| **Cost Management** | Infracost (free Cloud Pricing API) |
| **Containers** | Kubernetes (K3s), Docker, AWS ECR |
| **Service Mesh** | Istio Ambient (ztunnel, waypoint, istiod) |
| **Secrets** | HashiCorp Vault, External Secrets Operator |
| **Certificates** | Cert-Manager, LetsEncrypt |
| **Observability** | Prometheus, Loki, Grafana, Alloy |
| **Database** | MySQL (RDS), PostgreSQL |
| **Cloud** | AWS (S3, IAM, ECR, RDS, Route 53) |
| **Ingress** | NGINX Ingress Controller |

## Deployment Workflow

```bash
# All deployments are Git-driven
git add base-apps/my-app/
git commit -m "feat: deploy my-app"
git push origin main

# ArgoCD automatically:
# 1. Detects change in repository
# 2. Compares desired vs actual state
# 3. Syncs resources to cluster
# 4. Reports status back to Git
```

---

**Author:** Ari Sela
**Repository:** [github.com/arigsela/kubernetes](https://github.com/arigsela/kubernetes)
