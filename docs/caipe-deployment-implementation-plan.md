# CAIPE Multi-Agent Platform Deployment - Implementation Plan

**Date:** 2025-11-23
**Target Cluster:** homelab-k3s (dev-eks compatible)
**Deployment Method:** GitOps via ArgoCD
**Chart Version:** ai-platform-engineering v0.4.11 (App v0.2.1)

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Phase 1: Vault Secrets Preparation](#phase-1-vault-secrets-preparation)
5. [Phase 2: GitOps Repository Structure](#phase-2-gitops-repository-structure)
6. [Phase 3: Deploy Supervisor Agent](#phase-3-deploy-supervisor-agent)
7. [Phase 4: Deploy Sub-Agents](#phase-4-deploy-sub-agents)
8. [Phase 5: Deploy Backstage Agent Forge UI](#phase-5-deploy-backstage-agent-forge-ui)
9. [Phase 6: Testing & Validation](#phase-6-testing--validation)
10. [Phase 7: OnCall Agent Integration (Optional)](#phase-7-oncall-agent-integration-optional)
11. [Troubleshooting](#troubleshooting)
12. [References](#references)

---

## Overview

### What is CAIPE?

**CAIPE (Community AI Platform Engineering)** is an open-source Multi-Agent System (MAS) that enables intelligent automation for platform engineering tasks. It uses a supervisor-based architecture where specialized sub-agents handle specific domains (GitHub, ArgoCD, Kubernetes, etc.).

### Deployment Goals

Deploy CAIPE to homelab Kubernetes cluster with:
- ✅ Supervisor agent for orchestration
- ✅ agent-github for GitHub operations
- ✅ agent-argocd for ArgoCD management
- ✅ Backstage Agent Forge UI for web interface
- ✅ Anthropic Claude integration (Sonnet 4 / Haiku 3.5)
- ✅ Vault-based secret management via External Secrets Operator
- ✅ GitOps workflow via ArgoCD

### Components Overview

| Component | Image | Purpose | Ports |
|-----------|-------|---------|-------|
| **Supervisor** | ghcr.io/cnoe-io/ai-platform-engineering:stable | Task routing & orchestration | 8000 (A2A) |
| **Agent-GitHub** | ghcr.io/cnoe-io/agent-github:stable | GitHub operations | 8000 (A2A) |
| **Agent-ArgoCD** | ghcr.io/cnoe-io/agent-argocd:stable | ArgoCD management | 8000 (A2A) |
| **MCP-ArgoCD** | ghcr.io/cnoe-io/mcp-argocd:stable | ArgoCD MCP server | 8000 (HTTP) |
| **Backstage Forge** | ghcr.io/cnoe-io/backstage-plugin-agent-forge:latest | Web UI | 3000 (UI), 7007 (API) |

---

## Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Backstage Agent Forge UI                  │
│                  (Port 3000 - User Interface)                │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP
                         ↓
┌─────────────────────────────────────────────────────────────┐
│                    Supervisor Agent                          │
│              (Orchestrator - Port 8000 A2A)                  │
│                                                              │
│  • Routes tasks to specialized sub-agents                   │
│  • Manages conversation context                             │
│  • Coordinates multi-agent workflows                         │
└───────┬─────────────────────┬───────────────────────────────┘
        │ A2A Protocol        │ A2A Protocol
        ↓                     ↓
┌──────────────────┐  ┌──────────────────────────────┐
│  Agent-GitHub    │  │     Agent-ArgoCD             │
│  (Port 8000)     │  │     (Port 8000)              │
│                  │  │                              │
│  Uses Remote     │  │  ┌─────────────────────────┐ │
│  MCP Server      │  │  │   MCP-ArgoCD Server     │ │
│  (GitHub API)    │  │  │   (Port 8000)           │ │
│                  │  │  └─────────────────────────┘ │
└──────────────────┘  └──────────────────────────────┘
```

### Request Flow

1. User sends query via Backstage UI: "List recent GitHub PRs for repo X"
2. Backstage → Supervisor (HTTP POST /a2a/invoke)
3. Supervisor analyzes query, determines agent-github is needed
4. Supervisor → Agent-GitHub (A2A protocol)
5. Agent-GitHub → GitHub API (via remote MCP server)
6. Response flows back: Agent → Supervisor → Backstage → User

### Model Selection Strategy

**Recommended Configuration:**

| Component | Model | Reason |
|-----------|-------|---------|
| **Supervisor** | claude-sonnet-4-20250514 | Complex routing decisions |
| **Agent-GitHub** | claude-3-5-haiku-20241022 | Fast, cheap for simple ops |
| **Agent-ArgoCD** | claude-sonnet-4-20250514 | Complex K8s reasoning |

**Simple Setup (all use same model):**
- Set `ANTHROPIC_MODEL_NAME` globally in Vault
- All agents inherit this default

**Advanced Setup (per-agent models):**
- Override `ANTHROPIC_MODEL_NAME` in values.yaml per agent
- Optimize cost/performance trade-offs

---

## Prerequisites

### Cluster Requirements

- ✅ Kubernetes cluster running (K3s, GKE, EKS)
- ✅ ArgoCD installed and configured
- ✅ External Secrets Operator installed
- ✅ Vault backend configured with Kubernetes auth
- ✅ nginx-ingress-controller installed
- ✅ cert-manager installed (for TLS certificates)

### Resource Requirements

**Minimum cluster capacity:**
- CPU: 1.5 cores available
- Memory: 3.5 GB available
- Storage: 5 GB for logs/data

**Per-component resources:**
```yaml
supervisor-agent:     250m CPU, 512Mi RAM (limits: 500m/1Gi)
agent-github:         100m CPU, 256Mi RAM (limits: 200m/512Mi)
agent-argocd:         100m CPU, 256Mi RAM (limits: 200m/512Mi)
mcp-argocd:           50m CPU,  128Mi RAM (limits: 100m/256Mi)
backstage-forge:      250m CPU, 512Mi RAM (limits: 500m/1Gi)
```

### Required Credentials

1. **Anthropic API Key** - For Claude model access
2. **GitHub Personal Access Token** - For GitHub agent (scopes: `repo`, `read:org`, `read:user`)
3. **ArgoCD Token** - For ArgoCD agent (account with read/write access)

### Verify Prerequisites

```bash
# Check ArgoCD is running
kubectl get pods -n argocd

# Check External Secrets Operator
kubectl get pods -n external-secrets

# Check Vault connectivity
kubectl exec -n external-secrets deploy/external-secrets -c external-secrets -- \
  wget -q -O- http://vault.vault.svc.cluster.local:8200/v1/sys/health

# Check nginx-ingress
kubectl get pods -n ingress-nginx

# Check available resources
kubectl top nodes
```

---

## Phase 1: Vault Secrets Preparation

### 1.1 Create Vault Secrets for LLM (Global)

**Path:** `k8s-secrets/ai-platform/llm`

```bash
# Option A: Use Sonnet 4 for all agents (recommended starting point)
kubectl exec -n vault vault-0 -- vault kv put secret/k8s-secrets/ai-platform/llm \
  LLM_PROVIDER="anthropic-claude" \
  ANTHROPIC_API_KEY="sk-ant-api03-YOUR-KEY-HERE" \
  ANTHROPIC_MODEL_NAME="claude-sonnet-4-20250514"

# Option B: Use Haiku 3.5 for cost savings
kubectl exec -n vault vault-0 -- vault kv put secret/k8s-secrets/ai-platform/llm \
  LLM_PROVIDER="anthropic-claude" \
  ANTHROPIC_API_KEY="sk-ant-api03-YOUR-KEY-HERE" \
  ANTHROPIC_MODEL_NAME="claude-3-5-haiku-20241022"
```

**Verify:**
```bash
kubectl exec -n vault vault-0 -- vault kv get secret/k8s-secrets/ai-platform/llm
```

### 1.2 Create GitHub Agent Secrets

**Path:** `k8s-secrets/ai-platform/github-agent`

```bash
# Create GitHub PAT at: https://github.com/settings/tokens
# Required scopes: repo, read:org, read:user

kubectl exec -n vault vault-0 -- vault kv put secret/k8s-secrets/ai-platform/github-agent \
  GITHUB_PERSONAL_ACCESS_TOKEN="ghp_YOUR_TOKEN_HERE"
```

**Verify:**
```bash
kubectl exec -n vault vault-0 -- vault kv get secret/k8s-secrets/ai-platform/github-agent
```

### 1.3 Create ArgoCD Agent Secrets

**Generate ArgoCD token:**

```bash
# Get ArgoCD admin password
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d

# Login to ArgoCD
argocd login argocd-server.argocd.svc.cluster.local --username admin --password <password>

# Create token for CAIPE (expires in 1 year)
argocd account generate-token --account caipe-agent --id caipe-agent --expires-in 8760h

# Save token output
```

**Store in Vault:**

**Path:** `k8s-secrets/ai-platform/argocd-agent`

```bash
kubectl exec -n vault vault-0 -- vault kv put secret/k8s-secrets/ai-platform/argocd-agent \
  ARGOCD_TOKEN="<token-from-previous-step>" \
  ARGOCD_API_URL="http://argocd-server.argocd.svc.cluster.local" \
  ARGOCD_VERIFY_SSL="false"
```

**Verify:**
```bash
kubectl exec -n vault vault-0 -- vault kv get secret/k8s-secrets/ai-platform/argocd-agent
```

### 1.4 Configure Vault Kubernetes Auth for ai-platform Namespace

```bash
# Create Kubernetes auth role for ai-platform namespace
kubectl exec -n vault vault-0 -- vault write auth/kubernetes/role/ai-platform \
  bound_service_account_names=default \
  bound_service_account_namespaces=ai-platform \
  policies=ai-platform-policy \
  ttl=24h

# Create policy for ai-platform secrets
kubectl exec -n vault vault-0 -- vault policy write ai-platform-policy - <<EOF
path "secret/data/k8s-secrets/ai-platform/*" {
  capabilities = ["read", "list"]
}
EOF
```

**Verify:**
```bash
kubectl exec -n vault vault-0 -- vault read auth/kubernetes/role/ai-platform
```

---

## Phase 2: GitOps Repository Structure

### 2.1 Create Directory Structure

```bash
cd /Users/arisela/git/claude-agents/docs/reference/kubernetes

# Create ai-platform directory
mkdir -p base-apps/ai-platform/{external-secrets,configmaps}

# Directory structure:
# base-apps/
# ├── ai-platform.yaml                     # ArgoCD Application
# └── ai-platform/                         # Application directory
#     ├── namespace.yaml
#     ├── external-secrets/
#     │   ├── secret-store.yaml
#     │   ├── llm-secret.yaml
#     │   ├── github-agent-secret.yaml
#     │   └── argocd-agent-secret.yaml
#     ├── configmaps/
#     │   ├── prompt-config.yaml
#     │   └── supervisor-env-config.yaml
#     ├── values.yaml
#     ├── ingress-backstage.yaml
#     └── README.md
```

### 2.2 Create Namespace

**File:** `base-apps/ai-platform/namespace.yaml`

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: ai-platform
  labels:
    name: ai-platform
    app.kubernetes.io/managed-by: argocd
```

### 2.3 Create Vault SecretStore

**File:** `base-apps/ai-platform/external-secrets/secret-store.yaml`

```yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: ai-platform
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "ai-platform"
          serviceAccountRef:
            name: "default"
```

### 2.4 Create External Secrets

**File:** `base-apps/ai-platform/external-secrets/llm-secret.yaml`

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: llm-secret
  namespace: ai-platform
spec:
  refreshInterval: 15s
  secretStoreRef:
    kind: SecretStore
    name: vault-backend
  target:
    name: llm-secret
    creationPolicy: Owner
  data:
    - secretKey: LLM_PROVIDER
      remoteRef:
        key: ai-platform/llm
        property: LLM_PROVIDER
    - secretKey: ANTHROPIC_API_KEY
      remoteRef:
        key: ai-platform/llm
        property: ANTHROPIC_API_KEY
    - secretKey: ANTHROPIC_MODEL_NAME
      remoteRef:
        key: ai-platform/llm
        property: ANTHROPIC_MODEL_NAME
```

**File:** `base-apps/ai-platform/external-secrets/github-agent-secret.yaml`

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: github-agent-secret
  namespace: ai-platform
spec:
  refreshInterval: 15s
  secretStoreRef:
    kind: SecretStore
    name: vault-backend
  target:
    name: github-agent-secret
    creationPolicy: Owner
  data:
    - secretKey: GITHUB_PERSONAL_ACCESS_TOKEN
      remoteRef:
        key: ai-platform/github-agent
        property: GITHUB_PERSONAL_ACCESS_TOKEN
```

**File:** `base-apps/ai-platform/external-secrets/argocd-agent-secret.yaml`

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: argocd-agent-secret
  namespace: ai-platform
spec:
  refreshInterval: 15s
  secretStoreRef:
    kind: SecretStore
    name: vault-backend
  target:
    name: argocd-agent-secret
    creationPolicy: Owner
  data:
    - secretKey: ARGOCD_TOKEN
      remoteRef:
        key: ai-platform/argocd-agent
        property: ARGOCD_TOKEN
    - secretKey: ARGOCD_API_URL
      remoteRef:
        key: ai-platform/argocd-agent
        property: ARGOCD_API_URL
    - secretKey: ARGOCD_VERIFY_SSL
      remoteRef:
        key: ai-platform/argocd-agent
        property: ARGOCD_VERIFY_SSL
```

### 2.5 Create ConfigMaps

**File:** `base-apps/ai-platform/configmaps/prompt-config.yaml`

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: prompt-config
  namespace: ai-platform
data:
  prompt_config.yaml: |
    # CAIPE Supervisor Routing Configuration
    # This configures how the supervisor routes tasks to sub-agents

    agent_name: "Platform Engineering Assistant"

    system_prompt_template: |
      You are an expert Platform Engineering assistant with access to specialized sub-agents.

      **Available Sub-Agents:**
      - agent-github: GitHub repository operations (PRs, issues, commits, branches)
      - agent-argocd: ArgoCD application management (deployments, sync status, health)

      **Routing Guidelines:**
      1. For GitHub-related queries → Use agent-github
      2. For ArgoCD/deployment queries → Use agent-argocd
      3. For multi-domain tasks → Coordinate multiple agents sequentially

      **Response Format:**
      - Provide clear, actionable responses
      - Include relevant details (PR numbers, app status, etc.)
      - Suggest next steps when appropriate

      Think step-by-step and route tasks to the appropriate sub-agent.

    sub_agents:
      - name: "agent-github"
        host: "agent-github"
        port: 8000
        description: "Handles GitHub repository operations including PRs, issues, commits, and branches"
        capabilities:
          - "List pull requests"
          - "Get PR details and diff"
          - "List issues and comments"
          - "Search code and repositories"
          - "Get commit history"
          - "Manage branches"

      - name: "agent-argocd"
        host: "agent-argocd"
        port: 8000
        description: "Manages ArgoCD applications, deployments, and synchronization"
        capabilities:
          - "List applications"
          - "Get application status and health"
          - "Trigger application sync"
          - "View sync history"
          - "Manage application resources"
```

**File:** `base-apps/ai-platform/configmaps/supervisor-env-config.yaml`

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: supervisor-env-config
  namespace: ai-platform
data:
  # Supervisor configuration
  EXTERNAL_URL: "http://localhost:8000"
  SKIP_AGENT_CONNECTIVITY_CHECK: "false"
  AGENT_CONNECTIVITY_ENABLE_BACKGROUND: "true"

  # A2A Protocol configuration
  A2A_TRANSPORT: "p2p"  # Options: p2p (single cluster), slim (multi-cluster)

  # Agent endpoints (Kubernetes DNS)
  ENABLE_GITHUB: "true"
  GITHUB_AGENT_HOST: "agent-github"
  GITHUB_AGENT_PORT: "8000"

  ENABLE_ARGOCD: "true"
  ARGOCD_AGENT_HOST: "agent-argocd"
  ARGOCD_AGENT_PORT: "8000"
```

### 2.6 Create Helm Values

**File:** `base-apps/ai-platform/values.yaml`

```yaml
# CAIPE Helm Chart Values
# Chart: ai-platform-engineering v0.4.11

# Enable specific agents via tags
tags:
  basic: false
  complete: false
  backstage-agent-forge: true  # Enable Backstage UI
  agent-github: true            # Enable GitHub agent
  agent-argocd: true            # Enable ArgoCD agent
  # All other agents disabled
  agent-aws: false
  agent-backstage: false
  agent-confluence: false
  agent-jira: false
  agent-komodor: false
  agent-pagerduty: false
  agent-slack: false
  agent-splunk: false
  agent-webex: false
  rag-stack: false

# Global configuration
global:
  # Use External Secrets Operator for secret management
  createLlmSecret: false  # Secrets managed by ExternalSecret CRDs
  externalSecrets:
    enabled: true
    apiVersion: "v1beta1"

  llmSecrets:
    create: false
    secretName: "llm-secret"  # Created by ExternalSecret

  # Disable SLIM (multi-cluster) - we're using p2p (single cluster)
  slim:
    enabled: false

# Prompt configuration
promptConfigType: "default"  # Options: "default" or "deep_agent" (stricter mode)
# Custom prompt config loaded from ConfigMap (see configmaps/prompt-config.yaml)

######### Supervisor Agent Configuration #########
supervisor-agent:
  nameOverride: "supervisor-agent"

  image:
    repository: "ghcr.io/cnoe-io/ai-platform-engineering"
    tag: "stable"
    pullPolicy: "Always"
    args: ["platform-engineer"]

  # Environment variables from ConfigMap
  envFrom:
    - configMapRef:
        name: supervisor-env-config

  # LLM secret reference
  envFromSecrets:
    - secretRef:
        name: llm-secret

  # A2A protocol configuration
  multiAgentConfig:
    protocol: "a2a"
    port: "8000"

  # Resource limits
  resources:
    requests:
      memory: "512Mi"
      cpu: "250m"
    limits:
      memory: "1Gi"
      cpu: "500m"

  # Health checks
  livenessProbe:
    httpGet:
      path: /health
      port: 8000
    initialDelaySeconds: 30
    periodSeconds: 30

  readinessProbe:
    httpGet:
      path: /health
      port: 8000
    initialDelaySeconds: 10
    periodSeconds: 10

######### Agent-GitHub Configuration #########
agent-github:
  nameOverride: "agent-github"

  image:
    repository: "ghcr.io/cnoe-io/agent-github"
    tag: "stable"
    pullPolicy: "Always"

  # GitHub agent uses remote MCP server (no local MCP deployment)
  mcp:
    useRemoteMcpServer: true

  # Agent-specific secrets
  agentSecrets:
    create: false  # Managed by ExternalSecret
    secretName: "github-agent-secret"

  # LLM secret reference
  llmSecrets:
    secretName: "llm-secret"

  # Optional: Override model for GitHub agent (use Haiku for cost savings)
  env:
    ANTHROPIC_MODEL_NAME: "claude-3-5-haiku-20241022"  # Fast, cheap for simple ops
    # OR inherit global model by commenting out above line

  # Resource limits
  resources:
    requests:
      memory: "256Mi"
      cpu: "100m"
    limits:
      memory: "512Mi"
      cpu: "200m"

######### Agent-ArgoCD Configuration #########
agent-argocd:
  nameOverride: "agent-argocd"

  image:
    repository: "ghcr.io/cnoe-io/agent-argocd"
    tag: "stable"
    pullPolicy: "Always"

  # ArgoCD MCP server configuration
  mcp:
    image:
      repository: "ghcr.io/cnoe-io/mcp-argocd"
      tag: "stable"
      pullPolicy: "Always"
    mode: "http"  # Options: stdio, http (use http for network deployment)
    port: 8000

    # MCP server resource limits
    resources:
      requests:
        memory: "128Mi"
        cpu: "50m"
      limits:
        memory: "256Mi"
        cpu: "100m"

  # Agent-specific secrets
  agentSecrets:
    create: false  # Managed by ExternalSecret
    secretName: "argocd-agent-secret"

  # LLM secret reference
  llmSecrets:
    secretName: "llm-secret"

  # Optional: Override model for ArgoCD agent
  # env:
  #   ANTHROPIC_MODEL_NAME: "claude-sonnet-4-20250514"  # Complex K8s reasoning

  # Resource limits
  resources:
    requests:
      memory: "256Mi"
      cpu: "100m"
    limits:
      memory: "512Mi"
      cpu: "200m"

######### Backstage Agent Forge Configuration #########
backstage-plugin-agent-forge:
  nameOverride: "backstage-forge"

  image:
    repository: "ghcr.io/cnoe-io/backstage-plugin-agent-forge"
    tag: "latest"
    pullPolicy: "Always"

  # Service configuration
  service:
    type: ClusterIP
    ports:
      - name: http
        port: 3000
        targetPort: 3000
      - name: backend
        port: 7007
        targetPort: 7007

  # Environment variables
  env:
    SUPERVISOR_URL: "http://supervisor-agent:8000"

  # Resource limits
  resources:
    requests:
      memory: "512Mi"
      cpu: "250m"
    limits:
      memory: "1Gi"
      cpu: "500m"

  # Ingress disabled here - we'll create separate ingress with auth
  ingress:
    enabled: false
```

### 2.7 Create Backstage Ingress with Authentication

**File:** `base-apps/ai-platform/ingress-backstage.yaml`

**Option A: Basic Auth (Simple, recommended for homelab)**

First, create basic auth credentials:

```bash
# Generate htpasswd file
htpasswd -c auth caipe-user
# Enter password when prompted

# Encode to base64
cat auth | base64

# Store in Vault
kubectl exec -n vault vault-0 -- vault kv put secret/k8s-secrets/ai-platform/basic-auth \
  auth="$(cat auth | base64)"
```

Create ExternalSecret for basic auth:

**File:** `base-apps/ai-platform/external-secrets/basic-auth-secret.yaml`

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: backstage-basic-auth
  namespace: ai-platform
spec:
  refreshInterval: 15s
  secretStoreRef:
    kind: SecretStore
    name: vault-backend
  target:
    name: backstage-basic-auth
    creationPolicy: Owner
  data:
    - secretKey: auth
      remoteRef:
        key: ai-platform/basic-auth
        property: auth
```

Now create ingress:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: backstage-forge
  namespace: ai-platform
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/auth-type: basic
    nginx.ingress.kubernetes.io/auth-secret: backstage-basic-auth
    nginx.ingress.kubernetes.io/auth-realm: "CAIPE Platform - Authentication Required"
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - caipe.yourdomain.com  # CHANGE THIS
      secretName: backstage-forge-tls
  rules:
    - host: caipe.yourdomain.com  # CHANGE THIS
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: backstage-forge
                port:
                  number: 3000
```

**Option B: OAuth2 Proxy (Advanced)**

If you have existing OAuth2 proxy:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: backstage-forge
  namespace: ai-platform
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/auth-url: "https://oauth2-proxy.yourdomain.com/oauth2/auth"
    nginx.ingress.kubernetes.io/auth-signin: "https://oauth2-proxy.yourdomain.com/oauth2/start?rd=$escaped_request_uri"
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - caipe.yourdomain.com  # CHANGE THIS
      secretName: backstage-forge-tls
  rules:
    - host: caipe.yourdomain.com  # CHANGE THIS
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: backstage-forge
                port:
                  number: 3000
```

### 2.8 Create ArgoCD Application

**File:** `base-apps/ai-platform.yaml`

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: ai-platform
  namespace: argocd
  labels:
    app.kubernetes.io/name: ai-platform
spec:
  project: default

  source:
    repoURL: https://github.com/arigsela/kubernetes  # CHANGE THIS to your repo
    targetRevision: main
    path: base-apps/ai-platform

    # Helm chart configuration
    helm:
      valueFiles:
        - values.yaml

  destination:
    server: https://kubernetes.default.svc
    namespace: ai-platform

  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
    retry:
      limit: 5
      backoff:
        duration: 5s
        factor: 2
        maxDuration: 3m
```

### 2.9 Create README

**File:** `base-apps/ai-platform/README.md`

```markdown
# CAIPE Multi-Agent Platform

This directory contains the GitOps configuration for deploying CAIPE (Community AI Platform Engineering) to the homelab Kubernetes cluster.

## Components

- **Supervisor Agent**: Task orchestration and routing
- **Agent-GitHub**: GitHub operations (PRs, issues, commits)
- **Agent-ArgoCD**: ArgoCD application management
- **Backstage Agent Forge**: Web UI for interacting with agents

## Quick Start

1. Ensure secrets are configured in Vault (see main implementation plan)
2. Update ingress host in `ingress-backstage.yaml`
3. Commit and push to Git
4. ArgoCD will automatically deploy

## Access

- **Backstage UI**: https://caipe.yourdomain.com (basic auth required)
- **Supervisor API**: http://supervisor-agent.ai-platform.svc.cluster.local:8000

## Testing

```bash
# Check all pods are running
kubectl get pods -n ai-platform

# Check External Secrets synced
kubectl get externalsecrets -n ai-platform

# Test supervisor health
kubectl port-forward -n ai-platform svc/supervisor-agent 8000:8000
curl http://localhost:8000/health

# Access Backstage UI locally (bypass ingress)
kubectl port-forward -n ai-platform svc/backstage-forge 3000:3000
open http://localhost:3000
```

## Model Configuration

Default model: Set in Vault `ai-platform/llm` → `ANTHROPIC_MODEL_NAME`

Per-agent override: Edit `values.yaml` → `agent-github.env.ANTHROPIC_MODEL_NAME`

## Troubleshooting

See main implementation plan: `docs/caipe-deployment-implementation-plan.md`
```

---

## Phase 3: Deploy Supervisor Agent

### 3.1 Commit and Push GitOps Configuration

```bash
cd /Users/arisela/git/claude-agents/docs/reference/kubernetes

# Add all CAIPE files
git add base-apps/ai-platform.yaml
git add base-apps/ai-platform/

# Commit
git commit -m "Add CAIPE multi-agent platform deployment

- Supervisor agent for task orchestration
- agent-github for GitHub operations
- agent-argocd for ArgoCD management
- Backstage Agent Forge UI
- External Secrets integration with Vault
- Basic auth ingress for Backstage UI

Components:
- Helm chart: ai-platform-engineering v0.4.11
- LLM provider: Anthropic Claude (Sonnet 4 / Haiku 3.5)
- A2A protocol: p2p (single cluster)
"

# Push to main branch
git push origin main
```

### 3.2 Verify ArgoCD Syncs Application

```bash
# Check ArgoCD application status
kubectl get application -n argocd ai-platform

# Watch sync progress
argocd app get ai-platform --watch

# Or via kubectl
kubectl get application -n argocd ai-platform -o yaml
```

Expected output:
```
NAME: ai-platform
PROJECT: default
SERVER: https://kubernetes.default.svc
NAMESPACE: ai-platform
STATUS: Synced
HEALTH: Healthy
```

### 3.3 Verify Namespace and Resources Created

```bash
# Check namespace
kubectl get namespace ai-platform

# Check all resources
kubectl get all -n ai-platform

# Expected pods:
# - supervisor-agent-xxx
# - agent-github-xxx
# - agent-argocd-xxx
# - mcp-argocd-xxx
# - backstage-forge-xxx
```

### 3.4 Verify External Secrets Synced

```bash
# Check ExternalSecret status
kubectl get externalsecrets -n ai-platform

# Should show 4 ExternalSecrets:
# - llm-secret
# - github-agent-secret
# - argocd-agent-secret
# - backstage-basic-auth (if using basic auth)

# Check secrets created
kubectl get secrets -n ai-platform

# Verify secret data (without showing values)
kubectl get secret llm-secret -n ai-platform -o yaml | grep -E '(LLM_PROVIDER|ANTHROPIC)'
```

Expected output:
```
NAME                      STORE           REFRESH INTERVAL   STATUS
llm-secret                vault-backend   15s                SecretSynced
github-agent-secret       vault-backend   15s                SecretSynced
argocd-agent-secret       vault-backend   15s                SecretSynced
backstage-basic-auth      vault-backend   15s                SecretSynced
```

### 3.5 Check Supervisor Agent Health

```bash
# Check supervisor pod logs
kubectl logs -n ai-platform -l app=supervisor-agent --tail=50

# Check supervisor health endpoint
kubectl port-forward -n ai-platform svc/supervisor-agent 8000:8000 &
curl http://localhost:8000/health

# Kill port-forward
killall kubectl
```

Expected health response:
```json
{
  "status": "healthy",
  "agent": "supervisor-agent",
  "version": "0.2.1",
  "sub_agents": ["agent-github", "agent-argocd"]
}
```

### 3.6 Verify ConfigMap Mounted

```bash
# Check prompt config loaded
kubectl exec -n ai-platform deploy/supervisor-agent -- cat /app/prompt_config.yaml

# Should show the routing configuration from ConfigMap
```

---

## Phase 4: Deploy Sub-Agents

### 4.1 Verify Agent-GitHub Deployment

```bash
# Check agent-github pod
kubectl get pods -n ai-platform -l app=agent-github

# Check logs
kubectl logs -n ai-platform -l app=agent-github --tail=50

# Test health
kubectl port-forward -n ai-platform svc/agent-github 8001:8000 &
curl http://localhost:8001/health
killall kubectl
```

Expected health response:
```json
{
  "status": "healthy",
  "agent": "agent-github",
  "mcp_server": "remote",
  "capabilities": ["list_repos", "get_pr", "list_issues", ...]
}
```

### 4.2 Verify Agent-ArgoCD Deployment

```bash
# Check agent-argocd pod
kubectl get pods -n ai-platform -l app=agent-argocd

# Check MCP server pod
kubectl get pods -n ai-platform -l app=mcp-argocd

# Check agent logs
kubectl logs -n ai-platform -l app=agent-argocd --tail=50

# Check MCP server logs
kubectl logs -n ai-platform -l app=mcp-argocd --tail=50

# Test agent health
kubectl port-forward -n ai-platform svc/agent-argocd 8002:8000 &
curl http://localhost:8002/health
killall kubectl
```

Expected health response:
```json
{
  "status": "healthy",
  "agent": "agent-argocd",
  "mcp_server": "http://mcp-argocd:8000",
  "capabilities": ["list_apps", "get_app_status", "sync_app", ...]
}
```

### 4.3 Test Agent Connectivity from Supervisor

```bash
# Exec into supervisor pod
kubectl exec -it -n ai-platform deploy/supervisor-agent -- /bin/bash

# Test GitHub agent connectivity
curl http://agent-github:8000/health

# Test ArgoCD agent connectivity
curl http://agent-argocd:8000/health

# Exit pod
exit
```

Both should return healthy status.

---

## Phase 5: Deploy Backstage Agent Forge UI

### 5.1 Verify Backstage Deployment

```bash
# Check backstage-forge pod
kubectl get pods -n ai-platform -l app=backstage-forge

# Check logs
kubectl logs -n ai-platform -l app=backstage-forge --tail=50

# Check service
kubectl get svc -n ai-platform backstage-forge
```

### 5.2 Test Backstage Locally (Before Ingress)

```bash
# Port-forward to Backstage UI
kubectl port-forward -n ai-platform svc/backstage-forge 3000:3000

# Open browser
open http://localhost:3000
```

You should see the Backstage Agent Forge chat interface.

### 5.3 Verify Ingress Created

```bash
# Check ingress
kubectl get ingress -n ai-platform backstage-forge

# Check TLS certificate
kubectl get certificate -n ai-platform backstage-forge-tls

# Check ingress details
kubectl describe ingress -n ai-platform backstage-forge
```

Expected output:
```
NAME              CLASS   HOSTS                  ADDRESS         PORTS     AGE
backstage-forge   nginx   caipe.yourdomain.com   192.168.0.100   80, 443   5m
```

### 5.4 Test External Access via Ingress

```bash
# Test with curl (basic auth)
curl -u caipe-user:your-password https://caipe.yourdomain.com

# Or open in browser
open https://caipe.yourdomain.com
```

**Browser Test:**
1. Navigate to https://caipe.yourdomain.com
2. Enter basic auth credentials (caipe-user / password)
3. Should see Backstage Agent Forge chat interface

### 5.5 Verify Backstage → Supervisor Connection

Check Backstage logs for supervisor connectivity:

```bash
kubectl logs -n ai-platform -l app=backstage-forge | grep supervisor

# Should show successful connection to supervisor-agent:8000
```

---

## Phase 6: Testing & Validation

### 6.1 End-to-End Test: GitHub Agent

**Test Query:** "List the 5 most recent pull requests in the arigsela/kubernetes repository"

**Via Backstage UI:**
1. Open https://caipe.yourdomain.com
2. Enter query in chat: "List the 5 most recent pull requests in the arigsela/kubernetes repository"
3. Click Send
4. Observe response

**Via API (Direct):**

```bash
# Test supervisor A2A endpoint
kubectl port-forward -n ai-platform svc/supervisor-agent 8000:8000 &

curl -X POST http://localhost:8000/a2a/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "task": "List the 5 most recent pull requests in the arigsela/kubernetes repository",
    "context": {}
  }'

killall kubectl
```

**Expected Response:**
```json
{
  "result": "Here are the 5 most recent pull requests for arigsela/kubernetes:\n\n1. PR #42: Add CAIPE deployment (open) - created 2 hours ago\n2. PR #41: Update cert-manager config (merged) - created 1 day ago\n...",
  "status": "success",
  "agent": "agent-github"
}
```

### 6.2 End-to-End Test: ArgoCD Agent

**Test Query:** "What is the sync status of the chores-tracker-backend application in ArgoCD?"

**Via Backstage UI:**
1. Enter query: "What is the sync status of the chores-tracker-backend application?"
2. Observe response

**Via API:**

```bash
kubectl port-forward -n ai-platform svc/supervisor-agent 8000:8000 &

curl -X POST http://localhost:8000/a2a/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "task": "What is the sync status of the chores-tracker-backend application?",
    "context": {}
  }'

killall kubectl
```

**Expected Response:**
```json
{
  "result": "The chores-tracker-backend application status:\n- Sync Status: Synced\n- Health: Healthy\n- Last Sync: 2025-11-23 14:30:00\n- Revision: main (abc123)",
  "status": "success",
  "agent": "agent-argocd"
}
```

### 6.3 Multi-Agent Test

**Test Query:** "Check if there were any recent GitHub commits to the chores-tracker repo that might have caused the chores-tracker-backend ArgoCD application to sync"

This requires supervisor to:
1. Route to agent-github to get recent commits
2. Route to agent-argocd to get sync history
3. Correlate the information

**Via Backstage UI:**
1. Enter complex query
2. Observe supervisor coordinating multiple agents
3. Review comprehensive response

### 6.4 Monitor Logs During Testing

**Terminal 1: Supervisor logs**
```bash
kubectl logs -n ai-platform -l app=supervisor-agent -f
```

**Terminal 2: Agent-GitHub logs**
```bash
kubectl logs -n ai-platform -l app=agent-github -f
```

**Terminal 3: Agent-ArgoCD logs**
```bash
kubectl logs -n ai-platform -l app=agent-argocd -f
```

**Terminal 4: Backstage logs**
```bash
kubectl logs -n ai-platform -l app=backstage-forge -f
```

### 6.5 Performance Validation

**Measure response times:**

```bash
# Test simple GitHub query
time curl -X POST http://localhost:8000/a2a/invoke \
  -H "Content-Type: application/json" \
  -d '{"task": "List PRs in arigsela/kubernetes"}'

# Expected: < 5 seconds (with Haiku 3.5)
# Expected: < 10 seconds (with Sonnet 4)
```

**Check resource usage:**

```bash
# CPU and memory usage
kubectl top pods -n ai-platform

# Should be within limits:
# supervisor: < 500m CPU, < 1Gi RAM
# agents: < 200m CPU, < 512Mi RAM
```

---

## Phase 7: OnCall Agent Integration (Optional)

This phase integrates your existing `oncall` agent as a CAIPE sub-agent.

### 7.1 Convert OnCall to Dual-Interface

Modify oncall to expose both:
- Port 8000: Existing FastAPI REST API (for n8n)
- Port 8001: New A2A protocol handler (for CAIPE)

**See:** `docs/CAIPE-Agent-SDK-Options.md` for detailed A2A handler implementation.

### 7.2 Add OnCall to CAIPE Values

Update `base-apps/ai-platform/values.yaml`:

```yaml
# Add oncall agent configuration
agent-oncall:
  nameOverride: "agent-oncall"

  image:
    repository: "852893458518.dkr.ecr.us-east-2.amazonaws.com/oncall-agent"
    tag: "v0.1.0-a2a"  # New tag with A2A support
    pullPolicy: "Always"

  service:
    type: ClusterIP
    ports:
      - name: api
        port: 8000        # FastAPI REST API
        targetPort: 8000
      - name: a2a
        port: 8001        # A2A protocol
        targetPort: 8001

  # OnCall uses existing secrets (oncall-agent-api-secrets)
  agentSecrets:
    create: false
    secretName: "oncall-agent-api-secrets"

  llmSecrets:
    secretName: "llm-secret"

  resources:
    requests:
      memory: "512Mi"
      cpu: "500m"
    limits:
      memory: "1Gi"
      cpu: "1000m"
```

### 7.3 Update Supervisor ConfigMap

Add oncall to routing configuration:

```yaml
# base-apps/ai-platform/configmaps/prompt-config.yaml
sub_agents:
  # ... existing agents ...

  - name: "agent-oncall"
    host: "agent-oncall"
    port: 8001  # A2A port
    description: "Kubernetes troubleshooting and incident response agent"
    capabilities:
      - "Diagnose pod failures and restarts"
      - "Analyze logs for error patterns"
      - "Check deployment health and history"
      - "Correlate incidents with recent changes"
      - "AWS NAT gateway analysis"
      - "GitHub deployment correlation"
```

### 7.4 Update Supervisor Environment

```yaml
# base-apps/ai-platform/configmaps/supervisor-env-config.yaml
data:
  # ... existing config ...

  ENABLE_ONCALL: "true"
  ONCALL_AGENT_HOST: "agent-oncall"
  ONCALL_AGENT_PORT: "8001"  # A2A port
```

### 7.5 Test OnCall Integration

**Test Query:** "Check if there are any pods in CrashLoopBackOff state in the proteus-dev namespace"

This routes to oncall agent for K8s troubleshooting.

---

## Troubleshooting

### Issue: ExternalSecret Not Syncing

**Symptoms:**
```
kubectl get externalsecrets -n ai-platform
# Shows: SecretSyncError
```

**Debug:**
```bash
# Check ExternalSecret events
kubectl describe externalsecret llm-secret -n ai-platform

# Common issues:
# 1. Vault role not configured
# 2. Secret path incorrect
# 3. Service account not authorized

# Verify Vault role
kubectl exec -n vault vault-0 -- vault read auth/kubernetes/role/ai-platform

# Verify secret exists in Vault
kubectl exec -n vault vault-0 -- vault kv get secret/k8s-secrets/ai-platform/llm
```

**Fix:**
```bash
# Recreate Vault role if needed
kubectl exec -n vault vault-0 -- vault write auth/kubernetes/role/ai-platform \
  bound_service_account_names=default \
  bound_service_account_namespaces=ai-platform \
  policies=ai-platform-policy \
  ttl=24h
```

### Issue: Supervisor Can't Connect to Sub-Agents

**Symptoms:**
```
kubectl logs -n ai-platform -l app=supervisor-agent
# Shows: "Failed to connect to agent-github: connection refused"
```

**Debug:**
```bash
# Check agent pods are running
kubectl get pods -n ai-platform

# Check agent service endpoints
kubectl get endpoints -n ai-platform

# Test connectivity from supervisor pod
kubectl exec -n ai-platform deploy/supervisor-agent -- curl http://agent-github:8000/health
```

**Fix:**
```bash
# If agent pods aren't running, check deployment
kubectl describe deployment -n ai-platform agent-github

# Check for image pull errors, resource limits, etc.
kubectl get events -n ai-platform --sort-by='.lastTimestamp'
```

### Issue: Backstage UI Returns 502 Bad Gateway

**Symptoms:**
- Ingress works but returns 502 error
- Basic auth prompts correctly

**Debug:**
```bash
# Check backstage pod logs
kubectl logs -n ai-platform -l app=backstage-forge

# Check service endpoints
kubectl get endpoints -n ai-platform backstage-forge

# Test backstage health directly
kubectl exec -n ai-platform deploy/backstage-forge -- curl http://localhost:3000
```

**Fix:**
```bash
# If health check fails, check environment variables
kubectl exec -n ai-platform deploy/backstage-forge -- env | grep SUPERVISOR

# Should show: SUPERVISOR_URL=http://supervisor-agent:8000
```

### Issue: GitHub Agent Authentication Fails

**Symptoms:**
```
kubectl logs -n ai-platform -l app=agent-github
# Shows: "GitHub API authentication failed: 401 Unauthorized"
```

**Debug:**
```bash
# Verify GitHub token in secret
kubectl get secret github-agent-secret -n ai-platform -o yaml

# Verify token has correct scopes
# Test token manually:
curl -H "Authorization: token ghp_YOUR_TOKEN" https://api.github.com/user
```

**Fix:**
```bash
# Regenerate GitHub token with correct scopes: repo, read:org, read:user
# Update in Vault
kubectl exec -n vault vault-0 -- vault kv put secret/k8s-secrets/ai-platform/github-agent \
  GITHUB_PERSONAL_ACCESS_TOKEN="ghp_NEW_TOKEN"

# Wait for ExternalSecret to sync (15s)
# Or force restart agent
kubectl rollout restart deployment -n ai-platform agent-github
```

### Issue: ArgoCD Agent Can't Access ArgoCD API

**Symptoms:**
```
kubectl logs -n ai-platform -l app=agent-argocd
# Shows: "ArgoCD API connection failed: connection refused"
```

**Debug:**
```bash
# Check ArgoCD server is accessible
kubectl exec -n ai-platform deploy/agent-argocd -- \
  curl http://argocd-server.argocd.svc.cluster.local

# Verify ArgoCD token is valid
kubectl exec -n vault vault-0 -- vault kv get secret/k8s-secrets/ai-platform/argocd-agent
```

**Fix:**
```bash
# Generate new ArgoCD token
argocd account generate-token --account caipe-agent --expires-in 8760h

# Update in Vault
kubectl exec -n vault vault-0 -- vault kv put secret/k8s-secrets/ai-platform/argocd-agent \
  ARGOCD_TOKEN="new-token" \
  ARGOCD_API_URL="http://argocd-server.argocd.svc.cluster.local" \
  ARGOCD_VERIFY_SSL="false"

# Restart agent
kubectl rollout restart deployment -n ai-platform agent-argocd
```

### Issue: High LLM Costs

**Symptoms:**
- Anthropic API bills are higher than expected
- Many LLM calls for simple queries

**Debug:**
```bash
# Check which model is configured
kubectl get secret llm-secret -n ai-platform -o jsonpath='{.data.ANTHROPIC_MODEL_NAME}' | base64 -d

# Check agent logs for token usage
kubectl logs -n ai-platform -l app=supervisor-agent | grep "tokens"
```

**Optimization:**
```bash
# Switch to Haiku 3.5 for cost savings (80% cheaper than Sonnet)
kubectl exec -n vault vault-0 -- vault kv put secret/k8s-secrets/ai-platform/llm \
  LLM_PROVIDER="anthropic-claude" \
  ANTHROPIC_API_KEY="sk-ant-api03-..." \
  ANTHROPIC_MODEL_NAME="claude-3-5-haiku-20241022"

# Or use per-agent model override in values.yaml
# - Haiku for simple agents (GitHub)
# - Sonnet for complex agents (ArgoCD)
```

### Issue: Pods Stuck in Pending State

**Symptoms:**
```
kubectl get pods -n ai-platform
# Shows: supervisor-agent-xxx  0/1  Pending
```

**Debug:**
```bash
# Check events
kubectl describe pod -n ai-platform <pod-name>

# Common issues:
# - Insufficient resources
# - Image pull errors
# - Node selector mismatch
```

**Fix:**
```bash
# Check node resources
kubectl top nodes

# Check resource requests in values.yaml
# Reduce if cluster is resource-constrained

# Or scale down other workloads temporarily
kubectl scale deployment -n <namespace> <deployment> --replicas=0
```

---

## References

### Documentation

- **CAIPE Documentation**: https://cnoe-io.github.io/ai-platform-engineering/
- **CAIPE GitHub**: https://github.com/cnoe-io/ai-platform-engineering
- **A2A Protocol**: https://github.com/cnoe-io/ai-platform-engineering/docs/architecture/a2a-protocol.md
- **Anthropic API**: https://docs.anthropic.com/
- **External Secrets Operator**: https://external-secrets.io/

### Helm Charts

- **Main Chart**: ai-platform-engineering v0.4.11
- **Dependencies**: supervisor-agent v0.2.2, agent v0.3.2, backstage-plugin-agent-forge v0.1.0
- **Chart Location**: https://github.com/cnoe-io/ai-platform-engineering/tree/main/charts

### Related Implementation Plans

- **OnCall Agent Deployment**: `oncall-agent-deployment-plan.md` (your existing oncall setup)
- **CAIPE Agent SDK Options**: `docs/CAIPE-Agent-SDK-Options.md` (Anthropic SDK vs LangGraph)
- **CAIPE Integration Strategy**: `docs/CAIPE/CAIPE-Integration-Strategy.md` (deployment patterns)

### Support

- **CAIPE Discussions**: https://github.com/cnoe-io/ai-platform-engineering/discussions
- **CNOE Community**: https://cnoe.io/

---

## Next Steps

After successful deployment:

1. ✅ **Monitor for 24-48 hours** - Ensure stability, check logs, monitor costs
2. ✅ **Tune model selection** - Adjust Haiku vs Sonnet based on cost/performance
3. ✅ **Add more agents** - Expand with agent-aws, agent-slack as needed
4. ✅ **Integrate oncall** - Deploy oncall as CAIPE sub-agent (Phase 7)
5. ✅ **Create workflows** - Document common multi-agent workflows
6. ✅ **Set up monitoring** - Add Prometheus metrics, Grafana dashboards
7. ✅ **Production hardening** - Review security, add rate limiting, improve auth

**Deployment Complete!** 🎉

You now have a fully functional AI Platform Engineering assistant running in your homelab Kubernetes cluster.
