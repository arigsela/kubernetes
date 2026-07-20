---
type: "Kubernetes App Runbook"
title: "Atlantis — Runbook"
description: "Operational runbook for Atlantis: failure modes, checks, and fixes."
app: atlantis
catalog_entity: atlantis
kind: runbook
namespace: atlantis
last_reviewed: 2026-07-10
status: current
tags: [terraform, opentofu, gitops, ci-cd]
sources:
  - base-apps/atlantis.yaml
  - base-apps/atlantis-config.yaml
  - base-apps/atlantis/external-secrets.yaml
  - base-apps/atlantis/secret-store.yaml
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

### Symptom: `apply` never runs even though `plan` succeeded
`repoConfig` in `base-apps/atlantis.yaml` sets server-side
`apply_requirements: [approved, mergeable]` for
`github.com/arigsela/kubernetes` — Atlantis refuses `apply` until the PR is
both approved and mergeable (no failing checks, no conflicts).
- **Check:** the PR's review/mergeable status on GitHub, and
  `kubectl -n atlantis logs deploy/atlantis | grep -i "apply_requirements\|not mergeable\|not approved"`.
- **Fix:** get the PR approved and green, or PR a change to `repoConfig` if
  the requirement itself needs adjusting (it's in `allowed_overrides`, so a
  PR can locally override `apply_requirements` in its own `atlantis.yaml` if
  that's genuinely warranted).

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
