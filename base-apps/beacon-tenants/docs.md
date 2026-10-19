---
type: "Kubernetes App Guide"
title: "Beacon Tenants"
description: "beacon's enterprise tenants: one Crossplane XTenant per file, each composed into its own namespace, database, bucket and Vault folder"
app: beacon-tenants
catalog_entity: beacon-tenants
kind: docs
namespace: beacon
last_reviewed: 2026-10-19
status: draft
tags: [tenancy, crossplane, multi-tenant]
sources:
  - base-apps/beacon-tenants.yaml
  - base-apps/crossplane-compositions/xrd-tenant.yaml
  - base-apps/crossplane-compositions/composition-tenant.yaml
  - base-apps/crossplane-compositions/composition-rbac.yaml
  - base-apps/admission-policies/xtenant-guard.yaml
  - base-apps/beacon/jobs.yaml
  - scripts/provision-beacon-tenants-vault.sh
  - tests/composition/test_xtenant_render.py
  - tests/beacon/test_tenants.py
---

# Beacon Tenants

## What it is

beacon (`base-apps/beacon/`) serves free tenants from one pooled database under row-level
security. An **enterprise** tenant instead gets its own stack, and nobody writes any of it by
hand: one file here, an `XTenant` named after the tenant's slug, is the whole tenant.

```yaml
apiVersion: platform.arigsela.com/v1alpha1
kind: XTenant
metadata:
  name: <slug>                 # ^[a-z][a-z0-9-]{2,30}$, enforced by xtenant-guard
  annotations:
    argocd.argoproj.io/sync-options: Prune=false,Delete=false
spec:
  tenantId: <uuid>             # the pending row beacon's signup created
```

The `tenant` Composition (`base-apps/crossplane-compositions/composition-tenant.yaml`)
composes, for each one:

| What | Where |
|---|---|
| Namespace `tenant-<slug>` (ambient; workload defaults; OTel `python`) | cluster |
| ResourceQuota (1Gi requests, 2Gi limits) + LimitRange defaults | `tenant-<slug>` |
| default-deny ingress, CNPG operator → database (8000) | `tenant-<slug>` |
| CNPG Cluster `beacon-db` (1 instance, `ghcr.io/cloudnative-pg/postgresql:16.4`, out of the mesh) | `tenant-<slug>` |
| ESO `Password` generators → Secret `beacon-db-secrets` (created once) | `tenant-<slug>` |
| PushSecret → Vault `k8s-secrets/tenants/tenant-<slug>/{db,signing,api}` | `tenant-<slug>` |
| S3 bucket `beacon-tenant-<slug>-<first 8 hex of tenantId>` (Orphan), IAM user + policy + key → Secret `beacon-s3` | AWS |
| beacon's own stack, at the release `base-apps/beacon` runs: `beacon-scheduler` (+ `-daily`) CronJobs, `beacon-checker`, `beacon-notifier` (+ Service), flags (all off), an OTel `Instrumentation` | `tenant-<slug>` |
| Job `beacon-init-<api digest>`: roles (as the CNPG superuser), migrations, the tenant's own `tenants` row | `tenant-<slug>` |
| `probe-egress` (beacon's, with this namespace's database and notifier), checker → notifier, stack → database | `tenant-<slug>` |
| CronJob `beacon-dump-nightly`: `pg_dump` → `s3://<bucket>/nightly/` (kept 14 days), checked before and after the upload | `tenant-<slug>` |

The stack starts in order, from what the composition observes: the init Job once the database
is Ready, the workers once that release's init Job succeeded. A promotion (`base-apps/beacon/runbook.md`)
moves every tenant: a new init Job migrates its database first, and the workers keep their old
images until it succeeds. beacon-api's route to the tenant's database comes with beacon §T.45.

## Safety

- **Nothing deletes a tenant by accident.** Each XR carries `Prune=false,Delete=false` (removing
  the file, or deleting this Application, leaves it), this Application has no cascade finalizer,
  and the `xtenant-guard` ValidatingAdmissionPolicy refuses every delete unless
  `spec.offboard: true` and `status.backup` names a verified `s3://` dump (beacon §V.12).
- **The bucket survives the tenant** (`deletionPolicy: Orphan`). A slug is never reused (beacon
  keeps the tenant row as a tombstone) and the bucket name carries the tenant id (beacon §V.49).
- **One Composition renders every tenant**, and a change reaches all of them at once. CI renders
  every committed XTenant and each fixture with the pinned `crossplane` CLI and function-python
  and compares it with `tests/composition/xtenant/<case>/expected.yaml`; a Namespace, CNPG Cluster or
  Bucket that disappears or is renamed fails the build (beacon §V.50).
- **Vault**: one role for every tenant, `beacon-tenant` (SA `eso-tenant` in `tenant-*`), reaching
  only `k8s-secrets/tenants/<its namespace>/*`. Provisioned once by
  `scripts/provision-beacon-tenants-vault.sh` (beacon §V.52). The PushSecret deletes the tenant's
  keys when it goes; two Crossplane Usages keep its SecretStore and ServiceAccount until then.
- **Crossplane's extra rights** (`crossplane:compositions:tenant`): namespaces, quotas,
  LimitRanges and NetworkPolicies cluster-wide, plus CNPG and ESO kinds. The composition only
  ever names `tenant-<slug>`, which CI checks.

## Budget

At most 2 enterprise tenants while beacon runs on the homelab (beacon §V.51). Each requests about
0.5 GiB once its stack runs (database 256Mi, checker and notifier 128Mi each).

## Known limits

- The database is one instance on local-path storage. Nightly `pg_dump`s to the bucket are the
  backup; a node loss loses at most a day. Channel secrets in a dump are sealed with the
  tenant's signing key, which offboarding deletes from Vault: a restored dump keeps its monitors
  and history, but its webhook channels need their secrets again.
- A new namespace receives the `ecr-registry` pull secret on the next `ecr-auth` run (≤ 15 min),
  so the tenant's first image pulls may back off for a while.
