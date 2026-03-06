# Kyverno Policy Engine Implementation Plan

## Overview
Deploy Kyverno as the Kubernetes policy engine (OPA alternative) in the existing GitOps infrastructure, using the official Helm chart managed by ArgoCD, with custom starter policies in Audit mode.

**Why Kyverno over OPA/Gatekeeper?**
- Kubernetes-native: policies are written in YAML (no Rego learning curve)
- Policies are Kubernetes CRDs — perfect for GitOps
- Can validate, mutate, and generate resources
- CNCF Incubating project with active community

## Success Criteria
- [ ] Kyverno core engine running on infrastructure nodes
- [ ] Admission webhooks registered and healthy
- [ ] 5 starter policies deployed in Audit mode
- [ ] PolicyReports generated for existing workloads (no enforcement yet)
- [ ] No disruption to existing workloads or ArgoCD operations

## Research Findings

### Helm Chart Details
- **Repository**: `https://kyverno.github.io/kyverno/`
- **Chart**: `kyverno/kyverno` — latest version **3.7.1**
- **Separate policies chart**: `kyverno/kyverno-policies` (PSS standards — not used initially)
- **Must** be installed in a dedicated namespace

### Existing Patterns to Follow
- `base-apps/cert-manager.yaml` — Helm-based ArgoCD Application pattern
- `base-apps/external-secrets.yaml` — Helm values with nodeSelector/tolerations
- `base-apps/cert-manager-config.yaml` — Local git path pattern for additional manifests
- All infrastructure workloads use `nodeSelector: node.kubernetes.io/workload: infrastructure` + control-plane tolerations

### Dependencies
- ArgoCD (manages deployment)
- Infrastructure nodes (scheduling target)

## Architecture Decisions

### Decision 1: Policy Management Approach
**Options considered:**
1. **Kyverno-policies Helm chart** — PSS baseline out of the box, less control
2. **Custom local policies only** — Full control, GitOps-friendly, write from scratch
3. **Both** — Helm chart + local overrides

**Chosen:** Option 2 — Custom local policies in `base-apps/kyverno-policies/`. This gives full control, is cleanly GitOps-managed, and avoids Helm chart complexity for policies. We can always add the policies Helm chart later.

### Decision 2: Initial Enforcement Mode
**Chosen:** All policies start in **Audit** mode. This generates PolicyReports without blocking any workloads. Allows us to review violations before enforcing.

### Decision 3: Webhook Failure Policy
**Chosen:** `failurePolicy: Ignore` — If Kyverno is down, workloads can still be created. Prevents Kyverno from becoming a cluster availability risk during initial rollout.

### Decision 4: Namespace Exclusions
**Chosen:** Exclude `kube-system`, `argo-cd`, and `kyverno` namespaces from policy enforcement to avoid interfering with critical system components.

## Implementation

### Phase 1: Deploy Kyverno Core Engine

#### Task 1.1: Create Kyverno ArgoCD Application
**Files:** `base-apps/kyverno.yaml`
**Steps:**
1. Create ArgoCD Application manifest pointing to the official Helm chart
2. Set `source.repoURL: https://kyverno.github.io/kyverno/`
3. Set `source.chart: kyverno`, `source.targetRevision: 3.7.1`
4. Configure Helm values:
   - `admissionController.nodeSelector` and `tolerations` for infrastructure nodes
   - `backgroundController.nodeSelector` and `tolerations`
   - `cleanupController.nodeSelector` and `tolerations`
   - `reportsController.nodeSelector` and `tolerations`
   - `admissionController.replicas: 1` (can scale later)
   - `webhookConfiguration.failurePolicy: Ignore`
   - Resource requests/limits for all controllers
5. Set destination namespace to `kyverno`
6. Add `syncOptions: [CreateNamespace=true, ServerSideApply=true]` (ServerSideApply needed for large CRDs)
7. Standard automated sync policy with prune and selfHeal

**Testing:**
- [ ] ArgoCD Application syncs successfully (Healthy + Synced)
- [ ] All Kyverno pods running in `kyverno` namespace on infrastructure nodes
- [ ] `kubectl get validatingwebhookconfigurations` shows Kyverno webhooks
- [ ] `kubectl get clusterpolicies` returns empty list (CRDs installed)

---

### Phase 2: Create Starter Policies (Audit Mode)

#### Task 2.1: Create Kyverno Policies ArgoCD Application
**Files:** `base-apps/kyverno-policies.yaml`
**Steps:**
1. Create ArgoCD Application pointing to `base-apps/kyverno-policies` local path
2. Follow the pattern from `base-apps/argo-cd.yaml` (local git path)
3. Set destination namespace to `kyverno`
4. Standard automated sync with prune and selfHeal

**Testing:**
- [ ] ArgoCD Application created and syncs

#### Task 2.2: Create "Require Labels" Policy
**Files:** `base-apps/kyverno-policies/require-labels.yaml`
**Steps:**
1. Create ClusterPolicy requiring `app.kubernetes.io/name` label on Deployments and StatefulSets
2. Set `validationFailureAction: Audit`
3. Exclude `kube-system`, `argo-cd`, `kyverno` namespaces
4. Add informative violation message

**Testing:**
- [ ] Policy appears in `kubectl get clusterpolicies`
- [ ] PolicyReports generated for workloads missing labels

#### Task 2.3: Create "Disallow Privileged Containers" Policy
**Files:** `base-apps/kyverno-policies/disallow-privileged-containers.yaml`
**Steps:**
1. Create ClusterPolicy blocking `securityContext.privileged: true` on Pods
2. Set `validationFailureAction: Audit`
3. Exclude system namespaces
4. Add informative violation message

**Testing:**
- [ ] Policy synced and active
- [ ] PolicyReports flag any privileged containers in cluster

#### Task 2.4: Create "Require Resource Limits" Policy
**Files:** `base-apps/kyverno-policies/require-resource-limits.yaml`
**Steps:**
1. Create ClusterPolicy requiring `resources.limits.cpu` and `resources.limits.memory` on all containers
2. Set `validationFailureAction: Audit`
3. Exclude system namespaces

**Testing:**
- [ ] Policy active
- [ ] PolicyReports identify containers without resource limits

#### Task 2.5: Create "Disallow Default Namespace" Policy
**Files:** `base-apps/kyverno-policies/disallow-default-namespace.yaml`
**Steps:**
1. Create ClusterPolicy preventing Deployments/StatefulSets/Services in `default` namespace
2. Set `validationFailureAction: Audit`

**Testing:**
- [ ] Policy active
- [ ] Any resources in `default` namespace flagged in reports

#### Task 2.6: Create "Disallow Latest Tag" Policy
**Files:** `base-apps/kyverno-policies/disallow-latest-tag.yaml`
**Steps:**
1. Create ClusterPolicy blocking `:latest` or untagged container images
2. Set `validationFailureAction: Audit`
3. Exclude system namespaces

**Testing:**
- [ ] Policy active
- [ ] Workloads using `:latest` flagged in PolicyReports

---

### Phase 3: Validation and Documentation

#### Task 3.1: Review PolicyReports
**Steps:**
1. After policies are deployed, check `kubectl get policyreports -A` and `kubectl get clusterpolicyreports`
2. Review which existing workloads would be affected
3. Document findings and determine which workloads need updates before enforcement

**Testing:**
- [ ] PolicyReports exist and contain meaningful results
- [ ] No false positives on excluded namespaces

#### Task 3.2: Update base-apps README
**Files:** `base-apps/README.md`
**Steps:**
1. Add Kyverno section explaining the policy engine setup
2. Document how to add new policies (create ClusterPolicy YAML in `base-apps/kyverno-policies/`)
3. Document how to move policies from Audit to Enforce mode
4. Note the namespace exclusions

**Testing:**
- [ ] README accurately describes the setup

---

### Phase 4: Gradual Enforcement (Future — Out of Scope)
This phase is documented for reference but **not part of this implementation**:
1. Review audit reports and fix violations in existing workloads
2. Change `validationFailureAction` from `Audit` to `Enforce` one policy at a time
3. Consider switching `webhookConfiguration.failurePolicy` to `Fail` once stable
4. Consider adding the `kyverno-policies` Helm chart for PSS baseline/restricted profiles
5. Consider deploying Policy Reporter UI for dashboards

## Files Summary
| File | Type | Purpose |
|------|------|---------|
| `base-apps/kyverno.yaml` | ArgoCD Application | Kyverno Helm chart deployment |
| `base-apps/kyverno-policies.yaml` | ArgoCD Application | Custom policies from local path |
| `base-apps/kyverno-policies/require-labels.yaml` | ClusterPolicy | Require K8s labels on workloads |
| `base-apps/kyverno-policies/disallow-privileged-containers.yaml` | ClusterPolicy | Block privileged containers |
| `base-apps/kyverno-policies/require-resource-limits.yaml` | ClusterPolicy | Require CPU/memory limits |
| `base-apps/kyverno-policies/disallow-default-namespace.yaml` | ClusterPolicy | Block default namespace usage |
| `base-apps/kyverno-policies/disallow-latest-tag.yaml` | ClusterPolicy | Block :latest image tags |

## Risks and Mitigations
| Risk | Mitigation |
|------|------------|
| Kyverno webhooks block cluster operations | `failurePolicy: Ignore` + namespace exclusions |
| Breaking existing workloads | All policies start in Audit mode |
| ArgoCD sync interference | `argo-cd` namespace excluded from policies |
| Large CRD sync issues | `ServerSideApply=true` sync option |
| Resource overhead | Proper resource limits on all controllers |

## End-to-End Testing
1. Verify all ArgoCD Applications are Healthy + Synced
2. Verify Kyverno pods running on infrastructure nodes
3. Verify webhooks are registered
4. Deploy a test pod without labels/limits and confirm PolicyReport captures it
5. Confirm existing workloads are **not** disrupted

## References
- [Kyverno Helm Chart on ArtifactHub](https://artifacthub.io/packages/helm/kyverno/kyverno)
- [Kyverno Installation Docs](https://kyverno.io/docs/installation/)
- [Kyverno Helm Chart Values](https://github.com/kyverno/kyverno/blob/main/charts/kyverno/values.yaml)
- [Kyverno Policies Chart](https://github.com/kyverno/kyverno/tree/main/charts/kyverno-policies)
- [Kyverno vs OPA/Gatekeeper Comparison](https://nirmata.com/2025/02/07/kubernetes-policy-comparison-kyverno-vs-opa-gatekeeper/)
