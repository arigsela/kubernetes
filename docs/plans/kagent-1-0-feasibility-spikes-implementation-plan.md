# kagent 1.0 Feasibility Spikes Implementation Plan

**Status:** Approved — ready to execute
**Date:** 2026-09-26
**Owner:** Ari Sela
**Prior research:** [docs/research/2026-07-16-agent-substrate-in-kagent.md](../research/2026-07-16-agent-substrate-in-kagent.md)
**Note (2026-09-30):** `docs/research/` is **gitignored** by design (`.gitignore`: security-posture notes stay off this public repo). The prior research above and every `docs/research/kagent-1.0-spike/` file this plan creates are local-only, so "committed" / "reproducible from git" below cannot hold unless the spike files move elsewhere or get an explicit `.gitignore` exception.
**Upstream:** [kagent releases](https://github.com/kagent-dev/kagent/releases) (v1.0.0-alpha4, 2026-09-25) · upgrade guide `docs-site/content/kagent/1.x/operations/upgrade-from-0x.md` in [kagent-dev/website](https://github.com/kagent-dev/website)

## Overview

kagent 1.0 is a clean-break rewrite of the platform we run at 0.10.2. There is no in-place upgrade. The Agent CRD is gone, a 0.10 database is refused, agents run as gVisor Actors on Agent Substrate, and 1.0 needs Kubernetes 1.37+ with new feature gates. It is also still alpha (alpha4, no beta/GA date), while 0.10.x remains supported on `release/v0.10.x`.

This plan does **not** migrate anything. It runs **time-boxed feasibility spikes** on a throwaway k3s v1.37 VM on the same hypervisor and CPU as production. It ends in a go/no-go report and, if the answer is go, a full migration plan. The production cluster is not touched.

### Decisions already made for the eventual migration (recorded, not executed here)

| Decision | Choice | Why |
|---|---|---|
| Where 1.0 runs | **A second small k3s cluster**, then cut DNS/MCP over | Upstream says 0.10 and 1.0 cannot share a cluster (shared `kagent.dev` CRDs, no conversion). Side-by-side gives zero downtime and a trivial rollback. |
| homelab-agent (LangGraph BYO) | **Retire it** at migration time; homelab-knowledge stays the single knowledge agent | The 1.0 BYO contract (gRPC A2A on :80, gRPC TaskStore, no checkpoint API) is not supported by `kagent-langgraph` yet, and the agent duplicates homelab-knowledge. |
| History | **Keep the 0.10 database as a read-only archive.** 1.0 gets a fresh database. | Sessions and events are what `scripts/agent-audit.py` (plus its CronJob) and `scripts/mine-eval-corpus.py` read. Archiving keeps historical audit and eval mining working. Memory rows get a best-effort copy. |

## Success Criteria

- [ ] Every go/no-go gate (G1–G6) answered with evidence: commands run, output captured.
- [ ] A translation table maps all 7 Declarative agents to `AgentTemplate` + `Harness`, with each semantic gap and a proposed fix. *(8 when written; `qwen-test` was retired 2026-09-27.)*
- [ ] A list of the admission-policy exemptions Agent Substrate would need.
- [ ] Zero changes to the production cluster, and the spike can be reproduced from files in `docs/research/kagent-1.0-spike/`.
- [x] Side answer: does the **upstream Node 24 kagent UI** crash on our CPU? **Answered 2026-09-26 (`ef61222`):** upstream `ui:0.10.2` ran clean on all three nodes and the Node 20 UI fork was retired.

## Go/No-Go Gates

| Gate | Question | Tested in |
|---|---|---|
| **G1** | Does every required image run on the Xeon E5-2670 (or have a rebuild path)? | Task 2.1 |
| **G2** | Do gVisor Actors run, suspend and resume on this CPU? | Tasks 2.2, 3.2 |
| **G3** | Does k3s 1.37 with the Substrate feature gates work? | Task 1.2 |
| **G4** | Can per-tool human-approval gates be reproduced with per-binding `requireApproval`? | Task 4.2 |
| **G5** | Are the new delegation semantics acceptable, or do we have a concrete redesign? | Task 4.3 |
| **G6** (external) | Has upstream 1.0 reached beta/GA? | Task 5.1 |

## Research Findings

### What 1.0 changes (from the 2026-09-26 research pass against v1.0.0-alpha4)

**API and CRDs:**
- Only `kagent.dev/v1alpha3` is served: AgentTemplate, Harness, ModelConfig, ModelProviderConfig, RemoteMCPServer.
- The Agent, ToolServer, Memory and SandboxAgent CRDs are gone, and there is no conversion webhook.
- Field moves:
  - `systemMessage` → `systemPrompt` / `systemPromptFrom` (ConfigMap).
  - `modelConfig` becomes an object ref.
  - `memory` and `context.compaction` move to `Harness.spec.kagent`.
  - `stream` is dropped (always on).
  - `a2aConfig.skills` is dropped.
  - `runtime: python` becomes a Harness `workload.image` plus the command `kagent-adk static …`.
- MCP bindings accept only same-namespace `RemoteMCPServer`, so kmcp `MCPServer`s need a wrapper.
- `requireApproval` is one **bool per binding** instead of a per-tool list.
- **Agent-as-tool (`isolation: Shared`) hands the conversation off to the child.** The parent does not get the child's answer back, and `Dedicated` is rejected.

**Runtime:**
- Each conversation is an AgentInstance (in Postgres, over gRPC).
- The AgentInstance runs as a Substrate Actor in gVisor (systrap mode by default; no KVM), snapshotted to S3 and suspended after each turn.
- An agentgateway sidecar runs in the Substrate router and egress paths. It injects API keys at egress, which requires a CA pool.

**Cluster requirements:**
- Kubernetes 1.37+ with `certificates.k8s.io/v1beta1` and the ClusterTrustBundle, ClusterTrustBundleProjection and PodCertificateRequest gates.
- Kernel: cgroup v2, nftables and user namespaces.
- hostPorts 8085 and 9090.
- S3 for snapshots (rustfs is bundled).
- A **privileged** atelet DaemonSet, and worker pods running as UID 0 with SYS_ADMIN and seccomp/AppArmor Unconfined.
- Substrate has no k3s install profile ([agent-substrate/substrate#1782](https://github.com/agent-substrate/substrate/issues/1782)).

**Data:**
- A 0.10 DB is refused ("unsupported migration table. Use a new PostgreSQL database").
- Sessions, events, tasks, feedback and checkpoints are not carried over.
- The `memory` table keeps `vector(768)`, but the agent key becomes `<ns>__NS__<template>_<harness>`.

**Endpoints:**
- Per-agent A2A Services are gone; A2A goes through the controller at `/agents/{instance-id}` (A2A v1 method names).
- The `/mcp` tools become `list_agent_instances` / `invoke_agent_instance` (by instance UUID), plus checkpoint and fork tools. There is no create tool.
- The UI moved to Vite plus gRPC-Web.

**Open upstream issues that affect us:**

| Issue | Effect |
|---|---|
| kagent #2851 | Zero-argument tool calls are broken after resume on OpenAI-shaped adapters, which covers our llama.cpp ModelConfig |
| kagent #2897 | AgentInstances are never garbage-collected |
| kagent #2688 | MCP binding policy is incomplete |
| substrate #1837 | An Actor becomes unreachable after a node reboot |
| atelet | Registry auth looks GCP/anonymous-only, so private ECR pulls probably fail (unverified) |

### Hardware facts (measured 2026-09-26)

All three nodes are **Intel Xeon E5-2670 (Sandy Bridge) VMs**:
- They **have every x86-64-v2 feature** (SSE3, SSSE3, SSE4.1, SSE4.2, POPCNT, CX16, LAHF) and AVX.
- They **lack AVX2**, so the real ceiling is x86-64-v3.
- Coroot's ClickHouse (needs SSE4.2) already runs on worker-01, which corroborates this.

The repo's "nodes lack x86-64-v2" premise (2026-05-09 UI-fork design) was inferred, not measured. The Node 24 UI SIGILL was real, but its cause is unexplained, so Task 2.1 re-tests it. (Upstream `ui:0.10.2`, also Node 24, has since run clean on all three production nodes: `ef61222`, 2026-09-26.)

What this implies:
- Chainguard/Wolfi images (agentgateway, the 1.0 UI) and NumPy ≥ 2.4 should run.
- AVX2-only binaries will not: the Claude Code harness, UBI10 and CentOS Stream 10 bases.

### Relevant files (repo touch points for the eventual migration; unchanged by this plan)

| Path | Why it matters |
|---|---|
| `base-apps/kagent.yaml`, `base-apps/kagent-crds.yaml`, `base-apps/kagent-secrets.yaml` | Argo Applications for the 0.10.2 install |
| `base-apps/kagent/agents/*.yaml`, `base-apps/kagent/build-orchestrator.yaml` | The 7 Declarative agents plus the BYO homelab-agent being retired |
| `base-apps/kagent/model-configs/`, `embedding-model-config.yaml` | ModelConfigs: only the apiVersion changes in 1.0 |
| `base-apps/kagent/agent-docs-mcp*.yaml`, `backstage-catalog-mcp.yaml` | MCPServer/RemoteMCPServer; kmcp servers need RemoteMCPServer wrappers |
| `base-apps/admission-policies/agent-identity.yaml`, `agent-capability.yaml`, plus the workload-hygiene audits | CEL written against the `Agent` spec; hygiene audits would flag Substrate's privileged pods |
| `base-apps/admission-policies/kyverno-reports-rbac.yaml`, `base-apps/backstage/rbac.yaml` | RBAC over `kagent.dev` resources (Kyverno reports controller; Backstage TeraSky ingester) |
| `scripts/validate-agent-{identity,capability}.py`, `scripts/gen-agent-capability-policy.py`, `scripts/validate-catalog-refs.py` | Parse `v1alpha2` Agent specs |
| `scripts/agent-audit.py`, `scripts/gen-agent-audit-cronjob.py`, `base-apps/postgresql/agent-audit-cronjob.yaml` | Read the kagent `event`/`session` tables directly |
| `scripts/mine-eval-corpus.py`, `scripts/score-eval.py` | Evaluation E1/E2 read conversation history and invoke agents |
| `tests/admission-policies/fixtures/**` (incl. `crds/agents.yaml`), `tests/agent-capability/` | Fixtures pinned to the `v1alpha2` Agent CRD |
| `~/.claude/CLAUDE.md` "ask hk" shortcut | Uses `invoke_agent`, which 1.0 replaces with instance-based `invoke_agent_instance` |

### Existing patterns followed
- Pin every upstream version and record it, as with the kagent chart and UI image today.
- Keep exploratory work in `docs/research/`, outside `base-apps/`, so the master-app never syncs it (same as the 2026-07-16 Substrate research).
- Create secrets only for the spike and destroy them at the end. Production secrets and Vault paths stay out of it.

### Dependencies
- Hypervisor capacity for 1 VM (a second one is optional) using the same CPU type as the k3s nodes.
- Outbound access from the spike VM to ghcr.io, `gs://gvisor` (runsc), and api.anthropic.com.
- A dedicated low-budget Anthropic API key for the spike.

## Architecture Decisions

### Decision 1: Where the spikes run
**Options considered:**
1. **Laptop / kind** — fast, but proves nothing about our CPU (Apple Silicon, or a different x86 CPU).
2. **Carve a worker out of prod** — same hardware, but prod drops to 2 nodes and risks spillover.
3. **New VM on the same hypervisor** — same CPU with no prod impact beyond shared hypervisor capacity.

**Chosen:** Option 3. Results only transfer if the CPU flags match, and it keeps prod untouched.

### Decision 2: How the spike cluster is managed
**Options considered:**
1. **Argo-managed Applications** — consistent with GitOps, but risks the master-app picking up alpha manifests, and adds noise.
2. **Unmanaged throwaway cluster with versioned install files** — reproducible without coupling to prod GitOps.

**Chosen:** Option 2. Manifests and scripts live in `docs/research/kagent-1.0-spike/`.

### Decision 3: What to test
**Options considered:**
1. **Upstream examples** — cheap, but they don't exercise our patterns.
2. **Real translated agents** — test the approval gates, delegation, memory and MCP patterns we actually depend on.

**Chosen:** Option 2, using `k8s-reader` (read MCP), `k8s-agent` (approval gates) and a `homelab-knowledge`-style agent (delegation). These are where 1.0's semantics differ for us.

## Implementation

### Phase 0: Desk Prep (no infrastructure)
Turn the research into concrete, checkable inputs before spending VM time.

#### Task 0.1: Agent translation table
**Files:** `docs/research/kagent-1.0-spike/translation.md` (new)
**Steps:**
1. For each Declarative agent (`homelab-knowledge`, `k8s-agent`, `k8s-reader`, `istio-agent`, `skill-suggester`, `dungeon-crawler-carl-agent`, `qwen-test`, `build-orchestrator`), map every field to its `AgentTemplate` / `Harness` target using the upstream 1.x field table.
2. Mark each field as moved, changed, or dropped. Flag the semantic gaps: delegation handoff, per-binding approval, kmcp wrapping, dropped `a2aConfig.skills`, and memory/compaction moving to per-Harness.
3. Link each gap to the Phase 4 task that tests it.

**Testing:**
- [ ] Every field present in the agent manifests appears in the table, with a 1.0 target or "dropped".
- [ ] Every flagged gap references a Phase 4 task.

#### Task 0.2: Version and image manifest
**Files:** `docs/research/kagent-1.0-spike/versions.md` (new)
**Steps:**
1. Record the `v1.0.0-alpha4` chart versions (`kagent`, `kagent-crds`) and render them with `helm template`.
2. List every rendered image with its digest and runtime base image.
3. Record the Substrate version pinned in alpha4's `go/go.mod`, the rustfs and agentgateway sidecar images, and the exact apiserver/kubelet feature gates and `--runtime-config` flags from the Substrate chart README.

**Testing:**
- [ ] `helm template` succeeds for both charts at alpha4.
- [ ] The image list in `versions.md` matches the rendered manifests (a script diff shows no missing images).

### Phase 1: Spike Environment (G3)

#### Task 1.1: Provision the spike VM
**Files:** `docs/research/kagent-1.0-spike/README.md` (new; hypervisor settings, sizing, how to recreate)
**Steps:**
1. Create one VM on the same hypervisor: about 6 vCPU, 12 GB RAM, 60 GB disk, Ubuntu 24.04.
2. Set the CPU type to exactly match the k3s node VMs.
3. Add a second, identical VM only when running the cross-node restore test (Task 3.2).

**Testing:**
- [ ] `grep -m1 "model name" /proc/cpuinfo` shows `Xeon E5-2670`.
- [ ] Diff the `flags` line against a prod node's (read via a pod on `k3s-worker-01`): identical, v2 features present, `avx2` absent.

#### Task 1.2: Install k3s v1.37 with the Substrate prerequisites
**Files:** `docs/research/kagent-1.0-spike/install-k3s.sh` (new)
**Steps:**
1. Install `v1.37.0+k3s1`, passing the apiserver and kubelet feature gates and `--runtime-config=certificates.k8s.io/v1beta1=true` from Task 0.2.
2. Verify cgroup v2, nftables and user namespaces, and that hostPorts 8085 and 9090 are free.

**Testing:**
- [ ] `kubectl version` reports v1.37.x.
- [ ] `kubectl api-resources | grep -E "clustertrustbundles|podcertificaterequests"` returns both.
- [ ] `stat -fc %T /sys/fs/cgroup` returns `cgroup2fs`, `unshare -U true` exits 0, and `nft list ruleset` works.

### Phase 2: CPU and Image Compatibility (G1, G2)

#### Task 2.1: Image smoke matrix
**Files:** `docs/research/kagent-1.0-spike/results.md` (new)
**Steps:**
1. On the spike VM, pull each image by digest and run a meaningful command:
   - **Upstream kagent UI `0.10.2`** (Node 24 on Wolfi): start it and send an HTTP request, since it crashed per request in May.
   - **kagent 1.0 UI:** start nginx and fetch `/`.
   - **agentgateway sidecar:** `--version`, then start it with a minimal config.
   - **kagent-adk 1.0 image:** `python -c "import numpy, google.adk; print(numpy.__version__)"`.
   - **Controller, Substrate components, rustfs:** start each.
2. For any failure, capture `dmesg`/logs and identify the faulting instruction or library.

**Testing:**
- [ ] `results.md` has a pass/fail/SIGILL row with captured output for every image in `versions.md`.
- [ ] Upstream 0.10.2 UI verdict recorded. *(Already answered on production nodes: the Node 20 UI fork was retired 2026-09-26 in `ef61222`; no separate PR needed.)*

#### Task 2.2: gVisor on this CPU
**Steps:**
1. Download the runsc build atelet uses, pinned by date/sha256.
2. Run `runsc do true`, then a Python workload under runsc (systrap platform).

**Testing:**
- [ ] `runsc do true` exits 0.
- [ ] `runsc do python3 -c "import hashlib; print(hashlib.sha256(b'x').hexdigest())"` succeeds, with no SIGILL in `dmesg`.

### Phase 3: Substrate and kagent 1.0 Bring-up (G2)

#### Task 3.1: Install Agent Substrate and rustfs
**Files:** `docs/research/kagent-1.0-spike/values-substrate.yaml` (new)
**Steps:**
1. Install the Substrate CRDs and chart at the version from Task 0.2, with the bundled rustfs.

**Testing:**
- [ ] All pods in `ate-system` are Ready, and the atelet DaemonSet is Running on the node.

#### Task 3.2: Actor lifecycle
**Steps:**
1. Create a sample WorkerPool, ActorTemplate and Actor. Drive a request, let it suspend, then resume it.
2. (Optional, second VM) Snapshot on VM-A and restore on VM-B.

**Testing:**
- [ ] The Actor resumes with the state it had before suspending.
- [ ] (Optional) A cross-node restore succeeds. This exercises gVisor's CPU feature-set subset check.

#### Task 3.3: Install kagent 1.0.0-alpha4
**Files:** `docs/research/kagent-1.0-spike/values-kagent.yaml` (new)
**Steps:**
1. Deploy a fresh Postgres with pgvector, and Ollama with `nomic-embed-text`, inside the spike VM.
2. Create the spike-only Anthropic key Secret and an Anthropic `v1alpha3` ModelConfig (`claude-haiku-4-5`).
3. Install `kagent-crds` and `kagent` at alpha4 with Substrate enabled.

**Testing:**
- [ ] The controller logs show migrations complete, and the UI loads in a browser.
- [ ] `kubectl get agenttemplates,harnesses,modelconfigs,remotemcpservers` succeeds.

#### Task 3.4: Admission-policy compatibility
**Steps:**
1. Apply `base-apps/admission-policies/*` to the spike cluster in audit mode (native ValidatingAdmissionPolicy / MutatingAdmissionPolicy).
2. Exercise Substrate and kagent (Tasks 3.2–4.x), then collect the audit annotations and events.

**Testing:**
- [ ] `results.md` lists every violation by Substrate/kagent pods, with a proposed exemption per policy (e.g. `ate-system` namespace, atelet, worker pods).
- [ ] Record which policies no longer match anything (`agent-identity`, `agent-capability` target the removed `Agent` kind).

### Phase 4: Agent Semantics (G4, G5)

#### Task 4.1: Translate `k8s-reader`
**Files:** `docs/research/kagent-1.0-spike/agents/k8s-reader.yaml` (new)
**Steps:**
1. Translate using Task 0.1: an AgentTemplate plus a Harness, and a RemoteMCPServer wrapping kagent-tools.

**Testing:**
- [ ] "How many pods are in kube-system and are any not Running?" returns correct live data via tool calls.

#### Task 4.2: Approval gates (`k8s-agent`)
**Files:** `docs/research/kagent-1.0-spike/agents/k8s-agent.yaml` (new)
**Steps:**
1. Split the tools into two bindings on the same server: reads with `requireApproval: false`, and the destructive tools (`k8s_delete_resource`, `k8s_execute_command`, …) with `requireApproval: true`.

**Testing:**
- [ ] Asking it to delete a throwaway ConfigMap triggers an approval prompt. Rejecting it leaves the ConfigMap in place, and approving it deletes it.
- [ ] A read-only request completes with no approval prompt.
- [ ] If two bindings to one server aren't allowed, record it. G4 then fails unless a workaround exists.

#### Task 4.3: Delegation semantics
**Files:** `docs/research/kagent-1.0-spike/agents/delegation.yaml` (new)
**Steps:**
1. Build a `homelab-knowledge`-style parent with an agent-as-tool binding (`isolation: Shared`) to the `k8s-reader` template.
2. Ask a question that needs the parent to combine its own tool result with the child's answer.

**Testing:**
- [ ] Record what actually happens: whether the child's answer comes back to the parent, or the conversation is handed over.
- [ ] Pick a redesign for `build-orchestrator` and `homelab-knowledge`, e.g. expose the sub-agent through an MCP wrapper, or merge the tools into one agent. Test the chosen alternative once.

#### Task 4.4: Memory
**Steps:**
1. Enable Harness memory with the Ollama embedding ModelConfig.
2. (Optional) Copy a handful of rows from a read-only `pg_dump` of the prod `memory` table into the spike DB, rewriting the agent key to `<ns>__NS__<template>_<harness>`.

**Testing:**
- [ ] A fact stated in session 1 is recalled in a new session 2.
- [ ] (Optional) The copied prod memory rows are recalled.

#### Task 4.5: Models
**Steps:**
1. Run a multi-step tool question on `claude-haiku-4-5`.
2. Repeat on `claude-sonnet-5`.

**Testing:**
- [ ] Haiku: completes with correct tool use.
- [ ] Sonnet 5: record pass/fail. It fails on 0.10.2 because google-adk 1.38 can't send thinking blocks back (`NotImplementedError … thought=True`). A pass means homelab-knowledge could move to Sonnet 5 on 1.0.

#### Task 4.6: Access paths and the GitOps gap
**Steps:**
1. Point a Claude Code MCP config at the spike controller's `/mcp`, and call `list_agent_instances` / `invoke_agent_instance`.
2. Call an instance over A2A at `/agents/{instance-id}`.
3. Find out how instances are created (UI, CLI, gRPC) and whether any declarative path exists.
4. Desk check: does the Backstage TeraSky kagent plugin support `v1alpha3` AgentTemplate?

**Testing:**
- [ ] A working `invoke_agent_instance` call from Claude Code, with the replacement "ask hk" flow written up.
- [ ] A documented answer on whether instances can be declared from git, with a workaround if not.
- [ ] A TeraSky plugin compatibility verdict with a source link.

### Phase 5: Decide

#### Task 5.1: Findings report
**Files:** `docs/research/YYYY-MM-DD-kagent-1.0-spike-results.md` (new)
**Steps:**
1. Summarize G1–G6 with evidence links into `results.md` and the agent files.
2. Check G6: the upstream release status at report time.
3. If go: an effort estimate and an outline of the full migration plan (second cluster, homelab-agent retirement, archive DB plus repointing `agent-audit` / `mine-eval-corpus`, policy/script/fixture rewrites, "ask hk" update). If no-go: the blocking gates and what would unblock them.

**Testing:**
- [ ] Each gate is marked pass, fail or blocked, and backed by captured output.
- [ ] Re-read the report against this plan's Success Criteria; every item is checked or explicitly deferred.

#### Task 5.2: Teardown
**Steps:**
1. Delete the spike VM(s), and revoke the spike Anthropic key.
2. Keep all spike files in the repo.

**Testing:**
- [ ] The VM(s) are gone from the hypervisor, and the key shows as revoked in the Anthropic console.
- [ ] `docs/research/kagent-1.0-spike/` is committed and contains everything needed to reproduce the spike.

## End-to-End Testing

The spike is complete when a person unfamiliar with it can take `docs/research/kagent-1.0-spike/README.md` and recreate the environment:
- provision the VM and install k3s
- install Substrate and kagent
- apply the translated agents

and then reproduce three key results: the approval-gate behaviour, the delegation behaviour, and the image smoke matrix. The findings report answers G1–G6 without needing this conversation.

## Risks and Mitigations

- **Alpha churn invalidates results.** Pin every image by digest and record versions. The report states it is valid for alpha4 only, and the gates are re-checked against beta/GA before any migration.
- **Spike VM doesn't match prod CPU.** Task 1.1 compares the cpuinfo `flags` line directly. A mismatch halts the spike.
- **Hypervisor contention with prod.** Size the VM modestly, run it only during active spike work, and power it off between sessions.
- **Anthropic spend.** Use a dedicated spike key with a low budget, default to Haiku, and run Sonnet 5 only in Task 4.5.
- **Nightly runsc from `gs://gvisor` isn't reproducible.** Pin and record the exact build (date + sha256).
- **Scope creep into a migration.** No production manifest changes in this plan. (The one planned exception, retiring the UI fork, already happened on 2026-09-26.)
- **Substrate on k3s is unsupported upstream (substrate#1782).** Treat k3s-specific failures as findings, not blockers to work around at length. Time-box each task and record the failure.
