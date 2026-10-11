---
type: "Kubernetes App Runbook"
title: "kagent — Runbook"
description: "Operational runbook for kagent 1.0 on Agent Substrate: agent readiness, approvals, session resume, A2A access, alpha bumps and rollback."
app: kagent
catalog_entity: kagent
kind: runbook
namespace: kagent
last_reviewed: 2026-10-11
status: stable
tags: [ai-agent, kagent, mcp, anthropic, substrate]
sources:
  - base-apps/kagent.yaml
  - base-apps/kagent-crds.yaml
  - base-apps/kagent-secrets.yaml
  - base-apps/kagent/agents/homelab-knowledge.yaml
  - base-apps/kagent/agent-templates/homelab-knowledge.yaml
  - base-apps/kagent/agent-templates/k8s-agent.yaml
  - base-apps/kagent/harnesses/kagent-memory.yaml
  - base-apps/kagent/harnesses/kagent-stateless.yaml
  - base-apps/kagent/harnesses/kagent-chat.yaml
  - base-apps/kagent/model-configs/anthropic-claude-sonnet-5-5.yaml
  - base-apps/kagent/model-configs/anthropic-claude-haiku-5-5.yaml
  - base-apps/kagent/embedding-model-config.yaml
  - base-apps/kagent/agent-docs-mcp.yaml
  - base-apps/kagent/agent-docs-mcp-remote.yaml
  - base-apps/kagent/backstage-catalog-mcp.yaml
  - base-apps/kagent/kagent-postgres-external-secret.yaml
  - base-apps/kagent/kagent-anthropic-external-secret.yaml
  - base-apps/kagent/agent-docs-mcp-external-secret.yaml
  - base-apps/kagent/httproute-mcp.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - base-apps/cluster-rbac/kagent-tools.yaml
  - base-apps/substrate/runbook.md
  - base-apps/postgresql/init-kagent-v1-db.yaml
  - scripts/ask-hk
  - scripts/kagent_a2a.py
  - renovate.json5
  - docs/plans/kagent-1-0-on-k8s-1-37-implementation-plan.md
---

# kagent — Runbook

kagent 1.0 has no per-agent pods. The pods to look at are `kagent-controller` (A2A, `/mcp`,
reconciling), `kagent-ui`, `kagent-tools` (the MCP tool server), `agent-docs-mcp`, and the
Substrate workers `kagent-default-*` (label `ate.dev/worker-pool=kagent-default`), where the
sessions run as gVisor actors. Substrate itself is in `ate-system`
(`base-apps/substrate/runbook.md`).

## Failure modes

### Symptom: an `Agent` is not `Ready` (chat and A2A calls fail)
- **Check:** `kubectl -n kagent get agents,agenttemplates,harnesses,modelconfigs,remotemcpservers`,
  then `kubectl -n kagent describe agent <name>` for the condition and reason. An Agent needs its
  `templateRef` (AgentTemplate) and `harnessRef` (Harness) to exist; the template's
  `modelConfig`, the Harness's memory `modelConfigRef` (`embedding-model-config`) and every bound
  `RemoteMCPServer` must exist and be accepted; and the Harness's WorkerPool must exist
  (`kubectl -n kagent get workerpools`: a Harness pointing at a missing pool doesn't compile).
  Then `kubectl -n kagent logs deploy/kagent-controller --tail=200`.
- **Fix:** fix the reference in git; Argo CD self-heals on merge. If the WorkerPool is missing,
  the `kagent` app (chart value `substrateWorkerPool`) didn't sync. If everything is `Ready` but
  every call fails, check Substrate (`kubectl get pods -n ate-system`) and the next entries.

### Symptom: every model call fails with 403, or the agent answers nothing
- **Check:** the egress credential policy:
  `kubectl get cm -n ate-system k8s-credential-provider-namespace-policy -o yaml` must allow
  atespace `kagent` to read namespace `kagent`. Also confirm the key Secret exists and synced:
  `kubectl -n kagent get externalsecret kagent-anthropic-secrets`.
- **Fix:** `credentialProvider.namespacePolicies` in `base-apps/substrate.yaml` (the Substrate
  chart, not kagent's). A rotated or revoked Anthropic key: see *Rotate a Vault-backed secret*.

### Symptom: an answer stops mid-sentence but the task reports completed
- **Check:** the agent's ModelConfig `spec.anthropic.maxTokens`. The Go ADK's default is 8192,
  which caps thinking plus the answer together, and a truncated answer still reports completed
  (kagent#2895).
- **Fix:** keep `maxTokens: 16000` on both Anthropic ModelConfigs; raise it by PR if long answers
  still get cut. Prompt caching (`promptCaching: true`) keeps the cost of a longer turn down.

### Symptom: a turn is stuck waiting for approval
A call through a `requireApproval: true` binding (only `k8s-agent` and `istio-agent` have one,
directly or as `build-orchestrator`'s sub-agents) pauses the turn in `TASK_STATE_INPUT_REQUIRED`.
It never expires: `sessionIdleTTL` skips turns waiting for approval.
- **Check:** `scripts/ask-hk` exits 2 and prints what the agent is waiting on. In the UI
  (`https://kagent.arigsela.com`) the session shows the pending tool call. A session belongs to
  the `X-User-Id` that started it (`scripts/ask-hk` defaults to `admin@kagent.dev`, Backstage
  sends `backstage-portal`), so answer it as that user.
- **Fix:** approve or reject in the UI, or over A2A with the HITL extension
  `https://kagent.dev/extensions/hitl/v1`: reply in the same conversation with a
  `tool_approval_response` carrying `approvals: [{id, approved, rejection_reason}]` (`id` from
  the `tool_approval_request`). Reject keeps the object and the agent carries on; approve runs
  the call. If the session's worker went away meanwhile, see the next entry.

### Symptom: a write or destructive tool ran without an approval prompt
This should be impossible: admission and CI deny a mutating tool in a binding without
`requireApproval: true`.
- **Check:** the Grafana rule "Agent invoked a gated tool with NO approval" and
  `kubectl -n postgresql logs -l app=agent-audit --tail=5`; then `scripts/agent-audit.py --ungated`
  (`--source v1`) for the call. Compare the live template with git:
  `kubectl -n kagent get agenttemplate <name> -o yaml`.
- **Fix:** see `base-apps/postgresql/runbook.md` ("`agent-audit-ungated` Job failed"). If the
  live object differs from git, find what wrote it (Argo history, `kubectl get events`).

### Symptom: a conversation fails or hangs after a node drain, reboot or node loss
The session's actor was suspended to rustfs or its worker died; the next message has to restore
it, possibly on the other node.
- **Check:** `kubectl get pods -n kagent -l ate.dev/worker-pool -o wide`. A worker that survived a
  reboot keeps its name but has a new IP that its actors never learn (substrate#1837). Then the
  atelet logs on the target node (`kubectl logs -n ate-system -l app=atelet`) for a resume
  deadline (substrate#1761, resume is tied to a node with a ~6 s deadline) or a gVisor CPU
  feature mismatch (substrate#1657). Both workers on one node after a drain is expected (no
  anti-affinity) and harmless.
- **Fix:** delete the stale worker pods (the WorkerPool re-creates them;
  `ansible/playbooks/patch.yml` does this after the reboots it performs, an unplanned reboot needs
  it by hand). If the session still won't resume, **start a new conversation**: sessions are
  disposable, and memory-enabled agents keep their long-term memory in Postgres. CPU-flag mismatch
  is a hypervisor fix; see `base-apps/substrate/runbook.md`.

### Symptom: `scripts/ask-hk` (or any A2A call) gets HTTP 403
- **Check:** a 403 `RBAC: access denied` from `kagent-mcp.arigsela.com` comes from the Gateway's
  IP allow-list (`base-apps/istio-ingress/authorizationpolicy.yaml`), not kagent: your address
  isn't on it, or the WAN IP rotated. A timeout instead usually means the Route 53 record is stale.
  `kubectl -n kagent get httproute kagent-mcp -o yaml` shows whether `/agents/kagent/` is accepted.
  (A 403 from the *model*, inside a completed task, is the egress credential policy above.)
- **Fix:** if the WAN IP rotated, `base-apps/wan-ip-monitor/` moves DNS and opens the allow-list
  PR; see its runbook. A new client address goes into the rule for this host by PR. Meanwhile,
  bypass the Gateway: `kubectl port-forward -n kagent svc/kagent-controller 8083:8083 &` and
  `scripts/ask-hk --endpoint http://127.0.0.1:8083 "…"`.

### Symptom: A2A returns `-32004` / `KAGENT_SEND_NOT_ACCEPTED`
- **Cause:** the session was busy pausing or suspending, and the message was provably not
  dispatched.
- **Fix:** resend the identical request (`scripts/kagent_a2a.py` retries for 30 s). Don't blindly
  retry any other error: the input may already be running.

### Symptom: `homelab-knowledge` can't read the repo (GitHub MCP 401 "Bad credentials")
Open since 2026-10-11 (SPEC T101).
- **Check:** `kubectl -n kagent logs deploy/agent-docs-mcp --tail=50`, and
  `kubectl -n kagent get remotemcpserver agent-docs`.
- **Fix (owner):** a new fine-grained PAT, read-only Contents + Metadata on
  `arigsela/kubernetes`, written to Vault key `kagent-agent-docs-mcp` (property `github-token`).
  Then `kubectl -n kagent annotate externalsecret agent-docs-github-mcp-token force-sync=$(date +%s) --overwrite`
  and `kubectl -n kagent rollout restart deploy/agent-docs-mcp` (it reads the token from an env var).

### Symptom: a `RemoteMCPServer` isn't accepted, or an agent says it lacks a tool
- **Check:** `kubectl -n kagent get remotemcpservers` (`ACCEPTED`) and
  `kubectl -n kagent describe remotemcpserver <name>` (`status.discoveredTools`). For `agent-docs`,
  also the backing pod: `kubectl -n kagent get pods -l app.kubernetes.io/name=agent-docs-mcp`. A
  kmcp `MCPServer` only deploys the server; the `RemoteMCPServer` is what agents bind. A tool is
  available to an agent only if its AgentTemplate binding lists it.
- **Fix:** fix the target Service first. A missing tool: add it to the binding's `tools` by PR.
  A tool new to the taxonomy must be classified first (`agent-capability-taxonomy.yaml`), or CI
  and admission deny the template. Errors right after a controller restart (Backstage not
  answering yet) clear on their own.

### Symptom: memory calls fail (agents with memory error or are slow on the first turn)
- **Check:** the memory Harnesses use `embedding-model-config`
  (`http://ollama.ollama.svc.cluster.local:11434`, `nomic-embed-text`): `kubectl -n ollama get pods`,
  `kubectl -n ollama logs deploy/ollama --tail=50`, and
  `kubectl -n kagent describe modelconfig embedding-model-config`.
- **Fix:** an Ollama problem is `base-apps/ollama/runbook.md`. `k8s-reader` has no memory and is
  unaffected.

### Symptom: `kagent-controller` CrashLoopBackOff
- **Check:** `kubectl -n kagent logs deploy/kagent-controller --previous --tail=100`. Usual causes:
  the `kagent-postgres` Secret missing or stale (`kubectl -n kagent get externalsecret kagent-postgres`),
  pgvector missing from schema `extensions` in `kagent_v1` (the controller refuses to start), a
  migration error after a version change, or Substrate's API unreachable
  (`kubectl get pods -n ate-system`).
- **Fix:** Secret or database: re-sync the `postgresql` app so `init-kagent-v1-db` runs (it is a
  Sync hook: a hook-only change doesn't trigger a sync), then restart the controller (it reads the
  connection string once, at startup). A migration error after an alpha bump means the bump
  needed a clean database: see *Bump kagent and Substrate*.

### Symptom: a tool call fails with `secrets is forbidden` or `403` from the API server
Expected for Secrets: the tool server's ClusterRole (`base-apps/cluster-rbac/kagent-tools.yaml`)
grants everything except `secrets`, for every agent. For any other resource, the API group is
probably new: the role names non-core groups explicitly. Check with
`kubectl auth can-i <verb> <resource> --as=system:serviceaccount:kagent:kagent-tools`, add the
group to the role, and note it in the manifest header's dated list.

## How-to

### Ask an agent
`scripts/ask-hk "question"` (homelab-knowledge), `--agent kagent/<name>` for another agent,
`--context <id>` to continue (the id is printed after each answer). Exit 0 answered, 2 waiting for
input or approval, 1 failed. The UI is `https://kagent.arigsela.com`.

### Deploy / update
Agents are three files: the AgentTemplate (`agent-templates/`), the Agent (`agents/`, references
the template and a Harness), and, rarely, a new Harness (`harnesses/`). Run
`scripts/validate-agent-identity.py --repo-root .` and `scripts/validate-agent-capability.py --repo-root .`
before the PR. Chart values are in `base-apps/kagent.yaml`. Argo CD (`kagent`, `kagent-crds`,
`kagent-secrets`, all `prune`/`selfHeal`) syncs on merge. After a prompt or tool change, ask the
agent a **multi-step tool question**, not a one-liner: tool-loop failures only show from the second
model request on.

### Rotate a Vault-backed secret
Each ExternalSecret resolves through its own `SecretStore`, ESO ServiceAccount and Vault role,
reading a per-consumer Vault key. Rotate at that consumer's path:

| Secret (ExternalSecret if different) | SecretStore | Vault key (rotate here) |
|---|---|---|
| `agent-docs-github-mcp-token` | `vault-agent-docs-mcp` | `k8s-secrets/kagent-agent-docs-mcp` (`github-token`) |
| `backstage-mcp-token` | `vault-backstage-mcp` | `k8s-secrets/kagent-backstage-mcp` (`token`) |
| `kagent-postgres` | `vault-kagent-db` | `k8s-secrets/kagent-db` (`v1-password`; also see the postgresql app below) |
| `kagent-anthropic` (`kagent-anthropic-secrets`) | `vault-kagent-anthropic` | `k8s-secrets/kagent-anthropic` (`anthropic-api-key`) |

Update the value in Vault (`vault kv patch`, never in git). ESO picks it up within the 1h
`refreshInterval`, or force it: `kubectl -n kagent annotate externalsecret <name> force-sync=$(date +%s) --overwrite`.
Then:
- **Anthropic key, Backstage token:** no agent holds them; Substrate's credential provider
  resolves the Secret for the egress gateway. If calls keep using the old value after the Secret
  changed, restart `deploy/k8s-credential-provider` in `ate-system`.
- **GitHub token:** restart `deploy/agent-docs-mcp` (env var).
- **Database password:** 64 hex characters (the connection string doesn't escape it). Force the
  ExternalSecrets in `postgresql` and `kagent`, sync the `postgresql` app (its init Job sets the
  new password), then restart `deploy/kagent-controller`. Same procedure as Substrate's
  (`base-apps/substrate/runbook.md`, "Rotate the database passwords").

A `SecretSyncedError` with Vault **403 permission denied** means the Vault side of the scope is
wrong: the policy must grant `read` on exactly that path, and the kubernetes-auth role must bind
that ESO ServiceAccount in `kagent`.

### Restart the controller
`kubectl -n kagent rollout restart deploy/kagent-controller` is safe: sessions live in Substrate and
Postgres, and agents reconcile again once it is back. Verify with `kubectl -n kagent get agents`.
After a restart a few `RemoteMCPServer` reconcile errors can appear until each target answers.

### Bump kagent and Substrate (every alpha bump is a clean-database migration)
kagent 1.0 is built against one Substrate version (1.0.0-alpha11 ↔ 0.5.0-alpha2, from kagent's
`go.mod`). Every alpha so far has needed empty databases, so treat a bump as a migration, never as
a routine Renovate merge. Sessions and long-term memory reset.
1. **Prepare one PR** (Renovate opens it as `kagent-substrate` only after you tick it on the
   Dependency Dashboard, labelled `clean-db-migration`): the five charts (`kagent-crds`, `kagent`,
   `substrate-crds`, `substrate-podcert`, `substrate`) to the versions the kagent release names;
   re-pin **every** digest in the values of `base-apps/kagent.yaml` (controller, `agentImage`, UI,
   kmcp, tools, `substrateWorkerPool.workerImage`), `substrate-podcert.yaml` and `substrate.yaml`
   (plus the component digests in its header); the golang-adk `workload.image` in **all three**
   Harnesses (identical); and the CRD fixture under `tests/admission-policies/fixtures/crds/`.
   Re-render both charts with `helm template --kube-version` and read the release notes for
   CRD and value changes. A Renovate PR that bumps only the Harness image (the `kubernetes`
   manager sees it) must not be merged on its own.
2. **Run the guardrails** against the new CRDs: `python -m pytest tests/admission-policies -q`,
   the identity and capability validators, and `tests/agent-capability`.
3. **Archive the record:** take a final audit export
   (`kubectl create job -n postgresql --from=cronjob/agent-audit-export pre-bump-export`), then dump
   `kagent_v1` and `substrate` off-cluster with checksums (the pgvector Deployment has no backup
   script; `pg-backup.sh` covers only CNPG) to `s3://mysql-backups-asela-cluster/pg/`.
4. **Clean databases:** drop `kagent_v1` and `substrate` on the pgvector Postgres, then sync the
   `postgresql` app once: `init-kagent-v1-db` and `init-substrate-db` recreate them empty, and
   `init-kagent-audit-role` / `init-agent-audit-web-role` restore the audit grants.
5. **Merge**, then restart `kagent-controller` and Substrate's `ate-api-server` if they don't roll
   on their own.
6. **Accept:** 7 Agents `Ready`; each answers a multi-step tool question; `k8s-agent` pauses a
   delete for approval (reject keeps the object); `scripts/ask-hk` works; run
   `agent-audit-ungated` once by hand and read its line.

### Roll back
- **A bad agent, prompt or value change:** `git revert` the PR. Argo CD applies it.
- **A bad alpha bump:** revert the bump PR **and** restore the pre-bump `kagent_v1` and
  `substrate` dumps (step 3 above) into freshly created databases: the new version will have
  migrated the schema. Snapshots written by the newer Substrate may not restore; start new
  conversations.
- **Back to kagent 0.10.3** (last resort, never exercised): 0.10 and 1.x can't share a cluster
  (both own `mcpservers.kagent.dev`) and there is no conversion. Revert the cutover PRs in reverse
  order (#737 chart and CRDs, then #734 agents and v1alpha3 guardrails), check what later PRs
  assume 1.0 (the `/agents/kagent/` route and `scripts/ask-hk`, Backstage's `api.kagent.dev`
  RBAC, the audit CronJobs' `--source v1`), then make the archive writable again:
  `ALTER DATABASE kagent SET default_transaction_read_only = off`. 0.10 comes back with its
  history; nothing from 1.0 carries over. See Task 5.5 of
  `docs/plans/kagent-1-0-on-k8s-1-37-implementation-plan.md` (SPEC §T.99).
