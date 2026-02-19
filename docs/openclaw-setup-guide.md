# OpenClaw Setup Guide

This guide walks you through the initial setup for deploying OpenClaw on the K3s cluster using the SerhanEkicii Helm chart, managed through ArgoCD. It covers Vault secrets provisioning, DNS configuration, deployment verification, and device pairing — with security best practices baked in at every step.

## Architecture Overview

The deployment consists of two ArgoCD Applications:

| Application | Source | Purpose |
|---|---|---|
| `openclaw` | Helm chart `openclaw` v1.3.18 from `serhanekicii.github.io/openclaw-helm` | Main deployment: containers, service, ingress, config, network policies |
| `openclaw-config` | Git repo `base-apps/openclaw-config/` | Vault SecretStore + ExternalSecret for API keys and gateway token |

The Helm chart deploys:
- **Deployment** (1 replica, Recreate strategy) with OpenClaw main container + Chromium sidecar
- **PersistentVolumeClaim** (5Gi) for config, sessions, workspace, and skills
- **Service** on port 18789
- **Ingress** with TLS, IP whitelisting, and WebSocket timeouts
- **NetworkPolicy** with default-deny + explicit allow rules
- **ConfigMap** with the `openclaw.json` application config
- Two init containers for config initialization and skill installation

## Prerequisites

- kubectl access to the K3s cluster
- Vault CLI or UI access (at `vault.vault.svc.cluster.local:8200`)
- An Anthropic API key
- DNS management access (Route 53)

## Step 1: Create the Vault Role for OpenClaw

OpenClaw secrets are managed via the External Secrets Operator pulling from Vault. You need a Vault role that allows the `openclaw` namespace's default service account to read secrets.

```bash
# Port-forward to Vault if needed
kubectl port-forward -n vault svc/vault 8200:8200 &

# Authenticate to Vault
export VAULT_ADDR="http://127.0.0.1:8200"
vault login  # use your root/admin token

# Create a policy for OpenClaw
vault policy write openclaw - <<EOF
path "k8s-secrets/data/openclaw" {
  capabilities = ["read"]
}
path "k8s-secrets/metadata/openclaw" {
  capabilities = ["read", "list"]
}
EOF

# Create a Kubernetes auth role bound to the openclaw namespace
vault write auth/kubernetes/role/openclaw \
  bound_service_account_names=default \
  bound_service_account_namespaces=openclaw \
  policies=openclaw \
  ttl=1h
```

**Security note**: The role is scoped to only the `openclaw` path and only the `default` service account in the `openclaw` namespace. This follows the per-namespace isolation pattern used by all other apps in this cluster.

## Step 2: Store Secrets in Vault

You need two secrets: the Anthropic API key and a gateway authentication token.

### Generate a strong gateway token

```bash
GATEWAY_TOKEN=$(openssl rand -base64 32)
echo "Save this token somewhere safe: $GATEWAY_TOKEN"
```

### Write both secrets to Vault

```bash
vault kv put k8s-secrets/openclaw \
  anthropic-api-key="sk-ant-your-key-here" \
  gateway-token="$GATEWAY_TOKEN"
```

**Security best practices applied here:**
- API keys are stored in Vault, never committed to Git (referenced via `${ENV_VAR}` substitution in the OpenClaw config)
- The gateway token is randomly generated (32 bytes, base64) — not a human-chosen password
- Use a **dedicated** Anthropic API key for OpenClaw, separate from any other usage. This lets you set per-key spending limits and revoke independently if compromised
- If your Anthropic plan supports it, set a spending limit on this key — this is the cheapest "oh no" protection you can buy

## Step 3: Create DNS Record

Create an A record (or CNAME) pointing `openclaw.arigsela.com` to your cluster's ingress IP.

```bash
# Find your ingress controller's external IP
kubectl get svc -n nginx-ingress

# Then in Route 53 (or your DNS provider), create:
# openclaw.arigsela.com  →  A  →  <your-ingress-IP>
```

Cert-manager will automatically provision a TLS certificate via the `letsencrypt-prod` ClusterIssuer once the DNS record propagates and ArgoCD syncs the ingress.

## Step 4: Merge the PR and Verify ArgoCD Sync

Once the PR is merged to `main`, ArgoCD will automatically detect and sync both applications.

### Monitor the sync

```bash
# Watch both applications appear and sync
kubectl get applications -n argo-cd | grep openclaw

# Expected output (after a minute or two):
# openclaw         Synced   Healthy
# openclaw-config  Synced   Healthy
```

### Verify the ExternalSecret synced successfully

```bash
kubectl get externalsecret -n openclaw
# STATUS should be "SecretSynced"

# Verify the Kubernetes secret was created
kubectl get secret openclaw-env-secret -n openclaw
```

If the ExternalSecret shows `SecretSyncedError`, double-check:
1. The Vault role exists and is bound to `openclaw` namespace
2. The secret path `k8s-secrets/openclaw` exists with the right properties
3. The SecretStore can reach Vault (`kubectl describe secretstore -n openclaw vault-backend`)

### Verify the pod is running

```bash
kubectl get pods -n openclaw
# Should show 1/1 Running (or 2/2 if chromium sidecar counted separately)

kubectl logs -n openclaw deploy/openclaw -c main --tail=50
# Look for: "Gateway listening on port 18789"
```

### Verify network policies are active

```bash
kubectl get networkpolicy -n openclaw
# Should show the default-deny + allow rules
```

## Step 5: Verify Ingress and IP Whitelisting

### From a whitelisted IP

```bash
# Test HTTPS connectivity (from your whitelisted IP)
curl -I https://openclaw.arigsela.com
# Should return HTTP 200 or a WebSocket upgrade response
```

### From a non-whitelisted IP (verification)

```bash
# From a different network / VPN
curl -I https://openclaw.arigsela.com
# Should return HTTP 403 Forbidden
```

**Security notes on the ingress configuration:**
- **IP whitelisting** (`73.7.190.154/32, 170.85.56.189/32`): Only your IPs can reach the gateway. Update these in `base-apps/openclaw.yaml` if your IPs change.
- **TLS enforced**: `ssl-redirect` and `force-ssl-redirect` ensure no plaintext HTTP connections.
- **Extended timeouts** (600s): OpenClaw uses WebSocket connections for real-time communication. Standard 60s timeouts would drop active sessions.

## Step 6: Pair Your Device

OpenClaw requires device pairing for the Control UI. Since the gateway is behind nginx with `trustedProxies` configured, it correctly identifies your client IP rather than treating everything as localhost.

### Option A: Port-forward (recommended for first-time setup)

```bash
kubectl port-forward -n openclaw svc/openclaw 18789:18789
```

1. Open `http://localhost:18789` in your browser
2. Enter your gateway token (the one you stored in Vault)
3. Click **Connect**
4. Approve the pairing request:

```bash
# In another terminal, check for pending pairing requests
kubectl exec -n openclaw deploy/openclaw -c main -- openclaw gateway pair --list

# Approve the device
kubectl exec -n openclaw deploy/openclaw -c main -- openclaw gateway pair --approve <device-id>
```

### Option B: Via the ingress URL

1. Open `https://openclaw.arigsela.com` (from a whitelisted IP)
2. Enter your gateway token
3. Approve the pairing via kubectl exec as shown above

**Security note**: Device pairing adds a second layer of authentication beyond the gateway token. Even if someone obtains your token, they can't use the Control UI without an approved device identity. We have `dangerouslyDisableDeviceAuth: false` explicitly set to ensure this stays enabled.

## Security Hardening Summary

Here's what's configured and why, mapped to guidance from the security best practices sources:

### Network Layer

| Control | Configuration | Why |
|---|---|---|
| IP Whitelisting | `nginx.ingress.kubernetes.io/whitelist-source-range` | Only your IPs can reach the gateway — first line of defense |
| TLS Termination | cert-manager + letsencrypt-prod | Encrypts all traffic; the gateway sees HTTP from nginx |
| Network Policies | `networkpolicies.main.enabled: true` | Default-deny with explicit allows: ingress on 18789, DNS egress, internet egress (excluding private ranges). Limits blast radius if the pod is compromised |
| Trusted Proxies | `10.42.0.0/16`, `10.43.0.0/16` (K3s pod/service CIDRs) | Tells the gateway to trust `X-Forwarded-For` from nginx only. Without this, the gateway sees all connections as localhost and auto-approves them |

### Authentication Layer

| Control | Configuration | Why |
|---|---|---|
| Gateway Token | `${OPENCLAW_GATEWAY_TOKEN}` via Vault ExternalSecret | Token auth is required — gateway is fail-closed without it. Stored in Vault, never in Git |
| Device Pairing | `dangerouslyDisableDeviceAuth: false` | Second factor: even with the token, new devices need explicit approval via kubectl |
| Anthropic API Key | Via Vault ExternalSecret | Never stored in config files or Git. Dedicated key with spending limits recommended |

### Application Layer

| Control | Configuration | Why |
|---|---|---|
| Security Rules | 6 rules in `tools.securityRules` | Soft guardrails telling the agent to not reveal secrets, read sensitive dirs, run destructive commands, or trust embedded instructions |
| Config Mode | `overwrite` | Strict GitOps: config always matches what's in Git. No drift from UI changes persisting across restarts |
| Sensitive Data Redaction | `logging.redactSensitiveToolData: true` | Prevents API keys and secrets from appearing in logs |
| Web Search Disabled | `tools.webSearch: false` | Reduces attack surface from prompt injection via search results |
| Agent Timeout | 600 seconds, 1 concurrent task | Limits resource consumption from runaway tasks |

### Container Layer (chart defaults)

| Control | Configuration | Why |
|---|---|---|
| Non-root | `runAsUser: 1000`, `runAsNonRoot: true` | Container processes never run as root |
| Read-only rootfs | `readOnlyRootFilesystem: true` | Prevents writing to the container filesystem (only PVC and emptyDir are writable) |
| No privilege escalation | `allowPrivilegeEscalation: false` | Blocks setuid/setgid binaries |
| All capabilities dropped | `capabilities.drop: [ALL]` | Minimal Linux capabilities |
| Resource Limits | 2 CPU / 2Gi (main), 1 CPU / 1Gi (chromium) | Prevents resource exhaustion from runaway processes |

## Ongoing Operations

### Rotating the Gateway Token

```bash
# Generate a new token
NEW_TOKEN=$(openssl rand -base64 32)

# Update in Vault
vault kv patch k8s-secrets/openclaw gateway-token="$NEW_TOKEN"

# The ExternalSecret refreshes every hour. To force immediate refresh:
kubectl annotate externalsecret -n openclaw openclaw-env-secret force-sync=$(date +%s) --overwrite

# Restart the pod to pick up the new token
kubectl rollout restart deployment -n openclaw openclaw
```

### Updating Your Whitelisted IPs

Edit `base-apps/openclaw.yaml` and update the `whitelist-source-range` annotation. Commit and push — ArgoCD will update the ingress automatically.

### Checking OpenClaw Logs

```bash
# Main container logs
kubectl logs -n openclaw deploy/openclaw -c main --tail=100 -f

# Chromium sidecar logs
kubectl logs -n openclaw deploy/openclaw -c chromium --tail=50
```

### Running the Built-in Security Audit

```bash
kubectl exec -n openclaw deploy/openclaw -c main -- openclaw security audit
# For a deeper check:
kubectl exec -n openclaw deploy/openclaw -c main -- openclaw security audit --deep
```

## Threat Model Awareness

Even with all these controls, keep in mind:

1. **Prompt injection is the top risk**. OpenClaw processes untrusted input (web pages, documents, messages). The security rules are soft guidance — a sophisticated injection could bypass them. The network policies and container isolation are the hard boundaries.

2. **Container escape is possible but contained**. If an attacker achieves code execution inside the pod, network policies block access to internal cluster services (private IP ranges are denied in egress). They can reach the internet but not your other workloads.

3. **Skills are code**. If you install ClawHub skills, verify the package name and publisher. Pin versions for anything security-sensitive. Hundreds of malicious skills have been found on ClawHub.

4. **The Anthropic API key is the crown jewel**. A dedicated key with spending limits means a compromise burns a capped budget, not your entire account.

## Sources

This guide synthesizes security recommendations from:
- [OpenClaw Official Security Documentation](https://docs.openclaw.ai/gateway/security)
- [LumaDock: OpenClaw Security Best Practices Guide](https://lumadock.com/tutorials/openclaw-security-best-practices-guide)
- [Deploying OpenClaw on Kubernetes with Helm (Serhan Ekici)](https://serhanekici.com/openclaw-helm.html)
- [A Security-First Guide to Running OpenClaw (Medium / Coding Nexus)](https://medium.com/coding-nexus/a-security-first-guide-to-running-openclaw-in-9-steps-d0a5edccf4ec)
- [OpenClaw Gateway Configuration](https://docs.openclaw.ai/gateway/configuration)
