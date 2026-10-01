---
type: "Kubernetes App Runbook"
title: "PostgreSQL — Runbook"
description: "Operational runbook for PostgreSQL: plain-instance and init-Job failures, CNPG backups, switchover, replica lag, operator upgrades."
app: postgresql
catalog_entity: postgresql
kind: runbook
namespace: postgresql
last_reviewed: 2026-09-30
status: stable
tags: [database, stateful, pgvector, cnpg]
sources:
  - base-apps/postgresql/deployments.yaml
  - base-apps/postgresql/external-secrets.yaml
  - base-apps/postgresql/external-secrets-kagent.yaml
  - base-apps/postgresql/init-kagent-db.yaml
  - base-apps/postgresql/init-homelab-agent-db.yaml
  - base-apps/postgresql/pvc.yaml
  - base-apps/postgresql/cnpg-cluster.yaml
  - base-apps/postgresql/cnpg-scheduled-backup.yaml
  - base-apps/postgresql/external-secrets-cnpg-backup.yaml
  - base-apps/postgresql/agent-audit-cronjob.yaml
  - base-apps/cnpg-system.yaml
  - docs/troubleshooting/cnpg-managed-roles-inert.md
  - scripts/pg-backup.sh
  - recovery/CLUSTER-RECOVERY.md
---

# postgresql runbook

## Failure modes

### Symptom: postgresql pod stuck in `CreateContainerConfigError` / never Ready
`deployments.yaml` sources `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` entirely from the `postgresql-credentials` Secret, which is populated by an `ExternalSecret` (`external-secrets.yaml`) from Vault key `postgresql`. If that `ExternalSecret` hasn't synced (Vault sealed/unreachable, or the `postgresql` Kubernetes-auth role/policy in `secret-store.yaml` is wrong), the Secret never exists and the pod can't start.
- **Check:** `kubectl -n postgresql get externalsecret postgresql-credentials` (look at `STATUS`/`READY`) and `kubectl -n postgresql get secret postgresql-credentials`; then `kubectl -n postgresql describe pod -l app=postgresql` for the exact `CreateContainerConfigError` reason.
- **Fix:** if Vault itself is sealed/down, that's the `vault` app's runbook, not this one. If Vault is healthy but this namespace's `ExternalSecret` still fails, the `SecretStore`'s `auth.kubernetes.role: postgresql` (`secret-store.yaml`) likely doesn't match the Vault role/policy for this namespace — open a PR correcting the role name or Vault-side policy binding.

### Symptom: `init-kagent-db` Job fails / hits `BackoffLimitExceeded`, kagent has no database
`init-kagent-db.yaml` runs once to create the `kagent` role/database and enable `pgvector`, using both `postgresql-credentials` (root user) and `kagent-db-credentials` (from `external-secrets-kagent.yaml`, Vault key `kagent-db` via SecretStore `vault-kagent-db`). It loops on `pg_isready` before creating anything, but if either Secret is missing/stale when the Job's `backoffLimit: 5` is exhausted, or the root user's password rotated in Vault without the pod restarting, the SQL step fails.
- **Check:** `kubectl -n postgresql get job init-kagent-db` and `kubectl -n postgresql logs job/init-kagent-db --all-containers`; also confirm both `kubectl -n postgresql get secret postgresql-credentials kagent-db-credentials` exist.
- **Fix:** the `init-*` Jobs are Argo CD **Sync hooks** (deleted on success), not desired-state resources, so `selfHeal` does not recreate them: once both Secrets are present and valid, trigger a sync of the `postgresql` app (Argo CD UI "Sync") and every init hook re-runs - they are all idempotent. If the recurring cause is a race between Secret sync and Job start, open a PR adding an explicit wait/retry or an `initContainer` check against the Secrets.

### Symptom: a merged change to an `init-*` Job's SQL has no effect
The init Jobs (`init-kagent-db`, `init-homelab-agent-db`, `init-kagent-audit-role`, `init-agent-audit-web-role`) are Sync hooks, and hooks are not part of Argo CD's desired-state diff: a PR that changes **only** hook Jobs leaves the app `Synced` at the new revision without running a sync, so the new SQL never executes (seen 2026-09-27 with the audit roles' default-privileges fix).
- **Check:** `kubectl -n argo-cd get application postgresql -o jsonpath='{.status.operationState.startedAt}'` is older than the merge.
- **Fix:** trigger one sync of the `postgresql` app (Argo CD UI "Sync"); every init hook re-runs, idempotently. Then verify the effect (e.g. `SELECT pg_get_userbyid(defaclrole), defaclacl FROM pg_default_acl;` in database `kagent`).

### Symptom: plain `postgresql` pod stuck `Pending` after a node failure/drain
Storage is a single `local-path` `PersistentVolumeClaim` (`pvc.yaml`, `10Gi`, `ReadWriteOnce`), which `local-path-provisioner` binds to whichever node first created it. The `Deployment` also has `nodeSelector: node.kubernetes.io/workload: application` (`deployments.yaml`). There is no StatefulSet, replica, or backup, so this is a single point of failure for every database on the instance (root DB, `kagent`, `homelab_agent`, and the other consumers listed in docs.md). This entry is about the plain Deployment, not the CNPG cluster.
- **Check:** `kubectl -n postgresql get pod -l app=postgresql -o wide` (look for `Pending`/`ContainerCreating` and the assigned node) and `kubectl -n postgresql get pvc postgresql-pvc -o wide` to see which node the underlying `local-path` volume lives on.
- **Fix:** if the original node is truly gone, the local-path volume's data is gone with it — there is no cross-node replica to fail over to. Recovery means recreating `postgresql-pvc` from whatever external backup exists (none is defined in this directory) and restoring dumps manually; longer term, open a PR to add a periodic `pg_dump` CronJob or move to a replicated/operator-managed Postgres if this instance's availability requirements have grown.

### Symptom: `agent-audit-ungated` Job failed
Expected behaviour when there are open findings: the Job exits non-zero if a write/destructive tool ran in a session with no approval request and the call isn't acknowledged in `scripts/agent-audit-acknowledged.yaml`. The Grafana alert "Agent invoked a gated tool with NO approval" fires from the same summary line.
- **Check:** `kubectl -n postgresql logs -l app=agent-audit` shows the summary JSON (counts, agents, tools; never arguments; `acknowledged_invocations` is the triaged calls it left out). To see the arguments, run `scripts/agent-audit.py --ungated` yourself against `kagent` with the SELECT-only `kagent-audit-credentials` (the script prints the port-forward recipe). Rows marked `ack` are already triaged; the unmarked ones are new.
- **Fix:** a real finding means an agent's `requireApproval` is missing or being stripped, so fix it in the agent's manifest. Once it is fixed, acknowledge the session in `scripts/agent-audit-acknowledged.yaml` (session, agent, `through` = the `at` of its last call from `--ungated --format json`, and a reason naming the fix) and re-run `scripts/gen-agent-audit-cronjob.py`. Don't acknowledge a finding that hasn't been fixed: the alert would go quiet while the gate is still open. A connection or credential error is the `kagent-audit-credentials` ExternalSecret or the `kagent_audit_ro` role (re-run the Sync hooks). To change the CronJobs, edit `scripts/agent-audit.py` or the generator and re-run `scripts/gen-agent-audit-cronjob.py`. Never hand-edit `agent-audit-cronjob.yaml`.

## CNPG cluster (`postgresql-cluster`)

### Find the primary / check cluster health
```bash
kubectl -n postgresql get cluster postgresql-cluster          # phase, instances, ready, current primary
kubectl -n postgresql get pods -l cnpg.io/cluster=postgresql-cluster,cnpg.io/instanceRole=primary -o wide
```
With the `cnpg` kubectl plugin installed, `kubectl cnpg status postgresql-cluster -n postgresql` shows replication state, lag and the last backup in one view.

### Symptom: scheduled backup failed / no recent backup
- **Check:** `kubectl -n postgresql get backups --sort-by=.metadata.creationTimestamp` (the newest `postgresql-daily-backup-*` should be `completed`, from today's 02:00 UTC run), `kubectl -n postgresql describe backup <name>` for the error, and the cluster's `ContinuousArchiving` condition (`kubectl -n postgresql get cluster postgresql-cluster -o jsonpath='{.status.conditions}'`).
- **Fix:** most failures are S3 credentials: `kubectl -n postgresql get externalsecret postgresql-backup-credentials` must be synced from Vault key `postgresql-backup`, with keys `ACCESS_KEY_ID`/`ACCESS_SECRET_KEY`/`AWS_REGION`. Fix the Vault value, let ESO resync, then take a one-off backup to prove it (`kubectl cnpg backup postgresql-cluster -n postgresql`, or apply a `Backup` CR naming the cluster). If the operator is on 1.31+, the in-tree `barmanObjectStore` path no longer exists (see Operator below).

### Draining a worker / switchover
The cluster runs one instance per worker (required anti-affinity), with a primary PDB of minAvailable 1. Draining the primary's node makes the operator switch the primary over to the other worker. The drain then proceeds and donetick reconnects via `postgresql-cluster-rw`. No data is lost (planned switchover).
- **Check before draining:** both instances Ready and the replica streaming (`kubectl -n postgresql get cluster postgresql-cluster`). If only one instance is healthy, the PDB blocks the drain (`Cannot evict pod as it would violate the pod's disruption budget`), and that is correct: fix the replica first.
- **While the node is down:** the instance whose volume lives there stays `Pending` (node-bound `local-path`), and the cluster runs on one instance with no redundancy. Keep the window short and uncordon when done; the instance rejoins as a replica.
- **Manual switchover** (e.g. ahead of maintenance): `kubectl cnpg promote postgresql-cluster <replica-pod> -n postgresql`.

### Symptom: replica lagging or not streaming
- **Check:** `kubectl cnpg status postgresql-cluster -n postgresql`, or on the primary: `kubectl -n postgresql exec <primary> -c postgres -- psql -U postgres -c 'select application_name, state, replay_lag from pg_stat_replication;'`. A missing row means the replica isn't connected. Check its pod logs and whether its node is up.
- **Why it matters:** replication is **asynchronous**. Anything the replica hasn't replayed is lost if the primary crashes now. Sustained lag or a disconnected replica also blocks the next drain.
- **Fix:** a replica whose node is gone can't move (node-bound volume). Bring the node back, or delete that instance's PVC and pod so the operator re-clones a fresh replica onto a schedulable worker. That's safe only while the **primary** is healthy, so double-check which pod you're deleting.

### Symptom: unplanned failover happened
The operator promoted the replica after the primary died. Writes the replica hadn't replayed (the async window) are gone; Postgres won't report which ones. Check donetick for recent missing changes. The old primary rejoins as a replica when its node returns. For a full restore instead, recover from the barman backup (WAL archive allows point-in-time recovery) into a new cluster via `bootstrap.recovery`. See `recovery/CLUSTER-RECOVERY.md`.

### Symptom: a managed role (e.g. `donetick`) isn't created, or a Vault password rotation doesn't take effect
Known issue: managed-role reconciliation is inert on this cluster. Follow `docs/troubleshooting/cnpg-managed-roles-inert.md`: create or alter the role by hand from the password in the target Secret, and keep the declaration in `cnpg-cluster.yaml`.

### Logical backup / restore outside the operator
`scripts/pg-backup.sh --dest <off-cluster dir|s3://…> --all` runs `pg_dumpall` in the CNPG primary (or `--database <name>` for one DB) and writes a `.sql.gz` plus a `.sha256`. It refuses destinations inside cluster storage. Restore with `gunzip -c <artifact> | psql -U postgres` inside the primary. Take one before any operator upgrade. It does **not** cover the plain `postgresql` Deployment.

## Operator (`cnpg-system`)
### Upgrade the CNPG operator
Edit `targetRevision` in `base-apps/cnpg-system.yaml`, **one minor at a time**, and add the step to the history comment. Before each step: confirm a recent completed backup, take a `pg-backup.sh --all`, and check both instances are healthy. Expect the Postgres pods to roll. With two instances the primary should switch over rather than just restart, but plan for a short blip. The 30s `smartShutdownTimeout` caps the shutdown wait. **Do not take 1.31** until `postgresql-cluster` has moved off the in-tree `barmanObjectStore` to the Barman Cloud Plugin. If Argo CD then shows `field not declared in schema` on this app, restart the application controller (see the `argo-cd` runbook).
