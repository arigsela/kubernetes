---
type: "Kubernetes App Guide"
title: "PostgreSQL"
description: "Shared PostgreSQL + pgvector instance (root, kagent, homelab_agent DBs; no backup) + 2-instance CNPG cluster for donetick (daily S3 backups)"
app: postgresql
catalog_entity: postgresql
kind: docs
namespace: postgresql
last_reviewed: 2026-09-30
status: current
tags: [database, stateful, pgvector, cnpg]
sources:
  - base-apps/postgresql.yaml
  - base-apps/postgresql/deployments.yaml
  - base-apps/postgresql/services.yaml
  - base-apps/postgresql/pvc.yaml
  - base-apps/postgresql/secret-store.yaml
  - base-apps/postgresql/external-secrets.yaml
  - base-apps/postgresql/external-secrets-kagent.yaml
  - base-apps/postgresql/init-kagent-db.yaml
  - base-apps/postgresql/init-homelab-agent-db.yaml
  - base-apps/postgresql/homelab-agent-db-external-secret.yaml
  - base-apps/postgresql/cnpg-cluster.yaml
  - base-apps/postgresql/cnpg-scheduled-backup.yaml
  - base-apps/postgresql/external-secrets-cnpg-backup.yaml
  - base-apps/postgresql/donetick-database.yaml
  - base-apps/postgresql/external-secrets-donetick.yaml
  - base-apps/postgresql/init-kagent-audit-role.yaml
  - base-apps/postgresql/init-agent-audit-web-role.yaml
  - base-apps/postgresql/external-secrets-kagent-audit.yaml
  - base-apps/postgresql/external-secrets-agent-audit-web-db.yaml
  - base-apps/postgresql/agent-audit-cronjob.yaml
  - scripts/gen-agent-audit-cronjob.py
  - base-apps/cnpg-system.yaml
  - docs/troubleshooting/cnpg-managed-roles-inert.md
  - scripts/pg-backup.sh
---

# postgresql

## What it is
Two separate PostgreSQL instances share this namespace:

1. A shared, plain PostgreSQL instance — a single-replica `Deployment` (`deployments.yaml`) on image `pgvector/pgvector:0.8.2-pg18` (Postgres 18 with the `pgvector` extension available), giving other apps a single Postgres server that also supports vector columns. Address: `postgresql.postgresql.svc.cluster.local:5432`. Consumers include kagent, the homelab-agent memory store, agent-audit-web (read-only), Backstage (`base-apps/backstage/configmaps.yaml`) and, per the header of `cnpg-cluster.yaml`, n8n (n8n's DB host comes from Vault, so git can't confirm it).
2. A CloudNativePG-managed cluster `postgresql-cluster` (`cnpg-cluster.yaml`, Postgres 16.4, **2 instances** since 2026-09-30), which backs **donetick** at `postgresql-cluster-rw.postgresql.svc.cluster.local:5432` (`base-apps/donetick/configmap.yaml`). It was created 2025-12-03 by a since-deleted Argo CD Application (`postgresql-cnpg`) and adopted into this app on 2026-07-15. Unlike the plain Deployment, it has daily S3 backups (below). Its bootstrap database/owner is `n8n` (historical). The `chores_tracker` database and `chores_user` role inside it are **orphaned**: chores-tracker was removed on 2026-08-01, they were created by hand, and they aren't declared in Git. They still take up space and are still in every backup.

## How it's deployed
**Plain instance.** `deployments.yaml` defines one `Deployment` (`replicas: 1`) scheduled onto `node.kubernetes.io/workload: application` nodes, running as the `postgres` container user (`securityContext.runAsUser/runAsGroup/fsGroup: 999`). Data lives on `/var/lib/postgresql/data` (`PGDATA=/var/lib/postgresql/data/pgdata`), backed by the `postgresql-pvc` `PersistentVolumeClaim` (`pvc.yaml`: `storageClassName: local-path`, `10Gi`, `ReadWriteOnce`). `local-path` binds the volume to whichever node first mounts it, so this Postgres pod is effectively pinned to one node — there is no failover if that node or its disk is lost. The `postgresql` `Service` (`services.yaml`, `ClusterIP`, port `5432`) is the in-cluster address.

**CNPG cluster** (`cnpg-cluster.yaml`; the comments there are the design record):
- `instances: 2`, a primary plus one streaming replica. Placement: `nodeSelector: node.kubernetes.io/workload: application` (both workers, never the control plane) with **required** pod anti-affinity on `kubernetes.io/hostname`, so the two instances always sit on different workers. Each instance has its own 20Gi `local-path` volume bound to its node. The operator never moves a volume; it promotes the instance that lives elsewhere.
- **Replication is asynchronous** (CNPG default). A planned switchover loses nothing. An unplanned primary crash fails over to the replica and can lose the last transactions that hadn't streamed yet.
- **Drains:** the operator's primary PDB (minAvailable 1) blocks evicting the primary directly. With a replica available, the operator switches the primary to the other worker and the database stays up through a worker drain/reboot (e.g. `ansible/playbooks/patch.yml`). With `instances: 1` that PDB made every drain of the primary's node fail, which is why the second instance was added.
- `smartShutdownTimeout: 30`: shutdown waits at most 30s for clients (donetick's pooled connections never leave on their own), then disconnects them. The 180s default turned an operator upgrade into a ~3m outage.
- Services the operator creates: `postgresql-cluster-rw` (primary; what donetick uses), plus `-ro`/`-r` for replicas. To find the primary pod, use `kubectl -n postgresql get pods -l cnpg.io/cluster=postgresql-cluster,cnpg.io/instanceRole=primary`.
- No superuser secret (`enableSuperuserAccess` unset, so false): only the operator creates roles and databases here. The psql-Job pattern used on the plain instance doesn't work against this cluster.

## Databases it provisions
- **Primary/root database** (plain instance) — `deployments.yaml` sets `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` from the `postgresql-credentials` Secret's `database-name`/`n8n-user`/`n8n-password` keys. Despite the `n8n-*` key naming (a holdover from this credential's original consumer), this user is the server's effective root/admin login, used by the init Jobs below to create further roles and databases.
- **`kagent` database** (plain instance) — provisioned by the `init-kagent-db` `Job` (`init-kagent-db.yaml`), which polls `pg_isready` against `postgresql.postgresql.svc.cluster.local:5432`, then idempotently `CREATE ROLE`/`CREATE DATABASE` for the `kagent` user/db (credentials from the `kagent-db-credentials` Secret) and runs `CREATE EXTENSION IF NOT EXISTS vector` inside it so `kagent` can store vector embeddings.
- **`homelab_agent` database** (plain instance) — provisioned the same way by `init-homelab-agent-db.yaml`: role and database `homelab_agent`, password from `homelab-agent-db-credentials`, `pgvector` enabled. It's the homelab-agent's memory store (`MEMORY_DB_URL` in `base-apps/kagent/homelab-agent-db-external-secret.yaml`).
- All `init-*` Jobs are Argo CD **Sync hooks** (`HookSucceeded` delete policy, `ttlSecondsAfterFinished: 300`), idempotent and safe to re-run.
- **Read-only audit roles on `kagent`** — `kagent_audit_ro` (the agent-audit CronJobs below) and `kagent_audit_web_ro` (the `agent-audit-web` UI), each created by its own Sync-hook Job (`init-kagent-audit-role.yaml`, `init-agent-audit-web-role.yaml`) with identical SQL (`tests/agent-audit/test_audit_role_jobs.py` keeps it identical): revoke everything, then grant `CONNECT`/`USAGE`/`SELECT` only, plus `ALTER DEFAULT PRIVILEGES FOR ROLE <database owner>` so tables kagent creates later are readable too. Separate roles so either consumer's credential can be revoked alone.
- **`donetick` database** (CNPG cluster) — declared through CloudNativePG's `Database` CRD (`donetick-database.yaml`, `databaseReclaimPolicy: retain` so an Argo prune can't drop it). Its `donetick` role is declared in `cnpg-cluster.yaml` (`spec.managed.roles`) with a `kubernetes.io/basic-auth` password Secret from `external-secrets-donetick.yaml`. **That role declaration is not enforced.** The operator never reconciled it, so the role was created by hand on 2026-08-03, and a Vault password rotation does **not** reach Postgres until that's fixed. See `docs/troubleshooting/cnpg-managed-roles-inert.md`. (The older `n8n` managed role points at an Opaque Secret and is an example of what not to copy.)

## Agent-audit CronJobs
`agent-audit-cronjob.yaml` is **generated** by `scripts/gen-agent-audit-cronjob.py` from `scripts/agent-audit.py` and the capability taxonomy. CI fails on drift, so never edit it by hand. It holds a ConfigMap `agent-audit-code` and two CronJobs, both reading `kagent` through the SELECT-only `kagent-audit-credentials`:
- `agent-audit-ungated` (07:00 UTC daily): runs `agent-audit.py --summary` and emits an argument-free JSON line to stdout (and on to Loki). The Job **exits non-zero on findings** (write/destructive tool calls with no approval request), so a failed Job is the alert.
- `agent-audit-export` (01:30 UTC daily): exports the last 25h of redacted records as JSONL to `s3://asela-agent-audit-record/` using `agent-audit-s3-creds` (from `base-apps/agent-audit-aws-infrastructure/`).

## Credential flow (Vault)
Each consumer's credential comes through its **own** ESO `SecretStore` (Vault Kubernetes auth, `k8s-secrets` KV v2) and Vault key — only the instance's own credentials use the broad `vault-backend` store (`secret-store.yaml`, role `postgresql`):

| ExternalSecret file | Secret | SecretStore (Vault role) | Vault key |
|---|---|---|---|
| `external-secrets.yaml` | `postgresql-credentials` | `vault-backend` (`postgresql`) | `postgresql` |
| `external-secrets-cnpg-backup.yaml` | `postgresql-backup-credentials` | `vault-backend` (`postgresql`) | `postgresql-backup` |
| `external-secrets-kagent.yaml` | `kagent-db-credentials` | `vault-kagent-db` (`kagent-db`) | `kagent-db` |
| `external-secrets-donetick.yaml` | `donetick-db-credentials` | `vault-donetick-db` (`donetick-db`) | `donetick-db` |
| `homelab-agent-db-external-secret.yaml` | `homelab-agent-db-credentials` | `vault-homelab-agent-db` (`homelab-agent-db`) | `homelab-agent-db` |
| `external-secrets-kagent-audit.yaml` | `kagent-audit-credentials` | `vault-kagent-audit-ro` (`kagent-audit-ro`) | `kagent-audit-ro` |
| `external-secrets-agent-audit-web-db.yaml` | `agent-audit-web-db-credentials` | `vault-agent-audit-web-db` (`agent-audit-web-db`) | `agent-audit-web-db` |

`postgresql-credentials` carries `root-password`, `database-name`, `n8n-user`, `n8n-password`; `root-password` is synced but not referenced by any manifest here.

## Backups and data-loss scope
- **CNPG cluster: covered.** `cnpg-scheduled-backup.yaml` (`postgresql-daily-backup`, 02:00 UTC, `immediate: true`) takes a barman physical backup of the **whole** cluster, meaning every database in it (`donetick`, `n8n`, the orphaned `chores_tracker`) plus WAL archiving, to `s3://mysql-backups-asela-cluster/postgresql/` (`backup.retentionPolicy: 30d`, credentials `postgresql-backup-credentials`). `scripts/pg-backup.sh` adds an operator-independent `pg_dump`/`pg_dumpall` of the CNPG primary (insurance for when the operator itself is broken, since a barman restore needs a working operator).
- **Plain instance: not backed up.** Nothing in this directory (and nothing in `pg-backup.sh`, which only targets the CNPG cluster) backs up `postgresql-pvc`. Losing that PVC or its node loses the root DB, `kagent` (agent history and audit source), `homelab_agent`, and whatever else lives on it (Backstage, n8n).
- Cluster-wide backup and restore order: `recovery/CLUSTER-RECOVERY.md`.

## Operator
The CNPG operator is its own Application, `base-apps/cnpg-system.yaml` (chart `cloudnative-pg`, release `cnpg`, namespace `cnpg-system`, on the control-plane node). Its comments hold the rules:
- **Upgrade one minor at a time.** CNPG doesn't support skipping operator minors; the history of steps is in the file.
- Each step rolls the Postgres pods. The "~30-60s of downtime per step" note there was written for `instances: 1`. With the replica the primary can be switched over instead of just restarted, but that path hasn't been exercised on an operator upgrade yet, so budget for a short blip.
- **Blocker before 1.31:** CNPG 1.31.0 removes the in-tree `barmanObjectStore` backup that `postgresql-cluster` uses. Migrate to the Barman Cloud Plugin before taking that minor, or scheduled backups stop.

## Gotchas & tribal knowledge
- The two instances have different backup stories (above). Don't assume "Postgres is backed up".
- Async replication: a failover after an unplanned primary crash can lose recent writes. Switchovers (drains, upgrades) don't.
- Adding a role via `spec.managed.roles` doesn't work on this cluster today (inert reconciler). Create it by hand and keep the declaration, per the troubleshooting doc.
- Changing the CNPG `affinity` restarts instances even though the docs don't list it (`cnpg-cluster.yaml` comments), and an instance can only schedule on the node that holds its volume.
- Sync-hook-only PRs (the `init-*` Jobs) don't trigger a sync on their own (runbook).
