# Istio Ambient Mesh Deployment Plan

## Overview

Deploy Istio Ambient Mesh to the K3s cluster using ArgoCD and Helm, following existing GitOps patterns in the repository.

## Configuration Decisions

| Decision | Choice |
|----------|--------|
| **Istio Version** | 1.24.0 (Latest stable) |
| **Initial Namespace** | chores-tracker only |
| **Authorization Policies** | Yes, include examples |

## Prerequisites

- [ ] Verify Kubernetes version >= 1.24 (run `kubectl version`)
- [ ] Verify K3s/Flannel CNI compatibility with Istio CNI
- [ ] Ensure ArgoCD has access to Istio Helm repository

## Architecture

```text
+-------------------------------------------------------------+
|  Istio Ambient Mesh Components                              |
+-------------------------------------------------------------+
|  1. Gateway API CRDs    - Kubernetes Gateway API support    |
|  2. istio-base          - Istio CRDs and cluster resources  |
|  3. istiod              - Control plane (ambient profile)   |
|  4. istio-cni           - CNI plugin for traffic redirect   |
|  5. ztunnel             - L4 proxy DaemonSet (per node)     |
|  6. (Optional) Waypoint - L7 proxy for HTTP features        |
+-------------------------------------------------------------+
```

## File Structure to Create

```text
base-apps/
├── istio-gateway-api.yaml          # Gateway API CRDs (sync-wave: -3)
├── istio-base.yaml                 # Istio CRDs (sync-wave: -2)
├── istio-istiod.yaml               # Control plane (sync-wave: -1)
├── istio-cni.yaml                  # CNI plugin (sync-wave: 0)
├── istio-ztunnel.yaml              # ztunnel DaemonSet (sync-wave: 1)
└── istio-ambient-config/           # Configuration directory
    ├── namespace-labels.yaml       # Labels for mesh enrollment
    └── authorization-policies.yaml # Example security policies
```

---

## Phase 1: Gateway API CRDs

**File:** `base-apps/istio-gateway-api.yaml`

Install Kubernetes Gateway API CRDs required by Istio Ambient.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: istio-gateway-api
  namespace: argo-cd
  annotations:
    argocd.argoproj.io/sync-wave: "-3"
spec:
  project: default
  source:
    repoURL: https://github.com/kubernetes-sigs/gateway-api
    targetRevision: v1.2.0
    path: config/crd/standard
  destination:
    server: https://kubernetes.default.svc
    namespace: default
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=false
      - ServerSideApply=true
```

---

## Phase 2: Istio Base (CRDs)

**File:** `base-apps/istio-base.yaml`

Install Istio Custom Resource Definitions.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: istio-base
  namespace: argo-cd
  annotations:
    argocd.argoproj.io/sync-wave: "-2"
spec:
  project: default
  source:
    repoURL: https://istio-release.storage.googleapis.com/charts
    chart: base
    targetRevision: 1.24.0
    helm:
      values: |
        defaultRevision: default
  destination:
    server: https://kubernetes.default.svc
    namespace: istio-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

---

## Phase 3: Istiod Control Plane

**File:** `base-apps/istio-istiod.yaml`

Install Istiod with ambient profile.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: istio-istiod
  namespace: argo-cd
  annotations:
    argocd.argoproj.io/sync-wave: "-1"
spec:
  project: default
  source:
    repoURL: https://istio-release.storage.googleapis.com/charts
    chart: istiod
    targetRevision: 1.24.0
    helm:
      values: |
        profile: ambient
        pilot:
          nodeSelector:
            node.kubernetes.io/workload: infrastructure
          tolerations:
          - key: node-role.kubernetes.io/control-plane
            effect: NoSchedule
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
  destination:
    server: https://kubernetes.default.svc
    namespace: istio-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

---

## Phase 4: Istio CNI

**File:** `base-apps/istio-cni.yaml`

Install CNI plugin for traffic interception.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: istio-cni
  namespace: argo-cd
  annotations:
    argocd.argoproj.io/sync-wave: "0"
spec:
  project: default
  source:
    repoURL: https://istio-release.storage.googleapis.com/charts
    chart: cni
    targetRevision: 1.24.0
    helm:
      values: |
        profile: ambient
        cni:
          # K3s CNI bin/conf directories
          cniBinDir: /var/lib/rancher/k3s/data/current/bin
          cniConfDir: /var/lib/rancher/k3s/agent/etc/cni/net.d
          tolerations:
          - effect: NoSchedule
            operator: Exists
          - effect: NoExecute
            operator: Exists
  destination:
    server: https://kubernetes.default.svc
    namespace: istio-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

---

## Phase 5: Ztunnel DaemonSet

**File:** `base-apps/istio-ztunnel.yaml`

Install ztunnel (L4 proxy) on every node.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: istio-ztunnel
  namespace: argo-cd
  annotations:
    argocd.argoproj.io/sync-wave: "1"
spec:
  project: default
  source:
    repoURL: https://istio-release.storage.googleapis.com/charts
    chart: ztunnel
    targetRevision: 1.24.0
    helm:
      values: |
        tolerations:
        - effect: NoSchedule
          operator: Exists
        - effect: NoExecute
          operator: Exists
        resources:
          requests:
            cpu: 50m
            memory: 64Mi
          limits:
            cpu: 200m
            memory: 256Mi
  destination:
    server: https://kubernetes.default.svc
    namespace: istio-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

---

## Phase 6: Ambient Mesh Configuration

**File:** `base-apps/istio-ambient-config.yaml`

ArgoCD Application for mesh configuration.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: istio-ambient-config
  namespace: argo-cd
  annotations:
    argocd.argoproj.io/sync-wave: "2"
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/istio-ambient-config
  destination:
    server: https://kubernetes.default.svc
    namespace: istio-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - SkipDryRunOnMissingResource=true
    retry:
      limit: 5
      backoff:
        duration: 5s
        factor: 2
        maxDuration: 3m
```

**Directory:** `base-apps/istio-ambient-config/`

### namespace-labels.yaml

```yaml
# Label namespaces to enroll in ambient mesh
# Apply label: istio.io/dataplane-mode=ambient
apiVersion: v1
kind: Namespace
metadata:
  name: chores-tracker
  labels:
    istio.io/dataplane-mode: ambient
```

### authorization-policy-example.yaml

```yaml
# Example: Only allow frontend to call backend
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: backend-access-policy
  namespace: chores-tracker
spec:
  selector:
    matchLabels:
      app: chores-tracker-backend
  action: ALLOW
  rules:
  - from:
    - source:
        principals:
        - cluster.local/ns/chores-tracker/sa/chores-tracker-frontend
```

---

## Implementation Progress

### Step 0: Save Plan to Repository

- [x] Create `docs/istio-ambient-mesh-implementation-plan.md` with this plan

### Step 1: Create Istio ArgoCD Applications

- [x] Create `base-apps/istio-gateway-api.yaml`
- [x] Create `base-apps/istio-base.yaml`
- [x] Create `base-apps/istio-istiod.yaml`
- [x] Create `base-apps/istio-cni.yaml`
- [x] Create `base-apps/istio-ztunnel.yaml`

### Step 2: Create Configuration Directory

- [x] Create `base-apps/istio-ambient-config/` directory
- [x] Add namespace label configurations
- [x] Add example authorization policies

### Step 3: Create Config Application

- [x] Create `base-apps/istio-ambient-config.yaml`

### Step 4: Commit and Push

- [x] Commit all files to git
- [x] Push to remote
- [ ] ArgoCD will automatically sync (sync waves ensure correct order)

### Step 5: Verification

- [ ] Check all Istio pods are running: `kubectl get pods -n istio-system`
- [ ] Verify ztunnel DaemonSet: `kubectl get ds -n istio-system`
- [ ] Verify CNI installation: `kubectl logs -n istio-system -l k8s-app=istio-cni-node`
- [ ] Test mesh enrollment by labeling a namespace

### Step 6: Enable Mesh for Workloads

- [ ] Label namespaces: `kubectl label namespace chores-tracker istio.io/dataplane-mode=ambient`
- [ ] Verify mTLS: `istioctl proxy-status` or check ztunnel logs

---

## Critical Files to Modify/Create

| Action | File Path |
|--------|-----------|
| CREATE | `docs/istio-ambient-mesh-implementation-plan.md` |
| CREATE | `base-apps/istio-gateway-api.yaml` |
| CREATE | `base-apps/istio-base.yaml` |
| CREATE | `base-apps/istio-istiod.yaml` |
| CREATE | `base-apps/istio-cni.yaml` |
| CREATE | `base-apps/istio-ztunnel.yaml` |
| CREATE | `base-apps/istio-ambient-config.yaml` |
| CREATE | `base-apps/istio-ambient-config/namespace-labels.yaml` |
| CREATE | `base-apps/istio-ambient-config/authorization-policy-example.yaml` |

---

## K3s-Specific Considerations

1. **CNI Paths**: K3s uses non-standard CNI paths:
   - Binary: `/var/lib/rancher/k3s/data/current/bin`
   - Config: `/var/lib/rancher/k3s/agent/etc/cni/net.d`

2. **Flannel Compatibility**: Istio CNI works alongside Flannel; ensure proper ordering in CNI config

3. **Node Tolerations**: ztunnel and CNI need tolerations to run on all nodes including control plane

---

## Rollback Plan

If issues occur:

1. Delete ArgoCD Applications in reverse order (ztunnel -> cni -> istiod -> base -> gateway-api)
2. Remove namespace labels: `kubectl label namespace <ns> istio.io/dataplane-mode-`
3. Clean up CRDs if needed: `kubectl delete crd -l app=istio`

---

## Success Criteria

- [ ] All Istio pods running in `istio-system` namespace
- [ ] ztunnel DaemonSet has pods on all nodes
- [ ] CNI plugin installed successfully
- [ ] Can label namespace and see traffic intercepted
- [ ] mTLS working between labeled workloads
