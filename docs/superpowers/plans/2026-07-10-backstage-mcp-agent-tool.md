# Backstage MCP Tool for homelab-knowledge (v2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the `homelab-knowledge` kagent agent a read-only Backstage catalog MCP tool so it answers ownership/dependency/system questions from the resolved catalog graph, and define the `Group`/`System` entities so those relations resolve.

**Architecture:** Add `@backstage/plugin-mcp-actions-backend` to `arigsela/backstage` exposing a read-only `catalog` MCP server (`catalog:entity`) with static-token auth; define `Group:platform` + 4 `System`s and fully-qualify the pilots' owner/system refs so relations resolve; rebuild image `v1.3.0`. On the kagent side add ExternalSecrets (Vault token), a `RemoteMCPServer` with an `Authorization` header, and wire it as a third tool on `homelab-knowledge`. Deploy via a GitOps tag bump.

**Tech Stack:** Backstage 1.48 `@backstage/plugin-mcp-actions-backend@^0.1.14` · Backstage `GithubEntityProvider`/`url` Location · External Secrets Operator + Vault · kagent `RemoteMCPServer` (v1alpha2, `headersFrom`) · Argo CD GitOps.

## Global Constraints

- **Two repos.** Tasks 2–3 operate in `arigsela/backstage`. Tasks 1, 4, 5 operate in `arigsela/kubernetes` (this repo). Never mix a change into the wrong repo.
- **Read-only.** Expose only `catalog:entity` (never `catalog:*` — includes register/unregister/validate mutations). The agent reads and recommends.
- **Static-token auth.** MCP endpoint requires `Authorization: Bearer <MCP_TOKEN>`. The token lives only in Vault and ExternalSecret-derived K8s Secrets — never committed.
- **One token, two Vault properties (same raw value):** `backstage` key property `mcp-token` (→ `MCP_TOKEN` env) and `kagent` key property `backstage-mcp-token` (→ templated `Authorization` header). The user stores both before Task 4 deploys.
- **Image version:** `v1.3.0` (current `v1.2.0`). ECR `852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal`.
- **Build (Task 3) is user-run** — local Docker+buildx, AWS creds, Node 22/yarn. Cannot run in the agent environment.
- **Fully-qualified refs required.** Pilot entities live in per-app namespaces; Backstage resolves short `owner: platform`/`system: X` refs into the *entity's own* namespace. To point at the shared `default`-namespace Group/System, the pilot refs must be fully-qualified (`group:default/platform`, `default/platform-*`).
- **Ordering:** Task 1 (entities + ref edits) merges → Task 2 (backstage config incl. the `url` Location for those entities) → Task 3 (build) → Task 4 (secrets + RemoteMCPServer + deploy v1.3.0; needs Vault token stored first) → Task 5 (wire agent, using the tool name discovered in Task 4).

---

## File Structure

- `arigsela/kubernetes` → `catalog/platform-entities.yaml` (new) — `Group:platform` + 4 `System`s. Outside `base-apps/`, so Argo never syncs it.
- `arigsela/kubernetes` → `base-apps/{cert-manager,vault,argo-cd,chores-tracker-backend}/catalog-info.yaml` — fully-qualify `owner`/`system` (line 12–13 each).
- `arigsela/backstage` → `packages/backend/package.json` + `packages/backend/src/index.ts` — add the mcp-actions plugin.
- `arigsela/backstage` → `app-config.yaml` — `mcpActions.servers.catalog`, `backend.auth.externalAccess`, and the `url` Location.
- `arigsela/kubernetes` → `base-apps/backstage/external-secrets.yaml` (add `MCP_TOKEN`), `base-apps/kagent/backstage-mcp-external-secret.yaml` (new), `base-apps/kagent/backstage-catalog-mcp.yaml` (new RemoteMCPServer), `base-apps/backstage/deployments.yaml` (tag bump), `base-apps/kagent/agents/homelab-knowledge.yaml` (add tool + prompt).

---

## Task 1: Catalog entities + fully-qualify pilot refs (repo: this repo)

**Files:**
- Create: `catalog/platform-entities.yaml`
- Modify: `base-apps/cert-manager/catalog-info.yaml:12-13`, `base-apps/vault/catalog-info.yaml:12-13`, `base-apps/argo-cd/catalog-info.yaml:12-13`, `base-apps/chores-tracker-backend/catalog-info.yaml:12-13`

**Interfaces:**
- Produces: `Group:default/platform` and `System:default/{platform-networking,platform-secrets,platform-gitops,chores-tracker}`, referenced (fully-qualified) by the four pilot entities. Backstage ingests these once Task 2's `url` Location ships (Task 4 deploy).

- [ ] **Step 1: Create the entities file**

Create `catalog/platform-entities.yaml`:
```yaml
# Backstage-only catalog entities (Group + Systems) for the platform taxonomy.
# NOT a Kubernetes manifest and NOT under base-apps/, so Argo CD never syncs it.
# Ingested by Backstage via a url catalog Location (arigsela/backstage app-config).
# These give the base-apps/* Resources/Components resolvable owner (Group) and
# system (System) relations. All in the default namespace so the pilots'
# fully-qualified refs (group:default/platform, default/platform-*) resolve.
apiVersion: backstage.io/v1alpha1
kind: Group
metadata:
  name: platform
  description: Platform engineering — owns shared infrastructure capabilities.
spec:
  type: team
  children: []
---
apiVersion: backstage.io/v1alpha1
kind: System
metadata:
  name: platform-networking
  description: Networking and certificate capabilities.
spec:
  owner: platform
---
apiVersion: backstage.io/v1alpha1
kind: System
metadata:
  name: platform-secrets
  description: Secret management capabilities.
spec:
  owner: platform
---
apiVersion: backstage.io/v1alpha1
kind: System
metadata:
  name: platform-gitops
  description: GitOps and continuous delivery capabilities.
spec:
  owner: platform
---
apiVersion: backstage.io/v1alpha1
kind: System
metadata:
  name: chores-tracker
  description: Chores Tracker application system.
spec:
  owner: platform
```

- [ ] **Step 2: Fully-qualify the pilot owner/system refs**

In each pilot `catalog-info.yaml`, change line 12 `  owner: platform` → `  owner: group:default/platform`, and line 13 `  system: <name>` → `  system: default/<name>`:
- `base-apps/cert-manager/catalog-info.yaml`: `owner: group:default/platform`, `system: default/platform-networking`
- `base-apps/vault/catalog-info.yaml`: `owner: group:default/platform`, `system: default/platform-secrets`
- `base-apps/argo-cd/catalog-info.yaml`: `owner: group:default/platform`, `system: default/platform-gitops`
- `base-apps/chores-tracker-backend/catalog-info.yaml`: `owner: group:default/platform`, `system: default/chores-tracker`

- [ ] **Step 3: Validate**

Run:
```bash
yamllint -c .yamllint.yaml catalog/platform-entities.yaml base-apps/*/catalog-info.yaml
python3 scripts/validate-agent-docs.py 2>&1 | tail -5 || true
python3 - <<'PY'
import yaml
docs=list(yaml.safe_load_all(open('catalog/platform-entities.yaml')))
kinds=[(d['kind'],d['metadata']['name']) for d in docs]
assert ('Group','platform') in kinds, kinds
for s in ['platform-networking','platform-secrets','platform-gitops','chores-tracker']:
    assert ('System',s) in kinds, (s,kinds)
for app,sysname in [('cert-manager','platform-networking'),('vault','platform-secrets'),('argo-cd','platform-gitops'),('chores-tracker-backend','chores-tracker')]:
    e=yaml.safe_load(open(f'base-apps/{app}/catalog-info.yaml'))
    assert e['spec']['owner']=='group:default/platform', (app,e['spec']['owner'])
    assert e['spec']['system']==f'default/{sysname}', (app,e['spec']['system'])
print('OK: entities + fully-qualified refs')
PY
```
Expected: yamllint clean; validator no new errors; `OK: entities + fully-qualified refs`.

- [ ] **Step 4: Commit, PR, merge**

```bash
git add catalog/platform-entities.yaml base-apps/*/catalog-info.yaml
git commit -m "feat(catalog): add platform Group/Systems and fully-qualify pilot owner/system refs"
git push -u origin feat/backstage-platform-entities
gh pr create --fill
```
Expected: CI green (yaml-lint, agent-docs-validate). Merge so the entities file exists on `main` before Task 4 deploys.

---

## Task 2: Add the MCP Actions backend + config (repo: `arigsela/backstage`)

**Files:**
- Modify: `packages/backend/package.json` (add dep), `packages/backend/src/index.ts` (add plugin), `app-config.yaml` (mcpActions + externalAccess + url Location).

**Interfaces:**
- Consumes: the `catalog/platform-entities.yaml` file merged in Task 1.
- Produces: an image (built in Task 3) that serves `/api/mcp-actions/v1/catalog` (tool `catalog:entity`, static-token auth) and ingests the platform entities.

- [ ] **Step 1: Branch from up-to-date main**

```bash
cd ~/git/backstage && git checkout main && git pull
git checkout -b feat/mcp-actions-catalog
```

- [ ] **Step 2: Add the plugin dependency**

In `packages/backend/package.json`, add to `dependencies` (alphabetical, near the other `@backstage/plugin-*`):
```json
    "@backstage/plugin-mcp-actions-backend": "^0.1.14",
```

- [ ] **Step 3: Register the plugin in the backend**

In `packages/backend/src/index.ts`, immediately after the catalog line (`backend.add(import('@backstage/plugin-catalog-backend'));`, ~line 200), add:
```ts
backend.add(import('@backstage/plugin-mcp-actions-backend'));
```

- [ ] **Step 4: Configure the read-only catalog MCP server + static auth**

In `app-config.yaml`, extend the existing `backend.auth` block (currently just `dangerouslyDisableDefaultAuthPolicy: true`) to add `externalAccess`:
```yaml
  auth:
    dangerouslyDisableDefaultAuthPolicy: true
    externalAccess:
      - type: static
        options:
          token: ${MCP_TOKEN}
          subject: kagent-catalog-reader
```
And add a top-level `mcpActions` block (place it after the `catalog:` block):
```yaml
# MCP Actions backend: expose a read-only catalog MCP server at
# /api/mcp-actions/v1/catalog. Only catalog:entity (get entity + resolved
# relations) — NOT catalog:* (that would include register/unregister mutations).
mcpActions:
  servers:
    catalog:
      include:
        - id: 'catalog:entity'
```

- [ ] **Step 5: Register the platform entities via a url Location**

In `app-config.yaml` under `catalog.locations:`, add (after the last `file` location):
```yaml
    # Platform taxonomy (Group + Systems) from the kubernetes repo. Group is not
    # in the global catalog.rules allow-list, so this location admits it explicitly.
    - type: url
      target: https://github.com/arigsela/kubernetes/blob/main/catalog/platform-entities.yaml
      rules:
        - allow: [Group, System]
```

- [ ] **Step 6: Install, validate config, type-check**

```bash
export PATH="/opt/homebrew/opt/node@22/bin:$PATH"
corepack prepare yarn@4.4.1 --activate
yarn install
yarn backstage-cli config:check --lax
yarn tsc
```
Expected: install succeeds (lockfile updates with the new dep); `config:check` prints `Loaded config from app-config.yaml` with no schema errors; `tsc` no errors.

- [ ] **Step 7: Commit, PR, merge**

```bash
git add packages/backend/package.json packages/backend/src/index.ts app-config.yaml yarn.lock
git commit -m "feat(mcp): add read-only catalog MCP server + platform entities location"
git push -u origin feat/mcp-actions-catalog
gh pr create --fill
```
Merge to `main` (source for Task 3's build).

---

## Task 3: Build and push image `v1.3.0` (repo: `arigsela/backstage`) — USER-RUN GATE

**This cannot run in the agent environment** (needs local Docker+buildx, AWS ECR push, Node 22/yarn). Hand off to the user; resume at Task 4 once the image exists.

- [ ] **Step 1: Build from merged main**

```bash
cd ~/git/backstage && git checkout main && git pull
export PATH="/opt/homebrew/opt/node@22/bin:$PATH"
colima start --cpu 6 --memory 16 --disk 60   # if the Docker daemon isn't running
./scripts/build-and-push.sh --version v1.3.0
```
Expected: ends with `Successfully pushed and deployed: …/backstage-portal:v1.3.0` and `:latest`.

- [ ] **Step 2: Confirm the tag in ECR**

```bash
aws ecr describe-images --repository-name backstage-portal --region us-east-2 \
  --image-ids imageTag=v1.3.0 --query 'imageDetails[0].imageTags' --output json
```
Expected: array containing `"v1.3.0"`. Do not proceed to Task 4 until this succeeds.

---

## Task 4: Vault token + ExternalSecrets + RemoteMCPServer + deploy (repo: this repo)

**USER GATE (before this task's PR merges):** store the raw MCP token in Vault under **both** `k8s-secrets/backstage` property `mcp-token` **and** `k8s-secrets/kagent` property `backstage-mcp-token` (same value). The user does this (the agent does not handle Vault tokens).

**Files:**
- Modify: `base-apps/backstage/external-secrets.yaml` (add `MCP_TOKEN`), `base-apps/backstage/deployments.yaml:27` (tag bump)
- Create: `base-apps/kagent/backstage-mcp-external-secret.yaml`, `base-apps/kagent/backstage-catalog-mcp.yaml`

**Interfaces:**
- Consumes: image `v1.3.0` (Task 3); the Vault token (user gate).
- Produces: a live `RemoteMCPServer/backstage-catalog` in `kagent` exposing the `catalog:entity` tool — whose exact discovered tool name Task 5 consumes.

- [ ] **Step 1: Branch**

```bash
cd ~/git/kubernetes && git checkout main && git pull
git checkout -b feat/kagent-backstage-mcp
```

- [ ] **Step 2: Add `MCP_TOKEN` to the backstage ExternalSecret**

In `base-apps/backstage/external-secrets.yaml`, append to the `spec.data` list:
```yaml
    - secretKey: MCP_TOKEN
      remoteRef:
        key: backstage
        property: mcp-token
```

- [ ] **Step 3: Create the kagent ExternalSecret (templated Bearer header)**

Create `base-apps/kagent/backstage-mcp-external-secret.yaml`:
```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: backstage-mcp-token
  namespace: kagent
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: backstage-mcp-token
    creationPolicy: Owner
    # Build the full header value so the RemoteMCPServer injects it verbatim.
    template:
      engineVersion: v2
      data:
        authorization: "Bearer {{ .token }}"
  data:
    - secretKey: token
      remoteRef:
        key: kagent
        property: backstage-mcp-token
```

- [ ] **Step 4: Create the RemoteMCPServer**

Create `base-apps/kagent/backstage-catalog-mcp.yaml`:
```yaml
apiVersion: kagent.dev/v1alpha2
kind: RemoteMCPServer
metadata:
  name: backstage-catalog
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
    app.kubernetes.io/name: backstage-catalog
spec:
  description: Read-only Backstage catalog MCP (catalog:entity) for resolved entity relations
  protocol: STREAMABLE_HTTP
  url: http://backstage.backstage.svc.cluster.local/api/mcp-actions/v1/catalog
  timeout: 30s
  sseReadTimeout: 5m0s
  terminateOnClose: true
  headersFrom:
    - name: Authorization
      valueFrom:
        type: Secret
        name: backstage-mcp-token
        key: authorization
```

- [ ] **Step 5: Bump the backstage image**

In `base-apps/backstage/deployments.yaml:27`, change `:v1.2.0` → `:v1.3.0`.

- [ ] **Step 6: Validate**

```bash
YL=/Users/arisela/.claude/jobs/3654b4c1/tmp/yl/bin/yamllint
$YL -c .yamllint.yaml base-apps/kagent/backstage-mcp-external-secret.yaml base-apps/kagent/backstage-catalog-mcp.yaml base-apps/backstage/external-secrets.yaml base-apps/backstage/deployments.yaml
grep -q 'backstage-portal:v1.3.0' base-apps/backstage/deployments.yaml && echo "tag bumped"
kubectl apply --dry-run=server -f base-apps/kagent/backstage-mcp-external-secret.yaml -f base-apps/kagent/backstage-catalog-mcp.yaml -f base-apps/backstage/external-secrets.yaml -f base-apps/backstage/deployments.yaml 2>&1 | grep -v last-applied
```
Expected: yamllint clean; `tag bumped`; four `... created/configured (server dry run)` lines.

- [ ] **Step 7: Commit, PR, merge (after the Vault user gate)**

```bash
git add base-apps/kagent/backstage-mcp-external-secret.yaml base-apps/kagent/backstage-catalog-mcp.yaml base-apps/backstage/external-secrets.yaml base-apps/backstage/deployments.yaml
git commit -m "feat(kagent): stand up read-only Backstage catalog MCP + deploy backstage v1.3.0"
git push -u origin feat/kagent-backstage-mcp
gh pr create --fill
```
Merge; Argo syncs `backstage` (→ v1.3.0) and `kagent-secrets`.

- [ ] **Step 8: Verify auth is enforced and the entities resolve**

After Argo syncs and the backstage pod is on `v1.3.0`:
```bash
POD=$(kubectl -n backstage get pods -o jsonpath='{.items[0].metadata.name}')
# unauthenticated request should be rejected
kubectl -n backstage exec "$POD" -- node -e '
const http=require("http");const r=http.request("http://localhost:7007/api/mcp-actions/v1/catalog",{method:"POST",headers:{"content-type":"application/json"}},res=>{console.log("no-token status:",res.statusCode);res.resume();});r.on("error",e=>console.log("ERR",e.message));r.end("{}");'
# platform entities now resolved on cert-manager
kubectl -n backstage exec "$POD" -- node -e '
const http=require("http");http.get("http://localhost:7007/api/catalog/entities?limit=2000",res=>{let b="";res.on("data",d=>b+=d);res.on("end",()=>{const d=JSON.parse(b);const e=d.find(x=>x.kind==="Resource"&&x.metadata.name==="cert-manager");console.log("cert-manager relations:");(e.relations||[]).forEach(r=>console.log("   ",r.type,"->",r.targetRef));const g=d.find(x=>x.kind==="Group"&&x.metadata.name==="platform");console.log("Group:platform present:",!!g);})});'
```
Expected: `no-token status:` is `401` (or `403`) — auth enforced; `cert-manager relations` include `ownedBy -> group:default/platform` and `partOf -> system:default/platform-networking`; `Group:platform present: true`. If the no-token request returns `200`, STOP and report — the endpoint is unauthenticated (a finding to resolve before wiring the agent).

- [ ] **Step 9: Discover the exact MCP tool name (for Task 5)**

```bash
kubectl -n kagent get remotemcpserver backstage-catalog -o json | python3 -c "
import json,sys
d=json.load(sys.stdin)
st=d.get('status',{})
print('accepted:',{c['type']:c['status'] for c in st.get('conditions',[])})
for t in st.get('discoveredTools',[]):
    print('tool:', t.get('name') if isinstance(t,dict) else t)
"
```
Expected: `accepted: {'Accepted': 'True'}` and one `tool:` line (e.g. `catalog_entity` or a prefixed variant). **Record that exact tool name — Task 5 pins it in `toolNames`.** If `Accepted` is not `True`, check the backstage pod is on v1.3.0 and the token secret exists (`kubectl -n kagent get secret backstage-mcp-token`).

---

## Task 5: Wire the tool into homelab-knowledge (repo: this repo)

**Files:**
- Modify: `base-apps/kagent/agents/homelab-knowledge.yaml` (add tool + prompt)

**Interfaces:**
- Consumes: the `RemoteMCPServer/backstage-catalog` and the exact tool name discovered in Task 4 Step 9 (referred to below as `<TOOLNAME>`).

- [ ] **Step 1: Branch**

```bash
cd ~/git/kubernetes && git checkout main && git pull
git checkout -b feat/homelab-knowledge-backstage-tool
```

- [ ] **Step 2: Add the Backstage catalog tool**

In `base-apps/kagent/agents/homelab-knowledge.yaml`, in `spec.declarative.tools`, after the existing `agent-docs` McpServer entry, add (replace `<TOOLNAME>` with the exact name from Task 4 Step 9):
```yaml
    - type: McpServer
      mcpServer:
        apiGroup: kagent.dev
        kind: RemoteMCPServer
        name: backstage-catalog
        toolNames:
        - <TOOLNAME>
```

- [ ] **Step 3: Update the systemMessage to use the catalog tool for relations**

In the `## How to answer (retrieval-first)` section of the `systemMessage`, after the numbered retrieval steps, add:
```
      For OWNERSHIP, DEPENDENCY, or SYSTEM-membership questions ("who owns X?",
      "what depends on X?", "what system is X part of?"), use the Backstage
      catalog tool (catalog:entity) — it returns the entity's RESOLVED relations,
      including reverse relations like dependencyOf that are not present in any
      single catalog-info.yaml. Look the entity up by name (kind/namespace
      optional to disambiguate). Use the agent-docs GitHub MCP for narrative docs
      (docs.md/runbook.md) and as a fallback for catalog-info.yaml if the catalog
      tool is unavailable.
```

- [ ] **Step 4: Validate**

```bash
YL=/Users/arisela/.claude/jobs/3654b4c1/tmp/yl/bin/yamllint
$YL -c .yamllint.yaml base-apps/kagent/agents/homelab-knowledge.yaml && echo "yamllint clean"
python3 - <<'PY'
import yaml
a=yaml.safe_load(open('base-apps/kagent/agents/homelab-knowledge.yaml'))
tools=a['spec']['declarative']['tools']
bc=[t for t in tools if t.get('mcpServer',{}).get('name')=='backstage-catalog']
assert bc and bc[0]['mcpServer']['kind']=='RemoteMCPServer', tools
assert bc[0]['mcpServer'].get('toolNames'), 'toolNames must be set (empty => Unknown Tool)'
print('OK: backstage-catalog tool wired with toolNames', bc[0]['mcpServer']['toolNames'])
PY
kubectl apply --dry-run=server -f base-apps/kagent/agents/homelab-knowledge.yaml 2>&1 | grep -v last-applied
```
Expected: yamllint clean; `OK: backstage-catalog tool wired with toolNames [...]`; `agent.kagent.dev/homelab-knowledge configured (server dry run)`.

- [ ] **Step 5: Commit, PR, merge**

```bash
git add base-apps/kagent/agents/homelab-knowledge.yaml
git commit -m "feat(kagent): give homelab-knowledge the Backstage catalog MCP tool"
git push -u origin feat/homelab-knowledge-backstage-tool
gh pr create --fill
```
Merge; Argo syncs `kagent-secrets` → the agent pod rolls with the new tool.

- [ ] **Step 6: Verify end-to-end in the kagent UI**

Hard-reload the kagent UI, start a New Chat with `homelab-knowledge`, and ask the gold questions:
- "What depends on vault?" → expect **cert-manager** and **chores-tracker-backend** (reverse `dependencyOf`), via a `catalog:entity` tool call on `vault`.
- "Who owns cert-manager?" → **platform**.
- "What system is chores-tracker-backend part of?" → **chores-tracker**.

Confirm the agent's Tools panel lists the Backstage catalog tool (not "Unknown Tool") and the answers come from tool calls, not memory.

---

## Done criteria

- Tasks 1, 2, 4, 5 merged; Task 3 image `v1.3.0` deployed; Argo `backstage` Synced/Healthy on v1.3.0.
- Task 4 Step 8: unauthenticated MCP request rejected; cert-manager shows resolved `ownedBy`/`partOf`.
- Task 4 Step 9: `RemoteMCPServer/backstage-catalog` `Accepted=True` with the tool discovered.
- Task 5 Step 6: gold relation questions answered from the catalog graph via tool calls.
