---
type: "Kubernetes App Guide"
title: "admission-policies"
description: "Native API-server admission policies (ValidatingAdmissionPolicy / MutatingAdmissionPolicy) replacing the Kyverno webhook"
app: admission-policies
catalog_entity: admission-policies
kind: docs
namespace: kube-system
last_reviewed: 2026-10-19
status: stable
tags: [admission, cel, policy, security]
sources:
  - base-apps/admission-policies.yaml
  - base-apps/admission-policies/agent-identity.yaml
  - base-apps/admission-policies/agent-capability.yaml
  - base-apps/admission-policies/agent-capability-taxonomy.yaml
  - base-apps/admission-policies/agent-definitions-from-git.yaml
  - base-apps/admission-policies/disallow-latest-tag.yaml
  - base-apps/admission-policies/disallow-privileged-containers.yaml
  - base-apps/admission-policies/require-resource-limits.yaml
  - base-apps/admission-policies/require-labels.yaml
  - base-apps/admission-policies/disallow-default-namespace.yaml
  - base-apps/admission-policies/inject-ecr-pull-secret.yaml
  - base-apps/admission-policies/workload-defaults.yaml
  - base-apps/admission-policies/xtenant-guard.yaml
  - base-apps/admission-policies/kyverno-reports-rbac.yaml
  - scripts/gen-agent-capability-policy.py
  - scripts/validate-agent-capability.py
  - tests/admission-policies/conftest.py
  - tests/admission-policies/test_admission_policies.py
---

# admission-policies

## What it is
Admission rules that run **inside the Kubernetes API server**, as CEL expressions in
`ValidatingAdmissionPolicy` (GA 1.30) and `MutatingAdmissionPolicy` (GA 1.36) objects. They
replaced every Kyverno ClusterPolicy (docs/plans/k8s-136-features-implementation-plan.md,
Phase 3); the `kyverno-policies` app is retired. This app also holds the capability taxonomy
ConfigMap (`agent-capability-taxonomy.yaml`, source for the generated `agent-capability.yaml`)
and `kyverno-reports-rbac.yaml`, the read access to kagent's `api.kagent.dev` objects that
Kyverno's reports controller needs to report on these policies.

Why: Kyverno enforces through an admission **webhook** served by a single pod, and runs with
`forceFailurePolicyIgnore=true`. So whenever that pod is down or slow, its "Enforce" policies
silently stop enforcing. A native policy has no webhook and no pod: the API server evaluates
it on every request. Kyverno also does not state support for Kubernetes 1.36, and Kyverno 1.20
(~2026-11) removes the `kyverno.io/v1` ClusterPolicy type every one of those policies used (SPEC §T.80).

## Architecture & data flow
- Each policy is a `ValidatingAdmissionPolicy` (the rule: `matchConstraints` + CEL
  `validations`) plus a `ValidatingAdmissionPolicyBinding` (where and how it applies:
  `validationActions`).
- **Rollout stages**, per binding:
  - **shadow**: `validationActions: [Warn, Audit]`, `failurePolicy: Ignore`. A violation is a
    `kubectl` warning and an audit record, and blocks nothing. Every new policy starts here.
  - **enforcing**: `validationActions: [Deny]`, `failurePolicy: Fail`.
  `tests/admission-policies` fails if a binding's actions and its policy's failurePolicy
  disagree (a shadow policy that could block, or an enforcing one that fails open).
  History: during the migration each shadow policy ran next to the Kyverno ClusterPolicy it
  replaced so the verdicts could be compared, and the Kyverno policy was deleted at the flip.
  None remain — 2260b8f deleted the last five and 82f9dea retired the `kyverno-policies` app —
  so today shadow is judged on its own warnings, audit records and PolicyReports.
- **Reporting**: Kyverno's reports controller runs with `--validatingAdmissionPolicyReports=true`
  and writes native results to `kubectl get policyreports -A` (`source:
  ValidatingAdmissionPolicy`), but **only for policies labelled
  `reports.kyverno.io/enabled: "true"`**. That is opt-in, the reverse of Kyverno's own
  policies, and the Kyverno docs do not mention it; found in the v1.19.1 source. Every policy
  carries the label except two. `agent-capability-delegation` is parameterised, and Kyverno's
  engine mis-evaluates parameterised policies. `agent-definitions-from-git` depends on the
  request (who sent it, CREATE or UPDATE, the old object), and a background scan has no
  request, so it would report every agent definition as a violation.
  `test_kyverno_reporting_opt_in` enforces all three cases.
  Kyverno's end state is reporting only.

## Where config lives
- `agent-identity.yaml`: the agent identity contract (templates/agent-identity/README.md), as
  three policies: `agent-identity-scoped-store` (no `vault-backend` SecretStore in `kagent`),
  `agent-identity-no-monolithic-key` (nothing reads the destroyed Vault key `kagent`, cluster
  wide), `agent-identity-mcp-toolnames` (every MCP binding of a kagent 1.0 `AgentTemplate`,
  `api.kagent.dev/v1alpha3`, must list its `tools`; an empty list is a bind-all).
  **Enforcing** (`[Deny]`, `failurePolicy: Fail`); the Kyverno ClusterPolicy it replaced is
  deleted. Compared with the Kyverno version it also covers per-item `sourceRef.storeRef` and
  `dataFrom[].extract`, which the Kyverno rules missed.
- `agent-capability.yaml` is **generated** by `scripts/gen-agent-capability-policy.py` from
  `base-apps/admission-policies/agent-capability-taxonomy.yaml`. That is the same script and
  taxonomy that generated the Kyverno version (now deleted); CI runs it with
  `--check`. Since the kagent 1.0 cutover (2026-10-11) it matches `api.kagent.dev/v1alpha3` and
  holds four policies:
  - `agent-capability` (rules 1-5 and 2b, on `AgentTemplate`s, where the class label lives):
    declared class, every bound tool classified, the class permits the tools, and a
    write/destructive tool sits in an MCP binding with `requireApproval: true` (approval is per
    binding in 1.0). Rule 2b: a server-pinned tool is bound only from the one RemoteMCPServer the
    taxonomy's `server-pinned-tools` key names (a JSON object, `{tool: server}`). Today that is
    `replay_ui_steps` from `taskboard-browser`: it clicks and types, but only in a throwaway copy
    of Taskboard that its server starts per call, so its class `read` holds only there (SPEC
    §V.113, owner option 1). CI checks the same in `scripts/validate-agent-capability.py`
    (invariant 8), and a missing `server-pinned-tools` key is an error there, as for the byo keys.
  - `agent-capability-delegation` (rules 6-7): no `subAgent` of a higher class. The policy uses
    the **AgentTemplate kind as its parameter** with `paramRef.selector: {}`, so the API server
    evaluates it once per AgentTemplate in the request's namespace, each one a potential
    sub-agent. A sub-agent template whose class label is missing or not read/write counts as
    admin.
  - `agent-capability-refs` (rule 8): an `Agent` must reference a template and a Harness
    (`template.ref`/`harnessRef`); an inline `spec.template.inline` or `spec.harness` would bypass the
    other checks.
  - `agent-capability-runtime` (rule 9): a `Harness` must use the `kagent` runtime on an image
    from the taxonomy's `runtime-images` list; rules 1-7 are enforced inside that runtime.
    **One exception** (SPEC §V.101): the `byo` runtime on an image that is exactly an entry of
    the taxonomy's `byo-images` (`repository@sha256:digest`; never a repository, which would
    admit every digest pushed later, and never a tag). `claude` and `codex` are never admitted.
    The exception's other conditions span objects, so CI decides them
    (`scripts/validate-agent-capability.py`, invariant 7): every Agent on a byo Harness lives in
    `kagent` and runs a class `read` AgentTemplate whose every binding is a `RemoteMCPServer` in
    the taxonomy's `read-only-servers`, with only read tools and no subAgent. A byo runtime reaches a bound
    server's whole origin, so a server is listed only if it is read-only on the server side
    (`agent-docs`: `github-mcp-server --read-only`), proved per server in `tests/agent-capability`.

  **Enforcing** (`[Deny]`, `failurePolicy: Fail`); the Kyverno ClusterPolicy is deleted. The
  0.10 versions ran in shadow first and were replayed against every live object before the
  flip; the v1alpha3 versions shipped in the same sync as the new CRDs (SPEC §V.84).
- `agent-definitions-from-git.yaml` (hand-written, SPEC §V.101): denies CREATE and UPDATE of
  `Agent`, `AgentTemplate`, `Harness` and `RemoteMCPServer`, in every namespace (the kagent
  controller watches all of them), by anyone except Argo CD's application controller
  (`system:serviceaccount:argo-cd:argocd-application-controller`, the identity that syncs git)
  and the break-glass group `homelab:kagent-break-glass`. What CI checks in git is then what
  runs: the byo conditions above and the transitive delegation closure. It matches main
  resources only, so the kagent controller's status writes pass, and an UPDATE that leaves
  spec, labels and annotations unchanged passes for anyone: the garbage collector's finalizer
  removal in Argo's foreground prune (§B.18), a no-op storage migration, and
  `scripts/hop-verify.sh` re-applying a live template. Denied by design: `kubectl apply/edit`
  on these kinds with the x509 admin or Dex login, the kagent UI's create and edit (the
  controller's own service account), and the Argo CD UI's live edit (`argocd-server`).
  **Enforcing from the start**, not shadow first: the byo exception depends on it in the same
  change (§V.84), and an inventory of every writer (live `managedFields`, RBAC, the kagent
  alpha14 source; SPEC §T.130) found no legitimate one it blocks. Break glass: runbook. To
  soak it instead: `failurePolicy: Ignore` and `validationActions: [Warn, Audit]` on the two
  lines of `agent-definitions-from-git.yaml` marked `# enforcing` (hand-written, no generator), **only while
  `byo-images` holds no pinned digest and before any byo Agent syncs**: the byo admission
  depends on git being the only writer (runbook, "Break glass").
- Five **audit-only** workload-hygiene policies, one file each. They are `[Warn, Audit]` and
  `failurePolicy: Ignore` **permanently**; they report, never block:
  - `disallow-latest-tag` flags an image with no tag or `:latest` (a digest counts as pinned).
    It covers containers, initContainers, ephemeralContainers and image volumes. The Kyverno
    version's `?*:?*` pattern passed any image containing a `:`, so it never flagged anything.
  - `disallow-privileged-containers` flags any container with `privileged: true`.
  - `require-resource-limits` requires CPU and memory limits on app containers.
  - `require-labels` requires `app.kubernetes.io/name` on Deployments and StatefulSets.
  - `disallow-default-namespace` flags Deployments, StatefulSets and Services in `default`. The
    API server's own `default/kubernetes` Service is exempt.

  The first four exclude `kube-system`, `argo-cd` and `kyverno`, as the Kyverno versions (now deleted) did.
  The pod-level ones match Pod, Deployment, StatefulSet, DaemonSet, Job and CronJob, and
  extract the pod spec per kind in a `pod` variable, because native policies have no Kyverno
  "autogen". ReplicaSets are deliberately not matched: Kyverno's autogen reported every old
  revision-history ReplicaSet as a duplicate of its Deployment (54 of its 112 limits failures).
- `inject-ecr-pull-secret.yaml`: a **MutatingAdmissionPolicy** that adds the `ecr-registry`
  pull secret to any Pod pulling from ECR (container, initContainer or **image volume**). It
  replaces Kyverno's mutation, which needed Kyverno's webhook pod and ignored image volumes.
  It applies to every namespace except `kube-system`, `kube-public`, `kube-node-lease` and
  `kyverno`. A mutation has no shadow mode, so it was first bound to a test namespace and
  verified live before being widened. It is the only mechanism: Kyverno's
  `inject-ecr-pull-secret` ClusterPolicy has been deleted.
- `workload-defaults.yaml` (beacon §T.23): a **MutatingAdmissionPolicy** for namespaces that
  opt in with `platform.arigsela.com/defaults: enabled` (today only `beacon`). On the pod
  template of every Deployment, StatefulSet and CronJob, it adds what the author left out, and
  never overrides what the author set: labels `app.kubernetes.io/part-of` (the namespace),
  `backstage.io/kubernetes-id` (the object's name), `platform.arigsela.com/team` (the
  namespace's own label, else `platform`); annotation `prometheus.io/scrape: "false"` (a
  workload opts in with its own port and path); and, where the namespace also says
  `platform.arigsela.com/otel: python`, `instrumentation.opentelemetry.io/inject-python: "true"`,
  which is how beacon's notifier, with no tracing code, gets traced by the OpenTelemetry
  Operator (beacon §V.9). Objects istiod generates (`gateway.istio.io/managed`, a waypoint's
  Deployment) are never touched. CREATE and UPDATE, so a sync keeps the defaults; a change
  reaches a running workload on its next rollout. Fail-open.
- `xtenant-guard.yaml` (beacon §T.25): an enforcing **ValidatingAdmissionPolicy** on beacon's
  enterprise tenants (`XTenant`, `base-apps/beacon-tenants/`) and nothing else. On CREATE the
  name must be a tenant slug (`^[a-z][a-z0-9-]{2,30}$`, beacon §V.30). On DELETE, which takes the
  tenant's namespace and database with it, only an offboarded tenant (`spec.offboard: true`)
  whose `status.backup` names a verified `s3://` dump gets through (beacon §V.12), Argo CD's
  prune included. failurePolicy Fail.
- Tests: `tests/admission-policies/` boots a real k3s of the cluster's version in Docker,
  installs the kagent CRDs from `fixtures/crds/`, creates the cluster's real kagent objects
  (every AgentTemplate, Harness and Agent in `base-apps/kagent/`: the AgentTemplates are the
  delegation policy's parameters, and each must also re-apply with zero warnings), and server-side dry-runs
  `fixtures/<suite>/good/*.yaml` (must pass clean against EVERY policy) and
  `fixtures/<suite>/bad/<policy>/*.yaml` (must be flagged by that policy). Mutation fixtures,
  `fixtures/<suite>/mutate/<policy>/*.yaml`, are created as **real Pods**, and their admitted
  `imagePullSecrets` must equal the `test.homelab/expect-pull-secrets` annotation; or they are
  workloads whose admitted pod-template labels and annotations must equal the JSON in
  `test.homelab/expect-template`, on CREATE and again on an UPDATE (a dry-run replace of the
  unchanged manifest over the real object). The harness's namespaces `defaults-on` and
  `defaults-otel` carry the opt-in labels. The harness
  submits everything **as Argo CD's application controller** (impersonation), as production
  does, unless a fixture names another identity in `test.homelab/as-user` /
  `test.homelab/as-groups`; RBAC is opened to every authenticated identity so only admission
  decides. CI job `admission-policies-validate`.

## Gotchas & tribal knowledge
- **Test against a real API server.** kubeconform skips v1 MutatingAdmissionPolicy files
  (reports them as valid without checking), and the kyverno CLI mis-evaluates MAPs and
  parameterised VAPs. `kubectl --dry-run=server` compiles CEL but only a created policy is
  type-checked against the real CRD schema, so check `status.typeChecking` on the cluster after
  every change: `kubectl get validatingadmissionpolicies -o jsonpath='{range .items[*]}{.metadata.name}: {.status.typeChecking}{"\n"}{end}'`.
- **`variables` are not type-checked.** The API server type-checks `validations` (and flags a
  field that one of the matched kinds lacks, e.g. `object.spec.template` on a Pod), but not
  variable expressions, and a variable's value is untyped where it is used. So a typo in or
  after a variable shows up only in fixtures. That is why the per-kind pod-spec extraction
  lives in a variable, and why `test_every_matched_kind_has_a_bad_fixture` requires a bad
  fixture for every kind each policy matches.
- **MutatingAdmissionPolicy on 1.36: two traps**, both caught by the harness:
  - `ApplyConfiguration` cannot add to `imagePullSecrets`. Its entries are
    `LocalObjectReference`, an atomic struct, so the result is "may not mutate atomic arrays,
    maps or structs". With `failurePolicy: Ignore` that is a **silent no-op**.
  - A JSONPatch whose `value` is a **list of typed objects**
    (`value: [Object.spec.imagePullSecrets{...}]`) **panics** the API server's request handler
    (cel-go `ConvertToNative`). The create fails with a 500 **whatever the failurePolicy**.
    Add an empty list, then append one object. `test_api_server_did_not_panic` greps the
    harness API server's log for recovered panics.
- **A MutatingAdmissionPolicy has no `status` on 1.36**, so no `typeChecking` either. Only its
  mutate fixtures (real pods) and the panic check test it.
- **A parameterised policy denies until its param informer syncs.** After
  `agent-capability-delegation` is created, and after every **API-server restart** (k3s restart
  or upgrade), the API server needs a moment to sync its informer for the param kind
  (AgentTemplate since kagent 1.0). Until then the policy cannot evaluate, and with
  `failurePolicy: Fail` it **denies** read/write-class AgentTemplate writes: `failed to configure
  binding: paramKind ... not yet synced to use for admission`. Seconds, self-healing; an Argo
  sync in that window fails once and retries. Admin-class templates are not matched, and nothing
  else is affected. The harness waits it out before creating the kagent objects (it once failed
  a whole CI run).
- **The harness uses minimal typed CRDs** (`fixtures/crds/`), copied from the real schemas for
  the paths the policies read. A policy reading a new field needs that field added there, or
  type checking fails with `undefined field`.
- Guard every optional field with `has()`. A CEL runtime error is an admission failure once
  `failurePolicy: Fail`.
