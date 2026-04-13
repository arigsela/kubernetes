# Argo Workflows POC Implementation Plan

## Overview

Deploy Argo Workflows as a POC on the asela-cluster to learn workflow orchestration and establish a Kubernetes-native CI/pipeline capability alongside our existing ArgoCD GitOps stack.

### Goals
1. Deploy Argo Workflows via ArgoCD (following existing Helm-based app pattern)
2. Configure the UI with ingress access
3. Set up Vault integration for secret management
4. Create starter WorkflowTemplates demonstrating key capabilities
5. Create a practical CronWorkflow for an automated task

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Git Repository                        │
│  base-apps/argo-workflows.yaml    (ArgoCD Application)  │
│  base-apps/argo-workflows/        (Ingress, Secrets)    │
│  base-apps/argo-workflow-tasks/   (Templates, Crons)    │
│  base-apps/argo-workflow-tasks.yaml (ArgoCD Application) │
└──────────────────────┬──────────────────────────────────┘
                       │ GitOps sync
                       ▼
┌─────────────────────────────────────────────────────────┐
│                      ArgoCD                              │
│  master-app discovers argo-workflows.yaml               │
│  master-app discovers argo-workflow-tasks.yaml           │
└──────────────────────┬──────────────────────────────────┘
                       │ deploys
                       ▼
┌─────────────────────────────────────────────────────────┐
│              argo-workflows namespace                    │
│  ┌──────────────────┐  ┌──────────────────┐            │
│  │ Workflow Controller│  │   Argo Server    │            │
│  │ (watches CRDs,    │  │ (UI + REST API)  │            │
│  │  creates pods)    │  │                  │            │
│  └──────────────────┘  └──────────────────┘            │
│  ┌──────────────────┐  ┌──────────────────┐            │
│  │  SecretStore      │  │  ExternalSecret  │            │
│  │  (Vault backend)  │  │  (credentials)   │            │
│  └──────────────────┘  └──────────────────┘            │
│  ┌──────────────────────────────────────────┐          │
│  │  WorkflowTemplates / CronWorkflows       │          │
│  │  (deployed by argo-workflow-tasks app)    │          │
│  └──────────────────────────────────────────┘          │
└─────────────────────────────────────────────────────────┘
```

### Relevant Existing Files
| File | Purpose |
|------|---------|
| `base-apps/argo-rollouts.yaml` | Reference pattern for Helm-based Argo app |
| `base-apps/vault/` | Vault deployment (secret backend) |
| `base-apps/external-secrets.yaml` | External Secrets Operator |
| `base-apps/n8n/secret-store.yaml` | Reference SecretStore pattern |

---

## Phase 1: Core Deployment
**Goal**: Argo Workflows running and accessible via UI

### Subphase 1.1: ArgoCD Application (Helm)
- ✅ Create `base-apps/argo-workflows.yaml` — ArgoCD Application pointing to `argo-workflows` Helm chart from `https://argoproj.github.io/argo-helm` (chart version `0.45.19`)
- ✅ Configure Helm values:
  - Controller with nodeSelector (`node.kubernetes.io/workload: infrastructure`) and control-plane tolerations
  - Server enabled with nodeSelector and tolerations
  - Auth mode set to `server` (no login required for POC)
  - Controller configured to watch `argo-workflows` namespace
- ⬜ Commit and push — verify ArgoCD picks it up and syncs

### Subphase 1.2: Ingress for UI Access
- ✅ Create `base-apps/argo-workflows/` directory
- ✅ Create `base-apps/argo-workflows/nginx-ingress.yaml` — Ingress resource for Argo Workflows UI
  - Host: `argo-workflows.arigsela.com`
  - TLS via cert-manager (`letsencrypt-prod` cluster-issuer)
  - Backend: `argo-workflows-server` service on port 2746 (HTTPS backend protocol)
  - Hardening: IP whitelist + rate limits matching `argo-cd` ingress pattern
- ⬜ Commit and push — verify UI is accessible

### Phase 1 Validation
- [ ] ArgoCD shows `argo-workflows` app as Synced/Healthy
- [ ] Argo Workflows UI accessible at ingress URL
- [ ] `kubectl get pods -n argo-workflows` shows controller and server running

---

## Phase 2: Secret Management
**Goal**: Vault integration for workflow secrets

### Subphase 2.1: SecretStore + Vault Role
- ⬜ Create `base-apps/argo-workflows/secret-store.yaml` — SecretStore pointing to Vault
  ```yaml
  apiVersion: external-secrets.io/v1beta1
  kind: SecretStore
  metadata:
    name: vault-backend
    namespace: argo-workflows
  spec:
    provider:
      vault:
        server: "http://vault.vault.svc.cluster.local:8200"
        path: "k8s-secrets"
        version: "v2"
        auth:
          kubernetes:
            mountPath: "kubernetes"
            role: "argo-workflows"
            serviceAccountRef:
              name: "default"
  ```
- ⬜ Create Vault role `argo-workflows` with appropriate policy (manual step or Terraform)
- ⬜ Verify SecretStore status is `Valid`

### Phase 2 Validation
- [ ] SecretStore shows `Valid` status
- [ ] Can create a test ExternalSecret that resolves successfully

---

## Phase 3: Starter WorkflowTemplates
**Goal**: Reusable templates demonstrating key Argo Workflows concepts

### Subphase 3.1: ArgoCD App for Workflow Tasks
- ⬜ Create `base-apps/argo-workflow-tasks.yaml` — ArgoCD Application pointing to `base-apps/argo-workflow-tasks/` path
- ⬜ Create `base-apps/argo-workflow-tasks/` directory

### Subphase 3.2: Hello World DAG Template
- ⬜ Create `base-apps/argo-workflow-tasks/hello-world-dag.yaml`
  - Demonstrates: DAG template, parameters, multiple steps with dependencies
  - Steps: generate-message → fan-out to print-uppercase + print-lowercase → collect-results
  - Shows parameter passing between steps

### Subphase 3.3: Artifact Passing Template
- ⬜ Create `base-apps/argo-workflow-tasks/artifact-example.yaml`
  - Demonstrates: Artifact passing between steps (without external S3 — using volume-based artifacts or emptyDir)
  - Steps: generate-file → process-file → report-results

### Subphase 3.4: Practical Utility Templates
- ⬜ Create `base-apps/argo-workflow-tasks/cluster-health-check.yaml`
  - WorkflowTemplate that checks cluster health (node status, pod counts, resource usage)
  - Uses `resource` template type to query Kubernetes API
  - Demonstrates: RBAC needs for workflows, script templates, conditional logic

### Phase 3 Validation
- [ ] All WorkflowTemplates appear in Argo Workflows UI
- [ ] Can submit and run hello-world-dag from UI
- [ ] DAG visualization works in UI
- [ ] Artifact example completes successfully

---

## Phase 4: CronWorkflow — Practical Automation
**Goal**: A scheduled workflow that provides real value

### Subphase 4.1: Namespace Report CronWorkflow
- ⬜ Create `base-apps/argo-workflow-tasks/namespace-report-cron.yaml`
  - CronWorkflow that runs daily (or weekly)
  - Collects: pod count per namespace, resource usage, PVC utilization
  - Outputs a summary (logged to workflow output for now)
  - Demonstrates: CronWorkflow, script templates, RBAC for cross-namespace reads

### Subphase 4.2: RBAC for Workflows
- ⬜ Create `base-apps/argo-workflow-tasks/rbac.yaml`
  - ServiceAccount for workflow execution
  - ClusterRole with read access to pods, nodes, PVCs across namespaces
  - ClusterRoleBinding
  - Role/RoleBinding for workflow submission in `argo-workflows` namespace

### Phase 4 Validation
- [ ] CronWorkflow appears in UI with next scheduled run
- [ ] Can trigger CronWorkflow manually
- [ ] Output shows cluster health data
- [ ] Workflow history is retained and viewable in UI

---

## Phase 5: CI Pipeline Template (Stretch Goal)
**Goal**: Demonstrate a basic CI pipeline pattern

### Subphase 5.1: Generic CI WorkflowTemplate
- ⬜ Create `base-apps/argo-workflow-tasks/ci-pipeline-template.yaml`
  - Parameterized template accepting: repo URL, branch, image name
  - Steps: clone-repo → run-tests → build-image (Kaniko) → push-image
  - Demonstrates: WorkflowTemplate parameters, Kaniko image building, secret injection for registry creds
  - Note: Image push step requires ECR credentials via ExternalSecret

### Phase 5 Validation
- [ ] Can submit CI pipeline with parameters from UI
- [ ] Pipeline clones, tests, and builds successfully
- [ ] Image pushed to ECR (if credentials configured)

---

## Files to Create — Summary

| File | Type | Phase |
|------|------|-------|
| `base-apps/argo-workflows.yaml` | ArgoCD Application (Helm) | 1.1 |
| `base-apps/argo-workflows/nginx-ingress.yaml` | Ingress | 1.2 |
| `base-apps/argo-workflows/secret-store.yaml` | SecretStore | 2.1 |
| `base-apps/argo-workflow-tasks.yaml` | ArgoCD Application (path) | 3.1 |
| `base-apps/argo-workflow-tasks/hello-world-dag.yaml` | WorkflowTemplate | 3.2 |
| `base-apps/argo-workflow-tasks/artifact-example.yaml` | WorkflowTemplate | 3.3 |
| `base-apps/argo-workflow-tasks/cluster-health-check.yaml` | WorkflowTemplate | 3.4 |
| `base-apps/argo-workflow-tasks/namespace-report-cron.yaml` | CronWorkflow | 4.1 |
| `base-apps/argo-workflow-tasks/rbac.yaml` | RBAC resources | 4.2 |
| `base-apps/argo-workflow-tasks/ci-pipeline-template.yaml` | WorkflowTemplate | 5.1 |

## Manual / External Steps

| Step | Phase | Notes |
|------|-------|-------|
| Create Vault role `argo-workflows` | 2.1 | Vault policy for `k8s-secrets/argo-workflows/*` |
| Create DNS record for ingress | 1.2 | `argo-workflows.arigsela.com` → ingress IP |
| Verify Helm chart version | 1.1 | Check latest stable at https://argoproj.github.io/argo-helm |

## Resource Estimates

| Component | CPU Request | Memory Request |
|-----------|------------|----------------|
| Workflow Controller | 100m | 128Mi |
| Argo Server (UI) | 100m | 128Mi |
| **Total base** | **200m** | **256Mi** |
| Workflow pods (per step) | varies | varies |

---

## Progress Tracking

| Phase | Status | Tasks |
|-------|--------|-------|
| Phase 1: Core Deployment | 🟡 In Progress | (2/3 tasks — pending commit/sync) |
| Phase 2: Secret Management | ⬜ Not Started | (0/2 tasks) |
| Phase 3: Starter Templates | ⬜ Not Started | (0/4 tasks) |
| Phase 4: CronWorkflow | ⬜ Not Started | (0/2 tasks) |
| Phase 5: CI Pipeline | ⬜ Not Started | (0/1 tasks) |
| **Overall** | **~17%** | **(2/12 tasks)** |

**Last Updated**: 2026-04-13
**Current Status**: Phase 1 files authored; awaiting commit/push and cluster sync verification
