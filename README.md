# Kubernetes GitOps Infrastructure Repository

## Table of Contents
1. [Project Overview](#project-overview)
2. [Architecture](#architecture)
3. [Technology Stack](#technology-stack)
4. [Infrastructure Components](#infrastructure-components)
5. [Application Catalog](#application-catalog)
   - [Chores Tracker Application](#chores-tracker-application)
6. [Development Workflows](#development-workflows)
7. [Operations Guide](#operations-guide)
8. [Security and Compliance](#security-and-compliance)

## Project Overview

This repository implements a production-grade GitOps infrastructure for Kubernetes, providing automated deployment and lifecycle management for containerized applications. It follows cloud-native best practices with a focus on declarative configuration, automated reconciliation, and progressive delivery.

### Key Features
- **GitOps-driven deployments** via ArgoCD
- **Progressive delivery** with Kargo (test → uat → prod)
- **Infrastructure as Code** using Terraform and Crossplane
- **Automated secret management** with Vault and External Secrets
- **Self-healing infrastructure** with automated drift detection
- **Multi-environment support** with branch-based promotion

### Repository Information
- **Repository**: https://github.com/arigsela/kubernetes
- **Current Branch**: kargo-deployment
- **Target Cluster**: https://192.168.0.100:6443
- **License**: Apache 2.0

## Architecture

### GitOps Workflow

```
Developer → Git Push → GitHub Repository
                              ↓
                         ArgoCD Sync
                              ↓
                    Kubernetes Cluster
                              ↓
                    Application Running
```

### Directory Structure

```
kubernetes/
├── base-apps/                  # ArgoCD Applications and manifests
│   ├── master-app.yaml        # ApplicationSet watching this directory
│   ├── chores-tracker/        # Custom application
│   ├── vault/                 # Secrets management
│   ├── crossplane/            # Infrastructure provisioning
│   ├── external-secrets/      # Secret synchronization
│   └── monitoring/            # Observability stack
├── terraform/                  # Infrastructure as Code
│   ├── modules/               # Reusable Terraform modules
│   │   ├── argocd/           # ArgoCD deployment
│   │   ├── application-sets/ # ArgoCD ApplicationSets
│   │   └── kube-secrets/     # Kubernetes secrets
│   └── roots/                 # Environment configurations
│       └── asela-cluster/    # Main cluster config
└── kargo/                     # Progressive delivery configs
    ├── project.yaml          # Kargo project definition
    ├── warehouse.yaml        # Image monitoring
    └── stages/               # Environment stages
```

### Deployment Patterns

#### Master App Pattern
The `master-app` ApplicationSet automatically discovers and deploys any `.yaml` file in `/base-apps/`:

```yaml
syncPolicy:
  automated:
    prune: true      # Remove resources not in Git
    selfHeal: true   # Revert manual changes
```

#### Progressive Delivery Flow
```
Warehouse (monitors images) → Test Stage → UAT Stage → Prod Stage
         ↓                        ↓            ↓           ↓
    New Image              stage/test    stage/uat   stage/prod
```

## Technology Stack

### Core Technologies

| Technology | Purpose | Version/Details |
|------------|---------|-----------------|
| **ArgoCD** | GitOps continuous deployment | v7.8.13 (Helm chart) |
| **Kargo** | Progressive delivery orchestration | Latest |
| **Terraform** | Infrastructure provisioning | AWS ~> 5.78, Kubernetes 2.34.0 |
| **Crossplane** | Cloud resource provisioning | AWS Provider (IAM, S3), MySQL Provider |
| **Vault** | Secret management | Latest with Kubernetes auth |
| **External Secrets** | Secret synchronization | Vault integration with hourly refresh |
| **Nginx Ingress** | Ingress controller | v4.11.2 with hostNetwork mode |
| **Cert Manager** | TLS certificate management | LetsEncrypt production issuer |
| **Loki** | Log aggregation | v3.2.1 with S3 backend (30-day retention) |

### Monitoring Stack
- **ELK Stack**: Centralized logging
- **Monitoring**: Metrics and alerting (details in `/base-apps/monitoring/`)

### Container Registry
- **AWS ECR**: Private container images (us-east-2)
- **ECR Auth**: Automated credential sync via CronJob to chores-tracker, chores-tracker-frontend, and mysql namespaces
- **Public Registries**: Docker Hub, Quay.io for OSS components

## Infrastructure Components

### Terraform-Managed Resources

1. **ArgoCD Installation**
   - Deployed via Helm with CRDs
   - Configured for insecure mode (HTTP)
   - Master ApplicationSet for auto-discovery

2. **AWS Resources**
   - S3 Backend: `asela-terraform-states` bucket
   - Secrets Manager: Application secrets
   - ECR: Container registry

3. **Kubernetes Resources**
   - Namespaces with auto-creation
   - Service accounts and RBAC
   - ConfigMaps and Secrets

### State Management
```hcl
backend "s3" {
  bucket  = "asela-terraform-states"
  key     = "asela-cluster"
  region  = "us-east-2"
  encrypt = true
}
```

## Application Catalog

### Deployed Applications

| Application | Type | Namespace | Purpose |
|-------------|------|-----------|---------|
| chores-tracker-backend | Custom App | chores-tracker | FastAPI backend (v5.20.0) with MySQL RDS |
| chores-tracker-frontend | Custom App | chores-tracker-frontend | Node.js frontend (v1.5.0) |
| vault | Infrastructure | vault | Secret storage with Kubernetes auth |
| external-secrets | Infrastructure | external-secrets | Vault secret synchronization (1h refresh) |
| crossplane-aws-provider | Infrastructure | crossplane-system | AWS resource provisioning (S3, IAM) |
| crossplane-mysql-provider | Infrastructure | crossplane-system | MySQL user/database/grant management |
| cert-manager | Infrastructure | cert-manager | TLS certificates via LetsEncrypt |
| nginx-ingress | Infrastructure | ingress-nginx | Ingress controller (hostNetwork mode) |
| loki-aws-infrastructure | Infrastructure | logging | Loki S3 bucket and IAM resources |
| logging (Loki) | Monitoring | logging | Log aggregation with S3 backend (30d retention) |
| mysql-rds-backup | Backup | mysql | Daily MySQL RDS backups to S3 (30d retention) |
| ecr-auth | Utility | kube-system | Hourly ECR credential sync CronJob |
| whoami-test | Testing | whoami-test | Ingress testing application |

### Chores Tracker Application

#### Overview
The Chores Tracker is a production web application for household task management, featuring a REST API backend and web frontend.

#### Technical Specifications

**Container Image**
- Registry: AWS ECR (852893458518.dkr.ecr.us-east-2.amazonaws.com)
- Repository: chores-tracker
- Current Version: v0.9.9
- Pull Policy: IfNotPresent

**Deployment Configuration**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: chores-tracker
  namespace: chores-tracker
spec:
  replicas: 1
  selector:
    matchLabels:
      app: chores-tracker
  template:
    spec:
      containers:
      - name: chores-tracker
        image: 852893458518.dkr.ecr.us-east-2.amazonaws.com/chores-tracker:v0.9.9
        ports:
        - containerPort: 8000
        readinessProbe:
          httpGet:
            path: /api/v1/healthcheck
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 5
```

#### Configuration Management

**ConfigMap Settings**
```yaml
ENVIRONMENT: "production"
DEBUG: "False"
BACKEND_CORS_ORIGINS: "https://chores.arigsela.com"
```

**External Secrets (from Vault)**
- `DATABASE_URL`: Connection string for MySQL
- `SECRET_KEY`: Application encryption key
- `DB_PASSWORD`: Database authentication

**Secret Refresh**: Every 1 hour via External Secrets Operator

#### Database Architecture

Managed via Crossplane CRDs:

```yaml
# MySQL User
apiVersion: mysql.crossplane.io/v1alpha1
kind: User
metadata:
  name: chores-user
spec:
  resourceOptions:
    grantOption: true

# MySQL Database
apiVersion: mysql.crossplane.io/v1alpha1
kind: Database
metadata:
  name: chores-db

# Permissions Grant
apiVersion: mysql.crossplane.io/v1alpha1
kind: Grant
spec:
  privileges:
    - "ALL"
  userRef:
    name: chores-user
  databaseRef:
    name: chores-db
```

#### Networking

**Service Configuration**
```yaml
apiVersion: v1
kind: Service
metadata:
  name: chores-tracker
spec:
  type: ClusterIP
  ports:
  - port: 80
    targetPort: 8000
  selector:
    app: chores-tracker
```

**Ingress (Traefik IngressRoute)**
- Domain: https://chores.arigsela.com
- Entry Point: web (HTTP)
- Backend: chores-tracker service on port 80

#### Health Monitoring
- **Endpoint**: `/api/v1/healthcheck`
- **Type**: HTTP GET
- **Initial Delay**: 30 seconds
- **Check Interval**: 5 seconds
- **Purpose**: Kubernetes readiness verification

#### Application Stack
- **Framework**: Python-based (port 8000 suggests Django/FastAPI)
- **API Version**: v1
- **Frontend Domain**: https://chores.arigsela.com
- **CORS**: Enabled for frontend domain

## Development Workflows

### Adding a New Application

1. **Create Application Structure**
   ```bash
   mkdir -p base-apps/my-app
   # Add deployment.yaml, service.yaml, etc.
   ```

2. **Create ArgoCD Application**
   ```bash
   cat > base-apps/my-app.yaml << EOF
   apiVersion: argoproj.io/v1alpha1
   kind: Application
   metadata:
     name: my-app
     namespace: argo-cd
   spec:
     project: default
     source:
       repoURL: https://github.com/arigsela/kubernetes
       targetRevision: main
       path: base-apps/my-app
     destination:
       server: https://kubernetes.default.svc
       namespace: my-app
     syncPolicy:
       automated:
         prune: true
         selfHeal: true
       syncOptions:
         - CreateNamespace=true
   EOF
   ```

3. **Commit and Push**
   ```bash
   git add base-apps/my-app*
   git commit -m "feat: add my-app application"
   git push origin main
   ```

### Updating Applications

#### For Base Applications
```bash
# Update manifest
vi base-apps/chores-tracker/deployments.yaml

# Commit changes
git add base-apps/chores-tracker/deployments.yaml
git commit -m "chore: update chores-tracker to v1.0.0"
git push origin main
```

#### For Kargo-Managed Applications
1. Push new image to registry
2. Kargo warehouse detects new version
3. Promote through stages via Kargo UI/CLI

### Infrastructure Changes

```bash
cd terraform/roots/asela-cluster

# Plan changes
terraform plan

# Apply specific module
terraform apply -target=module.argocd

# Apply all changes
terraform apply
```

## Operations Guide

### Monitoring Application Status

1. **Via Git**: Check manifests in repository for desired state
2. **Via ArgoCD**: Access ArgoCD UI for sync status
3. **Via kubectl**: Direct cluster queries (read-only recommended)

### Common Operations

#### Force Sync Application
```bash
# ArgoCD will auto-sync, but for immediate sync:
# Use ArgoCD UI or CLI
```

#### Rollback Application
```bash
# Revert Git commit
git revert <commit-hash>
git push origin main
# ArgoCD will sync to previous state
```

#### Debug Failed Deployment
1. Check ArgoCD application status
2. Review pod logs in target namespace
3. Verify secrets and configmaps
4. Check resource quotas and limits

### Disaster Recovery

1. **Backup Strategy**
   - Git repository is source of truth
   - Terraform state in S3 with versioning
   - Vault data should be backed up separately

2. **Recovery Process**
   - Restore Git repository
   - Run Terraform to recreate infrastructure
   - ArgoCD will sync all applications

## Security and Compliance

### Secret Management
- **Never commit secrets** to Git
- Use External Secrets Operator for AWS Secrets Manager
- Use Vault for application secrets
- Rotate credentials regularly

### Access Control
- RBAC configured per namespace
- Service accounts for applications
- ECR authentication via ecr-credentials-sync

### Network Security
- Traefik ingress with TLS termination
- Internal services use ClusterIP
- CORS configured per application

### Audit Trail
- All changes tracked in Git history
- ArgoCD maintains sync history
- Terraform state versioning in S3

### Best Practices
1. Review all manifests before committing
2. Use semantic versioning for images
3. Test in lower environments first
4. Monitor resource usage and costs
5. Regular security updates for base images

## Recent Updates (November 2025)

### Infrastructure Improvements
- **Crossplane AWS Credentials**: Rotated and updated with least-privilege IAM policy
  - Scoped S3 permissions to `asela-chores-*` bucket prefix only
  - Limited IAM operations to `/serviceaccounts/` path and `loki-*` policies
  - Added ECR permissions for container image pulls
- **MySQL User Management**: Fixed password synchronization between Vault and RDS
  - Recreated Crossplane MySQL user to sync new passwords from Vault
  - Resolved 503 errors caused by database authentication failures

### Application Deployments
- **Chores Tracker Frontend**: Enabled and deployed
  - Namespace: `chores-tracker-frontend`
  - Image: `852893458518.dkr.ecr.us-east-2.amazonaws.com/chores-tracker-frontend:1.5.0`
  - Ingress: Priority 50 (handles all non-API traffic)
- **ECR Authentication**: Updated namespace configuration
  - Now syncs to: `chores-tracker`, `chores-tracker-frontend`, `mysql`
  - Removed references to non-deployed services (`oncall-agent`, `k8s-monitor`)
  - Hourly automated credential refresh

### Security & Access Control
- **IAM Policy Refinement**: Implemented least-privilege policies for Crossplane
  - S3: Bucket management scoped to project prefix
  - IAM: User management limited to service account path
  - ECR: Read-only access for image pulls
- **Secret Management**: All credentials now managed through Vault
  - External Secrets refresh every 1 hour
  - No hardcoded credentials in manifests

### Monitoring & Logging
- **Loki**: S3-backed log aggregation with 30-day retention
  - Bucket: `asela-chores-loki-logs-20251017`
  - Lifecycle policies for automated cleanup
  - IAM user `loki-s3-user` with least-privilege S3 access

### Networking
- **Nginx Ingress Controller**: Configured with hostNetwork mode
  - Runs on `k3s-control-01` node (10.0.1.50)
  - Requires pfSense port forwarding: 80/443 → 10.0.1.50
  - Domain: `chores.arigsela.com` (DNS: 73.7.190.154)
- **Ingress Priority**: Backend (100) > Frontend (50)
  - Backend handles `/api/*` paths
  - Frontend handles all other paths

### Database & Backups
- **MySQL RDS**: Production database for chores-tracker
  - Managed users via Crossplane MySQL Provider
  - Automated daily backups to S3
  - 30-day retention policy

---

*Last Updated: 2025-11-22*
*Repository: https://github.com/arigsela/kubernetes*