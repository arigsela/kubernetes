# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Start here:** Read [`index.md`](index.md) first — it is the OKF bundle root and navigation front door (system context, topology, per-app index). For an app, follow `base-apps/index.md` → the app's `docs.md`/`runbook.md`. The agent-docs contract is documented in [`templates/agent-docs/README.md`](templates/agent-docs/README.md).

@AGENTS.md

## Repository Overview

This is a GitOps repository for a homelab k3s cluster. Argo CD deploys everything under `base-apps/`, Terraform (applied by Atlantis) provisions the cluster-level and AWS bootstrap, Crossplane provisions per-app AWS resources, and Ansible manages the hosts. All changes are made through Git commits, which Argo CD syncs to the cluster.

## Architecture

### GitOps Workflow
- **Argo CD** monitors this repository and automatically syncs changes to the cluster
- **Master App Pattern**: A master Argo CD app-of-apps, defined in `base-apps/master-app.yaml` (it manages itself), watches the `base-apps/` directory and creates an Application for each top-level `.yaml` file
- **managed-apps ApplicationSet**: 12 apps have no hand-written Application; `base-apps/managed-apps.yaml` generates them from configs in `appsets/managed-apps/` (see `docs/managed-apps-appset.md`)
- **Auto-sync**: Applications use `prune: true` and `selfHeal: true` (a few, like Atlantis, disable prune to protect a PVC)
- **External Secrets Operator**: Manages secrets from Vault using Kubernetes authentication

### Directory Structure
- `/base-apps/` - Argo CD Applications and their Kubernetes manifests
  - Each top-level `.yaml` file creates an Argo CD Application; its manifests live in the matching subdirectory
  - Each application directory can contain its own `secret-store.yaml` for Vault integration
- `/appsets/managed-apps/` - Per-app configs for the `managed-apps` ApplicationSet
- `/terraform/` - Infrastructure as Code
  - `/roots/asela-cluster/` - The only active root (Argo CD install, IAM, Vault KMS key and credentials, Atlantis IAM)
  - `/modules/` - `argocd` (in use); `application-sets` and `kube-secrets` are no longer called by the root
- `/ansible/` - Host layer: the Ubuntu hypervisor and the three k3s VMs (see `ansible/README.md`)
  - `/playbooks/` - `bootstrap.yml`, `site.yml` (baseline), `patch.yml` (VMs), `patch-hypervisor.yml`, `k3s-authn.yml`
  - `/roles/` - `common`, `hypervisor`, `k3s_node` (holds the control plane's authn files)
- `/templates/` - The agent-docs and agent-identity contracts, and the Backstage New App template (`new-app/`)
- `/catalog/` - Backstage platform and API entities
- `/docs/` - Cross-cutting guides, troubleshooting notes, specs and plans (see `docs/index.md`)
- `/scripts/` - Generators, validators, backup/restore and Vault provisioning scripts
- `/tests/` - pytest suites, one directory per concern (commands in `AGENTS.md`)
- `/recovery/` - Backup & recovery: `CLUSTER-RECOVERY.md` (what is backed up, how to restore, known gaps)
- `SPEC.md` - The current task and invariant ledger; manifests cite its sections (`§T.n`, `§V.n`)

### Secret Management Architecture
- **Vault Backend**: HashiCorp Vault deployed in-cluster at `vault.vault.svc.cluster.local:8200`, auto-unsealed with AWS KMS
- **Authentication**: Kubernetes authentication method using service accounts
- **Storage Path**: KV v2 engine at path `k8s-secrets`
- **Per-Namespace SecretStores**: Each application namespace has its own SecretStore with role-based access; agent and database credentials get their own per-consumer SecretStore and Vault role (`templates/agent-identity/README.md`)

## Common Development Tasks

### Deploy / update apps & secrets
See `AGENTS.md` for build/test/validation commands, and `templates/agent-docs/README.md` for the per-app doc contract. Deploy pattern: add `base-apps/<app>.yaml` (Argo CD Application) + `base-apps/<app>/` manifests; Argo CD's master-app creates the Application on sync. For a simple app, a config in `appsets/managed-apps/` replaces the hand-written Application. Secret pattern: per-namespace `secret-store.yaml` + `external-secret*.yaml` resolving from Vault (`k8s-secrets`).

### Terraform changes (Atlantis, apply before merge)
Terraform in `terraform/roots/asela-cluster` is applied by the in-cluster Atlantis (OpenTofu) while the PR is still open. Merging is the last step, not the trigger.

1. Open the PR; Atlantis autoplans (`atlantis/plan: asela-cluster`)
2. Comment `atlantis apply`, or run the "Terraform Apply (gated)" Action with the PR number (gated by the `terraform-apply` Environment's required reviewer)
3. Confirm `atlantis/apply` is green, then merge

Merging before apply silently strands the change. Push once per PR; `atlantis unlock` clears a wedged workdir. Locally, use only the read-only checks in `AGENTS.md` (`fmt`, `validate`, `tflint`); never run `apply` from a laptop. The one exception is rebuilding the cluster while Atlantis can't run (it needs Vault): see "Rebuild from nothing" in `recovery/CLUSTER-RECOVERY.md`.

### Run Ansible (host layer)
```bash
cd ansible
ansible-playbook playbooks/site.yml --check --diff   # preview the baseline
ansible-playbook playbooks/patch.yml                 # patch + drained serial reboot of the VMs
ansible-playbook playbooks/k3s-authn.yml --check     # validate the API server authn config
```

### Branch Management
```bash
# Start from an up-to-date main
git checkout main && git pull

# Every change goes through a PR from a feature branch
git checkout -b feature/my-feature
```

## Key Technologies and Patterns

### Application Management
- **Argo CD**: All applications auto-sync from this repository using the master app pattern and the managed-apps ApplicationSet
- **External Secrets Operator**: Manages secrets from Vault using Kubernetes authentication
- **Crossplane**: Provisions per-app AWS resources (S3 buckets, IAM users) declaratively
- **Cert-Manager**: TLS via Let's Encrypt — a single Route 53 DNS-01 issuer (`letsencrypt-route53`); one explicit `Certificate` per host
- **Ingress**: Istio Gateway API (`base-apps/istio-ingress/`) with per-host `HTTPRoute`s, an IP allow-list `AuthorizationPolicy`, and the Coraza WAF (`base-apps/istio-waf/`)
- **Admission**: native ValidatingAdmissionPolicies / MutatingAdmissionPolicies in `base-apps/admission-policies/` (Kyverno only produces reports)

### Infrastructure Components
- **Terraform State**: Stored in S3 bucket `asela-terraform-states`
- **AWS Services**: ECR for images, Route 53 for DNS, S3 (state, Loki logs, backups, agent action record), KMS for Vault auto-unseal
- **Kubernetes API**: `https://10.0.1.50:6443` (k3s, single control-plane node plus two workers)
- **Databases**: in-cluster PostgreSQL via CloudNativePG (`base-apps/postgresql/`)
- **Vault**: In-cluster secret management with KV v2 engine; human login via Dex OIDC

### Deployment Patterns
- Applications use `syncPolicy.automated` with `prune` and `selfHeal`
- Namespaces are auto-created with `CreateNamespace=true`
- Each application manages its own SecretStore configuration
- Image versions are pinned in the manifests; an image update is a Git commit (there is no image updater)

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
3. **Terraform applies before merge**: see "Terraform changes" above
4. **Secrets Management**: Use Vault with Kubernetes auth - never commit secrets or tokens
5. **State Management**: Terraform state is remote in S3 - never commit `.terraform/` directories
6. **Secret Store Pattern**: Each application namespace should have its own SecretStore configuration
7. **Vault Roles**: Most app SecretStores use a Vault role named after the namespace (`n8n`, `logging`, …). Agent and database credentials use per-consumer roles with a dedicated service account (for example `kagent-db`, `donetick-db`, `homelab-agent-db`). Either way, the SecretStore's role and service account must match the Vault role's bindings
8. **Generated files**: `base-apps/index.md`, each app's `docs/` and `mkdocs.yml`, and a few manifests are generated; edit the source and re-run the generator (see `AGENTS.md`)
