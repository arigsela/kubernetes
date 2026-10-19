---
type: "Kubernetes App Runbook"
title: "admission-policies — Runbook"
description: "Operational runbook for the native admission policies: blocked changes, audit warnings, rollout."
app: admission-policies
catalog_entity: admission-policies
kind: runbook
namespace: kube-system
last_reviewed: 2026-10-19
status: stable
tags: [admission, cel, policy, security]
sources:
  - base-apps/admission-policies/agent-identity.yaml
  - base-apps/admission-policies/agent-capability.yaml
  - base-apps/admission-policies/agent-capability-taxonomy.yaml
  - base-apps/admission-policies/agent-definitions-from-git.yaml
  - base-apps/admission-policies/inject-ecr-pull-secret.yaml
  - base-apps/admission-policies/kyverno-reports-rbac.yaml
  - scripts/gen-agent-capability-policy.py
  - scripts/gen-agent-audit-cronjob.py
  - scripts/validate-agent-capability.py
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
  `scripts/gen-agent-capability-policy.py`, then regenerate (the YAML is generated).
  `agent-definitions-from-git` is hand-written: edit its YAML, and only under the precondition
  in "Break glass" below (no pinned byo digest yet). Add a fixture that
  reproduces the false positive to `tests/admission-policies/fixtures/<suite>/good/`.
- **Emergency (a policy blocks everything and git can't sync):**
  `kubectl delete validatingadmissionpolicybinding <name>`. That disables the policy at once,
  and Argo re-creates it at the next sync, so fix git first.

### Symptom: denied by `agent-definitions-from-git`: "`<Kind> '<name>' was created|changed directly by '<user>'`"
- An `Agent`, `AgentTemplate`, `Harness` or `RemoteMCPServer` was written by someone other than
  Argo CD's application controller: `kubectl apply/edit/patch` (x509 admin or Dex login), the
  kagent UI or API (they write as `kagent-controller`), or the Argo CD UI's live edit
  (`argocd-server`). That is the policy working: agent definitions come only from git, where CI
  checks what admission cannot (SPEC §V.101).
- **Fix:** make the change in git. In an emergency, break glass (How-to below).
- **If an Argo CD sync of `kagent-secrets` or `kagent` is denied** with this message, Argo CD is
  writing under another identity than `system:serviceaccount:argo-cd:argocd-application-controller`
  (the message names it). Today it syncs with its pod's own token: sync impersonation is off
  (`application.sync.impersonation.enabled` in `argocd-cm`) and there is no in-cluster cluster
  Secret (`kubectl -n argo-cd get secrets -l argocd.argoproj.io/secret-type=cluster`). If either
  changed, change the username in `agent-definitions-from-git.yaml` and `ARGO` in
  `tests/admission-policies/conftest.py` together.
- An update that changes only finalizers or ownerReferences (the garbage collector in a
  foreground prune) is never denied by it. An object stuck `Terminating` has another cause.

### Symptom: a byo `Harness` is denied by `agent-capability-runtime` ("not exactly an entry of the taxonomy's byo-images")
- Its `workload.image` must equal an entry of `byo-images` in
  `agent-capability-taxonomy.yaml` character for character: the same registry, repository and
  digest, no tag. A new image build is a new digest, so it needs a new entry: "Pin a byo image
  digest" below.

### Symptom: an AgentTemplate write is denied with `failed to configure binding: paramKind ... not yet synced`
- `agent-capability-delegation` could not evaluate yet: the API server has not synced its
  AgentTemplate informer (the policy's param kind since kagent 1.0), which happens for a few
  seconds after the policy is (re)created and after every API-server restart (k3s restart,
  upgrade). With `failurePolicy: Fail` that denies read/write-class templates.
- **Fix:** retry (Argo does on its own; `scripts/hop-verify.sh` rides it out). If it persists
  beyond a minute, check the API server is healthy (`kubectl get --raw /readyz`) and the CRD is
  served (`kubectl get agenttemplates.api.kagent.dev -A`).

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

### Symptom: PolicyReports have no results for kagent objects (agent-capability, agent-capability-refs, agent-capability-runtime, agent-identity-mcp-toolnames)
- Kyverno's reports controller evaluates the agent policies in the background, so it must
  list and watch kagent 1.0's `api.kagent.dev` `agenttemplates`, `agents` and `harnesses`.
  That access comes only from the `kyverno:read-kagent-agents` ClusterRole in
  `kyverno-reports-rbac.yaml`, which aggregates into the reports controller via the
  `rbac.kyverno.io/aggregate-to-reports-controller: "true"` label. Without it the controller
  reports nothing for them; admission is unaffected (the API server evaluates the policies
  itself).
- **Check:** `kubectl get clusterrole kyverno:read-kagent-agents`, and
  `kubectl auth can-i list agenttemplates.api.kagent.dev --as=system:serviceaccount:kyverno:kyverno-reports-controller`.
  The controller's log names the missing permission (`requires permissions get,list,watch for
  resource …`).
- **Also check** the policy carries `reports.kyverno.io/enabled: "true"` (docs.md,
  "Reporting"). `agent-capability-delegation` and `agent-definitions-from-git` deliberately do
  not, so they never appear.

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

### Break glass: write an agent definition directly
`agent-definitions-from-git` admits a direct write from an identity in the group
`homelab:kagent-break-glass`. No credential carries it; you join it for one command by
impersonation, which only cluster-admin may do (the x509 admin kubeconfig, or the Dex login
`oidc:arigsela`). Each such request is an impersonated one, so it is visible as such in the
API server's audit trail when auditing is on.
```bash
# x509 admin context (the default):
kubectl --as=system:admin --as-group=system:masters --as-group=homelab:kagent-break-glass \
  apply -f base-apps/kagent/agent-templates/<name>.yaml
# Dex context:
kubectl --context homelab-oidc --as=oidc:arigsela --as-group=homelab:kagent-break-glass apply -f ...
```
Argo CD's `selfHeal` puts git's version back at its next sync of `kagent-secrets`, so land the
same change in git (or pause auto-sync on that app) before the edit is reverted. If the policy
itself is broken: `kubectl delete validatingadmissionpolicybinding agent-definitions-from-git`
(Argo re-creates it at the next sync, so fix git first).

**To soak it instead of enforcing** (shadow: it warns and audits, blocks nothing): in
`base-apps/admission-policies/agent-definitions-from-git.yaml` (hand-written; there is no
`ENFORCE` constant for it) set `failurePolicy: Ignore` at line 62 and
`validationActions: [Warn, Audit]` at line 103. **Precondition:** soak only while `byo-images`
holds no pinned digest (every entry still ends in `REPLACE_WITH_DIGEST`) and before any byo
Agent syncs, so in practice land the policy on its own before the byo change. The byo admission
(SPEC §V.101) is safe only while git is the only writer of agent definitions, so flip back to
`[Deny]` and `failurePolicy: Fail` before a digest is pinned.

### Pin a byo image digest (or admit another byo image)
A `byo` runtime is admitted only on an image listed **exactly** in `byo-images`
(`agent-capability-taxonomy.yaml`, SPEC §V.101). Until the docs-checker image is pushed, its
entry ends in `@sha256:REPLACE_WITH_DIGEST`: the CRD rejects that, and
`scripts/validate-agent-capability.py` fails on it, so CI is red and nothing deploys on it.
1. Build and push the image (`arigsela/claude-agents` `docs-checker/`) to
   `852893458518.dkr.ecr.us-east-2.amazonaws.com/docs-checker`, then read its digest:
   `aws ecr describe-images --region us-east-2 --repository-name docs-checker --query 'imageDetails[].[imageDigest,imageTags]'`.
2. Put the digest in the image string in the taxonomy, the good fixture and the docs-checker
   Harness once it exists (`test_byo_fixture_images_are_listed` and the validator check that
   they agree):
   ```bash
   D=<64 hex chars, without the sha256: prefix>
   sed -i '' "s/docs-checker@sha256:REPLACE_WITH_DIGEST/docs-checker@sha256:$D/" \
     base-apps/admission-policies/agent-capability-taxonomy.yaml \
     tests/admission-policies/fixtures/agent-capability/good/good-byo-docs-checker.yaml \
     base-apps/kagent/harnesses/<the docs-checker Harness>.yaml
   ```
3. Regenerate what embeds the taxonomy, then validate:
   ```bash
   python3 scripts/gen-agent-capability-policy.py --repo-root .
   python3 scripts/gen-agent-audit-cronjob.py --repo-root .
   python3 scripts/validate-agent-capability.py --repo-root .
   python3 -m pytest tests/agent-capability/ tests/admission-policies/ -q
   ```
A later image is a NEW entry (keep the old one until no Harness runs it), never a repository or
a tag: a repository would admit every digest pushed there later. Every entry is a reviewed
image, and the Agents on it still have to satisfy invariant 7 in CI (class `read`, only
`read-only-servers`, only read tools). Adding a server to `read-only-servers` needs a
server-side read-only proof in `tests/agent-capability` (`READ_ONLY_PROOFS`).

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
