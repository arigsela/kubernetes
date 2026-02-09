# DevOps Maturity Implementation Plan

## Executive Summary

This plan addresses gaps identified in a DevOps architecture review of the Kubernetes GitOps platform. The platform demonstrates strong GitOps discipline, enterprise-grade secret management (Vault + External Secrets), and solid disaster recovery. However, it lacks pre-merge validation, environment parity, and progressive delivery capabilities.

**Overall Maturity: Stage 3-4 (Continuous Integration / Continuous Delivery)**

### Current Strengths

| Area | Detail |
|------|--------|
| **Secret Management** | Vault + External Secrets with per-namespace isolation, Kubernetes auth, KMS auto-unseal |
| **GitOps Discipline** | All changes flow through Git with ArgoCD auto-sync, prune, and self-heal |
| **Disaster Recovery** | Velero backups, comprehensive recovery scripts, documented runbooks |
| **Infrastructure as Code** | Terraform for AWS resources, Crossplane for in-cluster provisioning |
| **Observability** | Prometheus + Loki + Grafana + Alloy stack with Istio dashboards |

### Key Gaps

| Gap | Impact | Phase |
|-----|--------|-------|
| No CI validation pipeline | Misconfigurations deploy unchecked | Phase 1 |
| No branch protection | No review gates or audit trail | Phase 1 |
| No staging environment | All changes hit production directly | Phase 2 |
| No autoscaling (HPA) | Can't respond to load changes | Phase 2 |
| No security scanning | Vulnerable images deploy undetected | Phase 3 |
| No admission policies | No cluster-wide guardrails | Phase 3 |
| No progressive delivery | Releases are all-or-nothing | Phase 4 |
| No network policies | Unrestricted pod-to-pod traffic | Phase 3 |

---

## 12-Factor Compliance Scorecard

| # | Factor | Status | Finding |
|---|--------|--------|---------|
| I | Codebase | **+** | Single repo for infrastructure, app source repos separate. Clear 1:1 mapping. |
| II | Dependencies | **+** | Helm charts with pinned versions, Terraform modules, explicit image tags. |
| III | Config | **+** | ConfigMaps and Vault ExternalSecrets, separated from code. Env vars via `envFrom`. |
| IV | Backing Services | **+** | RDS, Vault, PostgreSQL, S3 all treated as attached resources. |
| V | Build, Release, Run | **~** | Build happens in app repos. No distinct versioned release artifacts. |
| VI | Processes | **+** | Stateless pods with external state in RDS/Vault/S3. |
| VII | Port Binding | **+** | All services expose via ClusterIP + Ingress. |
| VIII | Concurrency | **~** | Fixed replica counts (1-2). No HPA configured. |
| IX | Disposability | **+** | Health probes on most apps. Containers are ephemeral. |
| X | Dev/Prod Parity | **-** | Single environment (production only). No staging or dev. |
| XI | Logs | **+** | Loki + Alloy collecting logs. Prometheus for metrics. Grafana dashboards. |
| XII | Admin Processes | **~** | Recovery scripts and CronJobs exist. No migration framework. |

**Legend:** `+` Fully adopted | `~` Partially adopted | `-` Not adopted

**Score: 8/12 fully adopted, 3 partial, 1 gap**

---

## DevOps Maturity Scorecard

| Stage | Status | Finding |
|-------|--------|---------|
| Agile Practices | **~** | Version control strong. Single-operator limits collaboration. Good docs and runbooks. |
| Lean Practices | **~** | Incremental improvements visible. No DORA metrics. No retrospective cadence. |
| Continuous Integration | **~** | Infrastructure is cattle-not-pets. No automated tests, linting, or policy checks. |
| Continuous Delivery | **~** | ArgoCD provides automated pipeline. Everything-as-code. No security scanning. |
| Continuous Deployment | **~** | ArgoCD auto-syncs to production. IaC via Terraform + Crossplane. No feature toggles. |
| Continuous Operations | **~** | Istio mesh for mTLS. Velero backups. No canary/blue-green. Fixed scaling. |

---

## Prerequisites

- [ ] GitHub repository admin access (for branch protection rules)
- [ ] GitHub Actions enabled on the repository
- [ ] `kubectl` access to the cluster (for HPA and policy deployment)
- [x] Verify ArgoCD master-app `targetRevision` is set to `main` (updated in `terraform/modules/application-sets/application-sets.tf`)

---

## Phase 1: CI Validation & Branch Protection

**Goal:** Prevent misconfigurations from reaching production.
**Effort:** < 1 week
**Impact:** HIGH

### 1.1 GitHub Actions CI Pipeline

**File:** `.github/workflows/validate.yaml`

```yaml
name: Validate Manifests

on:
  pull_request:
    branches: [main]

jobs:
  yaml-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install yamllint
        run: pip install yamllint
      - name: Lint YAML files
        run: yamllint -c .yamllint.yaml base-apps/

  kubernetes-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install kubeconform
        run: |
          curl -sL https://github.com/yannh/kubeconform/releases/latest/download/kubeconform-linux-amd64.tar.gz | tar xz
          sudo mv kubeconform /usr/local/bin/
      - name: Validate Kubernetes manifests
        run: |
          find base-apps -name '*.yaml' -not -path '*/charts/*' | xargs kubeconform \
            -summary \
            -strict \
            -ignore-missing-schemas \
            -kubernetes-version 1.29.0

  ingress-policy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Check ingress IP whitelist
        run: |
          FAILED=0
          for f in $(grep -rl "kind: Ingress" base-apps/ --include="*.yaml"); do
            if ! grep -q 'whitelist-source-range' "$f"; then
              echo "FAIL: $f missing IP whitelist annotation"
              FAILED=1
            fi
          done
          exit $FAILED
```

### 1.2 Yamllint Configuration

**File:** `.yamllint.yaml`

```yaml
extends: default
rules:
  line-length:
    max: 200
  truthy:
    check-keys: false
  comments:
    min-spaces-from-content: 1
  document-start: disable
```

### 1.3 Branch Protection

Enable on GitHub (`Settings > Branches > Branch protection rules` for `main`):

- [x] Require a pull request before merging
- [x] Require status checks to pass (yaml-lint, kubernetes-validate)
- [x] Do not allow bypassing the above settings
- [ ] Require approvals (optional for solo operator)

### 1.4 Fix ArgoCD Target Revision

The master-app Terraform module currently points to `fix/cni-recovery`. Update to `main`:

**File:** `terraform/modules/application-sets/application-sets.tf`

```hcl
# Change targetRevision from "fix/cni-recovery" to "main"
targetRevision = "main"
```

### Acceptance Criteria

- [x] PRs to `main` trigger CI validation (`.github/workflows/validate.yaml` created)
- [x] Malformed YAML fails the pipeline (`yaml-lint` job configured)
- [x] Invalid Kubernetes manifests fail the pipeline (`kubernetes-validate` job with kubeconform configured)
- [x] Ingresses without IP whitelist fail the pipeline (`ingress-policy` job configured)
- [ ] Direct pushes to `main` are blocked (requires manual GitHub branch protection setup -- see section 1.3)

---

## Phase 2: Environment Parity & Autoscaling

**Goal:** Test changes before production and handle load dynamically.
**Effort:** 1-4 weeks
**Impact:** HIGH

### 2.1 Staging Namespace via Kustomize Overlays

Create a staging overlay that reuses production manifests with reduced resources.

**Directory structure:**

```text
base-apps/chores-tracker-backend/
├── base/                          # Current production manifests (move here)
│   ├── deployments.yaml
│   ├── services.yaml
│   ├── nginx-ingress.yaml
│   ├── configmaps.yaml
│   ├── secret-store.yaml
│   └── external-secrets.yaml
├── overlays/
│   ├── production/
│   │   └── kustomization.yaml    # production patches (replica count, resources)
│   └── staging/
│       └── kustomization.yaml    # staging patches (1 replica, reduced resources, staging hostname)
└── kustomization.yaml
```

**Example staging overlay** (`overlays/staging/kustomization.yaml`):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: chores-tracker-staging
bases:
  - ../../base
patches:
  - patch: |
      apiVersion: apps/v1
      kind: Deployment
      metadata:
        name: chores-tracker-backend
      spec:
        replicas: 1
        template:
          spec:
            containers:
              - name: chores-tracker-backend
                resources:
                  requests:
                    memory: "128Mi"
                    cpu: "50m"
                  limits:
                    memory: "256Mi"
                    cpu: "200m"
```

**ArgoCD Application for staging** (`base-apps/chores-tracker-backend-staging.yaml`):

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: chores-tracker-backend-staging
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/chores-tracker-backend/overlays/staging
  destination:
    server: https://kubernetes.default.svc
    namespace: chores-tracker-staging
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

> **Note:** The staging namespace will need to be added to the ECR auth CronJob loop in `base-apps/ecr-auth/cronjobs.yaml` and a Vault role configured for `chores-tracker-staging`.

### 2.2 Horizontal Pod Autoscaler

Add HPA for user-facing workloads. Start with chores-tracker-backend.

**File:** `base-apps/chores-tracker-backend/hpa.yaml`

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: chores-tracker-backend
  namespace: chores-tracker
spec:
  scaleTargetRef:
    apiVersion: argoproj.io/v1alpha1
    kind: Rollout
    name: chores-tracker-backend
  minReplicas: 2
  maxReplicas: 5
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
        - type: Pods
          value: 1
          periodSeconds: 60
```

> **Prerequisite:** Ensure `metrics-server` is deployed (standard in k3s). Deployments must have `resources.requests` set for CPU and memory.

### 2.3 Resource Requests and Limits Audit

Verify all deployments have resource requests/limits defined. Add them where missing:

```yaml
resources:
  requests:
    memory: "128Mi"
    cpu: "100m"
  limits:
    memory: "512Mi"
    cpu: "500m"
```

Applications to audit:
- [ ] chores-tracker-backend
- [ ] chores-tracker-frontend
- [ ] agent-ui-backend
- [ ] agent-ui-frontend
- [ ] n8n
- [ ] k8s-monitor

### Acceptance Criteria

- [ ] Staging namespace deploys a reduced-resource copy of chores-tracker
- [ ] Changes can be tested in staging before promoting to production
- [ ] HPA scales chores-tracker-backend between 2-5 replicas based on CPU
- [ ] All user-facing deployments have resource requests and limits

---

## Phase 3: Security Hardening

**Goal:** Enforce security policies and detect vulnerabilities.
**Effort:** 1-4 weeks
**Impact:** MEDIUM

### 3.1 Kyverno Admission Controller

Deploy Kyverno with baseline policies via ArgoCD.

**File:** `base-apps/kyverno.yaml`

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: kyverno
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://kyverno.github.io/kyverno
    chart: kyverno
    targetRevision: 3.x.x
    helm:
      values: |
        replicaCount: 1
        nodeSelector:
          node.kubernetes.io/workload: infrastructure
        tolerations:
          - key: node-role.kubernetes.io/control-plane
            operator: Exists
            effect: NoSchedule
  destination:
    server: https://kubernetes.default.svc
    namespace: kyverno
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

### 3.2 Baseline Kyverno Policies

**File:** `base-apps/kyverno/policies.yaml`

```yaml
# Policy 1: Require resource limits on all pods
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: require-resource-limits
spec:
  validationFailureAction: Audit  # Start with Audit, move to Enforce later
  rules:
    - name: check-resource-limits
      match:
        any:
          - resources:
              kinds:
                - Pod
      validate:
        message: "CPU and memory resource limits are required."
        pattern:
          spec:
            containers:
              - resources:
                  limits:
                    memory: "?*"
                    cpu: "?*"
---
# Policy 2: Deny privileged containers
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: deny-privileged
spec:
  validationFailureAction: Enforce
  rules:
    - name: deny-privileged-containers
      match:
        any:
          - resources:
              kinds:
                - Pod
      exclude:
        any:
          - resources:
              namespaces:
                - kube-system
                - istio-system
                - istio-cni
      validate:
        message: "Privileged containers are not allowed."
        pattern:
          spec:
            containers:
              - securityContext:
                  privileged: "!true"
---
# Policy 3: Require labels
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: require-app-label
spec:
  validationFailureAction: Audit
  rules:
    - name: check-app-label
      match:
        any:
          - resources:
              kinds:
                - Deployment
      validate:
        message: "The label 'app' is required on Deployments."
        pattern:
          metadata:
            labels:
              app: "?*"
```

### 3.3 Default-Deny Network Policies

Apply per namespace. Example for chores-tracker:

**File:** `base-apps/chores-tracker-backend/network-policy.yaml`

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-all
  namespace: chores-tracker
spec:
  podSelector: {}
  policyTypes:
    - Ingress
    - Egress
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-chores-tracker-traffic
  namespace: chores-tracker
spec:
  podSelector:
    matchLabels:
      app: chores-tracker-backend
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: nginx-ingress
      ports:
        - protocol: TCP
          port: 8000
  egress:
    # DNS
    - to: []
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    # RDS MySQL
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
      ports:
        - protocol: TCP
          port: 3306
    # Vault
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: vault
      ports:
        - protocol: TCP
          port: 8200
```

### 3.4 Image Scanning in App CI

Add to application source repo CI pipelines (e.g., `arigsela/chores-tracker`):

```yaml
# Add to existing GitHub Actions workflow
- name: Scan image with Trivy
  uses: aquasecurity/trivy-action@master
  with:
    image-ref: ${{ env.ECR_REGISTRY }}/chores-tracker-backend:${{ env.IMAGE_TAG }}
    format: 'table'
    exit-code: '1'
    severity: 'CRITICAL,HIGH'
```

### Acceptance Criteria

- [ ] Kyverno deployed and reporting policy violations in Audit mode
- [ ] Privileged container policy is in Enforce mode
- [ ] Default-deny NetworkPolicy applied to chores-tracker namespace
- [ ] Trivy scanning integrated in at least one app CI pipeline

---

## Phase 4: Progressive Delivery

**Goal:** Enable canary deployments leveraging the existing Istio ambient mesh.
**Effort:** 1-3 months
**Impact:** MEDIUM

### Current Istio Mesh Enrollment Status

| App | Namespace | In Mesh? | L7 Waypoint? |
|-----|-----------|----------|--------------|
| chores-tracker (legacy v5.3.0) | `chores-tracker` | Yes | Yes (`chores-tracker-waypoint`) |
| chores-tracker-backend (v7.0.2) | `chores-tracker` | Yes (inherited) | Yes (inherited) |
| chores-tracker-frontend (v1.5.3) | `chores-tracker-frontend` | **No** | No |

> **Important:** Argo Rollouts canary with Istio traffic routing requires L7 waypoint proxies. The `chores-tracker-frontend` namespace must be enrolled before canary deployments can work for the frontend.

### 4.1 Enroll chores-tracker-frontend in Istio Ambient Mesh (Prerequisite)

Add the frontend namespace to the existing mesh enrollment config.

**File:** `base-apps/istio-ambient-config/namespace-labels.yaml` (append)

```yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: chores-tracker-frontend
  labels:
    istio.io/dataplane-mode: ambient
    istio.io/use-waypoint: chores-tracker-frontend-waypoint
```

**File:** `base-apps/istio-ambient-config/waypoint-proxy-frontend.yaml` (create)

```yaml
# Waypoint Proxy for chores-tracker-frontend namespace
# Provides L7 (HTTP) processing required for Argo Rollouts traffic splitting
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: chores-tracker-frontend-waypoint
  namespace: chores-tracker-frontend
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

**Verification steps after deployment:**
1. Confirm namespace label: `kubectl get ns chores-tracker-frontend --show-labels`
2. Confirm waypoint is running: `kubectl get pods -n chores-tracker-frontend -l gateway.networking.k8s.io/gateway-name=chores-tracker-frontend-waypoint`
3. Confirm mTLS is active: check Istio ambient Grafana dashboard for L4 traffic in the frontend namespace

### 4.2 Deploy Argo Rollouts

**File:** `base-apps/argo-rollouts.yaml`

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: argo-rollouts
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://argoproj.github.io/argo-helm
    chart: argo-rollouts
    targetRevision: 2.x.x
    helm:
      values: |
        controller:
          nodeSelector:
            node.kubernetes.io/workload: infrastructure
          tolerations:
            - key: node-role.kubernetes.io/control-plane
              operator: Exists
              effect: NoSchedule
        dashboard:
          enabled: true
  destination:
    server: https://kubernetes.default.svc
    namespace: argo-rollouts
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

### 4.3 Convert Deployment to Rollout

Convert chores-tracker-backend from `Deployment` to `Rollout` with canary strategy:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: chores-tracker-backend
  namespace: chores-tracker
spec:
  replicas: 2
  selector:
    matchLabels:
      app: chores-tracker-backend
  template:
    # ... same pod template as current Deployment ...
  strategy:
    canary:
      steps:
        - setWeight: 20
        - pause: { duration: 5m }
        - setWeight: 50
        - pause: { duration: 5m }
        - setWeight: 80
        - pause: { duration: 5m }
      trafficRouting:
        istio:
          virtualServices:
            - name: chores-tracker-backend-vsvc
              routes:
                - primary
```

### 4.4 DORA Metrics Tracking

Add basic DORA metrics collection using existing Prometheus + Grafana:

| Metric | Source | Target |
|--------|--------|--------|
| Deployment Frequency | ArgoCD sync events | Weekly or more |
| Lead Time for Changes | Git commit to ArgoCD sync | < 1 hour |
| Mean Time to Recovery | Alert to resolution (manual tracking initially) | < 1 hour |
| Change Failure Rate | Failed syncs / total syncs | < 15% |

Consider deploying the [Four Keys](https://github.com/dora-team/fourkeys) project or using ArgoCD notifications to push events to Prometheus.

### Acceptance Criteria

- [x] `chores-tracker-frontend` namespace enrolled in Istio ambient mesh with waypoint proxy (`namespace-labels.yaml` updated, `waypoint-proxy-frontend.yaml` created)
- [x] Argo Rollouts controller deployed (`base-apps/argo-rollouts.yaml` created)
- [x] chores-tracker-backend uses canary strategy with Istio traffic splitting (Deployment converted to Rollout, stable/canary services + VirtualService created)
- [ ] Rollout dashboard accessible for monitoring canary progress (verify after ArgoCD sync)
- [ ] Basic DORA metrics visible in Grafana (deferred to future iteration)

---

## Phase 5: Platform Maturity (Strategic)

**Goal:** Long-term improvements for operational excellence.
**Effort:** 3+ months
**Impact:** LOW-MEDIUM

### 5.1 Self-Service App Onboarding Template

Create a Cookiecutter or scaffolding script that generates:
- ArgoCD Application YAML
- Deployment, Service, Ingress manifests (with IP whitelist, TLS)
- SecretStore + ExternalSecret (with Vault role)
- NetworkPolicy (default-deny + app-specific)
- HPA configuration

### 5.2 Chaos Engineering

Deploy LitmusChaos or use `kubectl` chaos scripts to test:
- Pod kill recovery (verify health checks and HPA)
- Network partition (verify circuit breakers)
- Node drain (verify pod disruption budgets)

### 5.3 PodDisruptionBudgets

Add PDBs for all multi-replica workloads:

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: chores-tracker-backend
  namespace: chores-tracker
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: chores-tracker-backend
```

### 5.4 GitOps PR Promotion Workflow

Implement a promotion pipeline:
1. PR merges to `main` deploy to **staging** automatically
2. After staging validation, a manual promotion (tag or label) triggers **production** sync
3. ArgoCD ApplicationSet generators manage environment routing

---

## Implementation Timeline

```text
Week 1-2:   Phase 1 - CI Pipeline + Branch Protection
Week 3-6:   Phase 2 - Staging Environment + HPA
Week 7-10:  Phase 3 - Kyverno + Network Policies + Scanning
Month 3-4:  Phase 4 - Argo Rollouts + Canary Deployments
Month 4+:   Phase 5 - Platform Maturity (ongoing)
```

## Risk Considerations

| Risk | Mitigation |
|------|------------|
| Kyverno Enforce mode blocks legitimate workloads | Start in Audit mode, review violations before switching to Enforce |
| Network policies break existing traffic | Apply to one namespace at a time, test connectivity before expanding |
| Kustomize overlay migration disrupts ArgoCD | Migrate one app at a time, verify ArgoCD detects the new path |
| HPA thrashing (rapid scale up/down) | Use stabilization windows and conservative scale-down policies |

---

## Appendix: Files to Create/Modify

| File | Action | Phase |
|------|--------|-------|
| `.github/workflows/validate.yaml` | Create | 1 |
| `.yamllint.yaml` | Create | 1 |
| `terraform/modules/application-sets/application-sets.tf` | Modify (targetRevision) | 1 |
| `base-apps/chores-tracker-backend/overlays/staging/kustomization.yaml` | Create | 2 |
| `base-apps/chores-tracker-backend-staging.yaml` | Create | 2 |
| `base-apps/chores-tracker-backend/hpa.yaml` | Create | 2 |
| `base-apps/ecr-auth/cronjobs.yaml` | Modify (add staging namespace) | 2 |
| `base-apps/kyverno.yaml` | Create | 3 |
| `base-apps/kyverno/policies.yaml` | Create | 3 |
| `base-apps/chores-tracker-backend/network-policy.yaml` | Create | 3 |
| `base-apps/istio-ambient-config/namespace-labels.yaml` | Modified (added frontend namespace) | 4 |
| `base-apps/istio-ambient-config/waypoint-proxy-frontend.yaml` | Created | 4 |
| `base-apps/argo-rollouts.yaml` | Created | 4 |
| `base-apps/chores-tracker-backend/deployments.yaml` | Modified (Deployment → Rollout with canary strategy) | 4 |
| `base-apps/chores-tracker-backend/services.yaml` | Modified (added stable + canary services) | 4 |
| `base-apps/chores-tracker-backend/virtualservice.yaml` | Created (Istio VirtualService for traffic splitting) | 4 |
