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

### Policy Engine (Kyverno)
- **kyverno** - Kubernetes policy engine (Helm chart v3.7.1) for validating, mutating, and generating resources
- **kyverno-policies** - Custom ClusterPolicies in Audit mode:
  - `require-labels` - Requires `app.kubernetes.io/name` on Deployments/StatefulSets
  - `disallow-privileged-containers` - Audits privileged containers
  - `require-resource-limits` - Requires CPU/memory limits on all containers
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

Applications pulling from AWS ECR need the `ecr-registry` imagePullSecret. This is automatically created by the ECR auth CronJob in the following namespaces:
- `chores-tracker`
- `chores-tracker-frontend`
- `mysql`

To add ECR auth to a new namespace, update `/base-apps/ecr-auth/cronjobs.yaml` line 47.

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

## Troubleshooting

### Application Not Deploying
1. Check ArgoCD application status: `kubectl get applications -n argo-cd`
2. Verify manifests are valid: `kubectl apply --dry-run=client -f base-apps/app-name/`
3. Check ArgoCD logs for sync errors

### Secrets Not Syncing
1. Verify ExternalSecret status: `kubectl get externalsecret -n namespace`
2. Check SecretStore connection: `kubectl describe secretstore -n namespace`
3. Validate Vault path and permissions

### ECR Pull Failures
1. Check if ecr-registry secret exists: `kubectl get secret ecr-registry -n namespace`
2. Verify ECR auth CronJob runs: `kubectl get cronjob -n kube-system ecr-credentials-sync`
3. Check namespace is in the sync list: `/base-apps/ecr-auth/cronjobs.yaml`

---

*Last Updated: 2026-03-06*
