---
type: "Kubernetes App Guide"
title: "admission-policies"
description: "Native API-server admission policies (ValidatingAdmissionPolicy / MutatingAdmissionPolicy) replacing the Kyverno webhook"
app: admission-policies
catalog_entity: admission-policies
kind: docs
namespace: kube-system
last_reviewed: 2026-09-25
status: current
tags: [admission, cel, policy, security]
sources:
  - base-apps/admission-policies.yaml
  - base-apps/admission-policies/agent-identity.yaml
  - base-apps/admission-policies/agent-capability.yaml
  - base-apps/admission-policies/disallow-latest-tag.yaml
  - base-apps/admission-policies/require-resource-limits.yaml
  - base-apps/admission-policies/inject-ecr-pull-secret.yaml
  - scripts/gen-agent-capability-policy.py
  - tests/admission-policies/conftest.py
  - tests/admission-policies/test_admission_policies.py
---

# admission-policies

## What it is
Admission rules that run **inside the Kubernetes API server**, as CEL expressions in
`ValidatingAdmissionPolicy` (GA 1.30) and `MutatingAdmissionPolicy` (GA 1.36) objects. They
are replacing the Kyverno ClusterPolicies in `base-apps/kyverno-policies/`, one policy at a
time (docs/plans/k8s-136-features-implementation-plan.md, Phase 3).

Why: Kyverno enforces through an admission **webhook** served by a single pod, and runs with
`forceFailurePolicyIgnore=true`. So whenever that pod is down or slow, its "Enforce" policies
silently stop enforcing. A native policy has no webhook and no pod: the API server evaluates
it on every request. Kyverno also does not state support for Kubernetes 1.36, and Kyverno 1.20
(~2026-11) removes the `kyverno.io/v1` ClusterPolicy type every current policy uses (SPEC §T.80).

## Architecture & data flow
- Each policy is a `ValidatingAdmissionPolicy` (the rule: `matchConstraints` + CEL
  `validations`) plus a `ValidatingAdmissionPolicyBinding` (where and how it applies:
  `validationActions`).
- **Rollout stages**, per binding:
  - **shadow**: `validationActions: [Warn, Audit]`, `failurePolicy: Ignore`. A violation is a
    `kubectl` warning and an audit record, and blocks nothing. Runs next to the still-active
    Kyverno policy so the two verdicts can be compared.
  - **enforcing**: `validationActions: [Deny]`, `failurePolicy: Fail`. The Kyverno policy is
    then deleted.
  `tests/admission-policies` fails if a binding's actions and its policy's failurePolicy
  disagree (a shadow policy that could block, or an enforcing one that fails open).
- **Reporting**: Kyverno's reports controller runs with `--validatingAdmissionPolicyReports=true`,
  so native policies show up in `kubectl get policyreports -A` like Kyverno's did. Kyverno's
  end state is reporting only.

## Where config lives
- `agent-identity.yaml`: the agent identity contract (templates/agent-identity/README.md), as
  three policies: `agent-identity-scoped-store` (no `vault-backend` SecretStore in `kagent`),
  `agent-identity-no-monolithic-key` (nothing reads the destroyed Vault key `kagent`, cluster
  wide), `agent-identity-mcp-toolnames` (McpServer tool refs must list `toolNames`). Currently
  **shadow**. Compared with the Kyverno version it also covers per-item
  `sourceRef.storeRef` and `dataFrom[].extract`, which the Kyverno rules missed.
- `agent-capability.yaml` is **generated** by `scripts/gen-agent-capability-policy.py` from
  `base-apps/kyverno-policies/agent-capability-taxonomy.yaml`. That is the same script and
  taxonomy that generate the Kyverno version, so the two cannot drift; CI runs it with
  `--check`. It holds two policies:
  - `agent-capability` (rules 1-5): declared class, every bound tool classified, the class
    permits the tools, and mutating tools sit behind `requireApproval`.
  - `agent-capability-delegation` (rules 6-7): no delegating to a higher class. The policy
    uses the **Agent kind as its parameter** with `paramRef.selector: {}`, so the API server
    evaluates it once per Agent in the request's namespace, each one a potential delegate. A
    delegate whose class label is missing or not read/write counts as admin.

  Currently **shadow**.
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

  The first four exclude `kube-system`, `argo-cd` and `kyverno`, as the Kyverno versions did.
  The pod-level ones match Pod, Deployment, StatefulSet, DaemonSet, Job and CronJob, and
  extract the pod spec per kind in a `pod` variable, because native policies have no Kyverno
  "autogen". ReplicaSets are deliberately not matched: Kyverno's autogen reported every old
  revision-history ReplicaSet as a duplicate of its Deployment (54 of its 112 limits failures).
- `inject-ecr-pull-secret.yaml`: a **MutatingAdmissionPolicy** that adds the `ecr-registry`
  pull secret to any Pod pulling from ECR (container, initContainer or **image volume**). It
  replaces Kyverno's mutation, which needed Kyverno's webhook pod and ignored image volumes.
  It applies to every namespace except `kube-system`, `kube-public`, `kube-node-lease` and
  `kyverno`. A mutation has no shadow mode, so it was first bound to a test namespace and
  verified live before being widened. Kyverno's `inject-ecr-pull-secret` still runs alongside
  it until deleted (both add the secret idempotently by name).
- Tests: `tests/admission-policies/` boots a real k3s of the cluster's version in Docker,
  creates the cluster's real Agents (`base-apps/kagent/agents/`: the delegation policy's
  parameters, and each must also re-apply with zero warnings), and server-side dry-runs
  `fixtures/<suite>/good/*.yaml` (must pass clean against EVERY policy) and
  `fixtures/<suite>/bad/<policy>/*.yaml` (must be flagged by that policy). Mutation fixtures,
  `fixtures/<suite>/mutate/<policy>/*.yaml`, are created as **real Pods**, and their admitted
  `imagePullSecrets` must equal the `test.homelab/expect-pull-secrets` annotation. CI job
  `admission-policies-validate`.

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
- **The harness uses minimal typed CRDs** (`fixtures/crds/`), copied from the real schemas for
  the paths the policies read. A policy reading a new field needs that field added there, or
  type checking fails with `undefined field`.
- Guard every optional field with `has()`. A CEL runtime error is an admission failure once
  `failurePolicy: Fail`.
