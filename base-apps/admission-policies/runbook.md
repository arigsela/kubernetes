---
type: "Kubernetes App Runbook"
title: "admission-policies — Runbook"
description: "Operational runbook for the native admission policies: blocked changes, shadow warnings, rollout."
app: admission-policies
catalog_entity: admission-policies
kind: runbook
namespace: kube-system
last_reviewed: 2026-09-25
status: current
tags: [admission, cel, policy, security]
sources:
  - base-apps/admission-policies/agent-identity.yaml
  - tests/admission-policies/test_admission_policies.py
---

# admission-policies — Runbook

## Failure modes
### Symptom: `kubectl apply` or an Argo sync is rejected with `ValidatingAdmissionPolicy '<name>' ... denied request`
- **Check:** the message names the policy and says what is wrong. Read the policy:
  `kubectl get validatingadmissionpolicy <name> -o yaml`.
- **Fix:** fix the object, not the policy; the policies encode the contracts in
  `templates/agent-identity/README.md` and the capability taxonomy. If the policy itself is
  wrong: set its binding back to shadow (`validationActions: [Warn, Audit]` plus
  `failurePolicy: Ignore` on the policy) in git, or `git revert` the flip. Add a fixture that
  reproduces the false positive to `tests/admission-policies/fixtures/<suite>/good/`.
- **Emergency (a policy blocks everything and git can't sync):**
  `kubectl delete validatingadmissionpolicybinding <name>`. That disables the policy at once,
  and Argo re-creates it at the next sync, so fix git first.

### Symptom: `Warning: Validation failed for ValidatingAdmissionPolicy '<name>'` on apply
- A policy in **shadow** flagged the object. Nothing was blocked. Once that policy moves to
  enforcing, the same object will be **denied**, so fix it now.

### Symptom: a policy silently never fires
- **Check:** `kubectl get validatingadmissionpolicy <name> -o jsonpath='{.status.typeChecking}'`.
  An `undefined field` warning means a path in the expression does not exist in the resource's
  schema, so the expression can never be true as written.
- **Check:** the binding exists (`kubectl get validatingadmissionpolicybinding`) and its
  `matchResources` / the policy's `matchConstraints` cover the resource and namespace.

## How-to
### Add or change a policy
1. Write the policy and binding in `base-apps/admission-policies/`. New policies start in
   shadow (`[Warn, Audit]`, `failurePolicy: Ignore`).
2. Add fixtures: at least one `bad/<policy-name>/` case and a `good/` case, in
   `tests/admission-policies/fixtures/<suite>/`.
3. `python3 -m pytest tests/admission-policies/` (needs Docker; CI runs it too).
4. After merge, check `status.typeChecking` on the cluster, and compare the shadow warnings
   against the Kyverno policy it replaces (`kubectl get policyreports -A`).
5. Flip to enforcing in a separate change: binding `[Deny]`, policy `failurePolicy: Fail`,
   delete the Kyverno policy.
