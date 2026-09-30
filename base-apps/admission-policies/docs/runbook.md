---
type: "Kubernetes App Runbook"
title: "admission-policies — Runbook"
description: "Operational runbook for the native admission policies: blocked changes, audit warnings, rollout."
app: admission-policies
catalog_entity: admission-policies
kind: runbook
namespace: kube-system
last_reviewed: 2026-09-30
status: current
tags: [admission, cel, policy, security]
sources:
  - base-apps/admission-policies/agent-identity.yaml
  - base-apps/admission-policies/agent-capability.yaml
  - base-apps/admission-policies/agent-capability-taxonomy.yaml
  - base-apps/admission-policies/inject-ecr-pull-secret.yaml
  - base-apps/admission-policies/kyverno-reports-rbac.yaml
  - scripts/gen-agent-capability-policy.py
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
  `failurePolicy: Ignore` on the policy) in git, or `git revert` the flip. For
  `agent-capability*` that means the `ENFORCE` constant in
  `scripts/gen-agent-capability-policy.py`, then regenerate (the YAML is generated). Add a fixture that
  reproduces the false positive to `tests/admission-policies/fixtures/<suite>/good/`.
- **Emergency (a policy blocks everything and git can't sync):**
  `kubectl delete validatingadmissionpolicybinding <name>`. That disables the policy at once,
  and Argo re-creates it at the next sync, so fix git first.

### Symptom: an Agent write is denied with `failed to configure binding: paramKind ... not yet synced`
- `agent-capability-delegation` could not evaluate yet: the API server has not synced its
  Agent informer, which happens for a few seconds after the policy is (re)created and after
  every API-server restart (k3s restart, upgrade). With `failurePolicy: Fail` that denies.
- **Fix:** retry (Argo does on its own). If it persists beyond a minute, check the API server
  is healthy (`kubectl get --raw /readyz`) and the Agent CRD is served
  (`kubectl get agents -A`).

### Symptom: `Warning: Validation failed for ValidatingAdmissionPolicy '<name>'` on apply
- A policy in **shadow** (`[Warn, Audit]`) flagged the object. Nothing was blocked. Once it
  moves to enforcing, the same object will be **denied**, so fix it now. (The agent policies,
  `agent-identity-*` and `agent-capability*`, are enforcing: they deny rather than warn.)
- A workload-hygiene policy (`disallow-latest-tag`, `disallow-privileged-containers`,
  `require-resource-limits`, `require-labels`, `disallow-default-namespace`) flagged it. These
  are audit-only permanently and never block. The message names the offending images or
  containers; fix them in the app's manifests when convenient.

### Symptom: a Pod pulling from ECR is in `ImagePullBackOff` with `no basic auth credentials` / 401
- **Check:** `kubectl get pod -n <ns> <pod> -o jsonpath='{.spec.imagePullSecrets}'`. It must
  list `ecr-registry`; `inject-ecr-pull-secret` adds it at pod creation.
- **Check:** the pod's namespace is not one the policy excludes (`kube-system`, `kube-public`,
  `kube-node-lease`, `kyverno`), and the pod was created after the policy existed (it acts
  only at creation). Reproduce with a dry run:
  `kubectl run probe -n <ns> --image=<the ECR image> --dry-run=server -o jsonpath='{.spec.imagePullSecrets}'`.
  `failurePolicy` is `Ignore`, so a policy error yields a pod without the secret rather than
  a rejected pod.
- **Check:** the `ecr-registry` Secret exists in the namespace and is fresh. The
  `ecr-credentials-sync` CronJob (`base-apps/ecr-auth/`) writes it into every non-system
  namespace every 15 minutes, so a brand-new namespace can lack it for up to 15 minutes.
  To create it at once: `kubectl create job -n kube-system --from=cronjob/ecr-credentials-sync ecr-sync-now`.

### Symptom: Pod creation fails with a 500 / `stream error ... INTERNAL_ERROR`
- A mutating policy expression may be panicking the API server. That fails the request
  **whatever the failurePolicy** (see docs.md gotchas). Look for `Observed a panic` in the
  k3s journal on the control plane (`journalctl -u k3s | grep -A5 'Observed a panic'`).
- **Emergency:** `kubectl delete mutatingadmissionpolicybinding <name>`, then fix git (Argo
  re-creates the binding at the next sync).

### Symptom: PolicyReports have no results for Agents (agent-capability, agent-identity-mcp-toolnames)
- Kyverno's reports controller evaluates the agent policies in the background, so it must
  list and watch `kagent.dev` Agents. That access comes only from the
  `kyverno:read-kagent-agents` ClusterRole in `kyverno-reports-rbac.yaml`, which aggregates
  into the reports controller via the `rbac.kyverno.io/aggregate-to-reports-controller: "true"`
  label. Without it the controller reports nothing for Agents; admission is unaffected (the
  API server evaluates the policies itself).
- **Check:** `kubectl get clusterrole kyverno:read-kagent-agents`, and
  `kubectl auth can-i list agents.kagent.dev --as=system:serviceaccount:kyverno:kyverno-reports-controller`.
  The controller's log names the missing permission (`requires permissions get,list,watch for
  resource kagent.dev/v1alpha2/Agent`).
- **Also check** the policy carries `reports.kyverno.io/enabled: "true"` (docs.md,
  "Reporting"). `agent-capability-delegation` deliberately does not, so it never appears.

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
4. After merge, check `status.typeChecking` on the cluster, then let the policy soak in
   shadow: review its warnings and results (`kubectl get policyreports -A`, source
   `ValidatingAdmissionPolicy`) against the live objects it matches, and dry-run the live
   objects back through it (see "List current workload-hygiene violations").
5. Flip to enforcing in a separate change: binding `[Deny]`, policy `failurePolicy: Fail`
   (for `agent-capability*`, the `ENFORCE` constant in `scripts/gen-agent-capability-policy.py`,
   then regenerate). Workload-hygiene policies stay audit-only.
