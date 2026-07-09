# Phase 2 — kagent Agent-Docs Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the `homelab-knowledge` kagent agent to answer from the live agent-docs — read from `arigsela/kubernetes@main` at query time via a read-only, repo-scoped GitHub MCP — instead of a staleable hardcoded prompt.

**Architecture:** Deploy the official `github-mcp-server` as a kagent `MCPServer` (read-only, single-repo token from Vault), then add it as a tool on `homelab-knowledge` and rewrite that agent's `systemMessage` to retrieve the atlas → index → per-app `docs.md`/`runbook.md`/`catalog-info.yaml`. Everything is declarative under `base-apps/kagent/` and syncs via Argo CD.

**Tech Stack:** kagent CRDs (`MCPServer` v1alpha1, `Agent` v1alpha2) · `github/github-mcp-server` container · External Secrets Operator + Vault (`k8s-secrets`, role `kagent`) · Argo CD (GitOps) · yamllint/kubeconform (CI, from `.github/workflows/validate.yaml`).

## Global Constraints

- **Additive & advisory only.** This adds one MCP server + one ExternalSecret and reworks one agent's prompt/tools. No workload, IAM, or other-agent changes. The agent only reads and recommends.
- **Read-only, single-repo MCP.** The GitHub MCP runs with `--read-only`; the token is a **fine-grained PAT limited to `arigsela/kubernetes` with read-only Contents**. No write/issue/PR tools.
- **Namespace:** everything in `kagent`. Secrets via the existing `SecretStore/vault-backend` (Vault `k8s-secrets`, role `kagent`).
- **GitOps:** manifests live under `base-apps/kagent/`; Argo CD (app `kagent`) syncs the directory. Do not `kubectl apply` — commit and let Argo sync. Work on branch `docs/phase2-kagent-agent-docs` (already checked out; the design spec is already committed there). Do not commit to `main`.
- **Ordering prerequisite:** the GitHub PAT must exist in Vault **before** merge, or the MCP pod crashloops waiting for its secret.
- **Commit style:** Conventional Commits, imperative subject (e.g. `feat(kagent): ...`).
- **v2 (out of scope):** a Backstage MCP for structured catalog relations — deferred, noted in the spec.

---

## File Structure

**Created:**
- `base-apps/kagent/agent-docs-mcp-external-secret.yaml` — ExternalSecret syncing the read-only GitHub PAT from Vault into a K8s Secret.
- `base-apps/kagent/agent-docs-mcp.yaml` — the `MCPServer` running `github-mcp-server` (read-only, repos toolset).

**Modified:**
- `base-apps/kagent/agents/homelab-knowledge.yaml` — add the MCP tool; rewrite `systemMessage` for retrieval; refresh `a2aConfig` examples.

**Manual (documented, not a repo file):** create the fine-grained PAT and store it in Vault.

---

## Task 1: GitHub PAT → Vault → ExternalSecret

**Files:**
- Create: `base-apps/kagent/agent-docs-mcp-external-secret.yaml`

**Interfaces:**
- Consumes: the existing `SecretStore` `vault-backend` in `kagent` (`base-apps/kagent/secret-store.yaml`), Vault path `k8s-secrets`.
- Produces: a K8s Secret `agent-docs-github-mcp-token` in `kagent` with key `GITHUB_PERSONAL_ACCESS_TOKEN`, consumed by Task 2's MCPServer.

- [ ] **Step 1: (Manual) Mint the fine-grained PAT and store it in Vault**

In GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate:
- **Resource owner:** `arigsela`; **Repository access:** only `arigsela/kubernetes`.
- **Repository permissions:** `Contents: Read-only` (and `Metadata: Read-only`, which is mandatory). Nothing else.

Then store it in Vault under the `kagent` KV path (the value the ExternalSecret reads):

```bash
# You must run this (interactive Vault login). Replace <PAT>.
vault kv patch k8s-secrets/kagent agent-docs-github-token="<PAT>"
```

Verify the property exists:

```bash
vault kv get -field=agent-docs-github-token k8s-secrets/kagent | head -c 8; echo "…(present)"
```

- [ ] **Step 2: Create the ExternalSecret manifest**

Create `base-apps/kagent/agent-docs-mcp-external-secret.yaml`:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: agent-docs-github-mcp-token
  namespace: kagent
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: agent-docs-github-mcp-token
    creationPolicy: Owner
  data:
    - secretKey: GITHUB_PERSONAL_ACCESS_TOKEN
      remoteRef:
        key: kagent
        property: agent-docs-github-token
```

- [ ] **Step 3: Lint the manifest**

Run: `yamllint -c .yamllint.yaml base-apps/kagent/agent-docs-mcp-external-secret.yaml`
Expected: no errors. (If `yamllint` isn't installed: `python3 -m venv /tmp/yl && /tmp/yl/bin/pip install -q yamllint==1.35.1 && /tmp/yl/bin/yamllint -c .yamllint.yaml base-apps/kagent/agent-docs-mcp-external-secret.yaml`.)

- [ ] **Step 4: Confirm it parses as a valid ExternalSecret**

Run:
```bash
python3 -c "import yaml,sys; d=yaml.safe_load(open('base-apps/kagent/agent-docs-mcp-external-secret.yaml')); assert d['kind']=='ExternalSecret'; assert d['spec']['data'][0]['secretKey']=='GITHUB_PERSONAL_ACCESS_TOKEN'; assert d['spec']['data'][0]['remoteRef']['property']=='agent-docs-github-token'; print('ok')"
```
Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
git add base-apps/kagent/agent-docs-mcp-external-secret.yaml
git commit -m "feat(kagent): ExternalSecret for read-only agent-docs GitHub MCP token"
```

---

## Task 2: The `agent-docs-mcp` MCPServer (github-mcp-server, read-only)

**Files:**
- Create: `base-apps/kagent/agent-docs-mcp.yaml`

**Interfaces:**
- Consumes: the K8s Secret `agent-docs-github-mcp-token` (Task 1) — injected as env via `deployment.secretRefs`.
- Produces: an `MCPServer` named `agent-docs-mcp` in `kagent` exposing read-only GitHub repo tools (e.g. `get_file_contents`, `search_code`) that Task 3's agent references by name.

- [ ] **Step 1: (Confirmed CRD shape — for reference)**

Verified against the live CRD: `spec.deployment.secretRefs` is a list of `{name: <k8s-secret>}` (its keys become container env vars); `spec.stdioTransport` has no required fields (so `transportType: stdio` alone is enough); `spec.deployment` also has `image`, `cmd`, `args`, `env` (`map[string]string`), `port`, `nodeSelector`, `tolerations`. This will be the cluster's **first** `MCPServer` (only `RemoteMCPServer`s exist today), so there's no in-repo example to copy — the manifest below is complete. Optional sanity check: `kubectl explain mcpserver.spec.deployment.secretRefs`.

- [ ] **Step 2: Pin the current github-mcp-server image tag**

Run:
```bash
curl -fsSL https://api.github.com/repos/github/github-mcp-server/releases/latest | python3 -c "import sys,json; print('ghcr.io/github/github-mcp-server:' + json.load(sys.stdin)['tag_name'])"
```
Use the printed `ghcr.io/github/github-mcp-server:<tag>` value in Step 3 (do not use `:latest`).

- [ ] **Step 3: Create the MCPServer manifest**

Create `base-apps/kagent/agent-docs-mcp.yaml` (replace `IMAGE_FROM_STEP_2`; keep node placement consistent with other kagent workloads):

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: agent-docs-mcp
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
    app.kubernetes.io/name: agent-docs-mcp
spec:
  # Read the agent-docs from arigsela/kubernetes@main over MCP. Read-only,
  # single-repo (token scope) — see agent-docs-mcp-external-secret.yaml.
  transportType: stdio
  deployment:
    image: IMAGE_FROM_STEP_2
    cmd: ./github-mcp-server
    args:
      - stdio
      - --read-only
      - --toolsets
      - repos
    # Injects GITHUB_PERSONAL_ACCESS_TOKEN from the K8s Secret (Task 1).
    secretRefs:
      - name: agent-docs-github-mcp-token
    env:
      GITHUB_TOOLSETS: repos
    nodeSelector:
      node.kubernetes.io/workload: infrastructure
    tolerations:
      - key: node-role.kubernetes.io/control-plane
        effect: NoSchedule
```

- [ ] **Step 4: Lint + validate the manifest**

Run:
```bash
yamllint -c .yamllint.yaml base-apps/kagent/agent-docs-mcp.yaml
python3 -c "import yaml; d=yaml.safe_load(open('base-apps/kagent/agent-docs-mcp.yaml')); assert d['kind']=='MCPServer'; assert '--read-only' in d['spec']['deployment']['args']; assert d['spec']['deployment']['secretRefs'][0]['name']=='agent-docs-github-mcp-token'; print('ok')"
```
Expected: yamllint clean and `ok`. (kubeconform will `-ignore-missing-schemas` the kagent CRD in CI — that's fine.)

- [ ] **Step 5: Commit**

```bash
git add base-apps/kagent/agent-docs-mcp.yaml
git commit -m "feat(kagent): deploy read-only github-mcp-server as agent-docs-mcp"
```

---

## Task 3: Rework the `homelab-knowledge` agent

**Files:**
- Modify: `base-apps/kagent/agents/homelab-knowledge.yaml`

**Interfaces:**
- Consumes: the `agent-docs-mcp` MCPServer (Task 2); the existing `k8s-agent` and `helm-agent` Agents; the agent-docs contract (`INFRASTRUCTURE_ATLAS.md`, `base-apps/_INDEX.md`, `base-apps/<app>/docs.md|runbook.md|catalog-info.yaml`).
- Produces: the reworked agent (no downstream consumers).

- [ ] **Step 1: Add the MCP tool to `spec.declarative.tools`**

In `base-apps/kagent/agents/homelab-knowledge.yaml`, the `tools:` list currently holds two `type: Agent` entries (`k8s-agent`, `helm-agent`). Add an MCP tool entry referencing `agent-docs-mcp`. The tool shape is verified against the live CRD: `tools[].type` ∈ {`McpServer`, `Agent`}; the `mcpServer` object takes `name` (required), `kind`, `apiGroup`, and optional `toolNames`. Set `tools:` to:

```yaml
    tools:
    - type: McpServer
      mcpServer:
        apiGroup: kagent.dev
        kind: MCPServer
        name: agent-docs-mcp
    - type: Agent
      agent:
        name: k8s-agent
    - type: Agent
      agent:
        name: helm-agent
```

- [ ] **Step 2: Replace the hardcoded architectural block in `systemMessage`**

Replace the entire `## What you know about` section (the hardcoded GitOps/master-app/secret-management/Crossplane/TLS/IDP/AI-agents bullet list — the stale part) with a retrieval instruction. The new `systemMessage` opening becomes:

```yaml
    systemMessage: |
      You are HomelabAssist, an expert assistant for this homelab Kubernetes
      cluster and its GitOps repo at github.com/arigsela/kubernetes. You answer
      "what/why/how" and triage questions by RETRIEVING the repo's agent-docs
      (never from memorized facts, which go stale), and by delegating to
      specialist agents for live cluster state.

      ## How to answer (retrieval-first)
      Use the agent-docs-mcp tool to read files from arigsela/kubernetes@main:
      1. Read `INFRASTRUCTURE_ATLAS.md` to orient (system context, topology,
         source registry, the "For agents" traversal rule).
      2. Read `base-apps/_INDEX.md` to find the app's row.
      3. Read that app's `base-apps/<app>/docs.md` (architecture/config),
         `runbook.md` (symptom → check → fix), and `catalog-info.yaml`
         (owner, system, dependsOn) as needed.
      4. Treat the files listed under a doc's `sources:` as authoritative —
         read them rather than guessing. NEVER invent file paths or resource
         names; if a doc doesn't cover it, say so and read the source.

      ## Live state vs docs
      - For LIVE cluster state (pod status, events, logs, sync status, RBAC)
        delegate to k8s-agent. For Helm releases/values/history delegate to
        helm-agent. Prefer these over guessing.
      - DRIFT: if the docs and the live state disagree (e.g. runbook says X,
        cluster shows Y), call it out explicitly as a drift finding — this is
        valuable signal, since the docs are meant to track reality.
```

Keep the existing `## Response format` and `## Constraints` sections (the `{{include "builtin/…"}}` lines, the GitOps-not-`kubectl` rule, the never-quote-secrets rule, the "answer → what I checked → specifics" format). Just remove the stale `## What you know about` block and swap the opening two paragraphs as above.

- [ ] **Step 3: Refresh the `a2aConfig` skill examples**

Update the `examples:` under the three skills to reflect retrieval and correct facts. In `repo-knowledge` replace the examples with:
```yaml
        examples:
        - What is cert-manager and how does it issue certs here?
        - Who owns chores-tracker-backend and what does it depend on?
        - Where does vault store its config and how is it unsealed?
```
In `cluster-troubleshooting`:
```yaml
        examples:
        - cert-manager Certificates are stuck pending — walk me through the runbook.
        - chores-tracker-backend is CrashLooping — what does its runbook say to check?
        - Is the argo-cd control plane healthy?
```
Leave `deployment-guidance` examples as-is (still valid).

- [ ] **Step 4: Lint + validate the agent manifest**

Run:
```bash
yamllint -c .yamllint.yaml base-apps/kagent/agents/homelab-knowledge.yaml
python3 -c "
import yaml
d=yaml.safe_load(open('base-apps/kagent/agents/homelab-knowledge.yaml'))
t=d['spec']['declarative']['tools']
names=[ (x.get('mcpServer') or x.get('agent') or {}).get('name') for x in t ]
assert 'agent-docs-mcp' in names, names
assert 'k8s-agent' in names and 'helm-agent' in names, names
sm=d['spec']['declarative']['systemMessage']
assert 'agent-docs-mcp' in sm and 'INFRASTRUCTURE_ATLAS.md' in sm
assert 'What you know about' not in sm, 'stale hardcoded block still present'
print('ok')
"
```
Expected: yamllint clean and `ok`.

- [ ] **Step 5: Commit**

```bash
git add base-apps/kagent/agents/homelab-knowledge.yaml
git commit -m "feat(kagent): rework homelab-knowledge to retrieve live agent-docs via MCP"
```

---

## Task 4: Deploy + post-merge behavioral validation

**Files:** none (validation only).

**Interfaces:**
- Consumes: everything from Tasks 1–3, live in the cluster after Argo CD syncs the merged PR.

> This task runs **after** the PR is merged (so Argo CD syncs `base-apps/kagent/`) and **after** the PAT is in Vault (Task 1 Step 1). It is a checklist, not code.

- [ ] **Step 1: Confirm the MCP server is up and its token synced**

```bash
kubectl -n kagent get externalsecret agent-docs-github-mcp-token
kubectl -n kagent get mcpserver agent-docs-mcp -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}{"\n"}'
kubectl -n kagent get pods -l app.kubernetes.io/name=agent-docs-mcp
```
Expected: ExternalSecret `SecretSynced`, MCPServer `Accepted=True`, the pod `Running`. If the pod crashloops, check it isn't missing the token (`kubectl -n kagent describe externalsecret agent-docs-github-mcp-token`).

- [ ] **Step 2: Confirm the agent picked up the tool**

```bash
kubectl -n kagent get agent homelab-knowledge -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}{"\n"}'
kubectl -n kagent get mcpserver agent-docs-mcp -o jsonpath='{range .status.discoveredTools[*]}{.name}{"\n"}{end}' | head
```
Expected: agent `Accepted=True`; discovered tools include a file-read tool (e.g. `get_file_contents`).

- [ ] **Step 3: Drive the gold questions (kagent UI / A2A)**

Ask the reworked `homelab-knowledge` agent each of these and confirm it (a) calls the `agent-docs-mcp` tool, (b) answers with correct current facts, (c) cites real `base-apps/...` paths:
1. "What is cert-manager and how does it issue certificates here?" → expects HTTP-01 prod/staging + a separate Route 53 DNS-01 issuer (from `cert-manager/docs.md`), NOT "DNS-01 for all".
2. "chores-tracker-backend keeps CrashLooping — what does its runbook say to check?" → expects the ExternalSecret/Postgres checks from `chores-tracker-backend/runbook.md`; datastore is **PostgreSQL/CloudNativePG** (not MySQL), workload is a **Rollout**.
3. "Who owns vault and what depends on it?" → reads `vault/catalog-info.yaml` + notes chores-tracker/cert-manager `dependsOn: resource:vault/vault`.
4. "How is the argo-cd control plane structured?" → reads `argo-cd/docs.md`; app-of-apps via `terraform/modules/application-sets/`, no `base-apps/master-app.yaml`.
5. "Is the cert-manager Route 53 ExternalSecret healthy?" → delegates to `k8s-agent` for live state (not answered from docs alone).

- [ ] **Step 4: Drift-detection spot check**

Ask a question whose doc fact you can compare to live state (e.g. "Does the vault runbook's unseal description match how vault is actually configured?"). Confirm the agent cross-checks docs vs `k8s-agent` and, if they differ, explicitly flags the drift.

- [ ] **Step 5: Record the result**

Note pass/fail per gold question in the PR. If the agent answered from memory instead of retrieving (no `agent-docs-mcp` tool call), tighten the `systemMessage` retrieval instruction and re-test.

---

## Final verification

- [ ] `yamllint -c .yamllint.yaml base-apps/kagent/agent-docs-mcp-external-secret.yaml base-apps/kagent/agent-docs-mcp.yaml base-apps/kagent/agents/homelab-knowledge.yaml` → clean.
- [ ] The reworked `homelab-knowledge.yaml` still has `k8s-agent` and `helm-agent` tools and no longer contains the stale `## What you know about` block.
- [ ] `git log --oneline` shows one commit per task (1–3), all on `docs/phase2-kagent-agent-docs` (none on `main`).
- [ ] Post-merge (Task 4): MCPServer `Accepted`, agent `Accepted`, and ≥4/5 gold questions answered from retrieved docs with correct current facts.

## Success criteria (from the spec)

- Agent reads the right doc files (atlas → index → app), visible in tool calls. ✅ Task 4 Step 3.
- Answers with correct current facts (Postgres/Rollout/HTTP-01). ✅ Task 4 Step 3.
- Cites real `file_path`s, no invented paths. ✅ Task 3 Step 2 (prompt) + Task 4 Step 3.
- Flags doc-vs-live drift. ✅ Task 3 Step 2 + Task 4 Step 4.
- Additive/advisory, read-only single-repo MCP. ✅ Tasks 1–2 (token scope + `--read-only`).
