# kagent 1.0 on Kubernetes 1.37 Implementation Plan

**Status:** Executed 2026-10-11 — see SPEC T94-T100. (Approved 2026-10-11.) This is now a record; the living docs are `base-apps/kagent/docs.md`, `base-apps/kagent/runbook.md` and `base-apps/substrate/`.
**Owner:** Ari Sela
**Supersedes:** [`kagent-1-0-feasibility-spikes-implementation-plan.md`](kagent-1-0-feasibility-spikes-implementation-plan.md) and its 2026-09-26 "second cluster" decision
**SPEC:** §T.89 (1.36 → 1.37 hop) and the kagent 1.0 rows added with this plan

## Overview

Move the homelab's kagent from **0.10.3** to **1.0.0-alpha11** (the latest alpha), in place on
the production cluster. kagent 1.0 documents Kubernetes 1.37+, so k3s goes from **v1.36.4+k3s1**
to **v1.37.1+k3s1** first. The agents move to **Claude Sonnet 5.5 / Haiku 5.5**: first on
0.10.3 through `runtime: go` (Phase 0), then on 1.0. `homelab-agent` (LangGraph BYO) is retired.

### Decisions (2026-10-11)

| Decision | Choice | Notes |
|---|---|---|
| Where 1.0 runs | **In place on prod, no scratch-VM preflight** | Supersedes the second-cluster decision. Phase 4 installs Substrate on prod *before* the kagent cutover, as a go/no-go. |
| k3s version | **v1.37.1+k3s1 from the `latest` channel** | Amends §T.89, which required `stable` (stable is v1.36.5). |
| What blocks the hop | **Only a hard 1.37 break** | Amends §V.2 for this hop. A component that doesn't *state* 1.37 support does not block it. |
| Models | Sonnet 5.5: `homelab-knowledge`, `k8s-agent`, `istio-agent`, `build-orchestrator`. Haiku 5.5: `k8s-reader`, `skill-suggester`, `dungeon-crawler-carl-agent` | |
| Interim models | Phase 0 on 0.10.3 via `runtime: go` | Also previews the Go agent runtime that 1.0 uses by default. |
| `homelab-agent` | **Retired** | kagent-langgraph does not implement 1.0's BYO contract (gRPC A2A, TaskStore). |
| Live state for `homelab-knowledge` | Binds `kagent-tool-server`'s 8 read tools directly | 1.0 sub-agent calls hand the conversation off and never return the child's answer. `build-orchestrator` keeps hand-off semantics (it is a router). |
| Agent audit pipeline | **Ported in this plan** | |
| Session snapshot store | Substrate's bundled rustfs on a node-pinned PVC | AWS S3 later: `ate-api-server` cannot take AWS credentials until kagent-dev/substrate#64. Sessions are disposable; memory lives in Postgres. |
| Databases | New `kagent_v1` and `substrate` on the existing pgvector Postgres | The 0.10 `kagent` database is kept read-only as the archive. |
| Pinning | kagent `1.0.0-alpha11`, Substrate `0.5.0-alpha2`, images by digest | Every alpha bump is its own clean-database migration (alpha11 itself required one). |
| "ask hk" | A2A `POST /agents/kagent/homelab-knowledge` through `scripts/ask-hk` | The 1.0 MCP endpoint has no create-session or invoke-by-agent tool. |

## Success Criteria

- [ ] All 3 nodes on `v1.37.1+k3s1`, ⊥ data loss, `hop-verify.sh gate` passed.
- [ ] Gateway API CRDs still Argo-owned at v1.5.1; `kubectl get helmchart -A` is empty.
- [ ] kagent 1.0.0-alpha11 + Substrate 0.5.0-alpha2 running on prod.
- [ ] All 7 agents Ready on Sonnet 5.5 / Haiku 5.5; each passes a live multi-step tool question.
- [ ] `k8s-agent` / `istio-agent` destructive tools need approval: reject keeps the object, approve deletes it.
- [ ] Admission policies and CI validators enforce identity and capability on `api.kagent.dev/v1alpha3` and fail on agent manifests they don't understand.
- [ ] The audit pipeline (ungated-tool alert, risk scoring, export, eval mining, agent-audit-web) reads 1.0 sessions; history readable from the archive; alerts fail closed.
- [ ] "ask hk", Backstage agent entities/cards and skill-suggester work against 1.0.
- [ ] `homelab-agent` gone (DB, secrets, Vault, ECR repo); no 0.10 leftovers.
- [ ] Docs, runbooks, SPEC, recovery doc and memory updated.

## Research Findings

Researched 2026-10-11 against kagent `v1.0.0-alpha11` (2026-10-09), Substrate fork
`kagent-dev/substrate v0.5.0-alpha2`, k3s `v1.37.1+k3s1` and Kubernetes `v1.37.1`. Two
independent throwaway k3s v1.37.1 runs (Docker, arm64) backed the 1.37 conclusions.

### Kubernetes 1.37 compatibility

**Breaks — one item, from k3s itself.** k3s 1.37 adds a bundled `gateway-api-crd` HelmChart
(`manifests/gateway-api-crd.yaml`, `takeOwnership: true`, `forceConflicts: true`, k3s#14573)
that installs Gateway API **v1.6.1** plus a `safe-upgrades` ValidatingAdmissionPolicy.
`disable: traefik` does not stop it (k3s#14677; k3s skips bundled manifests by file name,
`pkg/deploy/stage.go`). After the hop, Argo's `istio-gateway-api` (pinned v1.5.1, selfHeal)
fails to sync — *"Installing CRDs with version before v1.5.0 is prohibited by default"* — and
Argo v3.5 never retries a failed auto-sync of the same revision, so the gate goes red.
**Fix:** `disable+: [gateway-api-crd]` in a k3s server drop-in before the hop (tested: nothing
installed, no CRDs). Live traffic survives either way (all 40 Gateway API objects validate
against v1.6.1; Istio 1.30.5 checks only a minimum CRD version).

**Checked and fine.**
- All 17 Helm charts render identically at 1.36.4 and 1.37.1; none sets an upper `kubeVersion`.
- On k3s v1.37.1: 662/662 repo objects pass server-side dry-run (182 CRDs installed); the
  admission suite passes 76/76; the structured authn config loads (anonymous → 401); Istio
  1.30.5 programs a Gateway + HTTPRoute.
- 1.37 removes only `scheduling.k8s.io/v1alpha2` (unused). No removed feature gate or flag is in
  use (no `--feature-gates`; kubelet configz `featureGates: null`; cgroup v2 + systemd driver;
  `eventRecordQPS: 50`; no static pods; no SELinux). MutatingAdmissionPolicy is GA (v1) since 1.36.
- k3s v1.37.1 bundles kine v0.17.1 (no schema migration; adds RangeStream), CoreDNS v1.14.7,
  helm-controller v0.17.9; containerd 2.3.4, runc 1.4.2, flannel 0.28.4 and local-path 0.0.37
  are unchanged.

**Watch, not blocking.**
- kube-state-metrics#3142: on 1.37.1 with the systemd cgroup driver, `container_network_*`
  series lose `pod`/`namespace` labels (reported on cri-o). Dashboards use only CPU/memory
  series; verify after the hop.
- Kyverno v1.19.1 states ≤1.35 (reports only since §T.80). ESO 2.11 reached EOL 2026-10-06 on
  its normal cadence. Istio 1.31 adds 1.37 (optional; would pair with Gateway API v1.6). The
  istio-cni AppArmor annotation logs a deprecation warning.
- Not checked in depth: the `langflow-ide` namespace (kubectl-applied, not in git) and Crossplane
  `provider-sql` v0.9.0. Unrelated and already broken: `bitnami/kubectl:1.30` in
  `argo-workflow-tasks`.

**Substrate prerequisites on 1.37: none.** ClusterTrustBundle, ClusterTrustBundleProjection and
PodCertificateRequest are GA and on by default; `certificates.k8s.io/v1` serves both. Do **not**
set the gates (they lock in 1.38; a removed gate stops the API server from starting).

**Removed-API scan.** `pluto:v5` (= 5.23.5) and the latest 5.24.4 have no 1.37 entries, so the
scan is vacuous for 1.37 until it declares `scheduling.k8s.io/v1alpha2` itself (Task 1.3).

**k3s release state.** `stable` = v1.36.5+k3s1, `latest` = v1.37.1+k3s1 (2026-09-30).

### kagent 1.0.0-alpha11

- **Charts:** `oci://ghcr.io/kagent-dev/kagent/helm/{kagent-crds,kagent}` `1.0.0-alpha11`
  (subcharts kmcp 0.4.0, kagent-tools 0.3.0, grafana-mcp, oauth2-proxy);
  `oci://ghcr.io/kagent-dev/substrate/helm/{substrate-crds,substrate-podcert,substrate}`
  `0.5.0-alpha2` (release must be named `substrate` in `ate-system`).
- **Substrate prerequisites:** a prepared Postgres (NOLOGIN roles `substrate_owner` /
  `substrate_readwrite`, schema `substrate`, login users, Secrets
  `substrate-postgres-{readwrite,owner}`); 7 CA/JWT pool Secrets and the
  `ate-api-authentication` ConfigMap (normally generated by `kagent install`). CAs last 365
  days and rotating them needs restarts (substrate#1931).
- **kagent prerequisites:** Secret `kagent-postgres` (`connectionString`), role
  `kagent_owner`, schema `kagent`, pgvector pre-installed in schema `extensions`;
  `credentialProvider.namespacePolicies` or every model call returns 403.
- **Security profile:** nothing privileged. atelet runs as root with `DAC_OVERRIDE`; worker pods
  run as UID 0 with 13 capabilities and seccomp/AppArmor Unconfined; hostPorts 8085/9090. The
  repo's Deny policies match only `agents` and `externalsecrets`, so nothing blocks it; the
  hygiene audits will report it.
- **Semantics:** one RemoteMCPServer may be bound twice with different `requireApproval` (kagent
  runtime only). Sub-agents hand off and never return. Memory works with Ollama
  `nomic-embed-text` (768-dim). `promptTemplate` works. `Agent` is a declarative CR; Sessions
  are created on demand. MCP tools: `list_sessions`, `invoke_session`, checkpoint/fork, sandbox
  — no create tool. A2A at `/agents/{ns}/{name}`, unauthenticated, identity via `X-User-Id`.
- **Go Anthropic adapter:** drops thinking blocks; sends prior assistant text as role "user"
  (kagent#3026, open); default `max_tokens` 8192 and truncation is reported as completed
  (#2895, open). No thinking or effort field; `promptCaching` / `cacheTTL` exist.
- **Fixed since the spike plan:** #2851 (zero-argument tools, alpha9), #2897 (session GC,
  alpha6; `sessionIdleTTL` default 168h), private registry pulls via credential-provider plugins.
- **Still open:** substrate#1837 (worker IP after reboot), #1761 (resume pinned to a node,
  ~6 s deadline), #1657 (snapshot tied to CPU flags), kagent#2604 (network-sourced skills),
  #3026, #2895, #2688 (forwarded headers only).
- **CPU (Xeon E5-2670, x86-64-v2 + AVX, no AVX2):** the `claude` runtime (Claude Code CLI
  native binary) needs AVX2 and will not run. Everything else builds for the x86-64 baseline.
  gVisor runsc and NumPy are unverified on this CPU.
- **Churn:** 11 alphas in 3 weeks; alpha11 itself required clean databases (kagent#3111).

### Repo coupling — where today's guardrails stop working silently

- Admission policies (`agent-identity.yaml`, generated `agent-capability.yaml`) match
  `kagent.dev/v1alpha2 agents` and would match nothing; the delegation policy's `paramKind`
  would point at a missing CRD.
- CI validators collect `kind: Agent` and read `spec.declarative.*`; they would pass vacuously.
- `agent-audit.py` / `mine-eval-corpus.py` query `event`/`session` in `public` and key on
  `adk_request_confirmation`; the audit roles are granted on `public` only; the Grafana alerts
  use `noDataState: OK`.
- Backstage ingests agents through the per-agent Deployment (gone in 1.0); the portal code
  hard-codes `kagent.dev/v1alpha2`.
- Per-agent Service DNS is hard-coded in `homelab-agent`'s env, `capture-eval-answers.py` and
  the "ask hk" shortcut.
- `anthropic-claude-sonnet-4-6`'s Secret is owner-referenced to its ModelConfig and not in Vault.
- The pgvector Postgres holding `kagent` and `homelab_agent` has no backup.
- The `homelab-agent` ECR repo is Terraform-managed (`terraform/roots/asela-cluster/ecr.tf`).

### Relevant files

- kagent: `base-apps/kagent*.yaml`, `base-apps/kagent/**`
- Policies/RBAC/CI: `base-apps/admission-policies/{agent-identity,agent-capability,agent-capability-taxonomy,kyverno-reports-rbac}.yaml`,
  `base-apps/cluster-rbac/kagent-tools.yaml`, `base-apps/backstage/rbac.yaml`,
  `.github/workflows/validate.yaml`, `renovate.json5`
- Postgres/audit: `base-apps/postgresql/{init-kagent-db,init-kagent-audit-role,init-agent-audit-web-role,agent-audit-cronjob,external-secrets-kagent*}.yaml`,
  `base-apps/logging/grafana-alerting.yaml`
- Scripts: `scripts/{validate-agent-identity,validate-agent-capability,gen-agent-capability-policy,agent-audit,agent-audit-risk,gen-agent-audit-cronjob,mine-eval-corpus,capture-eval-answers}.py`, `scripts/hop-verify.sh`
- Tests: `tests/{admission-policies,agent-identity,agent-capability,agent-audit,eval-corpus,k3s-upgrade}/`
- Node config: `ansible/playbooks/k3s-authn.yml`, `ansible/roles/k3s_node/files/authn/`,
  `base-apps/system-upgrade-controller/plan-agent.yaml`
- Other repos: `~/git/backstage` (portal), `arigsela/agent-audit-web`, `arigsela/claude-agents`

### Existing patterns followed

- The hop procedure in `base-apps/system-upgrade-controller/runbook.md` (record:
  `k3s-1.36-upgrade-plan.md`): cold backups → `hop-verify.sh gate` → sync-window pause → manual
  control-plane hop → istio-cni step → workers via system-upgrade-controller.
- §V.81: node-level k3s config lives in git and is installed only by its Ansible playbook, with
  throwaway-k3s validation and auto-rollback.
- Per-consumer SecretStore + Vault role, provisioned by a script (`provision-homelab-agent-vault.sh`).
- One commit, one sync, one rollback point per component (§V.8); grep consumers before removing
  an app (§V.72); SPEC changes only through `/ck:spec`.

## Implementation

### Phase 0: Interim — 5.5 models on 0.10.3 (`runtime: go`)

Gets the new models running now and surfaces the Go runtime's quirks before 1.0 depends on them.
Replaced at cutover.

#### Task 0.1: Canary `k8s-reader` on Go + Haiku 5.5
**Files:** `base-apps/kagent/model-configs/anthropic-claude-{haiku,sonnet}-5-5.yaml` (new),
`base-apps/kagent/agents/k8s-reader.yaml`
**Steps:**
1. Add two `v1alpha2` ModelConfigs on `apiKeySecret: kagent-anthropic` (Vault-backed) with
   `anthropic: {maxTokens: 16000}`; no `temperature`/`topP`/`topK` (the 5.5 models reject them).
   Header comment: why Go, why `maxTokens`.
2. `k8s-reader`: `runtime: go`, `modelConfig: anthropic-claude-haiku-5-5`.
**Testing:**
- [ ] `validate-agent-identity.py`, yamllint, kubeconform pass.
- [ ] Live multi-step question ("Which pods in kube-system aren't Running, and what are their
  recent events?"): ≥2 tool calls, correct answer, no 400 in the agent's logs.
- [ ] A long answer isn't cut off (#2895); a follow-up that refers back keeps context (#3026).

#### Task 0.2: `homelab-knowledge` on Go + Sonnet 5.5
**Files:** `base-apps/kagent/agents/homelab-knowledge.yaml`
**Steps:** `runtime: go`, `modelConfig: anthropic-claude-sonnet-5-5`; update the comments that
say Sonnet 5 breaks the runtime.
**Testing:**
- [ ] An "ask hk" question that walks index.md → app docs → delegates to `k8s-reader` is answered correctly.
- [ ] The built-in prompt template renders; memory from session A is recalled in session B.

#### Task 0.3: Remaining agents and the chart default
**Files:** `base-apps/kagent/agents/{k8s-agent,istio-agent,skill-suggester,dungeon-crawler-carl-agent}.yaml`,
`base-apps/kagent/build-orchestrator.yaml`, `base-apps/kagent.yaml`, `base-apps/kagent/{docs,runbook}.md`
**Steps:**
1. `k8s-agent`, `istio-agent`, `build-orchestrator` → Go + Sonnet 5.5.
2. `skill-suggester`, `dungeon-crawler-carl-agent` → Go + Haiku 5.5; remove their
   `context.compaction` (Go ignores it, kagent#2728) with a comment.
3. `providers.anthropic.model` → `claude-haiku-5-5`.
4. Rewrite the runbook symptom and the docs' model table; run `gen-techdocs.py`.
**Testing:**
- [ ] `k8s-agent`: deleting a throwaway ConfigMap raises an approval; reject keeps it, approve deletes it.
- [ ] `build-orchestrator` delegates to `istio-agent` and returns its answer.
- [ ] `gen-techdocs.py --check`, `validate-agent-docs.py` pass.

#### Task 0.4: Retire `anthropic-claude-sonnet-4-6`
**Files:** `base-apps/kagent/model-configs/anthropic-claude-sonnet-4-6.yaml` (delete), docs
**Steps:** confirm nothing references it; delete the manifest (its owned Secret is
garbage-collected); drop the "not in Vault" exception from docs/runbook. The key itself is
revoked in the Anthropic console by the owner.
**Testing:**
- [ ] Argo prunes it; the Secret is gone; `kubectl get modelconfigs` lists only the new configs and `embedding-model-config`.

### Phase 1: Prepare the 1.37 hop

#### Task 1.1: Amend SPEC (`/ck:spec`)
**Files:** `SPEC.md`
**Steps:** §T.89 accepts `v1.37.1+k3s1` from `latest`; §V.2 1.37 exception (hard breaks only);
refresh §R.38/§R.39 and correct §R.40; add §R rows (gateway-api-crd, Substrate prerequisites,
alpha11 facts); new §V for bundled k3s components that overlap Argo-owned resources; §T rows
for Phases 0–6; mark the spike plan superseded.
**Testing:**
- [ ] `/ck:check` reports no drift for the touched rows.

#### Task 1.2: Disable k3s `gateway-api-crd` through Ansible (§V.81)
**Files:** `ansible/roles/k3s_node/files/authn/config.yaml.d/20-disable-bundled.yaml` (new),
`ansible/playbooks/k3s-authn.yml`
**Steps:**
1. Drop-in `disable+: [gateway-api-crd]`; add it to the playbook's file list.
2. Parameterize `k3s_image` so validation runs at 1.36.4 and 1.37.1; add
   `--disable gateway-api-crd` to the validation container's command.
3. The install runs inside the hop window as step 0 (Task 2.2).
**Testing:**
- [ ] `k3s-authn.yml --check` passes at `v1.36.4-k3s1` (unknown component name ignored) and at
  `v1.37.1-k3s1`, where `kubectl get helmchart -A` is empty and no Gateway API CRD exists.
- [ ] `tests/ansible/` passes.

#### Task 1.3: Version pins and the removed-API scan
**Files:** `tests/admission-policies/conftest.py`, `.github/workflows/validate.yaml`,
`tests/k3s-upgrade/test_api_scan.py`, `scripts/hop-verify.sh`, `recovery/CLUSTER-RECOVERY.md`,
`scripts/gen-cluster-overview-dashboard.py` (+ regenerated dashboard),
`ansible/roles/k3s_node/files/authn/authn-config.yaml`, `docs/troubleshooting/{in-place-resize,kubectl-oidc}.md`
**Steps:**
1. Scan: add `v1.37.0` to `TARGETS`; pin pluto `v5.24.4` by digest; pass
   `--additional-versions` with a file declaring `scheduling.k8s.io/v1alpha2` removed in v1.37.0.
2. Bump the k3s image pins (`--disable gateway-api-crd` in the harness) and stale version text.
**Testing:**
- [ ] `tests/admission-policies/` passes on 1.37.1 (Docker); `tests/k3s-upgrade/` passes and
  the scan fails on a planted `scheduling.k8s.io/v1alpha2` object.
- [ ] Generators' `--check` pass; CI green.

#### Task 1.4: The 1.37 hop runbook
**Files:** `docs/plans/k3s-1.37-upgrade-plan.md` (new), `base-apps/system-upgrade-controller/runbook.md`
**Steps:** retarget to `v1.37.1+k3s1`; component table from this plan; step 0 (drop-in);
post-hop checks: `kubectl get helmchart -A` empty, Gateway API CRDs still v1.5.1 and
`istio-gateway-api` Synced, `container_memory_working_set_bytes{pod!=""}` returns series.
**Testing:**
- [ ] `scripts/hop-verify.sh gate` passes on the 1.36 cluster (baseline).

### Phase 2: The 1.37 hop (operator window)

kagent 0.10.3 runs through the hop unchanged.

#### Task 2.1: Before the window
**Steps:** fresh off-cluster artifacts (`k3s-backup.sh --mode cold`, `pg-backup.sh --all`,
`vault-backup.sh`); `hop-verify.sh gate`; `argo-sync-window.sh pause`.
**Testing:**
- [ ] `GATE PASSED`; artifact checksums verify.

#### Task 2.2: Control-plane hop (manual, §V.17)
**Steps:**
1. `ansible-playbook playbooks/k3s-authn.yml` (installs the drop-in; restarts k3s 1.36.4 with auto-rollback).
2. Installer with `INSTALL_K3S_VERSION=v1.37.1+k3s1`, re-supplying `INSTALL_K3S_EXEC`; diff the unit.
3. istio-cni step; `hop-verify.sh watch`; `gate`.
**Testing:**
- [ ] Node on `v1.37.1+k3s1` within §V.9; no HelmCharts; Gateway API CRDs v1.5.1 and Argo-owned; `GATE PASSED`.

#### Task 2.3: Worker hop
**Files:** `base-apps/system-upgrade-controller/plan-agent.yaml`
**Steps:** one-line PR `version: v1.37.1+k3s1`; istio-cni step per worker; `gate`; resume
auto-sync in §V.30 order.
**Testing:**
- [ ] All 3 nodes on 1.37.1; gate passes; apps Synced+Healthy or explained (§V.47); cAdvisor
  label check passes; close §T.89.

### Phase 3: kagent 1.0 groundwork (parallel to Phases 1–2)

#### Task 3.1: Admission policies for `v1alpha3`
**Files:** `scripts/gen-agent-capability-policy.py`, `base-apps/admission-policies/{agent-capability,agent-identity,kyverno-reports-rbac}.yaml`,
`tests/admission-policies/fixtures/crds/` (rendered alpha11 CRDs), `fixtures/agent-{capability,identity}/**`, `conftest.py`
**Steps:**
1. Render `kagent-crds` 1.0.0-alpha11 into the fixtures.
2. Match `api.kagent.dev` `agenttemplates`: identity — every `tools[].mcp` binding lists its
   tools; capability rule 5 — a write/admin tool may only appear in a binding with
   `requireApproval: true`; delegation — `subAgent.templateRef` targets a class ≤ the parent's
   (`paramKind` AgentTemplate). The `capability.homelab/class` label lives on the AgentTemplate.
3. Ship in the cutover sync, after the CRDs (Task 5.4; §V.78 param-informer lag applies).
**Testing:**
- [ ] Docker harness: good fixtures admitted; denied — write tool in a non-approval binding,
  binding without tools, read agent delegating to admin; `test_api_server_did_not_panic`; §V.76 labels.

#### Task 3.2: CI validators fail closed
**Files:** `scripts/validate-agent-{identity,capability}.py`, `tests/agent-{identity,capability}/`
(incl. `test_kagent_tools_rbac.py`), `base-apps/cluster-rbac/kagent-tools.yaml`
**Steps:** parse AgentTemplate/Agent; fail on unrecognized kagent kinds/versions and on zero
AgentTemplates once the repo is on 1.0; the tools RBAC test renders the chart with our values and
asserts no chart ClusterRole binds the `kagent-tools` SA.
**Testing:**
- [ ] Suites pass; planted v1alpha2 fixtures (post-cutover mode) and an empty directory fail.

#### Task 3.3: Translate the agents
**Files:** `base-apps/kagent/` — `model-configs/*.yaml` (v1alpha3), `harnesses/*.yaml`,
`agent-templates/*.yaml`, `agents/*.yaml`, `agent-docs-mcp-remote.yaml`, `backstage-catalog-mcp.yaml`,
`agent-docs-mcp.yaml` (kmcp 0.4.0 schema)
**Steps:**
1. ModelConfigs: `anthropic: {maxTokens: 16000, promptCaching: true, cacheTTL: "5m"}`, `stream: true`.
2. Harnesses: kagent runtime, `golang-adk` image by digest, memory → `embedding-model-config`,
   compaction for chat agents, snapshot policy → rustfs.
3. `homelab-knowledge` binds agent-docs, backstage-catalog and the 8 read tools
   (`requireApproval: false`); no sub-agent; prompt says it reads live state itself.
4. `k8s-agent` / `istio-agent`: two bindings (today's `requireApproval` lists → the `true` binding).
5. `build-orchestrator`: `subAgent.templateRef` → `k8s-agent`, `istio-agent` (hand-off).
6. Vendor `kagent-builtin-prompts` if the 1.0 chart no longer ships it; move `terasky.*`
   annotations onto the Agent; keep the dropped `a2aConfig.skills` text in docs.
**Testing:**
- [ ] Throwaway k3s 1.37.1: alpha11 CRDs + all translated manifests pass server-side dry-run.
- [ ] Task 3.1 policies admit all 7 agents; validators pass.

#### Task 3.4: Databases
**Files:** `base-apps/postgresql/init-{kagent-v1,substrate}-db.yaml` (new Jobs), ExternalSecrets
and SecretStores in `kagent` and `ate-system`, `scripts/provision-kagent-v1-vault.sh` (new)
**Steps:** `kagent_v1` (role `kagent_owner` + login user, schema `kagent`, schema `extensions`
with `vector`); `substrate` (NOLOGIN owner/readwrite roles, schema `substrate`, login users,
password auth). The 0.10 `kagent` DB is untouched.
**Testing:**
- [ ] Jobs complete; `\dn` / `\dx` as expected; ExternalSecrets `SecretSynced`; a throwaway psql pod connects with each string.

#### Task 3.5: Substrate CA pools and API authentication
**Files:** `scripts/provision-substrate-cas.sh` (new), `base-apps/substrate/` (SecretStores,
ExternalSecrets, `ate-api-authentication` ConfigMap, docs, runbook, catalog-info)
**Steps:** generate the 7 pools in `substrate_init.go`'s formats, store in Vault, deliver via
ESO; ConfigMap → the k3s service-account issuer; CA rotation runbook + a 30-day expiry alert.
**Testing:**
- [ ] Output keys/types match `substrate_init.go`; ExternalSecrets sync; the expiry rule evaluates.

#### Task 3.6: Port the agent audit pipeline
**Files:** `scripts/{agent-audit,agent-audit-risk,mine-eval-corpus,gen-agent-audit-cronjob}.py`,
`base-apps/postgresql/{agent-audit-cronjob,init-kagent-audit-role,init-agent-audit-web-role,external-secrets-kagent-audit}.yaml`,
`base-apps/logging/grafana-alerting.yaml`, `tests/{agent-audit,eval-corpus}/`, `arigsela/agent-audit-web`
**Steps:**
1. From the alpha11 source: the `kagent` schema's tables, how the Go ADK records tool calls and
   approvals, the agent id format.
2. `--source v010|v1` adapter (archive keeps `public.event/session`).
3. Audit-role grants on schema `kagent` in `kagent_v1` (`FOR ROLE` default privileges); keep the archive grants.
4. Ungated = a write-class tool call with no approval in the same session.
5. `noDataState: Alerting` on `agent-ungated-tool` and `agent-audit-risk-high`.
6. Same adapter in agent-audit-web.
**Testing:**
- [ ] Unit tests on v1-shaped fixtures: approved delete = gated, unapproved write = flagged; CronJob `--check` passes. Live check in Task 5.5.

#### Task 3.7: "ask hk" and A2A clients
**Files:** `scripts/capture-eval-answers.py`, `scripts/ask-hk` (new), `base-apps/kagent/httproute-mcp.yaml`
(`/agents/kagent/` → `kagent-controller:8083`), `tests/eval-corpus/test_capture_eval_answers.py`, `~/.claude/CLAUDE.md`
**Steps:** endpoint → `/agents/kagent/<agent>` with the A2A v1 method names;
`ask-hk "<q>" [--context <id>]` prints the answer and the session id; the route inherits the
host's IP allow-list.
**Testing:**
- [ ] Unit tests on recorded responses; after cutover a `--context` follow-up keeps context; a non-allow-listed request is denied (§V.53).

#### Task 3.8: Backstage
**Files:** `base-apps/backstage/rbac.yaml`, `kyverno-reports-rbac.yaml`; in `~/git/backstage`:
`app-config*.yaml`, `EntityPage.tsx`, ingestor config, `kagent-suggest`, `kagent-shared/resolver.ts`, the v1alpha2 scaffolder actions
**Steps:** RBAC for `api.kagent.dev`; card and custom resources → v1alpha3 Agent/AgentTemplate;
ingest `agents.api.kagent.dev` directly; skill-suggester over A2A; drop the removed annotation contract.
**Testing:**
- [ ] Portal tests pass; after cutover 7 agent entities exist, the card renders, a suggestion comes back.

#### Task 3.9: Argo apps and values for Substrate and kagent 1.0
**Files:** `base-apps/{substrate-crds,substrate-podcert,substrate}.yaml` (new),
`base-apps/{kagent-crds,kagent,kagent-secrets}.yaml`, `renovate.json5`
**Steps:**
1. Substrate: three OCI charts at 0.5.0-alpha2, release `substrate` in `ate-system`, sync waves,
   external Postgres, rustfs on a node-pinned PVC with `Prune=false` (§V.39), non-default
   credentials if supported.
2. kagent values: `database.postgres.connectionStringSecretRef`, `vectorEnabled`,
   `controller.substrate.*`, `substrateWorkerPool`, `credentialProvider.namespacePolicies`,
   `sessionIdleTTL`, `kagent-tools.rbac.create: false`, `grafana-mcp.enabled: false`,
   `otel.traces`/`otel.logs` → Coroot, images by digest.
3. Drop the SA `tracking-id` `ignoreDifferences`. Renovate: kagent + Substrate in lockstep, alpha bumps labelled.
**Testing:**
- [ ] `helm template --kube-version 1.37.1` renders all; every image digest-pinned; no `kagent-tools` ClusterRole; server-side dry-run passes.

#### Task 3.10: Operational fit
**Files:** `base-apps/logging/grafana-alerting.yaml` (Falco exclusions), hygiene audit
policies, `ansible/playbooks/patch.yml`, `ansible/inventory/group_vars/all.yml`
**Steps:** Falco excludes `ate-system` / `podcertificate-controller-system`; hygiene audits
exclude them with the reason; after any node reboot delete that node's Substrate worker pods
(#1837); preflight asserting identical CPU flags (#1657); keep `drain_timeout: 300` and document
that in-flight turns may crash.
**Testing:**
- [ ] Ansible suite passes; `patch.yml --check` shows the new steps.

### Phase 4: Substrate on prod (go/no-go)

#### Task 4.1: Install Substrate
**Steps:** confirm 8085/9090 are free on every node (read-only `ss -ltn`); merge the three apps.
**Testing:**
- [ ] `ate-system` and `podcertificate-controller-system` Ready; atelet on 3 nodes; a PodCertificateRequest issues; ClusterTrustBundles exist.

#### Task 4.2: gVisor smoke test per node
**Files:** `base-apps/substrate/smoke/` (temporary)
**Steps:** WorkerPool + ActorTemplate + Actor pinned to each node in turn; request → suspend → resume.
**Testing:**
- [ ] Resumes with state on all 3 nodes; no SIGILL in `dmesg`; snapshots in rustfs.
- [ ] **No-go:** revert Phase 4; kagent stays on 0.10.3 with the Phase 0 models.

### Phase 5: Cutover (agent outage window)

One PR per step (§V.8).

#### Task 5.1: Freeze and archive
**Steps:** final 0.10 audit export; `pg_dump` `kagent` and `homelab_agent` off-cluster with
checksums (one-off: `pg-backup.sh` covers only CNPG); `ALTER DATABASE kagent SET default_transaction_read_only = on`.
**Testing:**
- [ ] `pg_restore --list` reads both dumps; checksums stored.

#### Task 5.2: Retire `homelab-agent`
**Steps:** remove its 8 `kagent` and 4 `postgresql` files after the §V.72 grep; remove its ECR
repo from `ecr.tf` via Atlantis (apply before merge, §V.73); drop the DB and role; remove its
Vault keys/policies/roles and the Backstage MCP token copy. Its Anthropic key is revoked by the owner.
**Testing:**
- [ ] Clean prune; `atlantis/apply` green before merge; nothing left in Vault or the cluster.

#### Task 5.3: Remove kagent 0.10
**Steps:** PR A — v1alpha2 Agents/ModelConfigs/RemoteMCPServers + v1alpha2 policies (controller
still running, so finalizers clear); PR B — `base-apps/kagent.yaml`; PR C — 0.10 `kagent-crds`.
**Testing:**
- [ ] After each: no stuck finalizers; finally `kubectl get crd | grep kagent.dev` empty, no kagent pods.

#### Task 5.4: Install kagent 1.0
**Steps:** `kagent-crds` 1.0.0-alpha11 → `kagent` 1.0.0-alpha11 → Task 3.3 manifests, v1alpha3
policies (later sync wave) and the `/agents/` route.
**Testing:**
- [ ] Migrations complete in schema `kagent`; 7 Agents Ready; RemoteMCPServers show tools; UI and `/mcp` respond; a bad fixture is denied on server-side dry-run.

#### Task 5.5: Live acceptance
**Steps:** per agent a multi-step tool question, a long answer and a multi-turn follow-up
(transcripts in `docs/plans/kagent-1-0-acceptance.md`); approval gates on `k8s-agent` and
`istio-agent`; `homelab-knowledge` docs walk + live state + memory; `build-orchestrator`
hand-off; prompt caching (`cache_read_input_tokens` > 0); each audit CronJob once; `ask-hk`,
Backstage, eval capture; `patch.yml --limit k3s-worker-02` then resume a session; Sonnet 5.5
cyber-classifier refusals noted.
**Testing:**
- [ ] All pass. **Rollback** otherwise: revert 5.4, then 5.3, archive DB back to read-write — 0.10.3 returns with its database intact.

### Phase 6: Cleanup and documentation

#### Task 6.1: Docs
**Files:** `base-apps/kagent/{docs,runbook}.md`, `base-apps/substrate/*`, `scripts/agent-docs-scope.txt`,
postgresql/ollama/backstage docs, `index.md`, `base-apps/index.md`, `README.md`,
`recovery/CLUSTER-RECOVERY.md`, `CLAUDE.md`, `docs/adp-*.md`, `docs/platform/*`, `docs/index.md`;
mark the spike plan superseded; note in the Jev tool-guard design that its approval assumptions changed.
**Testing:**
- [ ] `gen-okf.py --check`, `gen-techdocs.py --check`, `validate-agent-docs.py`, `validate-catalog-refs.py` pass.

#### Task 6.2: Dead config
**Files:** the orphaned `mcp-basic-auth` trio, `templates/agent-identity/` (v1alpha3, ESO `v1`),
`scripts/hop-verify.sh` (agent probe → `api.kagent.dev`; empty answer fails, §V.74)
**Testing:**
- [ ] `tests/k3s-upgrade/test_hop_scripts.py` passes; the template validates.

#### Task 6.3: SPEC and memory
**Steps:** close the §T rows, add §B entries for anything found, update memories.
**Testing:**
- [ ] `/ck:check` clean.

## End-to-End Testing

Done when, with output captured in `docs/plans/kagent-1-0-acceptance.md`: `hop-verify.sh gate`
passes on 1.37.1; every Task 5.5 check passes; CI is green on main including the fail-closed
validators; a planted bad agent manifest is denied by admission and rejected by CI; an
unapproved write tool call shows up in the next audit run.

## Risks and Mitigations

- **No preflight: gVisor meets this CPU on prod first.** Substrate is installed and smoke-tested before kagent is touched (Phase 4 go/no-go); on no-go kagent stays on 0.10.3.
- **Pre-stable k3s on the sole control plane.** Cold backups with drill-proven restore (§V.1, §V.16); rollback = fs restore + reinstall 1.36.4.
- **Alpha churn.** Pin by digest; every bump is its own clean-DB migration, so sessions and memory reset until beta.
- **0.10 memory is not migrated** (key format changed); it stays readable in the archive.
- **Go adapter quality** (#3026, #2895, thinking dropped). Phase 0 surfaces them early; fallback is the Python ADK 2.11 image via a Harness command override.
- **Substrate on reboots** (#1837, #1761, #1657, single-replica components). Post-reboot worker-pod cleanup, CPU-flag preflight; agents briefly unavailable during patching.
- **rustfs** (default credentials, 1 GiB, node-pinned). ClusterIP-only, credentials overridden if the chart allows; S3 after substrate#64.
- **No backups for the pgvector Postgres.** Archive dumped at cutover; adding it to the backup scripts is a follow-on.
- **CA expiry at 365 days.** Expiry alert + rotation runbook.
- **Unauthenticated A2A; trusted-proxy OIDC doesn't verify tokens.** Both hosts stay behind the IP allow-list; OIDC mode not enabled; denial proven (§V.53).
- **Claude harness and network-sourced skills** are unusable here; not used.
- **Gateway API CRD takeover; cAdvisor label loss.** The drop-in, plus post-hop checks.
- **Guardrails failing open.** Fail-closed validators and the v1alpha3 policies ship in the same cutover sync.
