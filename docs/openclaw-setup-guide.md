# OpenClaw Setup Guide

This guide walks you through the complete setup for deploying OpenClaw on the K3s cluster — from creating the Slack app, through Vault provisioning and deployment, to sending your first message. Security best practices are baked into every step.

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Prerequisites](#prerequisites)
- [Step 1: Create the Slack App](#step-1-create-the-slack-app)
- [Step 2: Create an Anthropic API Key](#step-2-create-an-anthropic-api-key)
- [Step 3: Provision Vault Secrets](#step-3-provision-vault-secrets)
- [Step 4: Create the DNS Record](#step-4-create-the-dns-record)
- [Step 5: Merge the PR and Verify ArgoCD Sync](#step-5-merge-the-pr-and-verify-argocd-sync)
- [Step 6: Pair Your Device via the Control UI](#step-6-pair-your-device-via-the-control-ui)
- [Step 7: Connect Slack and Send Your First Message](#step-7-connect-slack-and-send-your-first-message)
- [Step 8: Run the Security Audit](#step-8-run-the-security-audit)
- [Security Hardening Summary](#security-hardening-summary)
- [Ongoing Operations](#ongoing-operations)
- [Threat Model Awareness](#threat-model-awareness)
- [Sources](#sources)

---

## Architecture Overview

The deployment consists of two ArgoCD Applications:

| Application | Source | Purpose |
|---|---|---|
| `openclaw` | Helm chart `openclaw` v1.3.18 from `serhanekicii.github.io/openclaw-helm` | Main deployment: containers, service, ingress, config, network policies |
| `openclaw-config` | Git repo `base-apps/openclaw-config/` | Vault SecretStore + ExternalSecret for API keys and tokens |

The Helm chart deploys:

- **Deployment** (1 replica, Recreate strategy) with OpenClaw main container + Chromium sidecar
- **PersistentVolumeClaim** (5Gi) for config, sessions, workspace, and skills
- **Service** on port 18789
- **Ingress** with TLS, IP whitelisting, and WebSocket-friendly timeouts
- **NetworkPolicy** with default-deny + explicit allow rules
- **ConfigMap** with the `openclaw.json` application config (Slack channel, gateway auth, security rules)
- Two init containers for config initialization and skill installation

### How Slack Connectivity Works

OpenClaw connects to Slack via **Socket Mode** — an outbound WebSocket connection from the pod to Slack's servers. This means:

- No public webhook URL is needed for Slack events
- The pod initiates the connection outbound, so the network policies allow it (egress to internet is permitted)
- The ingress/IP whitelisting protects the Control UI, not the Slack connection
- If the pod restarts, it reconnects automatically

---

## Prerequisites

Before starting, make sure you have:

- `kubectl` access to the K3s cluster
- Vault CLI or UI access (`vault.vault.svc.cluster.local:8200`)
- A Slack workspace where you have permission to install apps
- Access to the [Anthropic Console](https://console.anthropic.com) to create an API key
- DNS management access (Route 53)

---

## Step 1: Create the Slack App

### 1a. Create the app from manifest

Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App** > **From a manifest**.

Select your workspace, then paste this YAML manifest:

```yaml
display_information:
  name: OpenClaw
  description: OpenClaw AI assistant
  background_color: "#1a1a2e"
features:
  app_home:
    home_tab_enabled: false
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
  bot_user:
    display_name: OpenClaw
    always_online: true
  slash_commands:
    - command: /openclaw
      description: Send a message to OpenClaw
      usage_hint: "[your message]"
      should_escape: false
oauth_config:
  scopes:
    bot:
      - chat:write
      - channels:history
      - channels:read
      - groups:history
      - groups:read
      - im:history
      - im:read
      - im:write
      - mpim:history
      - mpim:read
      - users:read
      - app_mentions:read
      - assistant:write
      - reactions:read
      - reactions:write
      - pins:read
      - pins:write
      - emoji:read
      - files:read
      - files:write
      - commands
settings:
  event_subscriptions:
    bot_events:
      - app_mention
      - message.channels
      - message.groups
      - message.im
      - message.mpim
      - reaction_added
      - reaction_removed
  interactivity:
    is_enabled: true
  org_deploy_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
```

Click **Create**.

### 1b. Generate the App-Level Token

After creating the app, you'll land on the **Basic Information** page.

1. Scroll to **App-Level Tokens**
2. Click **Generate Token and Scopes**
3. Name it `openclaw-socket` and add the scope `connections:write`
4. Click **Generate**
5. Copy the token (starts with `xapp-`) — you'll need this for Vault

### 1c. Install to Workspace and Copy Bot Token

1. Go to **OAuth & Permissions** in the left sidebar
2. Click **Install to Workspace** and authorize
3. Copy the **Bot User OAuth Token** (starts with `xoxb-`) — you'll need this for Vault

### 1d. Verify Event Subscriptions

Go to **Event Subscriptions** in the left sidebar. It should already be enabled with the bot events from the manifest. If not, enable it and add:

- `app_mention`
- `message.channels`
- `message.groups`
- `message.im`
- `message.mpim`
- `reaction_added`
- `reaction_removed`

### 1e. Verify App Home

Go to **App Home** and confirm the **Messages Tab** is enabled. This allows users to DM the bot.

**Security notes on the Slack app:**
- We use **Socket Mode** (no public webhook endpoint needed). The connection is outbound-only from your pod to Slack.
- **No user token** is configured — the bot token handles all operations. User tokens (`xoxp-`) grant elevated access and should only be added if you have a specific need.
- `userTokenReadOnly: true` is set in the config as a safety net — even if a user token is added later, it can only be used for reads.
- `groupPolicy: allowlist` means the bot only responds in channels you explicitly approve — it won't leak into random channels.

---

## Step 2: Create an Anthropic API Key

1. Go to [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)
2. Click **Create Key**
3. Name it `openclaw-k3s` (or similar — something that identifies this specific deployment)
4. Copy the key (starts with `sk-ant-`)

**Security best practices:**
- Use a **dedicated key** for OpenClaw, separate from any personal or development usage. This lets you revoke it independently and track spend.
- **Set a spending limit** on this key if your plan supports it. This is the cheapest "oh no" protection you can buy — if the agent goes haywire or a prompt injection triggers excessive API calls, the limit caps the damage.
- The key is stored in Vault and injected as an environment variable. It never appears in Git, config files, or logs (`redactSensitiveToolData: true` is enabled).

---

## Step 3: Provision Vault Secrets

### 3a. Connect to Vault

```bash
# Port-forward to Vault if needed
kubectl port-forward -n vault svc/vault 8200:8200 &

# Authenticate
export VAULT_ADDR="http://127.0.0.1:8200"
vault login  # use your root/admin token
```

### 3b. Create the Vault Policy and Kubernetes Auth Role

```bash
# Create a policy scoped to only the openclaw path
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

**Security note:** The role is scoped to only the `openclaw` path and only the `default` service account in the `openclaw` namespace. This follows the per-namespace isolation pattern used by all other apps in this cluster.

### 3c. Generate the Gateway Token and Store All Secrets

```bash
# Generate a strong random gateway token
GATEWAY_TOKEN=$(openssl rand -base64 32)

# Write all secrets to Vault in one command
vault kv put k8s-secrets/openclaw \
  anthropic-api-key="sk-ant-YOUR-KEY-HERE" \
  gateway-token="$GATEWAY_TOKEN" \
  slack-app-token="xapp-YOUR-APP-TOKEN-HERE" \
  slack-bot-token="xoxb-YOUR-BOT-TOKEN-HERE"

# Save the gateway token somewhere safe — you'll need it for device pairing
echo "Gateway token: $GATEWAY_TOKEN"
```

**What each secret does:**

| Vault Property | Env Variable | Purpose |
|---|---|---|
| `anthropic-api-key` | `ANTHROPIC_API_KEY` | Authenticates with Anthropic's API for the Claude model |
| `gateway-token` | `OPENCLAW_GATEWAY_TOKEN` | Authenticates connections to the OpenClaw gateway (Control UI, WebSocket clients) |
| `slack-app-token` | `SLACK_APP_TOKEN` | Establishes the Socket Mode WebSocket connection to Slack |
| `slack-bot-token` | `SLACK_BOT_TOKEN` | Authenticates bot API operations (sending messages, reading channels, etc.) |

---

## Step 4: Create the DNS Record

Create an A record pointing `openclaw.arigsela.com` to your cluster's ingress IP.

```bash
# Find your ingress controller's external IP
kubectl get svc -n nginx-ingress
```

In Route 53 (or your DNS provider), create:

```
openclaw.arigsela.com  →  A  →  <your-ingress-IP>
```

Cert-manager will automatically provision a TLS certificate via the `letsencrypt-prod` ClusterIssuer once the DNS record propagates and ArgoCD syncs the ingress.

---

## Step 5: Merge the PR and Verify ArgoCD Sync

Once the PR is merged to `main`, ArgoCD automatically detects and syncs both applications.

### 5a. Monitor the sync

```bash
# Watch both applications appear and sync
kubectl get applications -n argo-cd | grep openclaw

# Expected output (after a minute or two):
# openclaw         Synced   Healthy
# openclaw-config  Synced   Healthy
```

### 5b. Verify the ExternalSecret synced

```bash
kubectl get externalsecret -n openclaw
# STATUS should be "SecretSynced"

# Verify the Kubernetes secret was created with all 4 keys
kubectl get secret openclaw-env-secret -n openclaw -o jsonpath='{.data}' | python3 -c "import sys,json; print('\n'.join(json.load(sys.stdin).keys()))"
# Should list: ANTHROPIC_API_KEY, OPENCLAW_GATEWAY_TOKEN, SLACK_APP_TOKEN, SLACK_BOT_TOKEN
```

If the ExternalSecret shows `SecretSyncedError`, check:
1. The Vault role exists: `vault read auth/kubernetes/role/openclaw`
2. The secret path exists: `vault kv get k8s-secrets/openclaw`
3. The SecretStore can reach Vault: `kubectl describe secretstore -n openclaw vault-backend`

### 5c. Verify the pod is running

```bash
kubectl get pods -n openclaw
# Should show Running with all containers ready

kubectl logs -n openclaw deploy/openclaw -c main --tail=50
# Look for: "Gateway listening on port 18789"
# Look for: "Slack: connected" (confirms Socket Mode is working)
```

### 5d. Verify network policies

```bash
kubectl get networkpolicy -n openclaw
# Should show the default-deny + allow rules
```

---

## Step 6: Pair Your Device via the Control UI

OpenClaw requires device pairing for the Control UI. This is a second authentication factor beyond the gateway token.

### Option A: Port-forward (recommended for first-time setup)

```bash
kubectl port-forward -n openclaw svc/openclaw 18789:18789
```

1. Open `http://localhost:18789` in your browser
2. Enter your gateway token (from Step 3c)
3. Click **Connect**
4. You'll see a pairing request notification. Approve it:

```bash
# In another terminal — list pending pairing requests
kubectl exec -n openclaw deploy/openclaw -c main -- openclaw gateway pair --list

# Approve your device
kubectl exec -n openclaw deploy/openclaw -c main -- openclaw gateway pair --approve <device-id>
```

### Option B: Via the ingress URL

1. Open `https://openclaw.arigsela.com` (from a whitelisted IP)
2. Enter your gateway token
3. Approve the pairing via kubectl exec as above

**Security note:** Device pairing means that even if someone obtains your gateway token, they can't use the Control UI without an approved device. We have `dangerouslyDisableDeviceAuth: false` explicitly set.

---

## Step 7: Connect Slack and Send Your First Message

### 7a. Verify the Slack connection

After the pod starts, check that Slack connected:

```bash
kubectl logs -n openclaw deploy/openclaw -c main --tail=100 | grep -i slack
# Look for: "Slack: connected" or "Slack socket mode: connected"
```

If you see connection errors, verify:
- The `SLACK_APP_TOKEN` starts with `xapp-` (not `xoxb-`)
- The `SLACK_BOT_TOKEN` starts with `xoxb-` (not `xapp-`)
- Socket Mode is enabled in your Slack app settings
- The App-Level Token has the `connections:write` scope

### 7b. DM the bot

1. Open Slack and find **OpenClaw** in your Apps section (left sidebar, under "Apps")
2. Send a direct message: `Hello, are you working?`
3. The bot should respond. If it asks for a pairing code, it will show you a code — approve it via the Control UI or kubectl.

**If the bot doesn't respond in DMs:**
- Verify the Messages Tab is enabled in your Slack app's **App Home** settings
- Check that `im:history` and `im:read` scopes were added (if scopes were added after initial install, you must **reinstall** the app to apply them)

### 7c. Add the bot to a channel

1. In Slack, go to the channel where you want OpenClaw
2. Type `/invite @OpenClaw` or click the channel settings and add the app
3. Mention the bot: `@OpenClaw what can you help me with?`

By default (`groupPolicy: allowlist`), the bot will only respond in channels you explicitly approve. To approve a channel, use the Control UI's Slack settings or tell the bot in a DM to add the channel.

### 7d. Allow specific users for DMs

OpenClaw uses allowlists for DMs too. To grant a user DM access:

1. Open the Control UI (`https://openclaw.arigsela.com` or via port-forward)
2. Navigate to the Slack channel settings
3. Add the user's Slack User ID to the DM allowlist

To find a Slack User ID: click on the user's profile in Slack > click the three dots > **Copy member ID**.

---

## Step 8: Run the Security Audit

After everything is running, use OpenClaw's built-in security audit:

```bash
# Basic audit
kubectl exec -n openclaw deploy/openclaw -c main -- openclaw security audit

# Deep audit (checks more things, takes longer)
kubectl exec -n openclaw deploy/openclaw -c main -- openclaw security audit --deep
```

Review the output and address any warnings. Common findings and responses:

| Finding | Expected? | Action |
|---|---|---|
| Gateway auth enabled | Yes (pass) | None — we configured token auth |
| Trusted proxies configured | Yes (pass) | None — set to K3s CIDRs |
| Device auth enabled | Yes (pass) | None — explicitly set to false for disable |
| Browser control enabled | Yes (expected) | The Chromium sidecar is part of the deployment — it's contained in the pod |
| Web search disabled | Yes (pass) | None — intentionally disabled to reduce prompt injection surface |

---

## Security Hardening Summary

### Network Layer

| Control | Configuration | Why |
|---|---|---|
| IP Whitelisting | `whitelist-source-range: 73.7.190.154/32,170.85.56.189/32` | Only your IPs can reach the Control UI — first line of defense |
| TLS Termination | cert-manager + letsencrypt-prod | All traffic encrypted; gateway sees HTTP from nginx |
| Network Policies | `networkpolicies.main.enabled: true` | Default-deny with explicit allows: ingress on 18789, DNS egress, internet egress (excluding private ranges). Limits blast radius if the pod is compromised |
| Trusted Proxies | `10.42.0.0/16`, `10.43.0.0/16` (K3s CIDRs) | Gateway trusts `X-Forwarded-For` from nginx only. Without this, gateway auto-approves all connections as localhost |

### Authentication Layer

| Control | Configuration | Why |
|---|---|---|
| Gateway Token | `${OPENCLAW_GATEWAY_TOKEN}` via Vault | Token auth is fail-closed — gateway refuses connections without it |
| Device Pairing | `dangerouslyDisableDeviceAuth: false` | Second factor: new devices need explicit kubectl approval |
| Slack Socket Mode | Outbound connection only | No inbound webhook URL to protect — connection initiates from pod |
| Slack Allowlists | `groupPolicy: allowlist` | Bot only responds in explicitly approved channels and DMs |

### Application Layer

| Control | Configuration | Why |
|---|---|---|
| Security Rules | 6 rules in `tools.securityRules` | Soft guardrails: no secret disclosure, no sensitive dir reads, no destructive commands, untrusted content handling |
| Config Mode | `overwrite` | Strict GitOps — config always matches Git, no drift |
| Sensitive Data Redaction | `redactSensitiveToolData: true` | API keys and secrets stay out of logs |
| Web Search Disabled | `tools.webSearch: false` | Reduces prompt injection surface from search results |
| Read-only User Token | `userTokenReadOnly: true` | Even if a user token is added later, it can only read |
| Agent Timeout | 600 seconds, 1 concurrent task | Limits runaway resource consumption |

### Container Layer (chart defaults)

| Control | Configuration | Why |
|---|---|---|
| Non-root | `runAsUser: 1000`, `runAsNonRoot: true` | Processes never run as root |
| Read-only rootfs | `readOnlyRootFilesystem: true` | Only PVC and emptyDir are writable |
| No privilege escalation | `allowPrivilegeEscalation: false` | Blocks setuid/setgid |
| All capabilities dropped | `capabilities.drop: [ALL]` | Minimal Linux capabilities |
| Resource Limits | 2 CPU / 2Gi (main), 1 CPU / 1Gi (chromium) | Prevents resource exhaustion |

---

## Ongoing Operations

### Rotating the Gateway Token

```bash
NEW_TOKEN=$(openssl rand -base64 32)
vault kv patch k8s-secrets/openclaw gateway-token="$NEW_TOKEN"

# Force immediate ExternalSecret refresh
kubectl annotate externalsecret -n openclaw openclaw-env-secret \
  force-sync=$(date +%s) --overwrite

# Restart the pod to pick up the new token
kubectl rollout restart deployment -n openclaw openclaw

echo "New gateway token: $NEW_TOKEN"
```

After rotating, you'll need to re-pair your devices.

### Rotating the Slack Tokens

If you regenerate tokens in the Slack app settings:

```bash
vault kv patch k8s-secrets/openclaw \
  slack-app-token="xapp-NEW-TOKEN" \
  slack-bot-token="xoxb-NEW-TOKEN"

kubectl annotate externalsecret -n openclaw openclaw-env-secret \
  force-sync=$(date +%s) --overwrite
kubectl rollout restart deployment -n openclaw openclaw
```

### Updating Whitelisted IPs

Edit `base-apps/openclaw.yaml` and update the `whitelist-source-range` annotation. Commit and push — ArgoCD handles the rest.

### Checking Logs

```bash
# Main container (agent, gateway, Slack channel)
kubectl logs -n openclaw deploy/openclaw -c main --tail=100 -f

# Chromium sidecar
kubectl logs -n openclaw deploy/openclaw -c chromium --tail=50

# Init container logs (if pod fails to start)
kubectl logs -n openclaw deploy/openclaw -c init-config
kubectl logs -n openclaw deploy/openclaw -c init-skills
```

### Installing Skills

The Helm chart's `init-skills` container installs skills declaratively at pod startup. The default installs the `weather` skill. To add more, update the chart values in `base-apps/openclaw.yaml`.

**A warning on skills:** Cisco's research found that roughly 26% of audited third-party ClawHub skills contained vulnerabilities, including data exfiltration. Before adding any skill:

1. Check the skill's GitHub repository — read the source code
2. Verify the publisher's identity and history
3. Check download counts and community reviews on ClawHub
4. Pin to a specific version (not `latest`)
5. Run `openclaw security audit --deep` after installing

For now, the `weather` skill (bundled by default) is the only one installed. It's maintained by the OpenClaw team and has been widely vetted.

---

## Threat Model Awareness

Even with all these controls, understand the residual risks:

1. **Prompt injection is the top risk.** OpenClaw processes untrusted input (web pages, documents, Slack messages). The security rules are soft guidance — a sophisticated injection could bypass them. Network policies and container isolation are the hard boundaries.

2. **Container escape is possible but contained.** If an attacker achieves code execution inside the pod, network policies block access to internal cluster services (private IP ranges denied in egress). They can reach the internet but not your other workloads.

3. **Slack is an input vector.** Anyone who can DM the bot or post in an allowed channel can send it instructions. The allowlist policy limits who can reach the bot, but treat all Slack messages as untrusted input.

4. **The Anthropic API key is the crown jewel.** A dedicated key with spending limits means a compromise burns a capped budget, not your entire account.

5. **Skills are arbitrary code.** ClawHub has had hundreds of malicious packages. Only install what you need, verify the source, and pin versions.

---

## Sources

This guide synthesizes configuration and security recommendations from:

- [OpenClaw Official Security Documentation](https://docs.openclaw.ai/gateway/security)
- [OpenClaw Slack Channel Documentation](https://docs.openclaw.ai/channels/slack)
- [OpenClaw Gateway Configuration](https://docs.openclaw.ai/gateway/configuration)
- [OpenClaw Configuration Examples](https://docs.openclaw.ai/gateway/configuration-examples)
- [Deploying OpenClaw on Kubernetes with Helm (Serhan Ekici)](https://serhanekici.com/openclaw-helm.html)
- [A Security-First Guide to Running OpenClaw in 9 Steps (Coding Nexus)](https://medium.com/coding-nexus/a-security-first-guide-to-running-openclaw-in-9-steps-d0a5edccf4ec)
- [LumaDock: OpenClaw Security Best Practices](https://lumadock.com/tutorials/openclaw-security-best-practices-guide)
- [DeepWiki: OpenClaw Gateway Configuration](https://deepwiki.com/openclaw/openclaw/3.1-gateway-configuration)
