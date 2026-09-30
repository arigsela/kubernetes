---
type: "Kubernetes App Runbook"
title: "Atlantis — Runbook"
description: "Operational runbook for Atlantis: apply gate, merged-before-apply, wedged workdir, OpenTofu download, secret and webhook failures."
app: atlantis
catalog_entity: atlantis
kind: runbook
namespace: atlantis
last_reviewed: 2026-09-30
status: current
tags: [terraform, opentofu, gitops, ci-cd]
sources:
  - base-apps/atlantis.yaml
  - base-apps/atlantis-config.yaml
  - atlantis.yaml
  - .github/workflows/terraform-apply.yaml
  - base-apps/atlantis/external-secrets.yaml
  - base-apps/atlantis/secret-store.yaml
  - base-apps/atlantis/httproute.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
---

# atlantis runbook

## Failure modes

### Symptom: `plan`/`apply` fails with `unable to verify checksums signature: openpgp: key expired`
This is the well-known HashiCorp release-signing-key expiry breaking
Terraform binary downloads. It's why `base-apps/atlantis.yaml` already sets
`defaultTFDistribution: opentofu` / `defaultTFVersion: "1.12.3"` instead of
downloading `terraform`.
- **Check:** `kubectl -n atlantis logs deploy/atlantis | grep -i "distribution\|checksums signature"` — confirm the pod actually rendered
  `ATLANTIS_DEFAULT_TF_DISTRIBUTION=opentofu` (the older `--tf-distribution`
  flag/env is deprecated and silently ignored by Atlantis 0.40, so a stale
  value here is the usual regression). Also check the PR's `atlantis plan`
  comment for the specific error.
- **Fix:** PR a change to `base-apps/atlantis.yaml`'s Helm `values` —
  confirm `defaultTFDistribution: opentofu` (not `tofu`) and bump
  `defaultTFVersion` only to an OpenTofu release that still satisfies
  `providers.tf`'s `>= 1.11.0` constraint.

### Symptom: plan is green but nothing applies
Apply is **not** triggered by merge, and approval/mergeability are **not**
required: the repo-level `atlantis.yaml` overrides the server-side
`[approved, mergeable]` with `apply_requirements: []`. The only gate is the
`terraform-apply` GitHub Environment on the **Terraform Apply (gated)** Action
(`.github/workflows/terraform-apply.yaml`).
- **Check:** is the PR still `OPEN`? Did someone run the Action (Actions tab),
  and is its run waiting on Environment approval? Did Atlantis reply to the
  `atlantis apply -p asela-cluster` comment it posted?
- **Fix:** run the Action with the PR number and approve the deployment, or
  comment `atlantis apply -p asela-cluster` yourself. Then wait for
  `atlantis/apply: asela-cluster` to go green **before** merging.

### Symptom: PR merged, but the change never reached AWS/the cluster
Merged before apply. Atlantis dropped `plan.tfplan` and the locks when the PR
closed, and the Apply Action refuses a non-`OPEN` PR
(`PR #N is not OPEN (state=MERGED)`). No check reports anything, so the PR
looks green and merged.
- **Check:** the PR's checks show `atlantis/plan` but no successful
  `atlantis/apply`; the live resource still has the old value (e.g.
  `kubectl -n argo-cd get cm argocd-cm -o jsonpath='{.data.admin\.enabled}'`
  for an Argo CD setting).
- **Fix:** open a **fresh PR that touches a `.tf` file** (a comment change is
  enough) so autoplan runs against `main`, which already contains the merged
  change. Apply it on that open PR, then merge. Do not `tofu apply` from a
  laptop; the Environment gate and Atlantis's credentials are deliberate.

### Symptom: plan fails with `... is currently locked for this pull request by "plan"` or `getwd: no such file or directory`
Two pushes in quick succession. Atlantis serializes per project directory; the
second plan hits the first one's lock, and the re-clone can leave the first
process inside a deleted checkout. The workdir stays wedged, and re-running
`atlantis plan` reuses it. Neither error is about the Terraform code.
- **Fix:** comment `atlantis unlock` on the PR (releases locks and discards
  plans), **then** `atlantis plan`. A bare re-plan won't fix it.
- **Prevent:** batch all commits for a Terraform PR into **one push**, and don't
  push again until `atlantis/plan: asela-cluster` has finished.

### Symptom: Atlantis doesn't react to PRs/comments at all (webhooks)
- **Check:** GitHub repo settings → Webhooks → recent deliveries. A `403` means
  the delivering address isn't in the `atlantis.arigsela.com` rule of
  `base-apps/istio-ingress/authorizationpolicy.yaml`: GitHub changed its hook
  ranges (compare with `https://api.github.com/meta`), or the WAN IP rotated
  and broke every host at once. TLS failures: `kubectl -n atlantis get
  certificate atlantis-tls`. A signature error in the Atlantis logs means the
  webhook secret in Vault (`atlantis/webhook`) no longer matches GitHub's.
- **Fix:** update the CIDRs in `authorizationpolicy.yaml` via PR (the
  `istio-ingress` runbook covers WAN rotation). Webhook-secret drift: fix the
  Vault value, then let ESO resync. Don't lean on
  `base-apps/atlantis/network-policy.yaml` while debugging; it still names
  the removed `nginx-ingress` namespace and doesn't describe the real path
  (see docs.md, known issue).

### Symptom: `plan`/`apply` fails on GitHub auth, AWS auth, or a `TF_VAR_*` value
The chart sources GitHub (`vcsSecretName: atlantis-vcs`) and
AWS/Infracost/Kubernetes-provider credentials
(`environmentSecrets` → `atlantis-env`) entirely from Vault via
`base-apps/atlantis/external-secrets.yaml` / `secret-store.yaml`.
- **Check:** `kubectl -n atlantis get externalsecret atlantis-vcs atlantis-env` (look for `SecretSynced` status) and
  `kubectl -n atlantis get secretstore vault-backend -o yaml`. A `SecretSyncedError` here means Vault (role `atlantis`,
  `k8s-secrets` KV v2 path) doesn't have current values at `atlantis/github`,
  `atlantis/webhook`, `atlantis/aws`, `atlantis/infracost`, or `atlantis/k8s`.
- **Fix:** rotate/update the relevant value in Vault at the affected
  `remoteRef.key`/`property` (see `external-secrets.yaml` for the exact
  mapping); ExternalSecrets Operator re-syncs on its `refreshInterval: 1h` or
  can be forced sooner. If the `SecretStore` itself is failing, verify
  Vault's `atlantis` Kubernetes-auth role/policy grants `k8s-secrets` read
  (see `base-apps/vault` runbook for Vault-side auth checks).

### Symptom: Atlantis pod Pending
- **Check:** `kubectl -n atlantis get pods -o wide`, then `describe` the Pending pod. It is pinned to the control-plane node
  (`node.kubernetes.io/workload: infrastructure`) and its 5Gi `local-path`
  PVC `atlantis-data` is bound to that node.
- **Fix:** restore the control-plane node. Don't delete the PVC to "unstick" it;
  that discards the working directory, including saved plans and locks. The
  Application has `prune: false` so Argo never deletes it for you either.

## How-to
### Upgrade the chart or image
Edit `targetRevision` / `image.tag` in `base-apps/atlantis.yaml` and PR. Argo
syncs it on merge (this is GitOps, not Terraform). Because the Application has
`prune: false`, check for objects the new chart version no longer renders and
delete them by hand.
