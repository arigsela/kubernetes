# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Start here:** Read [`index.md`](index.md) first — it is the OKF bundle root and navigation front door (system context, topology, per-app index). For an app, follow `base-apps/index.md` → the app's `docs.md`/`runbook.md`. The agent-docs contract is documented in [`templates/agent-docs/README.md`](templates/agent-docs/README.md).

@AGENTS.md

## Repository Overview

This is a GitOps-based Kubernetes infrastructure repository that manages application deployments using ArgoCD, Kargo for progressive delivery, and Terraform for infrastructure provisioning. The repository follows a declarative approach where all changes are made through Git commits, which then automatically sync to the cluster.

## Architecture

### GitOps Workflow
- **ArgoCD** monitors this repository and automatically syncs changes to the cluster
- **Master App Pattern**: A master ArgoCD app-of-apps, defined in `base-apps/master-app.yaml` (it manages itself), watches the `base-apps/` directory and creates an Application for each top-level `.yaml` file
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
- `/ansible/` - Host layer: the Ubuntu hypervisor and the three k3s VMs (see `ansible/README.md`)
  - `/playbooks/` - `site.yml` (baseline), `patch.yml` (VMs), `patch-hypervisor.yml`, `k3s-authn.yml`
  - `/roles/` - `common`, `hypervisor`, `k3s_node` (holds the control plane's authn files)
- `/docs/` - Implementation plans and troubleshooting guides
- `/scripts/` - Operational scripts (monitoring, maintenance)

### Secret Management Architecture
- **Vault Backend**: HashiCorp Vault deployed in-cluster at `vault.vault.svc.cluster.local:8200`
- **Authentication**: Kubernetes authentication method using service accounts
- **Storage Path**: KV v2 engine at path `k8s-secrets`
- **Per-Namespace SecretStores**: Each application namespace has its own SecretStore with role-based access

## Common Development Tasks

### Deploy / update apps & secrets
See `AGENTS.md` for build/test/validation commands, and `templates/agent-docs/README.md` for the per-app doc contract. Deploy pattern: add `base-apps/<app>.yaml` (Argo CD Application) + `base-apps/<app>/` manifests; Argo CD's master-app creates the Application on sync. Secret pattern: per-namespace `secret-store.yaml` + `external-secret*.yaml` resolving from Vault (`k8s-secrets`).

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

### Run Ansible (host layer)
```bash
cd ansible
ansible-playbook playbooks/site.yml --check --diff   # preview the baseline
ansible-playbook playbooks/patch.yml                 # patch + drained serial reboot of the VMs
ansible-playbook playbooks/k3s-authn.yml --check     # validate the API server authn config
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
- **Chores Tracker**: FastAPI/Python backend with PostgreSQL (CloudNativePG), JWT auth, HTMX frontend
- **Cert-Manager**: TLS via Let's Encrypt — a single Route 53 DNS-01 issuer (`letsencrypt-route53`); one explicit `Certificate` per host
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

## PR Review Labels (pr-triage)

Every PR to `main` is triaged by `.github/workflows/pr-triage.yaml`. It gets exactly one of `review:skip`, `review:skim` or `review:read`, plus one sticky comment that explains the label. The owner uses the label to decide how closely to read the PR. It never blocks a merge.

- **Write PR titles and bodies that describe the change accurately.** They are classifier input. Never address instructions to the triage model; PR text is treated as untrusted and ignored as instructions.
- **Policy changes:** `.github/review-policy.yaml` is the risk policy. Any change under `.github/**` is always `review:read`. Triage reads the policy from the PR's *base* commit, so a policy change only takes effect after it merges.
- **Don't relabel by hand to change triage.** Change the policy instead. After changing thresholds, questions or the Jev model, re-run calibration in `arigsela/claude-agents`: `uv run --project pr-triage python -m pr_triage calibrate --repo arigsela/kubernetes --limit 200`.
- **Next steps:** for `skim`/`read` PRs the comment suggests `/pr-explainer <n>`, a rendered-manifest resource map and must-read hunks, and `/code-review <n>`. `/review-retro <n>` compares a past `/code-review` session with the fix PRs that followed.
- **Upgrading the action:** the workflow pins `arigsela/claude-agents/pr-triage` by commit SHA. Merge the change in claude-agents first, then bump the SHA here.

## Important Notes

1. **No Direct kubectl Commands**: All changes must go through Git
2. **Automatic Sync**: Changes to main branch deploy automatically
3. **Branch Strategy**: Use `main` for production deployments, feature branches for development
4. **Secrets Management**: Use Vault with Kubernetes auth - never commit secrets or tokens
5. **State Management**: Terraform state is remote in S3 - never commit `.terraform/` directories
6. **Secret Store Pattern**: Each application namespace should have its own SecretStore configuration
7. **Vault Roles**: Ensure Vault roles match the namespace names for proper access control