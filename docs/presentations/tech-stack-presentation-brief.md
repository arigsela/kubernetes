# Tech stack presentation brief — `arigsela/kubernetes`

**Purpose:** a slide-by-slide proposal for a talk about this repository, written for the agent
that builds the deck. Every factual claim below cites where in the repo (or the cluster) it can
be verified. Facts marked *(verify)* were not re-checked for this brief; confirm before stating.

**Prepared:** 2026-10-11, the day the cluster reached k3s v1.37.1 and kagent 1.0 went live.
Re-check versions against `base-apps/*.yaml` and `SPEC.md` if the talk is given later.

## The story in one line

> A three-node homelab, run with production discipline (GitOps only, invariants, tested
> policies, drilled backups), that grew an agent layer with real guardrails: an Agentic
> Development Platform in miniature.

**Audience assumptions:** platform / DevOps / SRE engineers. Adjust depth per the variants at
the end. **Target length:** 25–30 minutes plus 3 short demos (~8 min), or 12 slides without
demos for a 15-minute slot.

**Tone rules for the deck:**
- Lead with decisions and failures, not logos. Every architecture slide gets one "why".
- Numbers only where the repo backs them (cited below). No "enterprise-grade" language.
- Say "alpha" out loud for kagent 1.0 and Agent Substrate. It is a strength (early, measured),
  not something to hide.

---

## Slide 1 — Title

- **Title:** "A homelab run like production — and what happened when I gave it agents"
- **Subtitle:** GitOps, invariants, policy-as-code, and an agent platform on three old Xeons
- **Visual:** one photo or diagram of the three VMs; the point is modest hardware.
- **Speaker note:** the hardware is deliberately unimpressive. Everything interesting is in how
  it is run.

**Facts:** 3 k3s nodes (`k3s-control-01` + 2 workers) as VMs on one Ubuntu hypervisor, Intel
Xeon E5-2670 (2012-era, no AVX2) — `ansible/inventory/hosts.yml`, `SPEC.md` §C and the CPU
notes in `docs/plans/kagent-1-0-on-k8s-1-37-implementation-plan.md`. k3s `v1.37.1+k3s1` with a
SQLite datastore, single control plane — `SPEC.md` §G (2026-10-11 entry).

## Slide 2 — The framing: an Agentic Development Platform

- **Message:** the platform has three layers, built in this order.
  1. **Internal developer platform** (GitOps, IaC, secrets, edge, policy, data, observability,
     portal).
  2. **Paths and automation** (scaffolder, Renovate, PR triage, scheduled workflows).
  3. **Agent infrastructure** (runtime, identity, capability, audit, evaluation).
- **Visual:** three stacked layers; the rest of the deck walks bottom to top.
- **Speaker note:** the model is the Weave Intelligence "ADP" framing: a good IDP first, then
  agents on top. Identity was deliberately built before anything else in the agent layer.

**Facts:** the sequencing and gap analysis are recorded in
`docs/superpowers/specs/2026-07-14-adp-remaining-pillars-roadmap.md` and the engineering
narrative in `docs/adp-engineering-deep-dive.md`.

## Slide 3 — GitOps is the only write path

- **Message:** nothing is applied by hand. A change is a commit; Argo CD does the rest.
- **Bullets:**
  - Argo CD app-of-apps: `base-apps/master-app.yaml` manages itself and creates one
    Application per `base-apps/<app>.yaml`.
  - 12 simple apps come from an ApplicationSet instead of hand-written Applications
    (`base-apps/managed-apps.yaml`, `appsets/managed-apps/`, `docs/managed-apps-appset.md`).
  - `prune: true` + `selfHeal: true` everywhere; PVC-bearing apps are explicitly fenced from
    prune, enforced by a test (`tests/k3s-upgrade/test_prune_scope.py`, SPEC §V.39).
  - 59 Applications, all Synced + Healthy at the time of writing (cluster, 2026-10-11).
  - Renovate opens weekly upgrade PRs and never merges (`renovate.json5`,
    `docs/platform/renovate.md`).
- **Visual:** the master-app → Applications fan-out; one callout on the prune fence.
- **Speaker note:** the prune fence exists because `local-path` volumes have reclaim
  `Delete`: pruning a PVC deletes data. SPEC §V.19/§V.39 tell the story of discovering 10 of 21
  PVCs were exposed.

## Slide 4 — Every PR is triaged by a model before a human reads it

- **Message:** review attention is rationed by a classifier, not by habit.
- **Bullets:**
  - `.github/workflows/pr-triage.yaml` labels each PR `review:skip`, `review:skim` or
    `review:read` and leaves one sticky comment explaining why.
  - Policy lives in `.github/review-policy.yaml`; anything under `.github/**` is always `read`.
  - The classifier is "Jev", a cheap calibrated-probability model, the same one used later for
    agent risk triage and eval judging (consistency across the platform).
  - PR text is treated as untrusted input to the classifier (CLAUDE.md "PR Review Labels").
- **Visual:** screenshot of a triage comment on a real PR (any of today's PRs #725–#752).
- **Speaker note:** the classifier never blocks a merge; it decides how closely the owner
  reads. Calibration is re-run from `arigsela/claude-agents` when the policy changes.

## Slide 5 — Infrastructure as code in three layers

- **Message:** one tool per layer, and a hard rule about *when* Terraform applies.
- **Bullets:**
  - **Terraform (OpenTofu via in-cluster Atlantis):** the only root is
    `terraform/roots/asela-cluster` (Argo CD install, IAM, Vault KMS key, ECR repos, budgets).
    State in S3. **Apply happens from the open PR; merging is the last step.** Merging first
    strands the change silently — it has happened (SPEC §B.13, §V.73).
  - **Crossplane:** per-app AWS resources (S3 buckets, IAM users) declared next to the app.
  - **Ansible:** the hypervisor and the three VMs: baseline, patching with drained serial
    reboots, and k3s node-level config (`ansible/README.md`, `ansible/playbooks/`).
- **Visual:** three columns; highlight the apply-before-merge arrow.
- **Speaker note:** today's ECR repository deletion is a good example: Terraform's
  `prevent_destroy` forced a two-PR dance (move to an unprotected resource with
  `force_delete`, apply, then delete) — `terraform/roots/asela-cluster/ecr.tf` header.

## Slide 6 — Node config is code too (and validates itself before touching the node)

- **Message:** even `/etc/rancher/k3s` is git-managed, with a safety net.
- **Bullets:**
  - `ansible/playbooks/k3s-authn.yml` installs the API server's structured authentication
    config and k3s drop-ins. Before touching the node it boots a **throwaway k3s of the
    target version in Docker**, loads the files, and requires anonymous access to get 401.
  - Install by temp+rename, restart with auto-rollback on timeout, post-check (SPEC §V.81).
  - Today it carried the drop-in that stops k3s 1.37 from taking over the Gateway API CRDs
    (`20-disable-bundled.yaml`, SPEC §R.42, §V.83).
- **Visual:** the validate → preflight → install → verify pipeline as a strip.
- **Speaker note:** the test suite for this playbook runs the real playbook against a container
  "node" with stubbed `systemctl` (`tests/ansible/test_k3s_authn.py`, 21 tests).

## Slide 7 — Secrets and identity

- **Message:** one credential per consumer, nothing shared, humans via SSO.
- **Bullets:**
  - HashiCorp Vault in-cluster, auto-unsealed with AWS KMS (`base-apps/vault/`,
    `terraform/roots/asela-cluster/vault-kms.tf`). KV v2 mount `k8s-secrets`.
  - External Secrets Operator with a **per-consumer** `SecretStore` + Vault role + dedicated
    ServiceAccount: 30 SecretStores, 43 ExternalSecrets across 19 namespaces
    (`docs/platform/external-secrets.md`). No ClusterSecretStore.
  - The "agent principal" pattern: each agent/database credential has its own Vault role that
    can read exactly one path (`templates/agent-identity/README.md`).
  - Humans: Dex (GitHub) OIDC for Vault *and* `kubectl` (structured authn config, SPEC §T.92,
    `docs/troubleshooting/kubectl-oidc.md`). Root token is break-glass only.
  - Admission policy enforces the pattern: a broad store or a monolithic key is denied
    (`base-apps/admission-policies/agent-identity.yaml`).
- **Visual:** Vault → ESO → namespace Secrets with the one-role-per-consumer fan-out.
- **Speaker note:** the ESO walk `0.11 → 2.x` was staged across six releases because `0.17`
  removed the `v1beta1` API that 59 manifests used (SPEC §V.23, §V.26): a good "read the CRD
  storage version" lesson.

## Slide 8 — The edge: Istio Gateway API, allow-lists, WAF

- **Message:** north-south traffic is declarative per host, with the IP allow-list as the
  primary control.
- **Bullets:**
  - Istio 1.30 ambient; Gateway API CRDs pinned at v1.5.1 and Argo-owned
    (`base-apps/istio-gateway-api.yaml`); one `HTTPRoute` per host (`base-apps/<app>/httproute.yaml`).
  - IP allow-list `AuthorizationPolicy` (`base-apps/istio-ingress/authorizationpolicy.yaml`);
    Coraza WAF (`base-apps/istio-waf/`) with scoped exemptions and a validator
    (`scripts/validate-waf-scope.py`).
  - TLS: cert-manager with a single Route 53 DNS-01 issuer, one explicit `Certificate` per host
    (SPEC §V.71 — no ingress-shim, so deleting a route never garbage-collects a cert).
- **Case study box:** replacing ingress-nginx (archived upstream, capped at k8s 1.35). Host by
  host, old Ingress live until the replacement was proven, access-control equivalence proven by
  a real request from a non-allow-listed address (SPEC §V.53, §V.54;
  `docs/plans/ingress-nginx-replacement-handover.md`).
- **Speaker note:** the SNAT lesson (SPEC §B.9): an allow-list rule that includes the pod/node
  CIDR lets the gateway's own source IP match, so every internet client is "allowed".

## Slide 9 — Policy as code, tested against a real API server

- **Message:** admission policies are generated, versioned, and proven on a real k3s in CI.
- **Bullets:**
  - Native `ValidatingAdmissionPolicy` / `MutatingAdmissionPolicy` in CEL
    (`base-apps/admission-policies/`); Kyverno kept only for PolicyReports.
  - The agent capability policy is **generated** from a taxonomy that classifies every agent
    tool as read/write/admin (`agent-capability-taxonomy.yaml`,
    `scripts/gen-agent-capability-policy.py`); CI fails on drift.
  - `tests/admission-policies/` boots `rancher/k3s` in Docker and server-side dry-runs good
    and bad fixtures (100+ cases); a MutatingAdmissionPolicy is proven by real pod fixtures
    (SPEC §V.75).
  - Validators fail closed: an agent manifest of an unknown API version, or zero agents, is a
    CI failure, not a vacuous pass (SPEC §V.84).
- **Visual:** taxonomy → generator → policy → Docker k3s test loop.
- **Demo cue (Demo 3):** `kubectl apply --dry-run=server` of an AgentTemplate that binds
  `k8s_delete_resource` without approval → denied with an explanatory message.
- **Speaker note:** parameterised policies deny until the API server's param informer syncs
  after a restart; the test harness and the upgrade gate both account for it (SPEC §V.78).

## Slide 10 — Data and backups that are actually restorable

- **Message:** a backup that has not been restored is not a backup.
- **Bullets:**
  - CloudNativePG (`base-apps/postgresql/cnpg-cluster.yaml`) with barman to S3, daily
    `ScheduledBackup`.
  - A plain pgvector Postgres for app and agent data (n8n, Backstage, kagent, Substrate).
  - Operator-independent scripts: `scripts/k3s-backup.sh` / `k3s-restore.sh` (SQLite-aware:
    WAL files must not be shipped beside a snapshot, SPEC §B.1), `pg-backup.sh`,
    `vault-backup.sh`. Restore drills run in CI against real k3s/Postgres/Vault containers
    (`tests/k3s-upgrade/`).
  - Artifacts must be off-cluster (SPEC §V.21); the upgrade gate refuses stale ones.
- **Failure story:** on 2026-10-11 `pg-backup.sh` recorded a *truncated* dump as a good
  backup: `kubectl exec` cut the stream at ~129 MB with a valid gzip. Fix: stage in the pod,
  copy in chunks, verify sha256 and the dump's completion marker (SPEC §B.25, §V.85).
- **Visual:** the backup → checksum → S3 → drill loop; the failure as a red callout.

## Slide 11 — Observability and security signals

- **Message:** metrics, logs, traces and runtime security, with alerts that fail closed.
- **Bullets:**
  - Prometheus + Grafana + Loki + Alloy *(verify versions in `base-apps/logging/`)*; the
    cluster-overview dashboard is generated by a script, so it never drifts
    (`scripts/gen-cluster-overview-dashboard.py`).
  - Coroot (eBPF) receives OpenTelemetry traces and logs from the agents
    (`base-apps/kagent.yaml` otel values).
  - Falco for runtime detection (`docs/platform/falco.md`), with namespace-scoped exclusions
    for the agent sandboxes.
  - Agent-audit alert rules use `noDataState: Alerting`: if the audit stops reporting, the
    alert fires instead of staying green (`base-apps/logging/grafana-alerting.yaml`).
- **Speaker note:** cAdvisor metrics on 1.37 had a reported label regression; the hop runbook
  now checks `container_memory_working_set_bytes{pod!=""}` after every hop.

## Slide 12 — The developer portal and the docs contract

- **Message:** documentation is a contract, generated into the portal, and read by the agents.
- **Bullets:**
  - Backstage (`base-apps/backstage/`) with TechDocs, catalog ingestion from Kubernetes, and a
    New App scaffolder template (`templates/new-app/`).
  - Every in-scope app carries `docs.md`, `runbook.md` and `catalog-info.yaml` under a schema
    (`templates/agent-docs/README.md`); `scripts/validate-agent-docs.py` enforces it and
    `gen-techdocs.py` / `gen-okf.py` generate the mkdocs and index files (CI checks drift).
  - The same bundle (`index.md` → `base-apps/index.md` → each app's docs) is what the
    `homelab-knowledge` agent retrieves from. One source for humans and agents.
  - Backstage also exposes an MCP endpoint the agents call for catalog lookups.
- **Visual:** docs.md → generator → TechDocs / OKF index → agent retrieval.
- **Speaker note:** the portal's kagent plugins (agent cards, skill suggestion) are custom code
  in the `arigsela/backstage` repo, moved to the kagent 1.0 API in release v1.5.0.

## Slide 13 — The agent platform (headline slide)

- **Message:** agents run as sandboxed, snapshot-able actors, with tools, memory and approvals.
- **Bullets:**
  - **kagent 1.0.0-alpha11 on Agent Substrate 0.5.0-alpha2** (`base-apps/kagent*.yaml`,
    `base-apps/substrate*.yaml`). Every conversation is a **gVisor actor** on a worker pool
    that suspends to a snapshot between turns and resumes — including after a node reboot
    (verified 2026-10-11, SPEC §T.98/§T.99).
  - **7 agents** declared in git as `AgentTemplate` + `Harness` + `Agent`
    (`base-apps/kagent/agent-templates/`, `harnesses/`, `agents/`): a read-only repo/cluster
    assistant, a read-only Kubernetes investigator, Kubernetes and Istio operators, an
    orchestrator, and two small chat agents.
  - **Models:** Claude Sonnet 5.5 and Haiku 5.5, prompt caching on — 48% of prompt tokens
    served from cache, 86–98% inside tool loops (measured 2026-10-11).
  - **Tools over MCP:** a read-only GitHub MCP for the docs bundle, the Backstage catalog MCP,
    and the kagent Kubernetes/Istio tool server. **Memory:** pgvector + a local Ollama
    embedding model (`base-apps/kagent/embedding-model-config.yaml`).
  - **Access:** A2A over the gateway (`scripts/ask-hk`), behind the IP allow-list.
- **Visual:** request → gateway → controller → actor (gVisor) → MCP tools; a snapshot store on
  the side.
- **Demo cue (Demo 1):** `scripts/ask-hk "Is the ollama deployment healthy and which k3s
  version do the nodes run?"` — it reads docs, then checks live state.
- **Speaker note:** be explicit that this is alpha software and that every alpha bump is a
  clean-database migration (`base-apps/kagent/runbook.md`).

## Slide 14 — Human-in-the-loop by construction

- **Message:** destructive tools cannot run without a human, and that is enforced at three
  levels.
- **Bullets:**
  - Each operator agent binds its tool server twice: read tools ungated, mutating tools with
    `requireApproval: true` (`base-apps/kagent/agent-templates/k8s-agent.yaml`).
  - Admission denies any template that binds a write/admin tool without approval (Slide 9).
  - CI validators enforce the same taxonomy before merge.
  - Approvals arrive over the A2A HITL extension or the UI; verified today: reject keeps the
    object, approve deletes it.
- **Demo cue (Demo 2):** ask `k8s-agent` to delete a throwaway ConfigMap → task pauses in
  `INPUT_REQUIRED` → reject → object still there → approve → gone.
- **Visual:** the two-binding diagram with a lock on the mutating one.

## Slide 15 — Audit and evaluation for agents

- **Message:** every tool call is recorded, risk-scored, and the agents are graded.
- **Bullets:**
  - `scripts/agent-audit.py` reads the agent database, flags any write tool call with no
    approval decision, and exports the action record to S3 daily
    (`base-apps/postgresql/agent-audit-cronjob.yaml`).
  - Risk triage: the Jev classifier scores findings first; a human sees the rest
    (`scripts/agent-audit-risk.py`).
  - `agent-audit-web` (`base-apps/agent-audit-web/`) is the UI, behind Dex SSO.
  - **Eval corpus** (`tests/eval-corpus/homelab-knowledge.yaml`) scored by `scripts/score-eval.py`:
    deterministic rubric + leak hard-fail, then a cascade judge: Jev first, Claude Sonnet 5.5
    only when Jev is unsure.
- **Failure story:** moving the agents to a new runtime changed the event format and blinded
  the ungated-tool check for ~90 minutes. Lesson: after any runtime/model change, re-run the
  audit against a known gated call (SPEC §B.28).
- **Visual:** events → audit → risk tier → S3 / web; eval corpus → rubric → Jev → Sonnet.

## Slide 16 — Operational discipline: the SPEC ledger and the upgrade gate

- **Message:** invariants are written down, every bug becomes one, and upgrades are gated by
  a script.
- **Bullets:**
  - `SPEC.md`: goals, constraints, research, **85 invariants**, a task ledger and a bug log
    where each bug names the invariant that now catches it (e.g. §B.25 → §V.85).
  - `scripts/hop-verify.sh gate`: artifacts fresh and checksummed, nodes, Argo drift, CNPG,
    Vault, ESO, Istio dataplane, CNI/IPAM health, ingress, admission path. Non-zero = do not hop.
  - k3s walked 1.33 → 1.34 → 1.35 → 1.36 → 1.37 with this gate; the 1.37 control-plane hop
    took the API down for **42 seconds** (SPEC §T.96).
  - One pytest suite per concern (16 suites, `AGENTS.md`), run exactly as CI runs them.
- **Visual:** the backup → gate → pause → hop → verify → resume loop.
- **Speaker note:** the gate's own bug (§B.14: `set -e` silently aborted it on a clean cluster)
  is a good "test your tests" anecdote.

## Slide 17 — What it costs

- **Message:** all of this runs on old hardware and about ten dollars a month of AWS.
- **Bullets:**
  - AWS bill ≈ $10/month by design (Route 53, S3 for state/backups/logs/audit, KMS, ECR);
    budget managed in Terraform (`terraform/roots/asela-cluster/budgets.tf`, $15 alert).
  - ECR lifecycle keeps the two newest tagged images per repo; dead repos deleted via Terraform.
  - Model spend is small: prompt caching halves input tokens; Haiku 5.5 for the simple agents.
- **Visual:** a single bar: hardware (sunk) vs. monthly cloud.
- *(verify the current month's bill before quoting a number.)*

## Slide 18 — Lessons (the slide people remember)

Pick 4–5; all are documented in `SPEC.md` §B:
- Merging a Terraform PR before Atlantis applies strands the change silently (§B.13).
- A valid gzip is not a complete backup (§B.25).
- Argo CD drops a `null` Helm value, so "unset" is not "removed" (§B.27).
- A chart swap can leave untracked CRDs behind while the app reports Healthy (§B.26).
- An allow-list that includes your own pod CIDR allows everyone (§B.9).
- Changing an agent runtime changed the audit's input format; "0 findings" meant "0 visibility"
  (§B.28).
- `kubectl can-i` says yes to subresources the real request forbids — verify with the real call.

## Slide 19 — Honest limits and what's next

- kagent 1.0 and Agent Substrate are **alpha**; alpha bumps reset sessions and memory.
- The Go agent runtime drops the model's reasoning between tool calls; kagent exposes no
  effort/thinking control.
- Single control plane on SQLite: a control-plane hop is an API outage (bounded, measured).
- Open items: WorkerPool anti-affinity (SPEC §T.102), Argo Workflows only partly built
  (`docs/index.md`), a GitHub token for the docs MCP to rotate (§T.101).
- Next: evaluation depth (more corpus, hill-climbing on prompts), an L02 path registry.

## Slide 20 — Close

- Repo: `github.com/arigsela/kubernetes` (public). Start at `index.md`, then `SPEC.md`.
- One sentence: "The agents are the headline, but the reason they are safe to run is
  everything underneath them."

---

## Demo scripts (exact commands; all read-only or on throwaway objects)

**Demo 1 — ask the knowledge agent (≈30 s):**
```bash
scripts/ask-hk "Is the ollama deployment healthy, and which k3s version do the nodes run?"
```
It prints the answer and a `context: <id>` line; a follow-up with `--context <id>` shows
conversation continuity. (Note: its *docs* retrieval depends on the GitHub token in
SPEC §T.101 being rotated; the live-cluster half works regardless.)

**Demo 2 — the approval gate (≈60 s):**
```bash
kubectl -n default create configmap approval-gate-probe --from-literal=purpose=demo
scripts/ask-hk --agent kagent/k8s-agent "Delete the ConfigMap approval-gate-probe in default."
# exit 2: waiting for approval. Approve/reject in https://kagent.arigsela.com, then:
kubectl -n default get configmap approval-gate-probe
```

**Demo 3 — policy denies a bad agent (≈20 s):** server-side dry-run an `AgentTemplate` that
binds `k8s_delete_resource` with `requireApproval: false`; the API returns the
`agent-capability` denial text. A ready-made bad fixture lives under
`tests/admission-policies/fixtures/agent-capability/bad/`.

## Audience variants

- **15-minute version:** slides 1, 2, 3, 7, 9, 13, 14, 18, 20.
- **Security-focused:** expand 7, 8, 9, 14, 15; add the WAF exemptions and the Falco rules.
- **AI/agents-focused:** compress 3–12 into one "the platform underneath" slide; expand 13–15,
  19; run all three demos.

## Do not claim

- That this is HA (it is not; one control plane, SQLite).
- That kagent 1.0 is production software.
- Specific uptime figures: none are tracked.
- That Argo Workflows is in use (partly built only).
