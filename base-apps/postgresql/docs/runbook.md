---
type: "Kubernetes App Runbook"
title: "PostgreSQL — Runbook"
description: "Operational runbook for PostgreSQL: failure modes, checks, and fixes."
app: postgresql
catalog_entity: postgresql
kind: runbook
namespace: postgresql
last_reviewed: 2026-07-10
status: current
tags: [database, stateful, pgvector]
sources:
  - base-apps/postgresql/deployments.yaml
  - base-apps/postgresql/external-secrets.yaml
  - base-apps/postgresql/init-kagent-db.yaml
  - base-apps/postgresql/pvc.yaml
---

# postgresql runbook

## Failure modes

### Symptom: postgresql pod stuck in `CreateContainerConfigError` / never Ready
`deployments.yaml` sources `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` entirely from the `postgresql-credentials` Secret, which is populated by an `ExternalSecret` (`external-secrets.yaml`) from Vault key `postgresql`. If that `ExternalSecret` hasn't synced (Vault sealed/unreachable, or the `postgresql` Kubernetes-auth role/policy in `secret-store.yaml` is wrong), the Secret never exists and the pod can't start.
- **Check:** `kubectl -n postgresql get externalsecret postgresql-credentials` (look at `STATUS`/`READY`) and `kubectl -n postgresql get secret postgresql-credentials`; then `kubectl -n postgresql describe pod -l app=postgresql` for the exact `CreateContainerConfigError` reason.
- **Fix:** if Vault itself is sealed/down, that's the `vault` app's runbook, not this one. If Vault is healthy but this namespace's `ExternalSecret` still fails, the `SecretStore`'s `auth.kubernetes.role: postgresql` (`secret-store.yaml`) likely doesn't match the Vault role/policy for this namespace — open a PR correcting the role name or Vault-side policy binding.

### Symptom: `init-kagent-db` Job fails / hits `BackoffLimitExceeded`, kagent has no database
`init-kagent-db.yaml` runs once to create the `kagent` role/database and enable `pgvector`, using both `postgresql-credentials` (root user) and `kagent-db-credentials` (from `external-secrets-kagent.yaml`, Vault key `kagent`). It loops on `pg_isready` before creating anything, but if either Secret is missing/stale when the Job's `backoffLimit: 5` is exhausted, or the root user's password rotated in Vault without the pod restarting, the SQL step fails.
- **Check:** `kubectl -n postgresql get job init-kagent-db` and `kubectl -n postgresql logs job/init-kagent-db --all-containers`; also confirm both `kubectl -n postgresql get secret postgresql-credentials kagent-db-credentials` exist.
- **Fix:** delete the failed Job so Argo CD (`selfHeal`) recreates it once both Secrets are present and valid (`kubectl -n postgresql delete job init-kagent-db`) — this is a live action, not a manifest change, so only do it after confirming the Secrets are correct. If the recurring cause is a race between Secret sync and Job start, open a PR adding an explicit wait/retry or an `initContainer` check against the Secrets.

### Symptom: postgresql pod stuck `Pending` after a node failure/drain
Storage is a single `local-path` `PersistentVolumeClaim` (`pvc.yaml`, `10Gi`, `ReadWriteOnce`), which `local-path-provisioner` binds to whichever node first created it. The `Deployment` also has `nodeSelector: node.kubernetes.io/workload: application` (`deployments.yaml`). There is no StatefulSet, replica, or backup — this is a single point of failure for every database on the instance (root DB and `kagent`).
- **Check:** `kubectl -n postgresql get pod -l app=postgresql -o wide` (look for `Pending`/`ContainerCreating` and the assigned node) and `kubectl -n postgresql get pvc postgresql-pvc -o wide` to see which node the underlying `local-path` volume lives on.
- **Fix:** if the original node is truly gone, the local-path volume's data is gone with it — there is no cross-node replica to fail over to. Recovery means recreating `postgresql-pvc` from whatever external backup exists (none is defined in this directory) and restoring dumps manually; longer term, open a PR to add a periodic `pg_dump` CronJob or move to a replicated/operator-managed Postgres if this instance's availability requirements have grown.
