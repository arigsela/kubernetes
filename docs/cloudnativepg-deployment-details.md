# CloudNativePG Deployment Details

**Created**: 2025-12-03
**Status**: Operational

---

## Overview

This document describes the CloudNativePG PostgreSQL deployment in the Kubernetes cluster. This is a new PostgreSQL cluster managed by the CloudNativePG operator, intended to replace the existing standalone PostgreSQL deployment.

---

## Cluster Details

| Property | Value |
|----------|-------|
| **Cluster Name** | `postgresql-cluster` |
| **Namespace** | `postgresql` |
| **PostgreSQL Version** | 16.4 |
| **Instances** | 1 (single node) |
| **Storage Size** | 20Gi |
| **Storage Class** | `local-path` |

---

## Connection Information

### Service Endpoints

CloudNativePG creates several services for different access patterns:

| Service Name | Type | Purpose |
|--------------|------|---------|
| `postgresql-cluster-rw` | ClusterIP | **Primary (read-write)** - Use this for all write operations |
| `postgresql-cluster-ro` | ClusterIP | Read-only replicas (currently same as primary with 1 instance) |
| `postgresql-cluster-r` | ClusterIP | Any instance (round-robin) |

### Connection String Format

```
postgresql://<username>:<password>@postgresql-cluster-rw.postgresql.svc.cluster.local:5432/<database>
```

### Default Connection Parameters

| Parameter | Value |
|-----------|-------|
| **Host** | `postgresql-cluster-rw.postgresql.svc.cluster.local` |
| **Port** | `5432` |
| **Database** | `n8n` |
| **Username** | `n8n` |
| **SSL Mode** | `prefer` (optional, not enforced) |

---

## Credentials

Credentials are stored in Kubernetes secrets, managed by External Secrets Operator pulling from HashiCorp Vault.

### Secret: `postgresql-credentials`

Located in namespace `postgresql`, contains:

| Key | Description |
|-----|-------------|
| `username` | Database user (`n8n`) |
| `password` | Database password |

### Retrieving Credentials

```bash
# Get username
kubectl get secret postgresql-credentials -n postgresql -o jsonpath='{.data.username}' | base64 -d

# Get password
kubectl get secret postgresql-credentials -n postgresql -o jsonpath='{.data.password}' | base64 -d
```

### Vault Path

Credentials are sourced from Vault at path `k8s-secrets/postgresql` with keys:
- `n8n-user` - Username
- `n8n-password` - Password

---

## Database Configuration

### Initial Database

The cluster was bootstrapped with:
- **Database**: `n8n`
- **Owner**: `n8n`

### PostgreSQL Parameters

```yaml
max_connections: "100"
shared_buffers: "256MB"
effective_cache_size: "512MB"
maintenance_work_mem: "64MB"
checkpoint_completion_target: "0.9"
wal_buffers: "16MB"
default_statistics_target: "100"
random_page_cost: "1.1"
effective_io_concurrency: "200"
```

---

## Connecting from Another Pod

### Example: Direct psql Connection

```bash
# From any pod in the cluster
PGPASSWORD=$(kubectl get secret postgresql-credentials -n postgresql -o jsonpath='{.data.password}' | base64 -d)

kubectl run psql-client --rm -it --restart=Never \
  --image=postgres:16 \
  --env="PGPASSWORD=$PGPASSWORD" \
  -- psql -h postgresql-cluster-rw.postgresql.svc.cluster.local -U n8n -d n8n
```

### Example: From Application Deployment

```yaml
env:
  - name: DATABASE_HOST
    value: "postgresql-cluster-rw.postgresql.svc.cluster.local"
  - name: DATABASE_PORT
    value: "5432"
  - name: DATABASE_NAME
    value: "n8n"
  - name: DATABASE_USER
    valueFrom:
      secretKeyRef:
        name: postgresql-credentials
        key: username
  - name: DATABASE_PASSWORD
    valueFrom:
      secretKeyRef:
        name: postgresql-credentials
        key: password
```

---

## Backup Configuration

| Property | Value |
|----------|-------|
| **Method** | Barman Object Store (S3) |
| **S3 Bucket** | `mysql-backups-asela-cluster` |
| **S3 Path** | `postgresql/postgresql-cluster/` |
| **Schedule** | Daily at 2:00 AM UTC |
| **Retention** | 30 days |
| **WAL Archiving** | Enabled (continuous) |
| **Compression** | gzip |

---

## Resource Allocation

| Resource | Request | Limit |
|----------|---------|-------|
| **Memory** | 1Gi | 2Gi |
| **CPU** | 250m | None (bursting allowed) |

---

## Node Placement

The PostgreSQL pod runs on infrastructure nodes:

```yaml
nodeSelector:
  node.kubernetes.io/workload: infrastructure
tolerations:
  - key: node-role.kubernetes.io/control-plane
    effect: NoSchedule
```

---

## Monitoring

### Prometheus Metrics

Metrics are exposed on port `9187` at path `/metrics`.

Pod annotations for Prometheus auto-discovery:
```yaml
prometheus.io/scrape: "true"
prometheus.io/port: "9187"
prometheus.io/path: "/metrics"
```

### Key Metrics Available

- `cnpg_collector_up` - Exporter health
- `cnpg_pg_database_size_bytes` - Database size
- `cnpg_pg_stat_activity_count` - Active connections
- `cnpg_pg_replication_lag` - Replication lag (if replicas added)

---

## Useful Commands

### Check Cluster Status

```bash
kubectl get clusters -n postgresql
kubectl get pods -n postgresql
```

### View Cluster Details

```bash
kubectl describe cluster postgresql-cluster -n postgresql
```

### Check Backups

```bash
kubectl get backups -n postgresql
kubectl get scheduledbackups -n postgresql
```

### View Pod Logs

```bash
kubectl logs postgresql-cluster-1 -n postgresql
```

### Execute SQL Commands

```bash
kubectl exec -it postgresql-cluster-1 -n postgresql -- psql -U postgres
```

### Manual Backup

```bash
kubectl create -f - <<EOF
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  name: manual-backup-$(date +%Y%m%d%H%M%S)
  namespace: postgresql
spec:
  method: barmanObjectStore
  cluster:
    name: postgresql-cluster
EOF
```

---

## Migration Notes

### For Migrating Data INTO This Cluster

1. **Connection target**: `postgresql-cluster-rw.postgresql.svc.cluster.local:5432`
2. **Database**: `n8n` (already created)
3. **User**: `n8n` (already created with appropriate permissions)
4. **Method**: Use `pg_dump` from source, `psql` or `pg_restore` to this cluster

### pg_restore Example

```bash
# From a pod with access to both source dump and this cluster
pg_restore -h postgresql-cluster-rw.postgresql.svc.cluster.local \
  -U n8n -d n8n \
  --no-owner --no-privileges \
  /path/to/dump.sql
```

### psql Import Example

```bash
psql -h postgresql-cluster-rw.postgresql.svc.cluster.local \
  -U n8n -d n8n \
  -f /path/to/dump.sql
```

---

## Architecture Diagram

```
                                    ┌─────────────────────────────────┐
                                    │      cnpg-system namespace      │
                                    │  ┌───────────────────────────┐  │
                                    │  │  CloudNativePG Operator   │  │
                                    │  └───────────────────────────┘  │
                                    └─────────────────────────────────┘
                                                    │
                                                    │ manages
                                                    ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                           postgresql namespace                                 │
│                                                                               │
│  ┌─────────────────────┐    ┌─────────────────────┐    ┌──────────────────┐  │
│  │  postgresql-cluster │    │  postgresql-cluster │    │  postgresql-     │  │
│  │         -rw         │    │         -ro         │    │  credentials     │  │
│  │     (Service)       │    │     (Service)       │    │    (Secret)      │  │
│  └──────────┬──────────┘    └──────────┬──────────┘    └──────────────────┘  │
│             │                          │                                      │
│             └──────────┬───────────────┘                                      │
│                        │                                                      │
│                        ▼                                                      │
│           ┌────────────────────────┐                                         │
│           │  postgresql-cluster-1  │                                         │
│           │        (Pod)           │                                         │
│           │  ┌──────────────────┐  │                                         │
│           │  │  PostgreSQL 16.4 │  │◄──── Port 5432                          │
│           │  │                  │  │◄──── Metrics: 9187                      │
│           │  └──────────────────┘  │                                         │
│           │  ┌──────────────────┐  │                                         │
│           │  │   PVC (20Gi)     │  │                                         │
│           │  └──────────────────┘  │                                         │
│           └────────────────────────┘                                         │
│                        │                                                      │
│                        │ WAL Archive                                          │
│                        ▼                                                      │
│           ┌────────────────────────┐                                         │
│           │   S3: mysql-backups-   │                                         │
│           │   asela-cluster/       │                                         │
│           │   postgresql/          │                                         │
│           └────────────────────────┘                                         │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## References

- [CloudNativePG Documentation](https://cloudnative-pg.io/documentation/)
- [Implementation Plan](./cloudnativepg-implementation-plan.md)
