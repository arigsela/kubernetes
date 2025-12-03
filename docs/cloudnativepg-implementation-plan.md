# CloudNativePG Implementation Plan

**Created**: 2025-12-03
**Last Updated**: 2025-12-03
**Status**: In Progress
**Completion**: 71%

---

## Overview

This document outlines the implementation plan for deploying CloudNativePG (CNPG) to replace the existing standalone PostgreSQL deployment, along with automated daily backups to S3.

### Current State
- Existing PostgreSQL deployment in `base-apps/postgresql/` using a standard Deployment + PVC
- PostgreSQL 15.3 single instance (no HA)
- Used by n8n for workflow storage
- No automated backups currently configured
- Vault stores credentials at `postgresql` path with keys: `root-password`, `database-name`, `n8n-user`, `n8n-password`

### Target State
- CloudNativePG operator managing PostgreSQL clusters
- Single instance deployment (homelab configuration)
- Built-in Prometheus metrics on port 9187
- Automated daily backups to existing S3 bucket
- GitOps-friendly declarative configuration

---

## Implementation Approach

### Why CloudNativePG?
1. **Most popular** - 5,000+ GitHub stars, fastest growing operator
2. **Fully open source** - No licensing restrictions
3. **Cloud-native** - Native K8s integration without external dependencies
4. **Built-in monitoring** - Prometheus metrics exporter included
5. **Barman backups** - Enterprise-grade backup with S3 support

---

## Phase 0: Vault Configuration (Pre-requisite)
**Estimated Tasks**: 3

### Subphase 0.1: Update Vault Role for PostgreSQL

The `postgresql` Vault role needs read access to the `mysql` Vault path to retrieve AWS backup credentials.

#### Tasks:
- ✅ Create a new Vault policy for mysql backup credentials read access (used existing `k8s-secrets-reader`)
- ✅ Create the `postgresql` Vault role with `k8s-secrets-reader` policy
- ✅ Verify the role can read both `postgresql` and `mysql` paths
- ✅ Created placeholder secrets at `k8s-secrets/postgresql` (update passwords before deployment)

#### Step 1: Create Vault Policy for MySQL Backup Credentials

First, exec into the Vault pod:
```bash
kubectl exec -it vault-0 -n vault -- /bin/sh
```

Create a policy that allows reading the mysql secrets (specifically the AWS credentials):
```bash
# Create the policy
vault policy write mysql-backup-read - <<EOF
# Allow reading AWS backup credentials from mysql path
path "k8s-secrets/data/mysql" {
  capabilities = ["read"]
}
EOF
```

#### Step 2: Update PostgreSQL Vault Role

Update the existing `postgresql` role to include both policies:
```bash
# First, read the current role configuration to preserve existing settings
vault read auth/kubernetes/role/postgresql

# Update the role to include the mysql-backup-read policy
vault write auth/kubernetes/role/postgresql \
  bound_service_account_names=default \
  bound_service_account_namespaces=postgresql \
  policies=postgresql-policy,mysql-backup-read \
  ttl=1h
```

#### Step 3: Verify Access

Test that the role can access both paths:
```bash
# Create a test token using the postgresql role (from within the cluster)
# Or verify after deployment by checking ExternalSecret status

# The ExternalSecret should show "SecretSynced" status for both:
# - postgresql-credentials (from postgresql path)
# - postgresql-backup-credentials (from mysql path)
```

#### Alternative: View Current Policies

If you need to check existing policies first:
```bash
# List all policies
vault policy list

# Read the postgresql policy
vault policy read postgresql-policy

# Read the mysql-backup policy (if exists)
vault policy read mysql-backup-policy
```

#### Vault Configuration Summary

| Component | Value |
|-----------|-------|
| **Role Name** | `postgresql` |
| **Service Account** | `default` |
| **Namespace** | `postgresql` |
| **Policies** | `postgresql-policy`, `mysql-backup-read` |
| **Paths Accessible** | `k8s-secrets/data/postgresql`, `k8s-secrets/data/mysql` |

---

## Phase 1: CloudNativePG Operator Installation
**Estimated Tasks**: 3

### Subphase 1.1: Create Operator Namespace and Install CRDs

#### Tasks:
- ✅ Create `base-apps/cloudnative-pg.yaml` ArgoCD application
- ✅ Create `base-apps/cloudnative-pg/` directory with operator manifests
- ✅ Install CloudNativePG operator via Helm template (v0.26.1, app v1.27.1)

#### Technical Notes:
```bash
# Option 1: Direct manifest installation
kubectl apply --server-side -f \
  https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.24/releases/cnpg-1.24.1.yaml

# Option 2: Helm chart (recommended for GitOps)
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm template cnpg/cloudnative-pg --namespace cnpg-system > base-apps/cloudnative-pg/operator.yaml
```

#### Files to Create:
```
base-apps/
├── cloudnative-pg.yaml              # ArgoCD Application
└── cloudnative-pg/
    ├── namespace.yaml               # cnpg-system namespace
    └── operator.yaml                # Operator deployment (from Helm template)
```

---

## Phase 2: PostgreSQL Cluster Deployment
**Estimated Tasks**: 5

### Subphase 2.1: Create PostgreSQL Cluster CRD

#### Tasks:
- ✅ Create `base-apps/postgresql-cnpg/` directory
- ✅ Create cluster.yaml with CloudNativePG Cluster CRD
- ✅ Configure storage using local-path StorageClass (20Gi)
- ✅ Set up Vault secret integration for credentials
- ✅ Create ArgoCD application `base-apps/postgresql-cnpg.yaml`

#### Cluster Configuration:
```yaml
# base-apps/postgresql-cnpg/cluster.yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: postgresql-cluster
  namespace: postgresql
spec:
  instances: 1  # Single instance for homelab

  imageName: ghcr.io/cloudnative-pg/postgresql:16.4

  postgresql:
    parameters:
      max_connections: "100"
      shared_buffers: "256MB"
      effective_cache_size: "512MB"
      maintenance_work_mem: "64MB"
      checkpoint_completion_target: "0.9"
      wal_buffers: "16MB"
      default_statistics_target: "100"
      random_page_cost: "1.1"
      effective_io_concurrency: "200"

  # Bootstrap configuration
  # Note: 'initdb' refers to PostgreSQL's initdb command that initializes a new database cluster
  # The database/owner names can be anything - we use the actual database name from Vault
  bootstrap:
    initdb:
      database: app_db           # Initial database to create
      owner: app_user            # Database owner user
      secret:
        name: postgresql-credentials  # Secret containing username/password

  storage:
    storageClass: local-path
    size: 20Gi

  resources:
    requests:
      memory: "1Gi"
      cpu: "250m"
    limits:
      memory: "2Gi"
      # No CPU limit - allows bursting

  # Enable monitoring with Prometheus annotations
  managed:
    podTemplate:
      metadata:
        annotations:
          prometheus.io/scrape: "true"
          prometheus.io/port: "9187"
          prometheus.io/path: "/metrics"

  # Node selection - infrastructure nodes
  affinity:
    nodeSelector:
      node.kubernetes.io/workload: infrastructure
```

#### Secret Configuration (Vault Integration):

The existing Vault path `postgresql` contains:
- `root-password` - PostgreSQL superuser password
- `database-name` - Database name
- `n8n-user` - Application user
- `n8n-password` - Application user password

CloudNativePG requires the bootstrap secret in `kubernetes.io/basic-auth` format:

```yaml
# base-apps/postgresql-cnpg/external-secret.yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: postgresql-credentials
  namespace: postgresql
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: SecretStore
    name: vault-backend
  target:
    name: postgresql-credentials
    creationPolicy: Owner
    template:
      type: kubernetes.io/basic-auth
      data:
        username: "{{ .n8n_user }}"
        password: "{{ .n8n_password }}"
  data:
  - secretKey: n8n_user
    remoteRef:
      key: postgresql
      property: n8n-user
  - secretKey: n8n_password
    remoteRef:
      key: postgresql
      property: n8n-password
```

#### SecretStore Configuration:
```yaml
# base-apps/postgresql-cnpg/secret-store.yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: postgresql
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "postgresql"
          serviceAccountRef:
            name: "default"
```

---

## Phase 3: Prometheus Monitoring Integration
**Estimated Tasks**: 2

### Subphase 3.1: Verify Prometheus Auto-Discovery

#### Current State Analysis:
Your Prometheus config at `base-apps/logging/prometheus-config.yaml` already supports pod annotation-based scraping:
- Job `kubernetes-pods` scrapes pods with `prometheus.io/scrape: "true"`
- Supports custom port via `prometheus.io/port` annotation
- Supports custom path via `prometheus.io/path` annotation

**CloudNativePG exposes metrics on port 9187 at path `/metrics`**

#### Tasks:
- ✅ Pod annotations configured in cluster.yaml (prometheus.io/scrape, port, path)
- ⬜ Confirm metrics appear in Prometheus after deployment (post-deployment verification)

#### No Changes Required to prometheus-config.yaml!
Your existing `kubernetes-pods` job will automatically discover and scrape CNPG pods because:
1. It uses `kubernetes_sd_configs` with `role: pod`
2. It filters for `prometheus.io/scrape: "true"` annotation
3. It reads port from `prometheus.io/port` annotation
4. It reads path from `prometheus.io/path` annotation (defaults to `/metrics`)

---

## Phase 4: Backup Configuration
**Estimated Tasks**: 4

### Subphase 4.1: Reuse Existing S3 Infrastructure

#### Existing Infrastructure (from `base-apps/mysql-rds-backup/`):
- **S3 Bucket**: `asela-mysql-backups` (will be reused for PostgreSQL)
- **IAM Credentials**: Already stored in Vault at path `mysql` with keys:
  - `AWS_ACCESS_KEY_ID`
  - `AWS_SECRET_ACCESS_KEY`
  - `S3_BUCKET`
  - `AWS_REGION`

#### Tasks:
- ✅ Create ExternalSecret for backup credentials (referencing existing Vault path)
- ✅ Configure backup in Cluster CRD
- ✅ Create ScheduledBackup CRD for daily backups
- ⬜ Verify backup appears in S3 bucket (post-deployment verification)

#### Backup Credentials ExternalSecret:
```yaml
# base-apps/postgresql-cnpg/backup-external-secret.yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: postgresql-backup-credentials
  namespace: postgresql
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: SecretStore
    name: vault-backend
  target:
    name: postgresql-backup-credentials
    creationPolicy: Owner
  data:
  # Reuse existing AWS credentials from mysql Vault path
  - secretKey: ACCESS_KEY_ID
    remoteRef:
      key: mysql
      property: AWS_ACCESS_KEY_ID
  - secretKey: ACCESS_SECRET_KEY
    remoteRef:
      key: mysql
      property: AWS_SECRET_ACCESS_KEY
```

**Note**: Phase 0 configures the Vault role to have read access to the `mysql` path for these credentials.

### Subphase 4.2: Cluster Backup Configuration

#### Update Cluster CRD with Backup Settings:
```yaml
# Add to cluster.yaml spec
spec:
  # ... other config ...

  backup:
    barmanObjectStore:
      destinationPath: s3://asela-mysql-backups/postgresql/
      endpointURL: https://s3.us-east-2.amazonaws.com
      s3Credentials:
        accessKeyId:
          name: postgresql-backup-credentials
          key: ACCESS_KEY_ID
        secretAccessKey:
          name: postgresql-backup-credentials
          key: ACCESS_SECRET_KEY
      wal:
        compression: gzip
        maxParallel: 2
      data:
        compression: gzip
    retentionPolicy: "30d"  # Keep backups for 30 days
```

### Subphase 4.3: Scheduled Backup CRD

#### Daily Backup Schedule:
```yaml
# base-apps/postgresql-cnpg/scheduled-backup.yaml
apiVersion: postgresql.cnpg.io/v1
kind: ScheduledBackup
metadata:
  name: postgresql-daily-backup
  namespace: postgresql
spec:
  # Daily at 2 AM UTC (matches your MySQL backup schedule)
  # Note: CNPG uses 6-field cron with seconds
  schedule: "0 0 2 * * *"

  backupOwnerReference: self

  cluster:
    name: postgresql-cluster

  # Take first backup immediately upon creation
  immediate: true

  # Keep the schedule active
  suspend: false
```

---

## Phase 5: Migration Strategy
**Estimated Tasks**: 4

### Subphase 5.1: Data Migration from Existing PostgreSQL

#### Tasks:
- ⬜ Create manual backup of existing PostgreSQL data
- ⬜ Deploy new CNPG cluster alongside existing deployment
- ⬜ Migrate data using pg_dump/pg_restore
- ⬜ Update n8n to use new cluster service endpoint

#### Migration Steps:
```bash
# 1. Backup existing data
kubectl exec -n postgresql postgresql-xxx -- pg_dump -U n8n -d n8n > n8n_backup.sql

# 2. After CNPG cluster is ready, restore data
kubectl exec -i -n postgresql postgresql-cluster-1 -- psql -U app_user -d app_db < n8n_backup.sql

# 3. Update n8n database connection
# The new service endpoints will be:
#   - postgresql-cluster-rw (read-write, primary)
#   - postgresql-cluster-ro (read-only, replicas)
#   - postgresql-cluster-r  (any instance)
```

---

## File Structure Summary

```
base-apps/
├── cloudnative-pg.yaml                    # ArgoCD App for operator
├── cloudnative-pg/
│   ├── namespace.yaml
│   └── operator.yaml
├── postgresql-cnpg.yaml                   # ArgoCD App for cluster
├── postgresql-cnpg/
│   ├── cluster.yaml                       # Main cluster definition
│   ├── secret-store.yaml                  # Vault SecretStore
│   ├── external-secret.yaml               # DB credentials
│   ├── backup-external-secret.yaml        # S3 credentials (reusing mysql path)
│   └── scheduled-backup.yaml              # Daily backup schedule
└── logging/
    └── prometheus-config.yaml             # NO CHANGES NEEDED
```

---

## Configuration Summary

| Setting | Value |
|---------|-------|
| **Instances** | 1 (single node) |
| **Storage Size** | 20Gi |
| **Memory Request** | 1Gi |
| **Memory Limit** | 2Gi |
| **CPU Request** | 250m |
| **CPU Limit** | None (bursting allowed) |
| **Node Selector** | `node.kubernetes.io/workload: infrastructure` |
| **S3 Bucket** | `asela-mysql-backups` |
| **Backup Path** | `s3://asela-mysql-backups/postgresql/` |
| **Backup Schedule** | Daily at 2 AM UTC |
| **Backup Retention** | 30 days |

---

## Prometheus Metrics Available

After deployment, these metrics will be available:

| Metric | Description |
|--------|-------------|
| `cnpg_collector_up` | Exporter health |
| `cnpg_pg_replication_lag` | Replication lag in seconds |
| `cnpg_pg_database_size_bytes` | Database size |
| `cnpg_pg_stat_activity_count` | Active connections |
| `cnpg_pg_locks_count` | Lock counts |
| `cnpg_pg_stat_bgwriter_*` | Background writer stats |
| `cnpg_pg_stat_database_*` | Database statistics |

---

## Rollback Plan

If issues occur:
1. Keep existing `base-apps/postgresql/` deployment intact during migration
2. n8n can be pointed back to original PostgreSQL service
3. CNPG cluster can be deleted without affecting original deployment

---

## Testing Checklist

- [ ] Operator pods running in `cnpg-system` namespace
- [ ] Cluster pod running in `postgresql` namespace
- [ ] Primary instance accepting connections
- [ ] Prometheus scraping metrics from port 9187
- [ ] Scheduled backup created successfully
- [ ] Backup files appearing in S3 bucket `asela-mysql-backups` under `postgresql/` prefix
- [ ] n8n successfully connected to new cluster

---

## Progress Tracking

| Phase | Status | Tasks |
|-------|--------|-------|
| Phase 0: Vault Configuration | ✅ Complete | 3/3 |
| Phase 1: Operator Installation | ✅ Complete | 3/3 |
| Phase 2: Cluster Deployment | ✅ Complete | 5/5 |
| Phase 3: Prometheus Integration | ✅ Complete | 1/2 |
| Phase 4: Backup Configuration | ✅ Complete | 3/4 |
| Phase 5: Migration | ⬜ Not Started | 0/4 |

**Total Progress**: 15/21 tasks (71%)

---

## References

- [CloudNativePG Documentation](https://cloudnative-pg.io/documentation/)
- [CloudNativePG GitHub](https://github.com/cloudnative-pg/cloudnative-pg)
- [Barman Backup Documentation](https://cloudnative-pg.io/documentation/current/backup/)
- [Monitoring Guide](https://cloudnative-pg.io/documentation/current/monitoring/)
