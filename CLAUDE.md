# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is a GitOps-based Kubernetes infrastructure repository that manages application deployments using ArgoCD, Kargo for progressive delivery, and Terraform for infrastructure provisioning. The repository follows a declarative approach where all changes are made through Git commits, which then automatically sync to the cluster.

## Architecture

### GitOps Workflow
- **ArgoCD** monitors this repository and automatically syncs changes to the cluster
- **Master App Pattern**: A master ArgoCD application in `base-apps/master-app.yaml` watches the `/base-apps` directory and creates applications for each `.yaml` file
- **Auto-sync**: All applications have `prune: true` and `selfHeal: true` enabled
- **Kargo**: Manages progressive delivery across test → uat → prod environments

### Directory Structure
- `/base-apps/` - ArgoCD applications and their Kubernetes manifests
  - Each subdirectory contains deployment configs for one application
  - Each `.yaml` file in this directory creates an ArgoCD Application
- `/terraform/` - Infrastructure as Code
  - `/modules/` - Reusable Terraform modules (argocd, application-sets, kube-secrets)
  - `/roots/asela-cluster/` - Main cluster configuration
- `/kargo/` - Progressive delivery configurations for multi-stage deployments

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

### Check Application Status
Since all deployments are managed through GitOps, checking application status requires access to the ArgoCD UI or CLI. The repository itself shows the desired state.

## Key Technologies and Patterns

### Application Management
- **ArgoCD**: All applications auto-sync from this repository
- **Kargo**: Progressive delivery for applications in `/kargo` directory
- **External Secrets Operator**: Manages secrets from AWS Secrets Manager and Vault
- **Crossplane**: Provisions cloud resources declaratively

### Infrastructure Components
- **Terraform State**: Stored in S3 bucket `asela-terraform-states`
- **AWS Services**: ECR for images, Secrets Manager for sensitive data, RDS for databases
- **Kubernetes Provider**: Connects to cluster at `https://192.168.0.100:6443`

### Deployment Patterns
- Applications use `syncPolicy.automated` with `prune` and `selfHeal`
- Namespaces are auto-created with `CreateNamespace=true`
- Image updates trigger automatic deployments
- Kargo manages promotions across test/uat/prod stages

## Important Notes

1. **No Direct kubectl Commands**: All changes must go through Git
2. **Automatic Sync**: Changes to main branch deploy automatically
3. **Branch Strategy**: 
   - `main` branch for base applications
   - `stage/*` branches for Kargo-managed environments
4. **Current Branch**: Repository is on `kargo-deployment` branch
5. **Secrets Management**: Use External Secrets Operator or Vault - never commit secrets
6. **State Management**: Terraform state is remote in S3 - never commit state files