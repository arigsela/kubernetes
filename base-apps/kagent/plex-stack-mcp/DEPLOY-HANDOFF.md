# plex-stack-diagnostics — Deploy Handoff

The code + manifests are complete, tested (26/26), and all four manifests pass
`kubectl apply --dry-run=server` against the live cluster CRDs. What remains
needs your credentials / cloud access. **Do the steps in this order** — merging
before the image and Vault secret exist will leave the ArgoCD app Degraded
(ImagePullBackOff → RemoteMCPServer never ACCEPTED → Agent degraded), because
`kagent-secrets` auto-syncs with `prune: true`/`selfHeal: true`.

Branch: `feat/plex-stack-diagnostics-kagent` (worktree under the session scratchpad).

## 1. Build & push the MCP image to ECR
Registry/app/tag are already wired into `plex-stack-mcp.yaml`:
`852893458518.dkr.ecr.us-east-2.amazonaws.com/plex-stack-mcp:0.1.0`

```bash
cd base-apps/kagent/plex-stack-mcp
aws ecr get-login-password --region us-east-2 \
  | docker login --username AWS --password-stdin 852893458518.dkr.ecr.us-east-2.amazonaws.com
# create the repo once, if it doesn't exist:
aws ecr create-repository --repository-name plex-stack-mcp --region us-east-2 || true
docker build -t 852893458518.dkr.ecr.us-east-2.amazonaws.com/plex-stack-mcp:0.1.0 .
docker push 852893458518.dkr.ecr.us-east-2.amazonaws.com/plex-stack-mcp:0.1.0
```

## 2. Get the credentials
- **Family Plex token** (32401) and **Private Plex token** (32500): from each
  server — `Settings → Account`, or copy the `X-Plex-Token` from an authenticated
  request in the Plex web app. The two servers have distinct tokens.
- **qBittorrent API user**: in the qBittorrent WebUI → `Options → Web UI`, create
  a dedicated non-admin account for the agent (don't reuse your personal login).

## 3. Write the Vault secret
The ExternalSecret reads Vault KV `k8s-secrets` at key `plex-stack-mcp` via the
`vault-backend` SecretStore. The `vault` CLI isn't installed on the workstation —
either run it where you have Vault access, or exec into the in-cluster Vault pod:

```bash
# Option A — your vault CLI:
vault kv put k8s-secrets/plex-stack-mcp \
  plex_family_token='<FAMILY_TOKEN>' \
  plex_private_token='<PRIVATE_TOKEN>' \
  qbit_username='<BOT_USER>' \
  qbit_password='<BOT_PASS>'

# Option B — via the cluster (vault-0 in the vault namespace; needs VAULT_TOKEN set in the pod/env):
kubectl exec -n vault vault-0 -- vault kv put k8s-secrets/plex-stack-mcp \
  plex_family_token='<FAMILY_TOKEN>' plex_private_token='<PRIVATE_TOKEN>' \
  qbit_username='<BOT_USER>' qbit_password='<BOT_PASS>'
```

## 4. Open the PR and merge
```bash
git push -u origin feat/plex-stack-diagnostics-kagent
gh pr create --fill
```
Merge only after steps 1–3 are done.

## 5. Verify after ArgoCD syncs
```bash
kubectl get externalsecret plex-stack-mcp-creds -n kagent      # SecretSynced=True
kubectl get pods -n kagent -l app.kubernetes.io/name=plex-stack-mcp   # Running/Ready
kubectl get remotemcpserver plex-stack-mcp -n kagent           # ACCEPTED=True
kubectl get agent plex-stack-diagnostics -n kagent             # ACCEPTED=True, READY=True
```

## 6. Smoke-test the agent
Chat at `https://kagent.arigsela.com/agents/kagent/plex-stack-diagnostics/chat`:
1. "Is the private Plex up, and why can't I see my content?" → should call
   `plex_status("private")`, report reachable + version, and explain the
   account/claim-vs-down distinction (the real 32500 symptom).
2. "Any stalled torrents? Resume them." → `qbit_status()` then, after stating
   intent, `qbit_resume(all_stalled=True)`.
3. Point it at a service you've stopped → confirm it uses the honest-limitation
   phrasing and recommends the `docker compose` commands rather than claiming a restart.

## Notes / optional follow-ups (non-blocking)
- Live smoke-test is where CSRF `Referer` and the qBittorrent session behavior get
  their first real exercise (mocks can't cover those).
- Minor test-hardening left for later: an explicit test for the `all_stalled=True`
  happy path (auth required + stalled torrents present); an `Accept`-header
  assertion in `test_plex_client.py`.
- VPN/Gluetun diagnosis and container-restart (Option B) are intentionally out of
  scope — see the design spec's roadmap (phases 1.5 and 2).
