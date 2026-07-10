---
app: postgresql
catalog_entity: postgresql
kind: docs
namespace: postgresql
last_reviewed: 2026-07-10
status: current
tags: [database, stateful, pgvector]
sources:
  - base-apps/postgresql/deployments.yaml
  - base-apps/postgresql/services.yaml
  - base-apps/postgresql/pvc.yaml
  - base-apps/postgresql/secret-store.yaml
  - base-apps/postgresql/external-secrets.yaml
  - base-apps/postgresql/external-secrets-kagent.yaml
  - base-apps/postgresql/init-kagent-db.yaml
---

# postgresql

## What it is
A shared, plain PostgreSQL instance for the cluster — **not** CloudNativePG or any operator-managed StatefulSet. It runs as a single-replica `Deployment` (`deployments.yaml`) on image `pgvector/pgvector:0.8.2-pg18` (Postgres 18 with the `pgvector` extension available), giving other apps a single Postgres server that also supports vector columns.

## How it's deployed
`deployments.yaml` defines one `Deployment` (`replicas: 1`) scheduled onto `node.kubernetes.io/workload: application` nodes, running as the `postgres` container user (`securityContext.runAsUser/runAsGroup/fsGroup: 999`). Data lives on `/var/lib/postgresql/data` (`PGDATA=/var/lib/postgresql/data/pgdata`), backed by the `postgresql-pvc` `PersistentVolumeClaim` (`pvc.yaml`: `storageClassName: local-path`, `10Gi`, `ReadWriteOnce`). `local-path` binds the volume to whichever node first mounts it, so this Postgres pod is effectively pinned to one node — there is no failover if that node or its disk is lost. The `postgresql` `Service` (`services.yaml`, `ClusterIP`, port `5432`) is the in-cluster address other workloads use: `postgresql.postgresql.svc.cluster.local:5432`.

## Databases it provisions
- **Primary/root database** — `deployments.yaml` sets `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` from the `postgresql-credentials` Secret's `database-name`/`n8n-user`/`n8n-password` keys. Despite the `n8n-*` key naming (a holdover from this credential's original consumer), this user is the server's effective root/admin login, used by the init Job below to create further roles and databases.
- **`kagent` database** — provisioned by the `init-kagent-db` `Job` (`init-kagent-db.yaml`), which polls `pg_isready` against `postgresql.postgresql.svc.cluster.local:5432`, then idempotently `CREATE ROLE`/`CREATE DATABASE` for the `kagent` user/db (credentials from the `kagent-db-credentials` Secret) and runs `CREATE EXTENSION IF NOT EXISTS vector` inside it so `kagent` can store vector embeddings. The job is safe to re-run (existence checks before create/alter) and self-cleans after success (`ttlSecondsAfterFinished: 300`).

## Credential flow (Vault)
Two `SecretStore`-backed `ExternalSecret`s populate this namespace from the in-cluster Vault (`secret-store.yaml`: provider `vault`, `server: http://vault.vault.svc.cluster.local:8200`, KV v2 `path: k8s-secrets`, Kubernetes-auth `role: postgresql`):
- `external-secrets.yaml` → `postgresql-credentials` Secret, from Vault key `postgresql` (`root-password`, `database-name`, `n8n-user`, `n8n-password`). Note `root-password` is synced but not referenced by any manifest in this directory — it is not consumed by the Deployment or the init Job.
- `external-secrets-kagent.yaml` → `kagent-db-credentials` Secret, from Vault key `kagent` (`db-user`, `db-password`, `db-name`).

Both `ExternalSecret`s refresh hourly (`refreshInterval: 1h`) and use `creationPolicy: Owner`.

## Storage
Single 10Gi `local-path` PVC (`postgresql-pvc`) mounted at `/var/lib/postgresql/data`. There is no backup CronJob, replica, or standby in this directory — losing the PVC or its node loses all data for every database on this instance (the primary DB and `kagent`).
