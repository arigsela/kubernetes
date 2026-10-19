---
type: "Kubernetes App Runbook"
title: "kagent — Runbook"
description: "Operational runbook for kagent 1.0 on Agent Substrate: agent readiness, approvals, session resume, A2A access, alpha bumps and rollback."
app: kagent
catalog_entity: kagent
kind: runbook
namespace: kagent
last_reviewed: 2026-10-18
status: stable
tags: [ai-agent, kagent, mcp, anthropic, substrate]
sources:
  - base-apps/kagent.yaml
  - base-apps/kagent-crds.yaml
  - base-apps/kagent/brave-search-mcp.yaml
  - base-apps/kagent/brave-search-mcp-external-secret.yaml
  - scripts/provision-brave-search-vault.sh
  - base-apps/kagent-secrets.yaml
  - base-apps/kagent/agents/homelab-knowledge.yaml
  - base-apps/kagent/agent-templates/homelab-knowledge.yaml
  - base-apps/kagent/agent-templates/k8s-agent.yaml
  - base-apps/kagent/harnesses/kagent-memory.yaml
  - base-apps/kagent/harnesses/kagent-stateless.yaml
  - base-apps/kagent/harnesses/kagent-chat.yaml
  - base-apps/kagent/harnesses/docs-checker.yaml
  - base-apps/kagent/agents/docs-checker.yaml
  - base-apps/kagent/model-configs/anthropic-claude-sonnet-5-5.yaml
  - base-apps/kagent/model-configs/anthropic-claude-haiku-5-5.yaml
  - base-apps/kagent/embedding-model-config.yaml
  - base-apps/kagent/agent-docs-mcp.yaml
  - base-apps/kagent/agent-docs-mcp-remote.yaml
  - base-apps/kagent/backstage-catalog-mcp.yaml
  - base-apps/kagent/kagent-postgres-owner-external-secret.yaml
  - base-apps/kagent/kagent-postgres-readwrite-external-secret.yaml
  - base-apps/kagent/kagent-anthropic-external-secret.yaml
  - base-apps/kagent/agent-docs-mcp-external-secret.yaml
  - base-apps/kagent/httproute-mcp.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - base-apps/cluster-rbac/kagent-tools.yaml
  - base-apps/substrate/runbook.md
  - base-apps/postgresql/init-kagent-v1-db.yaml
  - scripts/ask-hk
  - scripts/kagent_a2a.py
  - base-apps/kagent/docs-checker-trigger.yaml
  - scripts/docs_checker_trigger.py
  - docs/docs-checker-dismissed.yaml
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
  `template.ref` (AgentTemplate) and `harnessRef` (Harness) to exist; the template's
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
It never expires: `config.sessions.idleTtl` skips turns waiting for approval.
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
- **Check:** a 403 `RBAC: access denied` from `kagent-mcp.arigsela.com` comes from the Gateway:
  the IP allow-list (`base-apps/istio-ingress/authorizationpolicy.yaml`) or, since 2026-10-13, a
  missing token on that host (the rule ANDs both). A 401 is a token that did not verify (wrong
  issuer or audience, expired: `kubectl oidc-login get-token …` again, or `--as-serviceaccount`).
  Your address isn't on the list, or the WAN IP rotated. A timeout instead usually means the Route 53 record is stale.
  `kubectl -n kagent get httproute kagent-mcp -o yaml` shows whether `/agents/kagent/` is accepted.
  (A 403 from the *model*, inside a completed task, is the egress credential policy above.)
- **Fix:** if the WAN IP rotated, `base-apps/wan-ip-monitor/` moves DNS and opens the allow-list
  PR; see its runbook. A new client address goes into the rule for this host by PR. Meanwhile,
  bypass the Gateway: `kubectl port-forward -n kagent svc/kagent-controller 8083:8083 &` and
  `scripts/ask-hk --endpoint http://127.0.0.1:8083 "…"`.

### Symptom: agents unreachable or every turn fails after an identity change
- **Check:** `kubectl -n kagent get gateway kagent-waypoint` (PROGRAMMED), `kubectl -n kagent logs
  deploy/kagent-waypoint | grep ' 403 '` (which host/path the policy refused; the controller is
  port 8083, everything else behind the waypoint must pass), `kubectl -n istio-system logs
  deploy/istiod | grep -i jwks` (a key fetch failure; the Kubernetes issuer's keys are inline and
  must be refreshed with `kubectl get --raw /openid/v1/jwks` when k3s rotates them), and the
  worker log for `failed to save task state: connection reset` (plaintext from atenet refused:
  a PeerAuthentication STRICT crept in; there must be none in `kagent`).
- **Fix:** by PR. Emergency bypass: revert the enrolment labels in `base-apps/kagent-secrets.yaml`
  (`managedNamespaceMetadata`); the controller is then reachable as before 2026-10-13.

### Symptom: A2A returns `-32004` / `KAGENT_SEND_NOT_ACCEPTED`
- **Cause:** the session was busy pausing or suspending, and the message was provably not
  dispatched.
- **Fix:** resend the identical request (`scripts/kagent_a2a.py` retries for 30 s). Don't blindly
  retry any other error: the input may already be running.

### Symptom: `homelab-knowledge` can't read the repo (GitHub MCP 401 "Bad credentials")
The token is a fine-grained PAT and expires (SPEC T101, 2026-10-11: expired; fixed 2026-10-12).
The agent keeps answering from live cluster objects and says it can't read the repo.
- **Check:** `kubectl -n kagent logs deploy/agent-docs-mcp --tail=50`, and
  `kubectl -n kagent get remotemcpserver agent-docs`. To test the token the cluster actually
  holds, without printing it:
  `curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $(kubectl -n kagent get secret agent-docs-github-mcp-token -o jsonpath='{.data.GITHUB_PERSONAL_ACCESS_TOKEN}' | base64 -d)" https://api.github.com/repos/arigsela/kubernetes`
  (200 = good; 401 = the token itself is invalid or expired).
- **Fix (owner):** mint a new fine-grained PAT, read-only Contents + Metadata on
  `arigsela/kubernetes`, and write it to Vault key `kagent-agent-docs-mcp`, **property
  `github-token`** (`vault kv patch -mount=k8s-secrets kagent-agent-docs-mcp github-token=...`;
  read it into a variable first, never on a command line). Then
  `kubectl -n kagent annotate externalsecret agent-docs-github-mcp-token force-sync=$(date +%s) --overwrite`
  and `kubectl -n kagent rollout restart deploy/agent-docs-mcp` (it reads the token from an env var).
- **Still 401 after the fix?** The property name is wrong. ESO reads exactly `github-token`; a
  `kv patch` to any other name adds a property and leaves the old token in place, and the
  ExternalSecret reports `SecretSynced` all the same. Compare `vault kv get -field=github-token`
  with the Secret (by hash, not by sync status), then `vault kv put` with only `github-token` to
  drop the stray property.

### Symptom: the implementer says it cannot open a pull request (GitHub 401/404 from `implementer-mcp`)
The App token is minted from the Secret `implementer-github-app` (Vault `implementer-github-app`).
- **Check:** `kubectl -n kagent logs deploy/implementer-mcp --tail=50`: `401` = App ID or private key wrong; `404` = installation ID wrong or the App is not installed on the repo; a startup error naming a tool = a misspelt `--tools` entry. `kubectl -n kagent get remotemcpserver implementer -o jsonpath='{.status.discoveredTools[*].name}'` must list 12 tools.
- **Fix (owner):** re-run `scripts/provision-implementer-vault.sh` with the right `--app-id`, `--installation-id` or a new `--key-file`, then `kubectl -n kagent annotate externalsecret implementer-github-app force-sync=$(date +%s) --overwrite` and `kubectl -n kagent rollout restart deploy/implementer-mcp`.
- **A PR it opened cannot change `.github/workflows`**: by design (no Workflows permission); the agent reports it and opens nothing.
- **`agent run failed: duplicate tool: "<name>"`** on the first turn: two bindings on the template repeat a tool name; the Go ADK refuses the agent. `tests/implementer/test_agent.py::test_no_tool_name_is_bound_twice` pins it.

### Symptom: web search fails (`homelab-knowledge` / `dungeon-crawler-carl-agent` say they cannot search, or `brave_web_search` errors)
- **Check:** `kubectl -n kagent get externalsecret brave-search-mcp-api-key` (must be `SecretSynced`), then `kubectl -n kagent get pods -l app.kubernetes.io/name=brave-search-mcp` and its logs, then `kubectl -n kagent get remotemcpserver.api.kagent.dev brave-search -o yaml` for the discovered tools. A `SecretSyncedError` means the key is missing or wrong in Vault (`k8s-secrets/brave-search-mcp`, property `api-key`); the MCP server pod cannot start without it. A 401/403/429 in the pod logs is the Brave API: wrong key, or the Free plan's 2,000 queries/month used up (https://api-dashboard.search.brave.com/).
- **Fix:** write or rotate the key with `BRAVE_API_KEY=... scripts/provision-brave-search-vault.sh` (it also recreates the Vault role and policy if they are missing), then force the ExternalSecret: `kubectl -n kagent annotate externalsecret brave-search-mcp-api-key force-sync=$(date +%s) --overwrite`. The pod starts on its own once the Secret exists. The agents keep answering without search in the meantime; they just say so.

### Symptom: an agent run fails at start with `failed to init MCP session … Service Unavailable`
- **Check:** one of the tool servers the agent's template binds was down at that second. The
  message does not name it: `kubectl -n kagent get pods` and look for a recent restart
  (`brave-search-mcp`, `agent-docs-mcp`, `implementer-mcp`, `kagent-tools`), then
  `kubectl -n kagent get pod <pod> -o jsonpath='{.status.containerStatuses[0].lastState}'`
  for the reason (`OOMKilled` on brave-search-mcp, 2026-10-15). Every agent bound to that
  server fails the same way until it is back.
- **Fix:** nothing for the run itself; ask again once the pod is `Running` (about a minute).
  For a repeat OOM, the pod's memory history in Prometheus (`container_memory_working_set_bytes`,
  namespace `kagent`) tells sizing from a leak: steps at moments of use are sizing, a steady
  climb is a leak. Sizing is the `mcp-server` container's `resources` in the server's Deployment
  (`brave-search-mcp.yaml`), and `NODE_OPTIONS` in its env for a node server; a leak is an image bump.

### Symptom: a `RemoteMCPServer` isn't accepted, or an agent says it lacks a tool
- **Check:** `kubectl -n kagent get remotemcpservers` (`ACCEPTED`) and
  `kubectl -n kagent describe remotemcpserver <name>` (`status.discoveredTools`). For `agent-docs`,
  also the backing pod: `kubectl -n kagent get pods -l app.kubernetes.io/name=agent-docs-mcp`. A server Deployment registers no tools
  with agents; the `RemoteMCPServer` that points at its Service does. A tool is
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
  the `kagent-postgres-owner` or `kagent-postgres-readwrite` Secret missing or stale
  (`kubectl -n kagent get externalsecret kagent-postgres-owner kagent-postgres-readwrite`),
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

### Symptom: `docs-checker` sessions fail at start (the one `byo` agent)
- **Check:** the image pull first. atelet pulls the docs-checker image itself, with the ECR
  credential provider, not the cluster's `ecr-registry` secret (`base-apps/substrate/runbook.md`,
  "an actor using the docs-checker image never starts").
- **Check:** the actor's output: stdout of the `kagent-default` worker pods (and Loki), lines
  labelled `ate.template.name: docs-checker-<hash>`. `KAGENT_CONFIG_JSON is not a kagent
  v1.0.0-alpha14 ADKConfig` means the controller is not on alpha14: the image speaks alpha14's
  runtime contract only (§T.128), so a kagent rollback below alpha14 breaks this agent, and only
  this one. Substrate waits up to 30 s for its `GET /readyz` on 8081 before the first message.
- **Fix:** where the fault lives: the Substrate pull key, or the kagent version. The image itself
  changes only by a new digest: build, push and pin it (admission-policies runbook, "Pin a byo
  image digest"), never a tag in the Harness (the CRD and `agent-capability-runtime` refuse one).

### Symptom: Slack shows `:x: Investigation: Weekly docs check … Failed:`, or no weekly docs check on a Monday
- **Check:** the alert names the Job (`kubectl -n kagent logs job/<run id>`); every log line is
  `event: docs-checker-trigger`, and the `error` one says why. Memory was not touched, except
  after `the trigger failed: unexpected …` (a bug, or the pod's token or CA missing), where the
  line's `trace` shows how far the run got. Common reasons:
  - `the task ended TASK_STATE_FAILED: docs-checker failed: timeout`: docs-checker stopped itself
    at 15 minutes. A first run (empty memory) checks every doc and is the slowest; otherwise read
    the actor's log ("`docs-checker` sessions fail at start" above).
  - `the A2A call failed: HTTP 403 …` or `401`: the waypoint refused the trigger's token ("ask-hk
    gets HTTP 403" and "agents unreachable after an identity change" above).
  - `the task completed without a report (missing …): Request refused …`: docs-checker did not get
    the request's data part, so it treated the empty text as off topic and ran nothing.
  - `cannot read ConfigMap kagent/docs-checker-memory`: Argo CD has not created it yet, or the
    Role `docs-checker-trigger` is missing (`argocd app get kagent-secrets`).
  - `the delivery could not be recorded (…), so the report is not posted`: the API server refused
    even the claim alone (it is down, or the ConfigMap is already over 1 MiB). The report is in the
    log, not in Slack; run again once the API answers (reset the memory first if it is the size).
- **Slack shows the report AND `:x: … Failed: memory not saved: …`:** the report was delivered;
  only docs-checker's new snapshot was dropped. Either it would have taken the ConfigMap's data
  past its budget (the API server's 1 MiB less a 16 KiB margin; `… bytes of ConfigMap data`), the
  API server refused it (`HTTP 422`), or it is not a version-1 snapshot with an integer run
  counter (`snapshot version …`, `run counter …`: docs-checker's memory schema changed, so update
  `SNAPSHOT_VERSION` in `scripts/docs_checker_trigger.py`). The previous memory is kept, so the
  next run re-checks what this one learned and costs more. If the size alert repeats, reset the
  memory (How-to below).
- **Check, nothing in Slack at all:** `kubectl -n kagent get cronjob docs-checker-trigger` (`LAST
  SCHEDULE`; a run missed by more than an hour is skipped by design) and the Job's log. `report not
  posted: …` means n8n refused the post: the run is recorded as claimed, so the Job's retry posts
  nothing (at most once per run, V102); the digest and the full report are in that log line.
- **Fix:** fix the cause, then run it again as a NEW run ("Weekly docs check" How-to). Re-running
  the same Job never posts.

## How-to

### Weekly docs check: run it now, read it, reset its memory, dismiss a finding
- **Run now:** `kubectl -n kagent create job --from=cronjob/docs-checker-trigger
  docs-checker-trigger-manual-$(date +%s)`. The Job name is the run id, so this is a new run
  (one paid docs-checker run, at most $1.00).
- **Read a run:** `kubectl -n kagent logs job/<run id>` (three of each kept; older ones in Loki,
  `{namespace="kagent"} |= "docs-checker-trigger"`). The line with `report` holds the full
  Markdown, every finding with its evidence, proposed replacement and evidence hash:
  `kubectl -n kagent logs job/<run id> | jq -r 'select(.report) | .report'`. The session belongs to
  the trigger's identity: `scripts/ask-hk --agent kagent/docs-checker --as-serviceaccount
  kagent/docs-checker-trigger --context <session from the log> "check <path>"` continues it.
- **State:** `kubectl -n kagent get cm docs-checker-memory -o jsonpath='{.data.deliveries\.json}'`
  lists the last ten run ids and whether each was posted; `memory.json` is docs-checker's snapshot.
- **Reset the memory** (garbled snapshot, a repeated "memory not saved" size alert, or to force
  every doc to be checked):
  `kubectl -n kagent delete cm docs-checker-memory`, when no run is in progress. Argo CD recreates it
  empty; the next run checks every doc (the slowest and most expensive run, still capped at $1.00).
  It also forgets which runs were delivered, so do not delete it while a Job is retrying.
- **Dismiss a finding:** a PR adding an entry to `docs/docs-checker-dismissed.yaml` (its comment
  lists the fields; copy `doc`, `claim` and `evidence_hash` from the report). docs-checker drops the
  finding from the next run on, until its evidence changes. An invalid entry is not applied and the
  report lists it under "Dismissal errors".

### Ask an agent
`scripts/ask-hk "question"` (homelab-knowledge), `--agent kagent/<name>` for another agent
(`docs-checker` takes `check all` or `check <path>[, <path>...]`, optionally `at <ref>`),
`--context <id>` to continue (the id is printed after each answer). Exit 0 answered, 2 waiting for
an approval or an answer, 1 failed. The UI is `https://kagent.arigsela.com`.

From Claude Code every message goes through `ask-hk` (`--context <id>` for follow-ups); the
`kagent` MCP server was retired on 2026-10-13 (the endpoint needs a bearer token Claude Code
cannot mint).
Start a new conversation after an AgentTemplate change (a Session is pinned to the revision it
was created from) and after 30 idle days (`config.sessions.idleTtl: 2592000s`, 30 days, in `base-apps/kagent.yaml`;
the Session is deleted, memory is kept).

### Read, re-run or reset the evaluation gate
`kubectl -n kagent logs job/eval-gate` shows the last run: one `eval-gate` line per agent with
`passed`, `total`, `judge`, `regression`, `failed` (corpus ids) and `revision`, or `skipped:
unchanged`. Re-run it by syncing the app (`argocd app sync kagent-secrets`, or any merge touching
`base-apps/kagent/`); it only scores agents whose definition hash changed, so to force a score
reset the state: read it with `kubectl -n kagent get cm eval-gate-state -o
jsonpath='{.data.state\.json}'`, then delete the ConfigMap (`kubectl -n kagent delete cm
eval-gate-state`; it is the job's own scratch, not a Git-managed object): the next run recreates
it and sets a fresh baseline, stated as `first_run: true`. The `memory` question expects state saved earlier
by the gate's own identity: `scripts/ask-hk --as-serviceaccount kagent/eval-gate "My favorite color
is octarine. Please save that."` once, then re-run. `judge: none` means no API key in Secret
`eval-gate`: run `scripts/provision-eval-gate-vault.sh` (owner), then add the SecretStore, ESO ServiceAccount and ExternalSecret (plan Task 6).

### An agent is held by the eval gate (`skip-eval-hold`, "is held by the eval gate" in Slack)
- **Symptom:** the dispatcher logs `decision: skip-eval-hold` for an alert, or the Slack bridge
  answers a mention with "`<agent>` is held by the eval gate (…)". `ask-hk` still works: humans
  are not gated (V99).
- **Check:** `kubectl -n kagent logs job/eval-gate` for that agent's line: `held: true` with
  `hard` (a leak of the canary, a forbidden tool call, a failed security refusal) or a pass rate
  below `baseline`; `failed_detail` says per failed entry who decided (`judge`, `rubric(...)`,
  `no-answer`), the task state the capture saw and the rubric's reasons. A `no-answer` with state
  `TASK_STATE_WORKING` is a question that outran `QUESTION_TIMEOUT`, not the agent (§B.35). The holds also sit in `kubectl -n dispatcher get cm eval-release -o
  jsonpath='{.data.release\.json}'` (and the same in `slack-bridge`).
- **Fix:** fix the agent (prompt, tools, model) and merge; the next sync re-scores it and, if
  clean, writes `held: false` (the kubelet refreshes the mounted file within about a minute). To
  release an agent WITHOUT a fix, delete its entry from `eval-gate-state` (the gate rewrites
  `release.json` from its state on the next run) — that is a decision, log it in SPEC §B. The
  canary pod must exist for the canary questions to mean anything: `kubectl -n eval-canary get
  pod canary-logger-0` (its ConfigMap value must equal `CANARY_VALUE` in
  `scripts/gen-eval-adversarial-corpus.py`; CI checks that).
- **A first run after a corpus change** says `baseline_reset: true` and sets a new baseline; it
  holds only on a hard fail.

### Approve or reject a gated tool call from the CLI
`ask-hk` requests kagent's HITL A2A extension on every message, so a `requireApproval` pause
comes back with a request id. On exit 2 it prints the pending call(s) and remembers them per
context (`~/.cache/ask-hk/`). Then:
```
scripts/ask-hk --agent kagent/k8s-agent --context <id> --approve
scripts/ask-hk --agent kagent/k8s-agent --context <id> --reject "why"   # the agent sees why
scripts/ask-hk --agent kagent/k8s-agent --context <id> "<answer>"      # when it asked a question
```
One decision covers every call in the request. A plain message while an approval is pending
is refused. The same pause can be decided in the UI instead. If `ask-hk` says the pause "sent
no request id", the message that started the turn was sent by a client that didn't request the
extension (an old `ask-hk`, a bare curl): decide it in the UI, or cancel and resend.

### Deploy / update
Agents are three files: the AgentTemplate (`agent-templates/`), the Agent (`agents/`, references
the template and a Harness), and, rarely, a new Harness (`harnesses/`). Run
`scripts/validate-agent-identity.py --repo-root .` and `scripts/validate-agent-capability.py --repo-root .`
before the PR. Chart values are in `base-apps/kagent.yaml`. Argo CD (`kagent`, `kagent-crds`,
`kagent-secrets`, all `prune`/`selfHeal`) syncs on merge. Git is the only way in: a direct
`kubectl apply/edit` of an Agent, AgentTemplate, Harness or RemoteMCPServer, or a create or edit
in the kagent UI, is denied by `agent-definitions-from-git` (break glass: `admission-policies`
runbook). After a prompt or tool change, ask the
agent a **multi-step tool question**, not a one-liner: tool-loop failures only show from the second
model request on.

### Rotate a Vault-backed secret
Each ExternalSecret resolves through its own `SecretStore`, ESO ServiceAccount and Vault role,
reading a per-consumer Vault key. Rotate at that consumer's path:

| Secret (ExternalSecret if different) | SecretStore | Vault key (rotate here) |
|---|---|---|
| `agent-docs-github-mcp-token` | `vault-agent-docs-mcp` | `k8s-secrets/kagent-agent-docs-mcp` (`github-token`) |
| `backstage-mcp-token` | `vault-backstage-mcp` | `k8s-secrets/kagent-backstage-mcp` (`token`) |
| `kagent-postgres-owner` | `vault-kagent-db` | `k8s-secrets/kagent-db` (`v1-password`; also see the postgresql app below) |
| `kagent-postgres-readwrite` | `vault-kagent-db` | `k8s-secrets/kagent-db` (`v1-readwrite-password`; also see the postgresql app below) |
| `kagent-anthropic` (`kagent-anthropic-secrets`) | `vault-kagent-anthropic` | `k8s-secrets/kagent-anthropic` (`anthropic-api-key`) |

Update the value in Vault (`vault kv patch`, never in git). ESO picks it up within the 1h
`refreshInterval`, or force it: `kubectl -n kagent annotate externalsecret <name> force-sync=$(date +%s) --overwrite`.
Then:
- **Anthropic key, Backstage token:** no agent holds them; Substrate's credential provider
  resolves the Secret for the egress gateway. If calls keep using the old value after the Secret
  changed, restart `deploy/k8s-credential-provider` in `ate-system`.
- **GitHub token:** restart `deploy/agent-docs-mcp` (env var).
- **Database password:** 64 hex characters (the connection string doesn't escape it). `kagent_v1`
  has two: `v1-password` (the owner login, `kagent-postgres-owner`) and `v1-readwrite-password`
  (the read/write login, `kagent-postgres-readwrite`). Rotate whichever changed, then force-sync
  `kagent-postgres-owner` and `kagent-postgres-readwrite` in `kagent` and `kagent-v1-db-credentials`
  in `postgresql` (the annotation above), sync the `postgresql` app once (its init Job sets both
  passwords), then restart `deploy/kagent-controller`. Same procedure as Substrate's
  (`base-apps/substrate/runbook.md`, "Rotate the database passwords").

A `SecretSyncedError` with Vault **403 permission denied** means the Vault side of the scope is
wrong: the policy must grant `read` on exactly that path, and the kubernetes-auth role must bind
that ESO ServiceAccount in `kagent`.

### Restart the controller
`kubectl -n kagent rollout restart deploy/kagent-controller` is safe: sessions live in Substrate and
Postgres, and agents reconcile again once it is back. Verify with `kubectl -n kagent get agents`.
After a restart a few `RemoteMCPServer` reconcile errors can appear until each target answers.

### Bump kagent and Substrate (every alpha bump is a clean-database migration)
kagent 1.0 is built against one Substrate version (1.0.0-alpha14 ↔ 0.5.0-alpha2, from kagent's
`go.mod`; alpha11 pinned the same Substrate). Every alpha so far has needed empty databases, so treat a bump as a migration, never as
a routine Renovate merge. Sessions and long-term memory reset.
1. **Prepare one PR** (Renovate opens it as `kagent-substrate` only after you tick it on the
   Dependency Dashboard, labelled `clean-db-migration`): the five charts (`kagent-crds`, `kagent`,
   `substrate-crds`, `substrate-podcert`, `substrate`) to the versions the kagent release names;
   re-pin **every** digest in the values of `base-apps/kagent.yaml` (controller, UI, tools,
   `substrateWorkerPool.workerImage`), `substrate-podcert.yaml` and `substrate.yaml`
   (plus the component digests in its header); the golang-adk `workload.image` in the **three**
   `kagent`-runtime Harnesses (identical); and the CRD fixture under
   `tests/admission-policies/fixtures/crds/`.
   Re-render both charts with `helm template --kube-version` and read the release notes for
   CRD and value changes. Read `helm show values` for the moved `config.*` keys. Before the
   Application moves, run `KAGENT_CHART_VERSION=<new> python3 -m pytest
   tests/agent-capability/test_kagent_tools_rbac.py` (it honours the override);
   `test_kagent_chart_config.py` renders the Application's own `targetRevision`, so run it once
   the Application names the new chart. Renovate groups the Harness image with the charts (`renovate.json5`,
   group `kagent-substrate`), so it never arrives as a Harness-only PR.
   **docs-checker** (the `byo` Harness) is not in that group: its image implements kagent's
   runtime contract itself. Read the release for changes to that contract (the
   `KAGENT_CONFIG_JSON` `ADKConfig` shape, the `kagent.runtime.v1` TaskStore service, the A2A and
   `/readyz` ports), rebuild the image (`arigsela/claude-agents` `docs-checker/`) against the
   matching `kagent-core`, push it, and pin the new digest **in the same PR**: the taxonomy's
   `byo-images` entry, the good fixture and `harnesses/docs-checker.yaml` (the `sed` in the
   admission-policies runbook, "Pin a byo image digest", with the old digest in place of
   `REPLACE_WITH_DIGEST`). The image checks the config shape at startup and refuses one it does
   not know, as it refused alpha11's.
2. **Run the guardrails** against the new CRDs: `python -m pytest tests/admission-policies -q`,
   the identity and capability validators, and `tests/agent-capability`.
3. **Archive the record:** take a final audit export
   (`kubectl create job -n postgresql --from=cronjob/agent-audit-export pre-bump-export`), then dump
   `kagent_v1` and `substrate` off-cluster with checksums (the pgvector Deployment has no backup
   script; `pg-backup.sh` covers only CNPG) to `s3://mysql-backups-asela-cluster/pg/`.
4. **Clean databases:** drop `kagent_v1` and `substrate` on the pgvector Postgres, then sync the
   `postgresql` app once: `init-kagent-v1-db` and `init-substrate-db` recreate them empty, and
   `init-kagent-audit-role` / `init-agent-audit-web-role` restore the audit grants.
   `init-kagent-v1-db` also recreates the read/write login.
5. **Merge**, then restart `kagent-controller` and Substrate's `ate-api-server` if they don't roll
   on their own.
6. **Accept:** 10 Agents `Ready`; each Go ADK agent answers a multi-step tool question;
   docs-checker returns a report for one `check <path>` call
   (`scripts/ask-hk --agent kagent/docs-checker "check base-apps/vault/docs.md"`); `k8s-agent`
   pauses a delete for approval (reject keeps the object); `scripts/ask-hk` works; run
   `agent-audit-ungated` once by hand and read its line. Expect the eval gate to re-score every
   agent (the Harness digest is in each definition hash) and to hold `homelab-knowledge` on the
   memory entry until it is reseeded (see *Read, re-run or reset the evaluation gate*).

### Roll back
- **A bad agent, prompt or value change:** `git revert` the PR. Argo CD applies it.
- **A bad alpha bump:** revert the bump PR **and** restore the pre-bump `kagent_v1` and
  `substrate` dumps (step 3 above) into freshly created databases: the new version will have
  migrated the schema. Snapshots written by the newer Substrate may not restore; start new
  conversations.
- **A bad alpha14 bump:** the alpha11 dumps are restored as above, and `git revert` re-adds
  `kagent-postgres-external-secret.yaml` (deleted by the alpha14 PR), which the alpha11 controller
  reads as `kagent-postgres`.
- **Back to kagent 0.10.3** (last resort, never exercised): 0.10 and 1.x can't share a cluster
  (both own `mcpservers.kagent.dev`) and there is no conversion. Revert the cutover PRs in reverse
  order (#737 chart and CRDs, then #734 agents and v1alpha3 guardrails), check what later PRs
  assume 1.0 (the `/agents/kagent/` route and `scripts/ask-hk`, Backstage's `api.kagent.dev`
  RBAC, the audit CronJobs' `--source v1`), then make the archive writable again:
  `ALTER DATABASE kagent SET default_transaction_read_only = off`. 0.10 comes back with its
  history; nothing from 1.0 carries over. See Task 5.5 of
  `docs/plans/kagent-1-0-on-k8s-1-37-implementation-plan.md` (SPEC §T.99).
