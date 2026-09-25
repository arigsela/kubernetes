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
- Tests: `tests/admission-policies/` boots a real k3s of the cluster's version in Docker,
  creates the cluster's real Agents (`base-apps/kagent/agents/`: the delegation policy's
  parameters, and each must also re-apply with zero warnings), and server-side dry-runs
  `fixtures/<suite>/good/*.yaml` (must pass clean against EVERY policy) and
  `fixtures/<suite>/bad/<policy>/*.yaml` (must be flagged by that policy). CI job
  `admission-policies-validate`.

## Gotchas & tribal knowledge
- **Test against a real API server.** kubeconform skips v1 MutatingAdmissionPolicy files
  (reports them as valid without checking), and the kyverno CLI mis-evaluates MAPs and
  parameterised VAPs. `kubectl --dry-run=server` compiles CEL but only a created policy is
  type-checked against the real CRD schema, so check `status.typeChecking` on the cluster after
  every change: `kubectl get validatingadmissionpolicies -o jsonpath='{range .items[*]}{.metadata.name}: {.status.typeChecking}{"\n"}{end}'`.
- **The harness uses minimal typed CRDs** (`fixtures/crds/`), copied from the real schemas for
  the paths the policies read. A policy reading a new field needs that field added there, or
  type checking fails with `undefined field`.
- Guard every optional field with `has()`. A CEL runtime error is an admission failure once
  `failurePolicy: Fail`.
