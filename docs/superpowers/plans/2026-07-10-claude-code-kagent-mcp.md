# Claude Code ↔ kagent MCP Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Invoke the `homelab-knowledge` kagent agent from the Claude Code CLI via kagent's existing `/mcp` endpoint, gated by nginx basic auth.

**Architecture:** No custom code. Add a basic-auth gate (credential from Vault via ExternalSecret) to the `kagent-mcp-nginx` ingress that already routes `/mcp` → `kagent-controller:8083`, keeping the existing IP allowlist. Then point the Claude Code CLI (user scope) at `https://kagent.arigsela.com/mcp` with an `Authorization: Basic` header; it gets `invoke_agent`/`list_agents`.

**Tech Stack:** nginx ingress basic auth · External Secrets Operator + Vault (`k8s-secrets/kagent`) · Argo CD GitOps · Claude Code MCP (HTTP transport).

## Global Constraints

- **Endpoint already exists.** kagent's controller serves MCP at `/mcp` (`serverInfo: kagent-agents` v0.9.4) with tools `invoke_agent {agent, task, context_id?}` and `list_agents`. Do not build a bridge.
- **Basic auth, not Bearer.** Snippet annotations are disabled on this ingress-nginx, so use the annotation-native `auth-type: basic`. Keep the existing `whitelist-source-range` IP allowlist.
- **Credential in Vault only.** Store the htpasswd line at `k8s-secrets/kagent` property `mcp-basic-auth`; never commit it. The plaintext `user:password` is only used locally for the Claude Code header.
- **Secret name:** `kagent-mcp-basic-auth` (K8s secret in `kagent`, key `auth`). Username: `claude`.
- **Order:** store the Vault credential BEFORE merging the ingress change, or `/mcp` returns 503 (auth-secret missing) until it exists.
- **No in-cluster consumer of the public `/mcp`** (verified: all RemoteMCPServers use internal service DNS) — basic auth is safe.

---

## File Structure

- `arigsela/kubernetes` → `base-apps/kagent/mcp-basic-auth-external-secret.yaml` (new) — ExternalSecret rendering the `kagent-mcp-basic-auth` K8s secret (`auth` key = htpasswd line) from Vault.
- `arigsela/kubernetes` → `base-apps/kagent/mcp-ingress.yaml` — add three basic-auth annotations.
- Claude Code user config (local, not in the repo) — added via `claude mcp add`.

No test files (config change; validation is behavioral via curl + Claude Code).

---

## Task 1: Gate `/mcp` with basic auth (repo: this repo)

**Files:**
- Create: `base-apps/kagent/mcp-basic-auth-external-secret.yaml`
- Modify: `base-apps/kagent/mcp-ingress.yaml:6-17` (annotations block)

**Interfaces:**
- Produces: an authenticated `https://kagent.arigsela.com/mcp` — the endpoint Task 2's Claude Code config consumes.

- [ ] **Step 1: Branch**

```bash
cd ~/git/kubernetes && git checkout main && git pull
git checkout -b feat/kagent-mcp-basic-auth
```

- [ ] **Step 2: Create the ExternalSecret**

Create `base-apps/kagent/mcp-basic-auth-external-secret.yaml`:
```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: kagent-mcp-basic-auth
  namespace: kagent
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    # nginx ingress basic auth expects a secret with an `auth` key in htpasswd
    # format (user:hashedpassword). Referenced by the ingress auth-secret annotation.
    name: kagent-mcp-basic-auth
    creationPolicy: Owner
  data:
    - secretKey: auth
      remoteRef:
        key: kagent
        property: mcp-basic-auth
```

- [ ] **Step 3: Add basic-auth annotations to the ingress**

In `base-apps/kagent/mcp-ingress.yaml`, add these three lines inside the existing `metadata.annotations` block (keep the whitelist + SSE/timeout annotations):
```yaml
    nginx.ingress.kubernetes.io/auth-type: basic
    nginx.ingress.kubernetes.io/auth-secret: kagent-mcp-basic-auth
    nginx.ingress.kubernetes.io/auth-realm: "kagent MCP"
```

- [ ] **Step 4: Validate**

```bash
YL=/Users/arisela/.claude/jobs/3654b4c1/tmp/yl/bin/yamllint
$YL -c .yamllint.yaml base-apps/kagent/mcp-basic-auth-external-secret.yaml base-apps/kagent/mcp-ingress.yaml
kubectl apply --dry-run=server -f base-apps/kagent/mcp-basic-auth-external-secret.yaml -f base-apps/kagent/mcp-ingress.yaml 2>&1 | grep -v last-applied
```
Expected: yamllint clean; `externalsecret.external-secrets.io/kagent-mcp-basic-auth created (server dry run)` and `ingress.networking.k8s.io/kagent-mcp-nginx configured (server dry run)`.

- [ ] **Step 5: USER GATE — generate the credential and store it in Vault**

The user runs this (agent does not handle Vault writes). Generate an htpasswd line for user `claude`, then store it in Vault:
```bash
# Pick a strong password, then generate the htpasswd line (bcrypt):
htpasswd -nbB claude '<password>'          # -> claude:$2y$05$...
# (no htpasswd? use: printf 'claude:%s\n' "$(openssl passwd -apr1 '<password>')")

# Store the FULL line in Vault (root/write token, as with prior secrets):
kubectl -n vault exec -i vault-0 -- sh -c "VAULT_TOKEN=<root> vault kv patch k8s-secrets/kagent mcp-basic-auth='claude:\$2y\$05\$...'"
```
Keep the plaintext `<password>` — Task 2 needs it for the Claude Code header. Confirm without revealing:
```bash
kubectl -n vault exec -i vault-0 -- sh -c "VAULT_TOKEN=<root> vault kv get -field=mcp-basic-auth k8s-secrets/kagent" >/dev/null && echo "stored"
```

- [ ] **Step 6: Commit, PR, merge (after the Vault gate)**

```bash
git add base-apps/kagent/mcp-basic-auth-external-secret.yaml base-apps/kagent/mcp-ingress.yaml
git commit -m "feat(kagent): gate the /mcp ingress with basic auth for Claude Code access"
git push -u origin feat/kagent-mcp-basic-auth
gh pr create --fill
```
Merge after CI (admin bypass). Then force the sync:
```bash
kubectl -n argo-cd annotate application kagent-secrets argocd.argoproj.io/refresh=hard --overwrite
```

- [ ] **Step 7: Verify the K8s secret synced and the auth is enforced**

```bash
kubectl -n kagent get secret kagent-mcp-basic-auth -o jsonpath='{.data.auth}' | base64 -d | cut -d: -f1
# expect: claude
# no credential -> 401
curl -s -o /dev/null -w "no-cred: %{http_code}\n" https://kagent.arigsela.com/mcp -X POST \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
# with credential -> 200 + server info
curl -s -w "\nwith-cred: %{http_code}\n" https://kagent.arigsela.com/mcp -X POST \
  -u 'claude:<password>' \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' | tail -c 160
```
Expected: secret user is `claude`; `no-cred: 401`; `with-cred: 200` with `serverInfo` `kagent-agents`. If `no-cred` is `503`, the Vault credential wasn't stored before sync — store it (Step 5) and re-sync.

---

## Task 2: Configure Claude Code and invoke the agent (local — USER-RUN)

**Files:** none in the repo (local Claude Code user config).

**Interfaces:**
- Consumes: the authenticated `/mcp` endpoint from Task 1.

- [ ] **Step 1: Add the MCP server at user scope**

```bash
claude mcp add --transport http --scope user kagent \
  https://kagent.arigsela.com/mcp \
  --header "Authorization: Basic $(printf 'claude:<password>' | base64)"
```
Expected: the command confirms the `kagent` MCP server was added to user config.

- [ ] **Step 2: Confirm Claude Code sees the tools**

```bash
claude mcp list
```
Expected: `kagent` listed and reachable (not failed). If it shows an auth/connection error, re-check the base64 header value (no trailing newline — `printf`, not `echo`).

- [ ] **Step 3: Resolve the exact agent name and invoke it**

In a Claude Code session, first list agents, then invoke:
- Ask: *"Use the kagent `list_agents` tool and show the agent names."* — confirm whether it's `homelab-knowledge` or a namespaced form.
- Ask: *"Use kagent `invoke_agent` with agent=<name from list_agents> and task='What depends on vault?'"*

Expected: `list_agents` returns the agents including homelab-knowledge; `invoke_agent` returns **cert-manager + chores-tracker-backend** (from the resolved `dependencyOf` relations). Reuse the returned `context_id` for follow-up turns in the same thread.

---

## Done criteria

- `https://kagent.arigsela.com/mcp` returns 401 without the credential and 200 with it; the K8s secret `kagent-mcp-basic-auth` exists with user `claude`.
- In-cluster agents unaffected (they use internal service DNS).
- From Claude Code, `list_agents` and `invoke_agent(agent=homelab-knowledge, task=…)` work and return correct answers.
- Rollback if needed: revert the ingress annotations (Argo redeploys the open ingress) and `claude mcp remove kagent`.
