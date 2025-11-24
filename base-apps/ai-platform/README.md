# CAIPE Multi-Agent Platform

This directory contains the GitOps configuration for deploying CAIPE (Community AI Platform Engineering) to the homelab Kubernetes cluster.

## Components

- **Supervisor Agent**: Task orchestration and routing
- **Agent-GitHub**: GitHub operations (PRs, issues, commits)
- **Agent-ArgoCD**: ArgoCD application management
- **Backstage Agent Forge**: Web UI for interacting with agents

## Configuration

- **Model**: Claude 3.5 Haiku (cost-optimized)
- **Access**: Internal only (ClusterIP service, no ingress)
- **Secrets**: Managed via Vault and External Secrets Operator
- **Namespace**: `ai-platform`

## Quick Start

1. Ensure secrets are configured in Vault (completed in Phase 1)
2. Commit and push to Git
3. ArgoCD will automatically deploy

## Access

Access Backstage UI via port-forward:
```bash
kubectl port-forward -n ai-platform svc/backstage-forge 3000:3000
# Open http://localhost:3000
```

Access Supervisor API:
```bash
kubectl port-forward -n ai-platform svc/supervisor-agent 8000:8000
# API available at http://localhost:8000
```

## Testing

```bash
# Check all pods are running
kubectl get pods -n ai-platform

# Check External Secrets synced
kubectl get externalsecrets -n ai-platform

# Test supervisor health
kubectl port-forward -n ai-platform svc/supervisor-agent 8000:8000
curl http://localhost:8000/health

# Access Backstage UI locally
kubectl port-forward -n ai-platform svc/backstage-forge 3000:3000
open http://localhost:3000
```

## Model Configuration

Default model: Claude 3.5 Haiku (set in Vault `ai-platform/llm` → `ANTHROPIC_MODEL_NAME`)

Per-agent override: Edit `values.yaml` → `agent-github.env.ANTHROPIC_MODEL_NAME`

## Troubleshooting

See main implementation plan: `docs/caipe-deployment-implementation-plan.md`

### Common Issues

**ExternalSecret not syncing:**
```bash
kubectl describe externalsecret llm-secret -n ai-platform
kubectl exec -n vault vault-0 -- vault kv get k8s-secrets/ai-platform/llm
```

**Pods not starting:**
```bash
kubectl describe pod -n ai-platform <pod-name>
kubectl logs -n ai-platform <pod-name>
```

**Supervisor can't connect to agents:**
```bash
kubectl exec -n ai-platform deploy/supervisor-agent -- curl http://agent-github:8000/health
kubectl get endpoints -n ai-platform
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Backstage Agent Forge UI (Port 3000)            │
│                     (ClusterIP Service)                      │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP
                         ↓
┌─────────────────────────────────────────────────────────────┐
│                  Supervisor Agent (Port 8000)                │
│                       A2A Orchestrator                       │
└───────┬─────────────────────┬───────────────────────────────┘
        │ A2A Protocol        │ A2A Protocol
        ↓                     ↓
┌──────────────────┐  ┌──────────────────────────────┐
│  Agent-GitHub    │  │     Agent-ArgoCD             │
│  (Port 8000)     │  │     (Port 8000)              │
│                  │  │  + MCP-ArgoCD Server         │
└──────────────────┘  └──────────────────────────────┘
```

## Related Documentation

- Implementation Plan: `docs/caipe-deployment-implementation-plan.md`
- CAIPE Docs: https://cnoe-io.github.io/ai-platform-engineering/
- GitHub: https://github.com/cnoe-io/ai-platform-engineering
