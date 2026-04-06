# Base Applications Directory

This directory contains ArgoCD Application manifests and their corresponding Kubernetes resources. The `master-app` ApplicationSet automatically discovers and deploys any `.yaml` file in this directory.

## Active Applications

### Production Applications
- **chores-tracker-backend** - FastAPI backend with MySQL RDS integration
- **chores-tracker-frontend** - Node.js frontend application
- **mysql-rds-backup** - Automated daily backups to S3

### Infrastructure Components
- **vault** - Secret management with Kubernetes authentication
- **external-secrets** - Vault-to-Kubernetes secret synchronization
- **crossplane-aws-provider** - AWS resource provisioning (S3, IAM)
- **crossplane-mysql-provider** - MySQL user/database management
- **cert-manager** - TLS certificate automation via LetsEncrypt
- **nginx-ingress** - HTTP/HTTPS ingress controller
- **loki-aws-infrastructure** - Loki S3 bucket and IAM setup
- **logging** - Loki/Prometheus/Grafana observability stack
- **ecr-auth** - ECR credential synchronization CronJob
- **whoami-test** - Ingress testing application

### Terraform CI/CD
- **atlantis** - PR-based Terraform plan/apply with Infracost cost estimation
  - Helm chart v6.1.0 (app version v0.40.0)
  - Image: `infracost/infracost-atlantis:atlantis0.40-infracost0.10`
  - Accessible at `atlantis.arigsela.com`
  - Secrets managed via Vault + External Secrets Operator

### Policy Engine (Kyverno)
- **kyverno** - Kubernetes policy engine (Helm chart v3.7.1) for validating, mutating, and generating resources
- **kyverno-policies** - Custom ClusterPolicies:
  - `inject-ecr-pull-secret` - Automatically injects `imagePullSecrets` into pods referencing ECR images
  - `generate-ecr-secret` - Clones `ecr-registry` secret into new namespaces on creation
  - `require-labels` - Requires `app.kubernetes.io/name` on Deployments/StatefulSets (Audit)
  - `disallow-privileged-containers` - Audits privileged containers
  - `require-resource-limits` - Requires CPU/memory limits on all containers (Audit)
  - `disallow-default-namespace` - Audits workloads in the default namespace
  - `disallow-latest-tag` - Audits containers using `:latest` or untagged images

### Service Mesh (Istio Ambient)
- **istio-gateway-api** - Kubernetes Gateway API CRDs (sync-wave: -3)
- **istio-base** - Istio CRDs and cluster resources (sync-wave: -2)
- **istio-istiod** - Control plane with ambient profile (sync-wave: -1)
- **istio-cni** - CNI plugin for K3s traffic interception (sync-wave: 0)
- **istio-ztunnel** - L4 proxy DaemonSet for mTLS and TCP metrics (sync-wave: 1)
- **istio-ambient-config** - Namespace enrollment and waypoint proxies (sync-wave: 2)

## Disabled Applications

The following applications are disabled (`.yaml.disabled`) and not deployed:
- **n8n** - Workflow automation platform
- **postgresql** - PostgreSQL database
- **oncall-agent** - Requires Anthropic API tokens
- **k8s-monitor** - Requires Anthropic API tokens

## Directory Structure

Each application follows this structure:
```
base-apps/
├── app-name.yaml              # ArgoCD Application manifest
└── app-name/                  # Kubernetes resources
    ├── deployments.yaml
    ├── services.yaml
    ├── nginx-ingress.yaml
    ├── configmaps.yaml
    ├── secret-store.yaml      # Vault SecretStore config
    ├── external-secret.yaml   # ExternalSecret resources
    └── crossplane_resources.yaml  # Crossplane managed resources
```

## Adding a New Application

1. Create application directory:
   ```bash
   mkdir -p base-apps/my-app
   ```

2. Add Kubernetes manifests to the directory
   - **No `imagePullSecrets` needed** for ECR images — Kyverno injects them automatically

3. Create ArgoCD Application:
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

4. Commit and push - ArgoCD will auto-deploy

## Disabling an Application

To disable an application without removing it:
```bash
git mv base-apps/app-name.yaml base-apps/app-name.yaml.disabled
git commit -m "chore: disable app-name"
git push origin main
```

ArgoCD will automatically remove the application from the cluster.

## Secret Management

Applications use Vault for secret storage with External Secrets Operator for synchronization:

1. **SecretStore** - Configures Vault connection for the namespace
2. **ExternalSecret** - Maps Vault secrets to Kubernetes secrets
3. **Refresh Interval** - Secrets sync every 1 hour

Example:
```yaml
# secret-store.yaml
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
          role: "my-app"

---
# external-secret.yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: my-app-secrets
  namespace: my-app
spec:
  refreshInterval: "1h"
  secretStoreRef:
    name: vault-backend
  target:
    name: my-app-secrets
  data:
  - secretKey: api-key
    remoteRef:
      key: my-app
      property: api-key
```

## ECR Authentication

ECR image pull authentication is fully automated via Kyverno and a CronJob:

```
New Namespace Created ──▶ Kyverno clones ecr-registry secret instantly
Pod with ECR Image    ──▶ Kyverno injects imagePullSecrets automatically
Every Hour            ──▶ CronJob refreshes ECR tokens in all namespaces
```

**No manual steps required.** When deploying a new application that pulls from ECR:
1. Create your deployment manifests (no `imagePullSecrets` needed)
2. Create the ArgoCD Application with `CreateNamespace=true`
3. Commit and push — Kyverno handles the rest

### How It Works
- **`generate-ecr-secret`** (Kyverno ClusterPolicy) — Clones the `ecr-registry` secret from `kube-system` into any new namespace on creation
- **`inject-ecr-pull-secret`** (Kyverno ClusterPolicy) — Mutates pods that reference `.dkr.ecr.` images to add `imagePullSecrets: [{name: ecr-registry}]`
- **`ecr-credentials-sync`** (CronJob) — Runs hourly, refreshes ECR tokens in all non-system namespaces via dynamic discovery

## Ingress Configuration

Applications use nginx-ingress with these patterns:

### Backend API (High Priority)
```yaml
nginx.ingress.kubernetes.io/priority: "100"
nginx.ingress.kubernetes.io/rewrite-target: /api/$1
path: /api/(.*)
```

### Frontend (Standard Priority)
```yaml
nginx.ingress.kubernetes.io/priority: "50"
path: /
```

## Istio Ambient Mesh

### Enrolling a Namespace in the Mesh

To add a namespace to the Istio Ambient mesh (L4 mTLS):

```yaml
# Add to base-apps/istio-ambient-config/namespace-labels.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: my-namespace
  labels:
    istio.io/dataplane-mode: ambient
```

### Adding L7 (HTTP) Processing

To enable L7 metrics and policies, add a waypoint proxy:

```yaml
# Add to base-apps/istio-ambient-config/waypoint-proxy.yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: my-namespace-waypoint
  namespace: my-namespace
  labels:
    istio.io/waypoint-for: all
spec:
  gatewayClassName: istio-waypoint
  listeners:
    - name: mesh
      port: 15008
      protocol: HBONE
      allowedRoutes:
        namespaces:
          from: Same
```

Then reference the waypoint in the namespace:

```yaml
metadata:
  labels:
    istio.io/dataplane-mode: ambient
    istio.io/use-waypoint: my-namespace-waypoint
```

### Viewing Mesh Metrics

- **Grafana Dashboard**: Access the "Istio Ambient Mesh" dashboard in the Istio folder
- **L4 Metrics**: TCP connections, bytes sent/received (from ztunnel)
- **L7 Metrics**: HTTP request rate, latency, success rate (from waypoint)

## Kyverno Policy Management

### Viewing Policy Reports
```bash
# View policy reports across all namespaces
kubectl get policyreports -A

# View cluster-wide policy reports
kubectl get clusterpolicyreports

# Detailed report for a specific namespace
kubectl describe policyreport -n <namespace>
```

### Adding a New Policy
1. Create a `ClusterPolicy` YAML file in `base-apps/kyverno-policies/`
2. Set `validationFailureAction: Audit` initially
3. Exclude system namespaces (`kube-system`, `argo-cd`, `kyverno`)
4. Commit and push — ArgoCD will auto-deploy the policy

### Moving a Policy from Audit to Enforce
1. Edit the policy file in `base-apps/kyverno-policies/`
2. Change `validationFailureAction: Audit` to `validationFailureAction: Enforce`
3. Commit and push — the policy will now block non-compliant resources

### Namespace Exclusions
All policies exclude `kube-system`, `argo-cd`, and `kyverno` namespaces to avoid interfering with critical system components. The Kyverno webhook is also configured with `failurePolicy: Ignore` to prevent cluster disruption.

## Atlantis (Terraform PR Automation)

Atlantis automates Terraform `plan` and `apply` via PR comments. It is deployed as an ArgoCD Helm application.

### How It Works

1. Developer opens a PR that modifies `terraform/**` files
2. Atlantis auto-runs `terraform plan` and posts the output as a PR comment
3. Infracost (baked into the Atlantis image) can estimate cost impact
4. After PR approval, run `atlantis apply` via PR comment to apply changes
5. Apply is blocked until the PR is approved and mergeable

### PR Commands

| Command | Description |
|---------|-------------|
| `atlantis plan` | Re-run plan for all projects |
| `atlantis plan -p asela-cluster` | Plan a specific project |
| `atlantis apply` | Apply all planned projects |
| `atlantis apply -p asela-cluster` | Apply a specific project |
| `atlantis unlock` | Release all project locks |

### Configuration

- **Repo config**: `atlantis.yaml` (root of repo) defines projects, autoplan triggers, and apply requirements
- **Server config**: `base-apps/atlantis.yaml` (ArgoCD Application) manages the Helm deployment
- **Secrets**: `base-apps/atlantis/external-secrets.yaml` syncs from Vault:
  - `atlantis-vcs` — GitHub token + webhook secret
  - `atlantis-env` — AWS credentials, Infracost API key, Kubernetes provider vars

### Adding a New Terraform Root

1. Add the project to `atlantis.yaml`:
   ```yaml
   - name: my-project
     dir: terraform/roots/my-project
     workspace: default
     autoplan:
       when_modified:
         - "**/*.tf"
         - "**/*.tfvars"
         - "../../modules/**/*.tf"
       enabled: true
     apply_requirements:
       - approved
       - mergeable
   ```
2. Ensure the Atlantis IAM user has permissions for the new root's AWS region/resources
3. Store any required credentials in Vault and update `external-secrets.yaml`

### Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   GitHub     │     │   Atlantis   │     │   Terraform  │     │     AWS      │
│   Webhook    │ ──▶ │   Pod (K8s)  │ ──▶ │  Plan/Apply  │ ──▶ │  Resources   │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
                            │
                     ┌──────▼──────┐
                     │    Vault    │
                     │  (secrets)  │
                     └─────────────┘
```

### Infracost (Cost Estimation)

Infracost runs as a GitHub Actions workflow (`.github/workflows/infracost.yaml`) on every PR that modifies `terraform/**` files. It posts a cost diff comment showing the monthly cost impact of changes.

The Infracost CLI is also available inside the Atlantis pod via the `infracost-atlantis` Docker image, using the free Cloud Pricing API (1,000 runs/month).

## Troubleshooting

### Application Not Deploying
1. Check ArgoCD application status: `kubectl get applications -n argo-cd`
2. Verify manifests are valid: `kubectl apply --dry-run=client -f base-apps/app-name/`
3. Check ArgoCD logs for sync errors

### Secrets Not Syncing
1. Verify ExternalSecret status: `kubectl get externalsecret -n namespace`
2. Check SecretStore connection: `kubectl describe secretstore -n namespace`
3. Validate Vault path and permissions

### Atlantis Plan Failures
1. Check Atlantis pod logs: `kubectl logs -n atlantis deploy/atlantis`
2. Verify secrets are synced: `kubectl get externalsecret -n atlantis`
3. Check project lock status: visit `https://atlantis.arigsela.com`
4. Ensure `atlantis.yaml` project dir matches the terraform root path
5. For "No value for required variable" errors: check that all variables have defaults or are injected via `TF_VAR_*` env vars

### ECR Pull Failures
1. Check if ecr-registry secret exists: `kubectl get secret ecr-registry -n namespace`
2. Verify Kyverno injected imagePullSecrets: `kubectl get pod <pod> -n <ns> -o jsonpath='{.spec.imagePullSecrets}'`
3. Check ECR CronJob status: `kubectl get cronjob -n kube-system ecr-credentials-sync`
4. Manually trigger token refresh: `kubectl create job --from=cronjob/ecr-credentials-sync ecr-manual -n kube-system`
5. Check Kyverno logs: `kubectl logs -n kyverno -l app.kubernetes.io/component=admission-controller --tail=50 | grep ecr`

---

*Last Updated: 2026-04-06*
