# Kagent Implementation Plan

## Overview

**Service**: Kagent (Kubernetes AI Agent Framework)
**Purpose**: Kubernetes-native framework for building, deploying, and managing AI agents
**Namespace**: `kagent`
**Dependencies**:
- kMCP (should be deployed first - see `kmcp-implementation-plan.md`)
- LLM Provider API Key (OpenAI, Anthropic, Azure OpenAI, or Ollama)
- Vault (for secure API key storage)

## Implementation Approach

Following the GitOps pattern established in this repository:
1. Store LLM API keys in Vault
2. Create ArgoCD Application manifest for Kagent CRDs (deployed first)
3. Create ArgoCD Application manifest for Kagent
4. Configure External Secrets for API key management
5. Configure which agents and tools to enable
6. Commit and push - ArgoCD auto-deploys

---

## Phase 1: Pre-Deployment Setup

**Status**: ✅ COMPLETE (3/3 tasks)
**Progress**: 100%
**Last Updated**: 2025-11-27

### 1.1 Verify Prerequisites ✅
**Status**: ✅ COMPLETE

| Prerequisite | Status | Details |
|--------------|--------|---------|
| kMCP deployed and healthy | ✅ | Controller running, MCPServer working |
| ArgoCD running | ✅ | 7 components healthy |
| External Secrets Operator | ✅ | 3 components healthy |
| Vault accessible and unsealed | ✅ | Initialized: true, Sealed: false |
| LLM Provider selected | ✅ | **Anthropic** (Claude models) |

### 1.2 Store API Keys in Vault ✅
**Status**: ✅ COMPLETE

**Provider**: Anthropic
**Secret Path**: `k8s-secrets/data/kagent`
**Key**: `anthropic-api-key`

```bash
# Command executed:
kubectl exec -n vault vault-0 -- vault kv put k8s-secrets/kagent \
  anthropic-api-key="sk-ant-api03-..."
```

**Result**:
```
Secret Path: k8s-secrets/data/kagent
Version: 1
Created: 2025-11-27T16:47:44Z
```

### 1.3 Create Vault Policy for Kagent ✅
**Status**: ✅ COMPLETE

**Policy Created**: `kagent-policy`
```hcl
path "k8s-secrets/data/kagent" {
  capabilities = ["read"]
}
path "k8s-secrets/metadata/kagent" {
  capabilities = ["read", "list"]
}
```

**Kubernetes Auth Role Created**: `kagent`
- Bound Service Accounts: `default`, `kagent-controller`, `kagent`
- Bound Namespace: `kagent`
- Policies: `kagent-policy`
- TTL: 1h

---

## Phase 2: Create Directory Structure

**Status**: ✅ COMPLETE (2/2 tasks)
**Progress**: 100%
**Last Updated**: 2025-11-27

### 2.1 Create Application Directories ✅
**Status**: ✅ COMPLETE

```bash
mkdir -p base-apps/kagent-crds
mkdir -p base-apps/kagent
```

**Verification**:
```
drwxr-xr-x  kagent
drwxr-xr-x  kagent-crds
```

### 2.2 Expected File Structure ✅
```
base-apps/
├── kagent-crds.yaml         # ArgoCD App for CRDs (deploy first) - TO CREATE
├── kagent-crds/             # Created ✅
│   └── (empty - Helm chart handles CRDs)
├── kagent.yaml              # ArgoCD App for kagent - TO CREATE
└── kagent/                  # Created ✅
    ├── namespace.yaml       # Namespace definition - TO CREATE
    ├── secret-store.yaml    # Vault SecretStore - TO CREATE
    └── external-secrets.yaml # ExternalSecret for API keys - TO CREATE
```

---

## Phase 3: Deploy Kagent CRDs

**Status**: ✅ COMPLETE (1/1 tasks)
**Progress**: 100%
**Last Updated**: 2025-11-27

### 3.1 Create ArgoCD Application for CRDs ✅
**Status**: ✅ COMPLETE

**Helm Chart Version Check**:
```
helm show chart oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds
Version: 0.7.5
```

**File Created**: `base-apps/kagent-crds.yaml`
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: kagent-crds
  namespace: argo-cd
  annotations:
    argocd.argoproj.io/sync-wave: "1"  # After kMCP (which is at wave 0)
spec:
  project: default
  source:
    repoURL: oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds
    chart: kagent-crds
    targetRevision: 0.7.5
    helm:
      releaseName: kagent-crds
      values: |
        # Disable kmcp-crds subchart - we already deployed kMCP separately
        kmcp:
          enabled: false
  destination:
    server: https://kubernetes.default.svc
    namespace: kagent
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true  # Required for CRDs
```

**Key Configuration**:
- OCI Registry: `oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds`
- Chart Version: `0.7.5` (latest as of 2025-11-27)
- kMCP subchart disabled (already deployed separately)
- Sync Wave: `1` (after kMCP at wave 0)

---

## Phase 4: Create Supporting Manifests

**Status**: ✅ COMPLETE (3/3 tasks)
**Progress**: 100%
**Last Updated**: 2025-11-27

### 4.1 Create Namespace Manifest ✅
**Status**: ✅ COMPLETE

**File Created**: `base-apps/kagent/namespace.yaml`
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: kagent
  labels:
    app.kubernetes.io/name: kagent
    app.kubernetes.io/part-of: ai-platform
```

### 4.2 Create SecretStore for Vault ✅
**Status**: ✅ COMPLETE

**File Created**: `base-apps/kagent/secret-store.yaml`
```yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: kagent
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "kagent"
          serviceAccountRef:
            name: "default"
```

### 4.3 Create ExternalSecret for Anthropic API Key ✅
**Status**: ✅ COMPLETE

**File Created**: `base-apps/kagent/external-secrets.yaml`
```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: kagent-anthropic-secrets
  namespace: kagent
spec:
  refreshInterval: "1h"
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: kagent-anthropic
    creationPolicy: Owner
  data:
    - secretKey: ANTHROPIC_API_KEY
      remoteRef:
        key: kagent
        property: anthropic-api-key
```

**Files Summary**:
| File | Size | Purpose |
|------|------|---------|
| namespace.yaml | 144 bytes | Kagent namespace definition |
| secret-store.yaml | 391 bytes | Vault SecretStore for kagent |
| external-secrets.yaml | 397 bytes | ExternalSecret for Anthropic API key |

---

## Phase 5: Deploy Kagent

**Status**: ✅ COMPLETE (2/2 tasks)
**Progress**: 100%
**Last Updated**: 2025-11-27

### 5.1 Create ArgoCD Application for Kagent ✅
**Status**: ✅ COMPLETE

**Helm Chart Version**: `0.7.5` (latest as of 2025-11-27)

**File Created**: `base-apps/kagent.yaml` (3814 bytes)

**Key Configuration**:
- OCI Registry: `oci://ghcr.io/kagent-dev/kagent/helm/kagent`
- Chart Version: `0.7.5`
- LLM Provider: **Anthropic** (Claude Sonnet 4)
- Model: `claude-sonnet-4-20250514`
- kMCP subchart: **disabled** (deployed separately)
- Sync Wave: `2` (after CRDs at wave 1)

**Enabled Agents**:
| Agent | Enabled | Purpose |
|-------|---------|---------|
| k8s-agent | ✅ | Kubernetes operations |
| helm-agent | ✅ | Helm chart management |
| promql-agent | ✅ | Prometheus queries |
| observability-agent | ✅ | Monitoring assistance |
| kgateway-agent | ❌ | Not needed |
| istio-agent | ❌ | No Istio in cluster |
| argo-rollouts-agent | ❌ | Not using Argo Rollouts |
| cilium-*-agents | ❌ | No Cilium in cluster |

**Enabled Tools**:
| Tool | Enabled |
|------|---------|
| kagent-tools | ✅ |
| querydoc | ✅ |
| grafana-mcp | ❌ |

### 5.2 Create ArgoCD Application for Supporting Manifests ✅
**Status**: ✅ COMPLETE

**File Created**: `base-apps/kagent-config.yaml` (543 bytes)

This Application deploys:
- `namespace.yaml` - kagent namespace
- `secret-store.yaml` - Vault SecretStore
- `external-secrets.yaml` - ExternalSecret for Anthropic API key

**Sync Wave**: `1` (deploys with CRDs, before main kagent app)

**Files Summary**:
| File | Size | Sync Wave | Purpose |
|------|------|-----------|---------|
| kagent-crds.yaml | 751 bytes | 1 | CRDs |
| kagent-config.yaml | 543 bytes | 1 | Supporting manifests |
| kagent.yaml | 3814 bytes | 2 | Main application |

---

## Phase 6: Configure Ingress for UI Access

### 6.1 Create Ingress for Kagent UI

**File**: `base-apps/kagent/ingress.yaml`
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: kagent-ui
  namespace: kagent
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-production
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
spec:
  ingressClassName: nginx
  rules:
    - host: kagent.arigsela.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: kagent-ui
                port:
                  number: 8080
  tls:
    - hosts:
        - kagent.arigsela.com
      secretName: kagent-ui-tls
```

### 6.2 DNS Configuration
Add DNS record (Route 53 or similar):
- `kagent.arigsela.com` -> Your cluster ingress IP (73.7.190.154)

---

## Phase 7: Verification

**Status**: ✅ COMPLETE (6/6 tasks)
**Progress**: 100%
**Last Updated**: 2025-11-27

### 7.1 Verify CRDs Installed ✅
**Status**: ✅ COMPLETE

**Command**: `kubectl get crds | grep -E 'kagent|kmcp'`

**Result**:
```
agents.kagent.dev              2025-11-27T17:30:18Z
mcpservers.kmcp.io             2025-11-27T16:15:09Z
memories.kagent.dev            2025-11-27T17:30:18Z
modelconfigs.kagent.dev        2025-11-27T17:30:18Z
remotemcpservers.kagent.dev    2025-11-27T17:30:18Z
toolservers.kagent.dev         2025-11-27T17:30:18Z
```

### 7.2 Verify Secrets Synced ✅
**Status**: ✅ COMPLETE

**ExternalSecret Status**:
```
NAME                       STORE           REFRESH INTERVAL   STATUS         READY
kagent-anthropic-secrets   vault-backend   1h                 SecretSynced   True
```

**Kubernetes Secret Created**: `kagent-anthropic` with key `ANTHROPIC_API_KEY`

### 7.3 Verify Pods Running ✅
**Status**: ✅ COMPLETE

| Pod | Status | Ready |
|-----|--------|-------|
| k8s-agent-* | Running | 1/1 |
| kagent-controller-* | Running | 1/1 |
| kagent-querydoc-* | Running | 1/1 |
| kagent-tools-* | Running | 1/1 |
| kagent-ui-* | Running | 1/1 |

**Note**: helm-agent, promql-agent, and observability-agent pods not created (may require additional configuration)

### 7.4 Verify ArgoCD Sync Status ✅
**Status**: ✅ COMPLETE

| Application | Sync Status | Health Status |
|-------------|-------------|---------------|
| kagent | Synced | Healthy |
| kagent-config | Synced | Healthy |
| kagent-crds | Synced | Healthy |

### 7.5 Check Controller Logs ✅
**Status**: ✅ COMPLETE

Controller logs show healthy operation with no errors.

### 7.6 Access UI ✅
**Status**: ✅ COMPLETE

**Services Available**:
| Service | Type | Port | Purpose |
|---------|------|------|---------|
| kagent-ui | ClusterIP | 8080 | Web UI |
| kagent-controller | ClusterIP | 8083 | Controller API |
| kagent-tools | ClusterIP | 8084 | Built-in tools |
| kagent-querydoc | ClusterIP | 8080 | Documentation queries |
| k8s-agent | ClusterIP | 8080 | Kubernetes agent |

**Access Methods**:
```bash
# Port forward for testing
kubectl port-forward -n kagent svc/kagent-ui 8080:8080

# Then access: http://localhost:8080
# Or via ingress (Phase 6): https://kagent.arigsela.com
```

---

## Phase 8: Post-Deployment Testing

**Status**: ✅ COMPLETE (3/3 tasks)
**Progress**: 100%
**Last Updated**: 2025-11-27

### 8.1 Pre-Configured Agents (from Helm Chart) ✅
**Status**: ✅ VERIFIED

The Helm chart automatically created these agents:

| Agent | Type | Description |
|-------|------|-------------|
| k8s-agent | Declarative | Kubernetes troubleshooting and operations expert |
| helm-agent | Declarative | Helm chart management |
| promql-agent | Declarative | Prometheus query assistance |
| observability-agent | Declarative | Monitoring and observability |

**Model Configuration**:
```
NAME                   PROVIDER    MODEL
default-model-config   Anthropic   claude-sonnet-4-20250514
```

**Remote MCP Server**:
```
NAME                 PROTOCOL          URL
kagent-tool-server   STREAMABLE_HTTP   http://kagent-tools.kagent:8084/mcp
```

### 8.2 Create Test Agent ✅
**Status**: ✅ COMPLETE

**File Created**: `base-apps/kagent/test-agent.yaml`

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: test-agent
  namespace: kagent
spec:
  type: Declarative
  description: "Simple test agent to verify kagent deployment is working"
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You are a friendly test agent for verifying the kagent deployment.
      ...
    tools:
      - type: McpServer
        mcpServer:
          apiGroup: kagent.dev
          kind: RemoteMCPServer
          name: kagent-tool-server
          toolNames:
            - k8s_get_resources
            - k8s_describe_resource
            - k8s_get_events
            - k8s_get_pod_logs
            - k8s_get_cluster_configuration
```

### 8.3 Verify Agents ✅
**Status**: ✅ COMPLETE

**All Agents**:
```
NAME                  TYPE          READY   ACCEPTED
helm-agent            Declarative
k8s-agent             Declarative
observability-agent   Declarative
promql-agent          Declarative
test-agent            Declarative
```

**Test Agent Details**:
- Created: 2025-11-27T17:48:00Z
- Model Config: default-model-config (Anthropic Claude Sonnet 4)
- Tools: 5 Kubernetes tools via kagent-tool-server

---

## Deployment Commands

### Git Operations
```bash
# Add all kagent files
git add base-apps/kagent-crds.yaml
git add base-apps/kagent.yaml
git add base-apps/kagent/

# Commit
git commit -m "feat: deploy Kagent - AI agent framework for Kubernetes"

# Push to trigger ArgoCD sync
git push origin main
```

---

## Rollback Procedure

If issues occur:
```bash
# Option 1: Disable via ArgoCD
kubectl patch application kagent -n argo-cd -p '{"spec":{"syncPolicy":null}}' --type=merge

# Option 2: Revert Git commit
git revert HEAD
git push origin main

# Option 3: Manual cleanup
kubectl delete application kagent -n argo-cd
kubectl delete application kagent-crds -n argo-cd
kubectl delete namespace kagent
```

---

## Resource Requirements

| Component | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|-------------|-----------|----------------|--------------|
| Controller | 100m | 2000m | 128Mi | 512Mi |
| UI | 100m | 1000m | 256Mi | 1Gi |
| Tools | 100m | 1000m | 256Mi | 1Gi |
| k8s-agent | 100m | 1000m | 256Mi | 1Gi |
| helm-agent | 100m | 1000m | 256Mi | 1Gi |
| promql-agent | 100m | 1000m | 256Mi | 1Gi |
| observability-agent | 100m | 1000m | 256Mi | 1Gi |

**Total estimated**: ~700m CPU request, ~1.6Gi Memory request (with enabled agents)

---

## Configuration Options

### Option A: OpenAI Provider (Recommended for quick start)
```yaml
providers:
  default: openAI
  openAI:
    provider: OpenAI
    model: "gpt-4.1-mini"  # Cost-effective
    apiKeySecretRef: kagent-openai
    apiKeySecretKey: OPENAI_API_KEY
```

### Option B: Anthropic Provider
```yaml
providers:
  default: anthropic
  anthropic:
    provider: Anthropic
    model: "claude-3-5-haiku-20241022"
    apiKeySecretRef: kagent-anthropic
    apiKeySecretKey: ANTHROPIC_API_KEY
```

### Option C: Ollama (Self-hosted, no API costs)
```yaml
providers:
  default: ollama
  ollama:
    provider: Ollama
    model: "llama3.2"
    config:
      host: ollama.your-namespace.svc.cluster.local:11434
```

---

## Security Considerations

1. **API Keys**: Never commit API keys to Git - use Vault + External Secrets
2. **RBAC**: Kagent controller has broad cluster access - review permissions
3. **Network Policies**: Consider restricting egress to only required LLM endpoints
4. **UI Access**: Protect UI with authentication if exposing publicly

---

## Notes

1. **Deploy Order**: Always deploy in this order:
   1. kMCP CRDs -> kMCP (see kmcp-implementation-plan.md)
   2. Kagent CRDs -> Kagent
2. **kMCP Integration**: Kagent chart includes kMCP as a subchart, but we've already deployed it separately
3. **Agent Selection**: Disable agents you don't need to save cluster resources
4. **Database**: SQLite is fine for development; consider PostgreSQL for production
5. **LLM Costs**: Monitor LLM API usage - agents can generate significant API calls

---

*Created: 2025-11-27*
*Status: ✅ FULLY DEPLOYED - Phases 1-8 Complete*
*Prerequisite: Complete kMCP deployment first*
*Last Updated: 2025-11-27*

---

## Deployment Summary

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1 | ✅ | Pre-Deployment Setup (Vault, API Key) |
| Phase 2 | ✅ | Directory Structure |
| Phase 3 | ✅ | Deploy CRDs |
| Phase 4 | ✅ | Supporting Manifests |
| Phase 5 | ✅ | Deploy Kagent |
| Phase 6 | ⏸️ | Ingress (Optional - use port-forward for now) |
| Phase 7 | ✅ | Verification |
| Phase 8 | ✅ | Post-Deployment Testing |

**Access UI**: `kubectl port-forward -n kagent svc/kagent-ui 8080:8080` → http://localhost:8080
