# Atlantis + Infracost Implementation Plan

**Status:** Phase 6 Complete (6/7 phases)
**Last Updated:** 2026-03-30
**Phase 1 Completed:** 2026-03-29
**Phase 2 Completed:** 2026-03-29
**Phase 3 Completed:** 2026-03-29
**Phase 4 Completed:** 2026-03-29
**Phase 5 Completed:** 2026-03-30
**Phase 6 Completed:** 2026-03-30

## Overview

Deploy Atlantis as a PR-based Terraform workflow automation tool and Infracost for cloud cost estimation. This eliminates local `terraform plan/apply` operations, centralizes credentials, provides PR-visible plans with cost estimates, and adds Terraform CI/CD validation.

**Why Atlantis?**
- Prevents Terraform drift by centralizing all plan/apply through PRs
- Engineers no longer need AWS credentials locally
- Built-in project locking prevents concurrent state modifications
- Complete audit trail of all infrastructure changes in PR comments

**Why Infracost?**
- Shows cost impact of infrastructure changes before apply
- Catches expensive resource changes during code review
- Free Cloud API tier (1,000 runs/month) sufficient for this setup

## Success Criteria

- [ ] Atlantis pod running on infrastructure nodes, accessible at atlantis.arigsela.com
- [ ] PR comments automatically show `terraform plan` output when `terraform/` files change
- [ ] `atlantis apply` blocked until PR is approved and mergeable
- [ ] Infracost cost estimates appear on PRs (both via Atlantis and GitHub Actions)
- [ ] Terraform validation (fmt, validate) runs in CI on every PR
- [ ] All secrets managed via Vault + External Secrets (no hardcoded credentials)
- [ ] Dedicated IAM user with scoped permissions for Atlantis

## Research Findings

### Atlantis Helm Chart
- **Repository**: `https://runatlantis.github.io/helm-charts`
- **Chart**: `atlantis`, version **6.1.0** (app version v0.40.0)
- **Image override**: `infracost/infracost-atlantis:atlantis0.40-infracost0.10` (Atlantis + Infracost baked in)
- **Key features**: PR-based plan/apply, project locking, custom workflows, Conftest support

### Infracost
- **CLI version**: v0.10.43
- **Pricing API**: Free Cloud API (hosted at pricing.api.infracost.io, 1,000 runs/month)
- **Atlantis integration**: Pre-built Docker image with Infracost CLI included
- **GitHub Action**: `infracost/actions/setup@v3`
- **Self-hosted Helm chart**: Removed (Enterprise-only) — using free Cloud API instead

### Existing Patterns to Follow
- `base-apps/external-secrets.yaml` — Helm-based ArgoCD Application with inline values
- `base-apps/cert-manager.yaml` — Helm-based ArgoCD Application with parameters
- `base-apps/backstage/` — SecretStore + ExternalSecret + Ingress pattern
- `terraform/roots/asela-cluster/iam.tf` — IAM user + policy + access key pattern
- `terraform/roots/asela-cluster/velero-s3.tf` — IAM user with scoped policy pattern
- `.github/workflows/validate.yaml` — Existing CI pipeline structure

### Dependencies
- ArgoCD (manages Atlantis deployment)
- Vault + External Secrets Operator (secret management)
- Cert-Manager (TLS for Atlantis ingress)
- Nginx Ingress Controller (ingress routing)
- Infrastructure workload nodes (scheduling target)

## Architecture Decisions

### Decision 1: Atlantis Image
**Options considered:**
1. **Official Atlantis image** (`ghcr.io/runatlantis/atlantis:v0.40.0`) — standard, no Infracost
2. **Infracost-Atlantis image** (`infracost/infracost-atlantis:atlantis0.40-infracost0.10`) — Atlantis + Infracost baked in

**Chosen:** Option 2 — Single image with both tools. Eliminates init containers or sidecar complexity. Infracost runs as a custom workflow step within Atlantis.

### Decision 2: GitHub Authentication
**Options considered:**
1. **GitHub App** — Better rate limits, granular permissions, no personal token
2. **Personal Access Token (PAT)** — Simpler setup, tied to a user account

**Chosen:** Option 2 (PAT) — Simpler initial setup. Can migrate to GitHub App later if needed.

### Decision 3: Infracost Pricing API
**Options considered:**
1. **Free Cloud API** — No infrastructure, 1,000 runs/month, API key required
2. **Self-hosted (IBM-Cloud fork)** — Full control, requires PostgreSQL + API + cron job
3. **Enterprise self-hosted** — Official Helm chart, paid

**Chosen:** Option 1 — Free Cloud API. 1,000 runs/month is far more than needed for a single-cluster setup. No infrastructure overhead.

### Decision 4: Infracost in GitHub Actions
**Chosen:** Run Infracost in GitHub Actions **in addition to** the Atlantis workflow. This provides cost visibility even if Atlantis is down, and runs earlier in the PR lifecycle (on PR open, before Atlantis plan).

### Decision 5: IAM Credential Delivery
**Options considered:**
1. **Terraform creates K8s secret directly** (like crossplane-admin pattern in iam.tf)
2. **Terraform outputs keys, manually store in Vault, ExternalSecret syncs to K8s**

**Chosen:** Option 2 — Store in Vault for consistency with the existing secret management architecture. Terraform outputs the access key, which is manually stored in Vault. ExternalSecret syncs it to the atlantis namespace.

## File Structure

```text
kubernetes/
├── atlantis.yaml                                    # NEW: Repo-level Atlantis project config
├── terraform/
│   └── roots/
│       └── asela-cluster/
│           └── atlantis-iam.tf                      # NEW: IAM user + scoped policy for Atlantis
├── base-apps/
│   ├── atlantis.yaml                                # NEW: ArgoCD Application (Helm chart)
│   └── atlantis/                                    # NEW: Directory
│       ├── secret-store.yaml                        # NEW: Vault SecretStore
│       ├── external-secrets.yaml                    # NEW: ExternalSecret (all secrets)
│       ├── nginx-ingress.yaml                       # NEW: Ingress with TLS + IP whitelist
│       └── network-policy.yaml                      # NEW: NetworkPolicy restricting egress
└── .github/
    └── workflows/
        ├── validate.yaml                            # EXISTING (no changes)
        ├── terraform-validate.yaml                  # NEW: TF fmt/validate/lint/tfsec
        └── infracost.yaml                           # NEW: Cost estimation on PRs
```

**Total new files:** 9
**Modified files:** 0

---

## Implementation

### Phase 1: Terraform Resources — IAM User + Policy for Atlantis

#### Task 1.1: Create Atlantis IAM User and Policy
**Files:** `terraform/roots/asela-cluster/atlantis-iam.tf`
**Steps:**
1. Create a `data "aws_caller_identity" "current" {}` data source (if not already present) to reference the AWS account ID dynamically
2. Create IAM user `atlantis-terraform` with path `/system/` and standard tags
3. Create IAM policy with **resource-scoped permissions** for each service managed by the Terraform root:
   - **S3 (state + managed buckets)**:
     - `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket`, `s3:GetBucketLocation`, `s3:GetBucketVersioning` on `arn:aws:s3:::asela-terraform-states` and `arn:aws:s3:::asela-terraform-states/*`
     - `s3:*` on `arn:aws:s3:::asela-velero-backups` and `arn:aws:s3:::asela-velero-backups/*` (Terraform manages this bucket's full lifecycle)
     - `s3:CreateBucket`, `s3:DeleteBucket`, `s3:PutBucketVersioning`, `s3:PutBucketEncryption`, `s3:PutBucketPublicAccessBlock`, `s3:PutLifecycleConfiguration`, `s3:GetBucketPolicy`, `s3:PutBucketPolicy` on `arn:aws:s3:::asela-*` (allows creating new buckets with the `asela-` prefix only)
   - **RDS**: `rds:*` on `arn:aws:rds:us-east-2:${data.aws_caller_identity.current.account_id}:db:asela-cluster-*` (scoped to asela-cluster instances)
   - **EC2 (security groups for RDS)**: `ec2:CreateSecurityGroup`, `ec2:DeleteSecurityGroup`, `ec2:AuthorizeSecurityGroupIngress`, `ec2:AuthorizeSecurityGroupEgress`, `ec2:RevokeSecurityGroupIngress`, `ec2:RevokeSecurityGroupEgress`, `ec2:DescribeSecurityGroups`, `ec2:DescribeAccountAttributes`, `ec2:DescribeAvailabilityZones`, `ec2:DescribeVpcs` on `*` (EC2 describe actions don't support resource-level restrictions)
   - **Secrets Manager**: `secretsmanager:*` on `arn:aws:secretsmanager:us-east-2:${data.aws_caller_identity.current.account_id}:secret:rds-mysql-asela-*` and `arn:aws:secretsmanager:us-east-2:${data.aws_caller_identity.current.account_id}:secret:aws-credentials-infra-*`
   - **KMS**: `kms:Create*`, `kms:Describe*`, `kms:Enable*`, `kms:List*`, `kms:Put*`, `kms:Update*`, `kms:Revoke*`, `kms:Disable*`, `kms:Get*`, `kms:Delete*`, `kms:TagResource`, `kms:UntagResource`, `kms:ScheduleKeyDeletion`, `kms:CancelKeyDeletion`, `kms:CreateAlias`, `kms:DeleteAlias` on `arn:aws:kms:us-east-2:${data.aws_caller_identity.current.account_id}:key/*` and `arn:aws:kms:us-east-2:${data.aws_caller_identity.current.account_id}:alias/vault-*`
   - **IAM (scoped to /system/ path)**: `iam:*` on `arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/system/*` and `arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/*` (allows managing service users under /system/ path only — prevents creating admin-level users)
4. Create access key for the IAM user
5. Add outputs for access key ID and user ARN
6. Add comment noting the secret key must be manually stored in Vault after `terraform apply`

**Security note:** The IAM policy is intentionally scoped to specific resource ARNs rather than using `*`. IAM actions are restricted to the `/system/` path to prevent privilege escalation. If new AWS resource types are added to Terraform in the future, the policy must be updated accordingly.

**Pattern reference:** Follow `velero-s3.tf` IAM user pattern with inline policy using `aws_iam_user_policy`

**Testing:**
- [ ] `terraform plan` shows 3 new resources (user, policy, access key)
- [ ] `terraform apply` succeeds
- [ ] Access key ID visible in outputs
- [ ] Secret access key retrievable via `terraform state show aws_iam_access_key.atlantis_key`
- [ ] Verify the IAM policy in AWS console — confirm no `*` resource wildcards on sensitive services (IAM, KMS)

---

### Phase 2: Vault Configuration (Manual Steps)

#### Task 2.1: Create Vault Kubernetes Auth Role for Atlantis ✅
**Completed:** 2026-03-29
- Vault policy `atlantis` created: `path "k8s-secrets/data/atlantis/*" { capabilities = ["read"] }`
- Kubernetes auth role `atlantis` created: bound to `default` SA in `atlantis` namespace, TTL 1h

#### Task 2.2: Store Secrets in Vault ✅ (placeholders pending user replacement)
**Completed:** 2026-03-29

All 4 secret paths created at `k8s-secrets/atlantis/`:

| Path | Key(s) | Status |
|------|--------|--------|
| `k8s-secrets/atlantis/github` | `token` | ✅ Set |
| `k8s-secrets/atlantis/webhook` | `secret` | ✅ Set: `66a42f5f31794b56b973c1f28470afbb` (used for GitHub webhook config in Phase 5) |
| `k8s-secrets/atlantis/aws` | `access-key`, `secret-key` | ✅ Set |
| `k8s-secrets/atlantis/infracost` | `api-key` | ✅ Set |

To replace placeholders:
```bash
vault kv patch k8s-secrets/atlantis/github token="<your-PAT>"
vault kv patch k8s-secrets/atlantis/aws secret-key="<from: terraform state pull | jq ...>"
vault kv patch k8s-secrets/atlantis/infracost api-key="<your-key>"  # ✅ Done
```

**Testing:**
- [x] `vault kv list k8s-secrets/atlantis/` — all 4 keys present
- [x] `vault read auth/kubernetes/role/atlantis` — role exists with correct bindings
- [x] `vault policy read atlantis` — policy grants read on atlantis/* path
- [x] All placeholder values replaced — Phase 2 fully complete

---

### Phase 3: Atlantis Deployment — ArgoCD + Supporting Manifests

#### Task 3.1: Create Atlantis ArgoCD Application
**Files:** `base-apps/atlantis.yaml`
**Steps:**
1. Create ArgoCD Application manifest using Helm chart source pattern (like `external-secrets.yaml`)
2. Set `source.repoURL: https://runatlantis.github.io/helm-charts`
3. Set `source.chart: atlantis`, `source.targetRevision: 6.1.0`
4. Configure Helm values:
   - **Image override**: `infracost/infracost-atlantis:atlantis0.40-infracost0.10`
   - **orgAllowlist**: `github.com/arigsela/*`
   - **atlantisUrl**: `https://atlantis.arigsela.com`
   - **GitHub config**: Reference K8s secret `atlantis-secrets` for `ATLANTIS_GH_USER`, `ATLANTIS_GH_TOKEN`, `ATLANTIS_GH_WEBHOOK_SECRET`
   - **AWS credentials**: Reference K8s secret for `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION=us-east-2`
   - **Infracost**: Reference K8s secret for `INFRACOST_API_KEY`
   - **Node placement**: `nodeSelector: node.kubernetes.io/workload: infrastructure` + control-plane toleration
   - **Resource limits** (Terraform plans are memory-intensive due to provider loading + state parsing):
     ```yaml
     resources:
       requests:
         memory: 1Gi
         cpu: 500m
       limits:
         memory: 4Gi
         cpu: 2
     ```
   - **Storage**: `volumeClaim.dataStorage: 5Gi`
   - **Ingress**: Disabled (using custom nginx-ingress.yaml instead)
   - **Default TF version**: `1.11.2`
   - **repoConfig**: Server-side repo configuration with custom workflow including Infracost steps
5. Set destination namespace to `atlantis`
6. Standard automated sync with prune, selfHeal, CreateNamespace

**Server-side repoConfig (embedded in Helm values):**
```yaml
repoConfig: |
  repos:
    - id: "github.com/arigsela/kubernetes"
      apply_requirements:
        - approved
        - mergeable
      allowed_overrides:
        - workflow
      allow_custom_workflows: false
      workflow: infracost
  workflows:
    infracost:
      plan:
        steps:
          - init
          - plan
          - run: >
              infracost diff --path=$SHOWFILE
              --format=json
              --out-file=/tmp/infracost-$BASE_REPO_OWNER-$BASE_REPO_NAME-$PULL_NUM.json
          - run: >
              infracost comment github
              --path=/tmp/infracost-$BASE_REPO_OWNER-$BASE_REPO_NAME-$PULL_NUM.json
              --repo=$BASE_REPO_OWNER/$BASE_REPO_NAME
              --pull-request=$PULL_NUM
              --behavior=update
              --github-token=$ATLANTIS_GH_TOKEN
      apply:
        steps:
          - apply
```

**Testing:**
- [ ] ArgoCD Application syncs successfully (Healthy + Synced)
- [ ] Atlantis pod running in `atlantis` namespace on infrastructure node
- [ ] Pod logs show successful startup with GitHub connection
- [ ] `kubectl get pods -n atlantis` shows 1/1 Running

#### Task 3.2: Create Vault SecretStore for Atlantis Namespace
**Files:** `base-apps/atlantis/secret-store.yaml`
**Steps:**
1. Create SecretStore following the established pattern (e.g., `base-apps/backstage/secret-store.yaml`)
2. Provider: Vault at `http://vault.vault.svc.cluster.local:8200`
3. Path: `k8s-secrets`, version: `v2`
4. Kubernetes auth with role: `atlantis`, serviceAccountRef: `default`

**Testing:**
- [ ] `kubectl get secretstore -n atlantis` shows vault-backend as Valid

#### Task 3.3: Create ExternalSecret for Atlantis Secrets
**Files:** `base-apps/atlantis/external-secrets.yaml`
**Steps:**
1. Create ExternalSecret `atlantis-secrets` in namespace `atlantis`
2. Set `refreshInterval: 1h`
3. Reference SecretStore `vault-backend`
4. Target K8s secret name: `atlantis-secrets`
5. Map the following Vault secrets to K8s secret keys:
   - `k8s-secrets/atlantis/github` → `github-token` (property: `token`)
   - `k8s-secrets/atlantis/webhook` → `webhook-secret` (property: `secret`)
   - `k8s-secrets/atlantis/aws` → `aws-access-key` (property: `access-key`)
   - `k8s-secrets/atlantis/aws` → `aws-secret-key` (property: `secret-key`)
   - `k8s-secrets/atlantis/infracost` → `infracost-api-key` (property: `api-key`)

**Testing:**
- [ ] `kubectl get externalsecret -n atlantis` shows SecretSynced
- [ ] `kubectl get secret atlantis-secrets -n atlantis` exists with all 5 keys

#### Task 3.4: Create Nginx Ingress for Atlantis
**Files:** `base-apps/atlantis/nginx-ingress.yaml`
**Steps:**
1. Create Ingress following established pattern (e.g., `base-apps/backstage/nginx-ingress.yaml`)
2. Host: `atlantis.arigsela.com`
3. TLS with cert-manager cluster-issuer `letsencrypt-prod`, secret name `atlantis-tls`
4. Annotations:
   - `cert-manager.io/cluster-issuer: "letsencrypt-prod"`
   - `nginx.ingress.kubernetes.io/ssl-redirect: "true"`
   - `nginx.ingress.kubernetes.io/force-ssl-redirect: "true"`
   - `nginx.ingress.kubernetes.io/whitelist-source-range: "73.7.190.154/32,170.85.56.189/32,170.85.130.202/32"`
   - `nginx.ingress.kubernetes.io/proxy-read-timeout: "600"` (Terraform plans can take time)
   - `nginx.ingress.kubernetes.io/proxy-connect-timeout: "60"`
   - `nginx.ingress.kubernetes.io/proxy-send-timeout: "600"`
5. Backend service: `atlantis` port `80` (Helm chart default service name)
6. IngressClassName: `nginx`

**Important note:** The IP whitelist blocks external access but GitHub webhooks originate from GitHub's IP ranges. Either:
- **Option A**: Add GitHub webhook IPs to the whitelist (GitHub publishes these via their meta API, but they change)
- **Option B**: Remove the whitelist and rely on Atlantis webhook secret validation for security
- **Option C**: Create a separate webhook ingress without IP whitelist, keep the UI ingress whitelisted

**Recommendation:** Option C — two ingress resources. One for the UI (whitelisted) and one for the `/events` webhook path (no whitelist, secured by webhook secret HMAC validation).

**Testing:**
- [ ] `kubectl get ingress -n atlantis` shows the ingress(es) with correct host
- [ ] TLS certificate issued by cert-manager
- [ ] `curl https://atlantis.arigsela.com` returns Atlantis UI (from whitelisted IP)
- [ ] GitHub webhooks reach `/events` endpoint

#### Task 3.5: Create NetworkPolicy for Atlantis Namespace
**Files:** `base-apps/atlantis/network-policy.yaml`
**Steps:**
1. Create a NetworkPolicy that restricts Atlantis pod egress to only required destinations:
   ```yaml
   apiVersion: networking.k8s.io/v1
   kind: NetworkPolicy
   metadata:
     name: atlantis-egress
     namespace: atlantis
   spec:
     podSelector:
       matchLabels:
         app: atlantis
     policyTypes:
       - Egress
     egress:
       # DNS resolution
       - to: []
         ports:
           - protocol: UDP
             port: 53
           - protocol: TCP
             port: 53
       # GitHub API (HTTPS)
       - to: []
         ports:
           - protocol: TCP
             port: 443
       # Vault (in-cluster)
       - to:
           - namespaceSelector:
               matchLabels:
                 kubernetes.io/metadata.name: vault
         ports:
           - protocol: TCP
             port: 8200
       # AWS APIs (HTTPS) — S3, RDS, IAM, KMS, Secrets Manager
       # Note: AWS API endpoints use HTTPS/443, already covered above
   ```
2. The policy allows:
   - **DNS** (port 53 UDP/TCP) — required for all name resolution
   - **HTTPS** (port 443) — covers GitHub API, AWS API endpoints, Infracost API
   - **Vault** (port 8200) — in-cluster Vault for ExternalSecret refresh
3. The policy blocks:
   - Direct access to other cluster workloads (pods in other namespaces)
   - Non-HTTPS egress to external services
   - Any protocol/port not explicitly listed

**Security note:** This NetworkPolicy works with your Istio ambient mesh. If using Istio's AuthorizationPolicy instead, adapt accordingly. The pod label selector (`app: atlantis`) must match the labels the Atlantis Helm chart applies — verify after deployment and adjust if needed.

**Testing:**
- [ ] `kubectl get networkpolicy -n atlantis` shows the policy
- [ ] Atlantis can still reach GitHub API (plans still post comments)
- [ ] Atlantis can still reach AWS APIs (terraform plan/apply works)
- [ ] Atlantis can still reach Vault (secrets refresh)
- [ ] Atlantis cannot reach other in-cluster services on non-standard ports

---

### Phase 4: Repository Configuration — atlantis.yaml

#### Task 4.1: Create Repo-Level Atlantis Configuration
**Files:** `atlantis.yaml` (repository root)
**Steps:**
1. Create `atlantis.yaml` defining the Terraform project:
   ```yaml
   version: 3
   automerge: false
   parallel_plan: true
   parallel_apply: false
   projects:
     - name: asela-cluster
       dir: terraform/roots/asela-cluster
       workspace: default
       terraform_version: v1.11.2
       autoplan:
         when_modified:
           - "*.tf"
           - "*.tfvars"
           - "../../modules/**/*.tf"
         enabled: true
       apply_requirements:
         - approved
         - mergeable
   ```
2. Key settings:
   - `autoplan.when_modified` includes shared modules path (`../../modules/**/*.tf`) so module changes trigger re-plan
   - `parallel_plan: true` for future multi-project support
   - `parallel_apply: false` for safety
   - `automerge: false` — require manual merge after apply

**Testing:**
- [ ] File validates as correct YAML
- [ ] Atlantis picks up the config (visible in Atlantis logs after push)

---

### Phase 5: GitHub Webhook Setup (Manual)

#### Task 5.1: Configure GitHub Repository Webhook
**Steps:**
1. Go to `github.com/arigsela/kubernetes/settings/hooks`
2. Click "Add webhook"
3. Configure:
   - **Payload URL**: `https://atlantis.arigsela.com/events`
   - **Content type**: `application/json`
   - **Secret**: The webhook secret stored in Vault (same value as `k8s-secrets/atlantis/webhook`)
   - **Events**: Select individual events:
     - `Pull request reviews`
     - `Pushes`
     - `Issue comments`
     - `Pull requests`
4. Save webhook
5. Verify the webhook shows a green checkmark (successful delivery)

**Testing:**
- [ ] Webhook delivery shows 200 response from Atlantis
- [ ] Opening a test PR that modifies a `.tf` file triggers an Atlantis plan comment

#### Task 5.2: Configure DNS Record
**Steps:**
1. Create an A/CNAME record in Route 53 for `atlantis.arigsela.com` pointing to your ingress IP/hostname
2. Verify DNS resolution: `dig atlantis.arigsela.com`

**Testing:**
- [ ] DNS resolves to the correct IP
- [ ] HTTPS works with valid certificate

---

### Phase 6: CI/CD Pipelines — GitHub Actions Workflows ✅ Complete (2026-03-30)

#### Task 6.1: Create Terraform Validation Workflow ✅
**Files:** `.github/workflows/terraform-validate.yaml`
**Steps:**
1. Create workflow triggered on PRs and pushes to main that modify `terraform/**` files
2. Jobs:
   - **changed-files**: Detect changed Terraform files (follow pattern from `validate.yaml`)
   - **terraform-fmt**: Run `terraform fmt -check -recursive` on terraform/ directory
   - **terraform-validate**: Run `terraform init -backend=false` + `terraform validate` on each root
   - **tflint**: Run TFLint for additional static analysis
   - **tfsec**: Run tfsec security scanner for OWASP/CIS compliance
3. Use `hashicorp/setup-terraform@v3` action for Terraform installation
4. Use `terraform-linters/setup-tflint@v4` for TFLint installation
5. Use `aquasecurity/tfsec-action@v1.0.3` for tfsec security scanning
6. Pin Terraform version to `1.11.2`

**tfsec configuration:**
- tfsec scans for security misconfigurations: public S3 buckets, unencrypted resources, overly permissive security groups, missing logging, etc.
- Run against the full `terraform/` directory (not just changed files) since security issues may pre-exist
- Set `--soft-fail` initially so it reports issues without blocking PRs (switch to hard-fail after existing issues are resolved)
- Output format: `sarif` for GitHub Code Scanning integration, or `lovely` for PR-readable output
- Example step:
  ```yaml
  - name: Run tfsec
    uses: aquasecurity/tfsec-action@v1.0.3
    with:
      working_directory: terraform/
      soft_fail: true
      format: lovely
  ```

**Testing:**
- [x] Workflow deployed via PR #132
- [x] `terraform fmt` check, `terraform validate`, TFLint, tfsec (soft-fail) all configured

#### Task 6.2: Create Infracost GitHub Actions Workflow ✅
**Files:** `.github/workflows/infracost.yaml`

**AWS credential strategy:** Infracost parses HCL and uses its Cloud Pricing API for cost lookups — it does **not** need real AWS credentials for static cost estimation. Use `--terraform-init-flags="-backend=false"` to skip backend initialization (which would require S3 access). This means the GHA workflow only needs `INFRACOST_API_KEY`, not AWS credentials.

**Steps:**
1. Create workflow triggered on PRs that modify `terraform/**` files
2. Jobs:
   - **infracost**: Run cost estimation and post PR comment
3. Steps:
   - Checkout code (both PR branch and base branch)
   - Install Infracost CLI via `infracost/actions/setup@v3`
   - Generate Infracost baseline from main branch:
     ```bash
     git checkout ${{ github.event.pull_request.base.sha }}
     infracost breakdown \
       --path=terraform/roots/asela-cluster \
       --terraform-init-flags="-backend=false" \
       --format=json \
       --out-file=/tmp/infracost-base.json
     ```
   - Generate Infracost diff from PR branch:
     ```bash
     git checkout ${{ github.event.pull_request.head.sha }}
     infracost diff \
       --path=terraform/roots/asela-cluster \
       --terraform-init-flags="-backend=false" \
       --compare-to=/tmp/infracost-base.json \
       --format=json \
       --out-file=/tmp/infracost.json
     ```
   - Post comment:
     ```bash
     infracost comment github \
       --path=/tmp/infracost.json \
       --repo=$GITHUB_REPOSITORY \
       --pull-request=${{ github.event.pull_request.number }} \
       --behavior=update \
       --github-token=${{ secrets.GITHUB_TOKEN }}
     ```
4. Required secrets: `INFRACOST_API_KEY` (stored as GitHub Actions secret)
5. **No AWS credentials needed** — Infracost parses HCL statically and looks up prices via its Cloud API

**Pre-requisite:** Add `INFRACOST_API_KEY` as a repository secret in GitHub (Settings > Secrets and variables > Actions)

**Limitation:** Static HCL parsing may not resolve dynamic values (e.g., `count` from data sources, conditional resources). For these edge cases, the Atlantis-based Infracost step (which runs against the actual Terraform plan) will provide accurate costs.

**Testing:**
- [x] Workflow deployed via PR #132
- [x] No AWS credentials needed — uses --terraform-init-flags="-backend=false"

#### Task 6.3: Add GitHub Actions Secret for Infracost ✅
**Steps:**
1. Go to `github.com/arigsela/kubernetes/settings/secrets/actions`
2. Add new repository secret:
   - Name: `INFRACOST_API_KEY`
   - Value: Same API key stored in Vault at `k8s-secrets/atlantis/infracost`

**Testing:**
- [x] `INFRACOST_API_KEY` added to GitHub Actions secrets

---

### Phase 7: Testing and Validation

#### Task 7.1: End-to-End Atlantis Test
**Steps:**
1. Create a test branch:
   ```bash
   git checkout -b test/atlantis-validation
   ```
2. Make a minor Terraform change (e.g., add a tag to an existing resource in `rds.tf`)
3. Open a PR to `main`
4. Verify:
   - Atlantis automatically runs `terraform plan` and posts output as PR comment
   - Infracost cost estimate appears as a separate PR comment (from Atlantis workflow)
   - Infracost GitHub Actions workflow also posts a cost comment
   - Terraform validation workflow passes (fmt, validate, tflint)
   - `atlantis apply` is blocked until PR is approved
5. Approve the PR
6. Comment `atlantis apply` — verify apply succeeds
7. Merge the PR
8. Revert the test change in a follow-up PR

#### Task 7.2: Verify Lock Behavior
**Steps:**
1. Open two PRs that modify the same Terraform root (`terraform/roots/asela-cluster/`)
2. Verify Atlantis plans the first PR successfully
3. Verify the second PR shows a lock conflict message
4. Apply and merge the first PR
5. Re-plan the second PR — verify it now plans without conflict

#### Task 7.3: Verify Security Controls
**Steps:**
1. Verify `atlantis apply` is blocked without PR approval
2. Verify webhook secret validation (send a test webhook with wrong secret — should be rejected)
3. Verify IP whitelist on Atlantis UI (access from non-whitelisted IP should be blocked)
4. Verify no secrets are exposed in plan output

**Testing:**
- [ ] Full plan → approve → apply → merge cycle works
- [ ] Lock contention handled correctly
- [ ] Security controls enforced
- [ ] All CI workflows pass

#### Task 7.4: Document Atlantis Operations Runbook
**Steps:**
1. Add an operational reference section to this plan (or a separate runbook) covering common Atlantis operations:

**Atlantis PR Commands Reference:**

| Command | Description |
|---------|-------------|
| `atlantis plan` | Re-run plan for all projects in this PR |
| `atlantis plan -d terraform/roots/asela-cluster` | Plan a specific project directory |
| `atlantis plan -- -var="key=value"` | Plan with extra Terraform variables |
| `atlantis apply` | Apply all planned projects |
| `atlantis apply -d terraform/roots/asela-cluster` | Apply a specific project |
| `atlantis unlock` | Release all locks held by this PR without applying |

**Handling Abandoned PRs with Locks:**
If a PR is closed or abandoned while Atlantis holds a lock on a project:
1. **Preferred**: Comment `atlantis unlock` on the PR before closing it
2. **If PR is already closed**: Navigate to the Atlantis UI at `https://atlantis.arigsela.com` and manually release the lock from the Locks page
3. **Emergency**: If Atlantis is down, locks are stored in BoltDB on the PVC. Restarting the pod and using the UI is the safest approach

**Emergency Local Terraform Access:**
If Atlantis is unavailable and an urgent infrastructure change is needed:
1. Ensure no Atlantis locks exist for the target project (check UI or wait for pod recovery)
2. Run Terraform locally using personal AWS credentials
3. Document the out-of-band change in a GitHub issue
4. Once Atlantis recovers, open a PR with a no-op change to re-sync Atlantis state

**Testing:**
- [ ] Runbook covers lock management, emergency procedures, and common commands

---

## Files Summary

| File | Type | Purpose |
|------|------|---------|
| `terraform/roots/asela-cluster/atlantis-iam.tf` | Terraform | IAM user + policy for Atlantis AWS access |
| `base-apps/atlantis.yaml` | ArgoCD Application | Atlantis Helm chart deployment |
| `base-apps/atlantis/secret-store.yaml` | SecretStore | Vault backend for atlantis namespace |
| `base-apps/atlantis/external-secrets.yaml` | ExternalSecret | Syncs GitHub/AWS/Infracost secrets from Vault |
| `base-apps/atlantis/nginx-ingress.yaml` | Ingress | TLS ingress with IP whitelist + webhook path |
| `base-apps/atlantis/network-policy.yaml` | NetworkPolicy | Restricts Atlantis egress to GitHub/AWS/Vault only |
| `atlantis.yaml` | Atlantis Config | Repo-level project definition and autoplan rules |
| `.github/workflows/terraform-validate.yaml` | GitHub Actions | Terraform fmt, validate, tflint, tfsec on PRs |
| `.github/workflows/infracost.yaml` | GitHub Actions | Infracost cost estimation on PRs |

## Vault Secrets Reference

| Vault Path | Properties | Used By |
|------------|-----------|---------|
| `k8s-secrets/atlantis/github` | `token` | Atlantis (GitHub API + PR comments) |
| `k8s-secrets/atlantis/webhook` | `secret` | Atlantis (webhook HMAC validation) |
| `k8s-secrets/atlantis/aws` | `access-key`, `secret-key` | Atlantis (Terraform AWS provider) |
| `k8s-secrets/atlantis/infracost` | `api-key` | Atlantis + GitHub Actions (cost API) |

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Atlantis server compromise exposes AWS credentials | Resource-scoped IAM policy (no wildcard resources), Vault-managed secrets, IP-whitelisted UI, NetworkPolicy restricting egress |
| Malicious `.tf` in PR runs code on Atlantis server | `allow_custom_workflows: false`, `orgAllowlist` restricts to own repos, NetworkPolicy limits blast radius |
| Webhook IP whitelist blocks GitHub webhook delivery | Separate ingress for `/events` path without IP whitelist, secured by HMAC |
| Plan output leaks sensitive values | Mark sensitive outputs in Terraform, review plan comments |
| Atlantis downtime blocks all Terraform operations | GitHub Actions Infracost still works; emergency: run Terraform locally |
| IAM access key in Terraform state | State encrypted in S3, access key rotatable, monitor with CloudTrail |
| Stale plans on long-lived PRs | Atlantis re-plans on new commits; team process: re-plan before apply |

## Future Enhancements (Out of Scope)

1. **Migrate to GitHub App** — Better rate limits and permissions model
2. **Conftest/OPA policies** — Enforce Terraform guardrails (e.g., require tags, block public resources)
3. **Drift detection CronJob** — Scheduled `terraform plan` to detect out-of-band changes
4. **Multiple Terraform roots** — Add more projects to `atlantis.yaml` as infrastructure grows
5. **Slack notifications** — Post Atlantis events to a Slack channel
6. **Redis locking** — If scaling to multiple Atlantis replicas

## References

- [Atlantis Documentation](https://www.runatlantis.io/docs/)
- [Atlantis Helm Chart (ArtifactHub)](https://artifacthub.io/packages/helm/atlantis/atlantis)
- [Atlantis Server-Side Repo Config](https://www.runatlantis.io/docs/server-side-repo-config)
- [Atlantis Custom Workflows](https://www.runatlantis.io/docs/custom-workflows)
- [Infracost Atlantis Integration](https://github.com/infracost/infracost-atlantis)
- [Infracost GitHub Actions](https://github.com/infracost/actions)
- [Infracost CLI Documentation](https://www.infracost.io/docs/)
