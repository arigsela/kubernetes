# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is a GitOps-based Kubernetes infrastructure repository that manages application deployments using ArgoCD, Kargo for progressive delivery, and Terraform for infrastructure provisioning. The repository follows a declarative approach where all changes are made through Git commits, which then automatically sync to the cluster.

## Architecture

### GitOps Workflow
- **ArgoCD** monitors this repository and automatically syncs changes to the cluster
- **Master App Pattern**: A master ArgoCD application in `base-apps/master-app.yaml` watches the `/base-apps` directory and creates applications for each `.yaml` file
- **Auto-sync**: All applications have `prune: true` and `selfHeal: true` enabled
- **External Secrets Operator**: Manages secrets from Vault using Kubernetes authentication

### Directory Structure
- `/base-apps/` - ArgoCD applications and their Kubernetes manifests
  - Each subdirectory contains deployment configs for one application
  - Each `.yaml` file in this directory creates an ArgoCD Application
  - Each application directory can contain its own `secret-store.yaml` for Vault integration
- `/terraform/` - Infrastructure as Code
  - `/modules/` - Reusable Terraform modules (argocd, application-sets, kube-secrets)
  - `/roots/asela-cluster/` - Main cluster configuration with provider configs
- `/docs/` - Implementation plans and troubleshooting guides
- `/scripts/` - Operational scripts (monitoring, maintenance)

### Secret Management Architecture
- **Vault Backend**: HashiCorp Vault deployed in-cluster at `vault.vault.svc.cluster.local:8200`
- **Authentication**: Kubernetes authentication method using service accounts
- **Storage Path**: KV v2 engine at path `k8s-secrets`
- **Per-Namespace SecretStores**: Each application namespace has its own SecretStore with role-based access

## Common Development Tasks

### Deploy a New Application
```bash
# 1. Create application directory
mkdir -p base-apps/my-app

# 2. Add Kubernetes manifests (deployments.yaml, services.yaml, etc.)

# 3. Create ArgoCD Application manifest
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

# 4. Commit and push - ArgoCD will auto-deploy
git add base-apps/my-app*
git commit -m "Deploy my-app"
git push origin main
```

### Update an Existing Application
```bash
# Edit the application files
vi base-apps/chores-tracker/deployments.yaml

# Commit and push - changes auto-sync
git add base-apps/chores-tracker/deployments.yaml
git commit -m "Update chores-tracker image to v1.0.0"
git push origin main
```

### Add Secret Management to Application
```bash
# Create a SecretStore for your application namespace
cat > base-apps/my-app/secret-store.yaml << EOF
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: my-app
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "my-app"  # Must match Vault role configuration
          serviceAccountRef:
            name: "default"
EOF

# Create an ExternalSecret to sync specific secrets
cat > base-apps/my-app/external-secret.yaml << EOF
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: my-app-secrets
  namespace: my-app
spec:
  refreshInterval: 15s
  secretStoreRef:
    kind: SecretStore
    name: vault-backend
  target:
    name: my-app-secrets
    creationPolicy: Owner
  data:
  - secretKey: database-password
    remoteRef:
      key: my-app/database
      property: password
EOF
```

### Run Terraform Commands
```bash
cd terraform/roots/asela-cluster

# Initialize (first time or after module changes)
terraform init

# Preview changes
terraform plan

# Apply changes
terraform apply

# Target specific resources
terraform apply -target=module.argocd
```

### Branch Management
```bash
# Current branch should be main for most changes
git checkout main

# Feature branches for experimental work
git checkout -b feature/my-feature

# Check current branch and status
git branch --show-current
git status
```

## Key Technologies and Patterns

### Application Management
- **ArgoCD**: All applications auto-sync from this repository using the master app pattern
- **External Secrets Operator**: Manages secrets from Vault using Kubernetes authentication
- **Crossplane**: Provisions cloud resources declaratively
- **Cert-Manager**: Handles TLS certificate management with DNS-01 challenges

### Infrastructure Components
- **Terraform State**: Stored in S3 bucket `asela-terraform-states`
- **AWS Services**: ECR for images, Route 53 for DNS management, RDS for databases
- **Kubernetes Provider**: Connects to cluster at `https://192.168.0.100:6443`
- **Vault**: In-cluster secret management with KV v2 engine

### Deployment Patterns
- Applications use `syncPolicy.automated` with `prune` and `selfHeal`
- Namespaces are auto-created with `CreateNamespace=true`
- Each application manages its own SecretStore configuration
- Image updates trigger automatic deployments via ArgoCD

### Application Examples
- **Chores Tracker**: FastAPI/Python backend with MySQL, JWT auth, HTMX frontend
- **Cert-Manager**: Automated TLS certificate management with Route 53 DNS challenges
- **External Secrets**: Vault integration for secure secret management

## Architecture Decision Records

### Secret Management Pattern
- **Decision**: Use distributed SecretStore configs per application namespace
- **Rationale**: Better security isolation, easier role-based access control
- **Implementation**: Each app has `secret-store.yaml` with namespace-specific Vault roles

### Authentication Strategy
- **Decision**: Kubernetes authentication for Vault access instead of token-based
- **Rationale**: More secure, automatic rotation, leverages existing RBAC
- **Configuration**: Service account references with role-based access

## Important Notes

1. **No Direct kubectl Commands**: All changes must go through Git
2. **Automatic Sync**: Changes to main branch deploy automatically
3. **Branch Strategy**: Use `main` for production deployments, feature branches for development
4. **Secrets Management**: Use Vault with Kubernetes auth - never commit secrets or tokens
5. **State Management**: Terraform state is remote in S3 - never commit `.terraform/` directories
6. **Secret Store Pattern**: Each application namespace should have its own SecretStore configuration
7. **Vault Roles**: Ensure Vault roles match the namespace names for proper access control