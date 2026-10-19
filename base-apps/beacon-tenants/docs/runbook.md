---
type: "Kubernetes App Runbook"
title: "Beacon Tenants — Runbook"
description: "Operational runbook for beacon's enterprise tenants: onboarding by PR, what the composition creates, and why a delete is refused."
app: beacon-tenants
catalog_entity: beacon-tenants
kind: runbook
namespace: beacon
last_reviewed: 2026-10-19
status: draft
tags: [tenancy, runbook]
sources:
  - base-apps/beacon-tenants.yaml
  - base-apps/crossplane-compositions/composition-tenant.yaml
  - base-apps/admission-policies/xtenant-guard.yaml
  - scripts/provision-beacon-tenants-vault.sh
---

# Beacon Tenants — Runbook

Everything here is a Git change. Never `kubectl apply/edit/patch/delete`; the read-only
commands below only inspect.

## How-to

### Before the first tenant (once)
`scripts/provision-beacon-tenants-vault.sh` (see `base-apps/beacon/runbook.md`). Without the
`beacon-tenant` role every tenant's SecretStore stays invalid and no secret is generated.

### Onboard an enterprise tenant
1. The customer signs up as enterprise in beacon. beacon stores a `pending` tenant and the Grafana
   alert "Beacon enterprise signup pending" fires (beacon §T.26).
2. Open the PR adding `base-apps/beacon-tenants/<slug>.yaml` (the shape is in `docs.md`), with
   `tenantId` = the pending tenant's id. Its title cites the beacon task, e.g. `feat(beacon-tenants):
   onboard <slug> (beacon §T.26)`. CI renders it: add its golden with
   `UPDATE_GOLDENS=1 python -m pytest tests/composition/test_xtenant_render.py` and review the diff.
3. Merge. Check, read-only:
   `kubectl get xtenant <slug>` (READY True), `kubectl -n tenant-<slug> get cluster,externalsecret,pushsecret`,
   `kubectl get bucket,user,accesskey -l beacon.arigsela.com/tenant=<slug>`.

### Offboard a tenant (beacon §T.27)
Two PRs, never one: the first sets `spec.offboard: true` and drops the `Prune=false,Delete=false`
annotation; the tenant's workers stop, its database is dumped to its bucket under `offboard/`,
verified, and the XR's `status.backup` is set (Job `beacon-offboard-dump` in `tenant-<slug>`).
Only then does the second PR, deleting the file, get past `xtenant-guard`. The full procedure
comes with beacon §T.27.

## Failure modes

### Symptom: Argo CD reports "admission webhook ... denied" / ValidatingAdmissionPolicy 'xtenant-guard' denied the request
- On create: the file name/XR name is not a slug (`^[a-z][a-z0-9-]{2,30}$`). Rename it.
- On delete (Argo prune): the XR has no verified backup yet. This is the guard working. Finish
  the offboarding PR first (`spec.offboard: true`) and wait for `status.backup`.

### Symptom: the XTenant stays READY False
`kubectl describe xtenant <slug>` lists the unready composed resources. The usual order:
the Namespace and SecretStore first (a SecretStore error `invalid role name` means the Vault
script was never run), then the generated secret, then the CNPG Cluster (a few minutes), then
the AWS resources (the bucket and IAM user take about a minute each).

### Symptom: the tenant's checker/notifier never appear
They are composed only after Job `beacon-init-<digest>` succeeded:
`kubectl -n tenant-<slug> logs job/beacon-init-<digest> --all-containers`. Its first container
waits for the database's superuser secret (the CNPG Cluster must be Ready); `migrate` needs the
roles; `init-tenant-db` needs a beacon-api that has the command (v0.3.0 or later).

### Symptom: a nightly dump failed
`kubectl -n tenant-<slug> logs job/<beacon-dump-nightly-…> --all-containers`. `pg-dump` stops
if the dump does not list (`pg_restore --list`); `upload` stops if the object is missing or
empty after the upload. A bucket/IAM problem shows as `AccessDenied` in `upload`.

### Symptom: a namespace `tenant-<slug>` is stuck Terminating
The PushSecret's finalizer deletes the tenant's Vault keys and needs its SecretStore and
ServiceAccount, which two Crossplane Usages keep until it is gone. If Vault is down the finalizer
waits; it finishes once Vault answers. Do not remove finalizers by hand: the Vault keys would stay.
