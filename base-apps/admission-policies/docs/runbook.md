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
  - base-apps/admission-policies/inject-ecr-pull-secret.yaml
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
- An agent policy (`agent-identity-*`, `agent-capability*`) in **shadow** flagged the object.
  Nothing was blocked. Once that policy moves to enforcing, the same object will be
  **denied**, so fix it now.
- A workload-hygiene policy (`disallow-latest-tag`, `disallow-privileged-containers`,
  `require-resource-limits`, `require-labels`, `disallow-default-namespace`) flagged it. These
  are audit-only permanently and never block. The message names the offending images or
  containers; fix them in the app's manifests when convenient.

### Symptom: a Pod pulling from ECR is in `ImagePullBackOff` with `no basic auth credentials` / 401
- **Check:** `kubectl get pod -n <ns> <pod> -o jsonpath='{.spec.imagePullSecrets}'`. It must
  list `ecr-registry`; `inject-ecr-pull-secret` adds it at pod creation.
- **Check:** the pod's namespace is matched by the `inject-ecr-pull-secret` binding (currently
  only `admission-test`; Kyverno's `inject-ecr-pull-secret` still covers the rest) and that
  `kubectl get mutatingadmissionpolicy inject-ecr-pull-secret -o jsonpath='{.status}'` shows
  no type-checking warnings. `failurePolicy` is `Ignore`, so a policy error yields a pod
  without the secret rather than a rejected pod.
- **Check:** the `ecr-registry` Secret exists in the namespace and is fresh (ecr-auth
  CronJob; ECR tokens last 12h).

### Symptom: Pod creation fails with a 500 / `stream error ... INTERNAL_ERROR`
- A mutating policy expression may be panicking the API server. That fails the request
  **whatever the failurePolicy** (see docs.md gotchas). Look for `Observed a panic` in the
  k3s journal on the control plane (`journalctl -u k3s | grep -A5 'Observed a panic'`).
- **Emergency:** `kubectl delete mutatingadmissionpolicybinding <name>`, then fix git (Argo
  re-creates the binding at the next sync).

### Symptom: a policy silently never fires
- **Check:** `kubectl get validatingadmissionpolicy <name> -o jsonpath='{.status.typeChecking}'`.
  An `undefined field` warning means a path in the expression does not exist in the resource's
  schema, so the expression can never be true as written.
- **Check:** the binding exists (`kubectl get validatingadmissionpolicybinding`) and its
  `matchResources` / the policy's `matchConstraints` cover the resource and namespace.

## How-to
### List current workload-hygiene violations
- Dry-run the live object back through the API server; each violation prints as a warning.
  One object per call, because kubectl de-duplicates identical warnings:
  `kubectl get deploy -n <ns> <name> -o yaml | kubectl replace --dry-run=server -f -`
- `kubectl get policyreports -A`: native results carry `source: ValidatingAdmissionPolicy`.
  A policy appears there only if labelled `reports.kyverno.io/enabled: "true"`; a new or
  changed policy triggers a rescan of all resources.

### Add or change a policy
1. Write the policy and binding in `base-apps/admission-policies/`. New policies start in
   shadow (`[Warn, Audit]`, `failurePolicy: Ignore`).
2. Add fixtures: at least one `bad/<policy-name>/` case **per kind the policy matches** (the
   static tests enforce it) and a `good/` case, in `tests/admission-policies/fixtures/<suite>/`.
3. `python3 -m pytest tests/admission-policies/` (needs Docker; CI runs it too).
4. After merge, check `status.typeChecking` on the cluster, and compare the shadow warnings
   against the Kyverno policy it replaces (`kubectl get policyreports -A`).
5. Flip to enforcing in a separate change: binding `[Deny]`, policy `failurePolicy: Fail`,
   delete the Kyverno policy.
