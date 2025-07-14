# Database Blue/Green Strategy for PostgreSQL with Multiple Databases

This document outlines our approach for implementing blue/green deployments with PostgreSQL databases that support writable replicas for comprehensive testing.

## Executive Summary

After analyzing AWS RDS Blue/Green deployments, RDS Proxy, and Crossplane capabilities, we recommend a **custom blue/green strategy** using separate RDS instances. This approach provides:
- Fully writable green environment for testing
- Support for multiple PostgreSQL databases
- Integration with existing GitOps workflow
- Minimal application changes

## Why Not AWS RDS Blue/Green Deployments?

### Key Limitations
1. **Green Environment is Read-Only**: Cannot test write operations before switchover
2. **Incompatible with RDS Proxy**: Must choose between Blue/Green or RDS Proxy
3. **Limited Control**: 1-minute downtime during switchover is unavoidable
4. **Multiple Database Complexity**: Managing multiple databases adds complexity

### RDS Proxy Incompatibility
```
"RDS Blue/Green doesn't support RDS Proxy, so we have to remove 
the RDS proxy from our setup to use this feature."
```

## Recommended Architecture

### Overview
```
┌─────────────────┐     ┌─────────────────┐
│  Blue RDS       │     │  Green RDS      │
│  (Production)   │     │  (Staging)      │
│  - db1          │     │  - db1          │
│  - db2          │     │  - db2          │
│  - db3          │     │  - db3          │
└────────┬────────┘     └────────┬────────┘
         │                       │
         │  ┌─────────────────┐  │
         └──┤ External Secrets├──┘
            └────────┬────────┘
                     │
            ┌────────┴────────┐
            │  Applications   │
            │ (Argo Rollouts) │
            └─────────────────┘
```

### Components

1. **Separate RDS Instances**
   - Blue: Production PostgreSQL instance
   - Green: Staging PostgreSQL instance (fully writable)
   - Both instances contain identical databases

2. **Data Synchronization**
   - Initial: AWS RDS snapshots
   - Ongoing: PostgreSQL logical replication or custom ETL

3. **Connection Management**
   - External Secrets manages database URLs
   - Applications use environment-specific connection strings
   - No RDS Proxy (to maintain flexibility)

## Implementation Guide

### Phase 1: Infrastructure Setup

#### 1.1 Create RDS Instances with Terraform

```hcl
# terraform/rds_blue_green.tf

# Blue (Production) RDS Instance
resource "aws_db_instance" "postgres_blue" {
  identifier     = "postgres-blue"
  engine         = "postgres"
  engine_version = "15.4"
  instance_class = "db.t3.medium"
  
  allocated_storage     = 100
  storage_encrypted     = true
  storage_type          = "gp3"
  
  db_name  = "postgres"
  username = "postgres"
  password = random_password.postgres_blue.result
  
  vpc_security_group_ids = [aws_security_group.rds.id]
  db_subnet_group_name   = aws_db_subnet_group.main.name
  
  backup_retention_period = 7
  backup_window          = "03:00-04:00"
  maintenance_window     = "sun:04:00-sun:05:00"
  
  enabled_cloudwatch_logs_exports = ["postgresql"]
  
  # Enable automated backups for snapshots
  skip_final_snapshot = false
  final_snapshot_identifier = "postgres-blue-final-${formatdate("YYYY-MM-DD-hhmm", timestamp())}"
  
  tags = {
    Name        = "postgres-blue"
    Environment = "production"
    Type        = "blue-green"
  }
}

# Green (Staging) RDS Instance
resource "aws_db_instance" "postgres_green" {
  identifier     = "postgres-green"
  engine         = "postgres"
  engine_version = "15.4"
  instance_class = "db.t3.medium"
  
  allocated_storage     = 100
  storage_encrypted     = true
  storage_type          = "gp3"
  
  db_name  = "postgres"
  username = "postgres"
  password = random_password.postgres_green.result
  
  vpc_security_group_ids = [aws_security_group.rds.id]
  db_subnet_group_name   = aws_db_subnet_group.main.name
  
  backup_retention_period = 7
  backup_window          = "03:00-04:00"
  maintenance_window     = "sun:04:00-sun:05:00"
  
  enabled_cloudwatch_logs_exports = ["postgresql"]
  
  skip_final_snapshot = false
  final_snapshot_identifier = "postgres-green-final-${formatdate("YYYY-MM-DD-hhmm", timestamp())}"
  
  tags = {
    Name        = "postgres-green"
    Environment = "staging"
    Type        = "blue-green"
  }
}

# Store credentials in AWS Secrets Manager
resource "aws_secretsmanager_secret" "postgres_blue" {
  name = "postgres-blue-credentials"
}

resource "aws_secretsmanager_secret_version" "postgres_blue" {
  secret_id = aws_secretsmanager_secret.postgres_blue.id
  secret_string = jsonencode({
    username = aws_db_instance.postgres_blue.username
    password = random_password.postgres_blue.result
    endpoint = aws_db_instance.postgres_blue.endpoint
    port     = aws_db_instance.postgres_blue.port
  })
}

resource "aws_secretsmanager_secret" "postgres_green" {
  name = "postgres-green-credentials"
}

resource "aws_secretsmanager_secret_version" "postgres_green" {
  secret_id = aws_secretsmanager_secret.postgres_green.id
  secret_string = jsonencode({
    username = aws_db_instance.postgres_green.username
    password = random_password.postgres_green.result
    endpoint = aws_db_instance.postgres_green.endpoint
    port     = aws_db_instance.postgres_green.port
  })
}
```

#### 1.2 Create Multiple Databases

```yaml
# base-apps/postgres-databases/databases.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: create-postgres-databases
  namespace: database-management
spec:
  template:
    spec:
      containers:
      - name: create-dbs
        image: postgres:15
        env:
        - name: PGPASSWORD
          valueFrom:
            secretKeyRef:
              name: postgres-admin-credentials
              key: password
        command:
        - /bin/bash
        - -c
        - |
          # Create databases on both blue and green instances
          for db in chores_db inventory_db analytics_db; do
            echo "Creating database $db on blue instance..."
            psql -h postgres-blue.region.rds.amazonaws.com -U postgres -c "CREATE DATABASE $db;"
            
            echo "Creating database $db on green instance..."
            psql -h postgres-green.region.rds.amazonaws.com -U postgres -c "CREATE DATABASE $db;"
          done
      restartPolicy: OnFailure
```

### Phase 2: Data Synchronization

#### 2.1 Initial Data Copy Using Snapshots

```yaml
# base-apps/postgres-sync/snapshot-restore-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: postgres-snapshot-restore
  namespace: database-management
spec:
  template:
    spec:
      serviceAccountName: rds-snapshot-manager
      containers:
      - name: snapshot-restore
        image: amazon/aws-cli:latest
        env:
        - name: AWS_REGION
          value: us-east-1
        command:
        - /bin/bash
        - -c
        - |
          #!/bin/bash
          set -e
          
          echo "Creating snapshot of blue instance..."
          SNAPSHOT_ID="manual-snapshot-$(date +%Y%m%d%H%M%S)"
          aws rds create-db-snapshot \
            --db-instance-identifier postgres-blue \
            --db-snapshot-identifier $SNAPSHOT_ID
          
          echo "Waiting for snapshot to complete..."
          aws rds wait db-snapshot-completed \
            --db-snapshot-identifier $SNAPSHOT_ID
          
          echo "Restoring snapshot to temporary instance..."
          TEMP_INSTANCE="postgres-temp-$(date +%Y%m%d%H%M%S)"
          aws rds restore-db-instance-from-db-snapshot \
            --db-instance-identifier $TEMP_INSTANCE \
            --db-snapshot-identifier $SNAPSHOT_ID
          
          echo "Waiting for restore to complete..."
          aws rds wait db-instance-available \
            --db-instance-identifier $TEMP_INSTANCE
          
          # Note: You would then need to dump and restore the data
          # from temp instance to green instance
          echo "Data migration would happen here..."
          
          echo "Cleaning up temporary instance..."
          aws rds delete-db-instance \
            --db-instance-identifier $TEMP_INSTANCE \
            --skip-final-snapshot
      restartPolicy: OnFailure
```

#### 2.2 Alternative: PostgreSQL Logical Replication

```sql
-- On Blue (Publisher)
-- Enable logical replication
ALTER SYSTEM SET wal_level = logical;
ALTER SYSTEM SET max_replication_slots = 10;
ALTER SYSTEM SET max_wal_senders = 10;

-- Create publication for each database
\c chores_db
CREATE PUBLICATION chores_pub FOR ALL TABLES;

\c inventory_db
CREATE PUBLICATION inventory_pub FOR ALL TABLES;

\c analytics_db
CREATE PUBLICATION analytics_pub FOR ALL TABLES;

-- On Green (Subscriber)
-- Create subscriptions
\c chores_db
CREATE SUBSCRIPTION chores_sub
  CONNECTION 'host=postgres-blue.region.rds.amazonaws.com dbname=chores_db user=replicator password=xxx'
  PUBLICATION chores_pub;

\c inventory_db
CREATE SUBSCRIPTION inventory_sub
  CONNECTION 'host=postgres-blue.region.rds.amazonaws.com dbname=inventory_db user=replicator password=xxx'
  PUBLICATION inventory_pub;

\c analytics_db
CREATE SUBSCRIPTION analytics_sub
  CONNECTION 'host=postgres-blue.region.rds.amazonaws.com dbname=analytics_db user=replicator password=xxx'
  PUBLICATION analytics_pub;
```

### Phase 3: Application Configuration

#### 3.1 External Secrets Setup

```yaml
# base-apps/chores-tracker/external_secrets_blue_green.yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: chores-tracker-db-secrets
  namespace: chores-tracker
spec:
  refreshInterval: 15s
  secretStoreRef:
    name: vault-secret-store
    kind: SecretStore
  target:
    name: chores-tracker-db-secrets
    creationPolicy: Owner
  data:
  # Blue (Production) database
  - secretKey: DATABASE_URL_BLUE
    remoteRef:
      key: postgres/blue
      property: chores_db_url
  # Green (Staging) database  
  - secretKey: DATABASE_URL_GREEN
    remoteRef:
      key: postgres/green
      property: chores_db_url
  # Active database URL (switches during blue/green deployment)
  - secretKey: DATABASE_URL
    remoteRef:
      key: postgres/active
      property: chores_db_url
```

#### 3.2 Crossplane Database Management

```yaml
# base-apps/crossplane-postgres/provider.yaml
apiVersion: pkg.crossplane.io/v1
kind: Provider
metadata:
  name: provider-sql
spec:
  package: crossplane/provider-sql:v0.9.0
---
# base-apps/crossplane-postgres/providerconfig-blue.yaml
apiVersion: postgresql.sql.crossplane.io/v1alpha1
kind: ProviderConfig
metadata:
  name: postgres-blue
spec:
  credentials:
    source: PostgreSQLConnectionSecret
    connectionSecretRef:
      namespace: crossplane-system
      name: postgres-blue-connection
---
# base-apps/crossplane-postgres/providerconfig-green.yaml
apiVersion: postgresql.sql.crossplane.io/v1alpha1
kind: ProviderConfig
metadata:
  name: postgres-green
spec:
  credentials:
    source: PostgreSQLConnectionSecret
    connectionSecretRef:
      namespace: crossplane-system
      name: postgres-green-connection
```

### Phase 4: Blue/Green Switchover Process

#### 4.1 Switchover Script

```yaml
# base-apps/postgres-switchover/switchover-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: postgres-blue-green-switch
  namespace: database-management
spec:
  template:
    spec:
      containers:
      - name: switchover
        image: bitnami/kubectl:latest
        command:
        - /bin/bash
        - -c
        - |
          #!/bin/bash
          set -e
          
          # Current active environment
          CURRENT_ACTIVE=${CURRENT_ACTIVE:-blue}
          NEW_ACTIVE=${NEW_ACTIVE:-green}
          
          echo "Switching from $CURRENT_ACTIVE to $NEW_ACTIVE..."
          
          # Update External Secrets to point to new active
          kubectl patch externalsecret chores-tracker-db-secrets \
            -n chores-tracker \
            --type merge \
            -p '{"spec":{"data":[{"secretKey":"DATABASE_URL","remoteRef":{"key":"postgres/'$NEW_ACTIVE'","property":"chores_db_url"}}]}}'
          
          # Wait for secret refresh
          sleep 20
          
          # Trigger application rollout restart
          kubectl rollout restart rollout chores-tracker -n chores-tracker
          
          echo "Switchover complete!"
      restartPolicy: OnFailure
```

#### 4.2 Monitoring Dashboard

```yaml
# base-apps/postgres-monitoring/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: postgres-blue-green-dashboard
  namespace: monitoring
data:
  dashboard.json: |
    {
      "dashboard": {
        "title": "PostgreSQL Blue/Green Status",
        "panels": [
          {
            "title": "Active Environment",
            "targets": [
              {
                "expr": "postgres_active_environment"
              }
            ]
          },
          {
            "title": "Replication Lag",
            "targets": [
              {
                "expr": "postgres_replication_lag_seconds"
              }
            ]
          },
          {
            "title": "Connection Count",
            "targets": [
              {
                "expr": "postgres_connections_active"
              }
            ]
          }
        ]
      }
    }
```

## Operational Procedures

### Daily Operations

1. **Monitor Replication Lag**
   ```bash
   kubectl exec -it postgres-monitor-pod -- psql -c "SELECT * FROM pg_stat_replication;"
   ```

2. **Test Green Environment**
   - Deploy preview version using Argo Rollouts
   - Run integration tests against green database
   - Verify all write operations work correctly

### Blue/Green Deployment Process

1. **Preparation**
   - Ensure green database is synchronized
   - Run database migrations on both environments
   - Update application code if needed

2. **Testing**
   - Deploy to green environment
   - Run comprehensive tests (reads AND writes)
   - Monitor performance metrics

3. **Switchover**
   - Execute switchover script
   - Monitor application health
   - Verify zero data loss

4. **Rollback (if needed)**
   - Switch back to blue environment
   - Investigate issues
   - Plan remediation

### Maintenance Tasks

1. **Weekly Snapshot Refresh**
   ```bash
   kubectl create job --from=cronjob/postgres-snapshot-sync postgres-sync-$(date +%Y%m%d)
   ```

2. **Monthly Failover Test**
   - Practice blue/green switchover
   - Validate rollback procedures
   - Update runbooks

## Best Practices

### 1. Data Consistency
- Use transactions for critical operations
- Implement idempotent database migrations
- Monitor replication lag continuously

### 2. Testing Strategy
- Test write operations in green environment
- Validate data integrity after sync
- Run performance benchmarks

### 3. Security
- Separate credentials for blue/green
- Rotate passwords regularly
- Audit database access

### 4. Cost Management
- Use smaller instance sizes for green during idle
- Schedule green environment scaling
- Monitor and optimize storage usage

## Advantages Over RDS Blue/Green

1. **Writable Green Environment**: Full testing capabilities
2. **Multiple Database Support**: Easy management of multiple databases
3. **Flexible Sync Options**: Choose between snapshot, logical replication, or ETL
4. **No RDS Proxy Limitation**: Can add RDS Proxy later if needed
5. **GitOps Compatible**: Fully integrated with existing workflow

## Migration Timeline

- **Week 1**: Set up Terraform infrastructure
- **Week 2**: Configure External Secrets and Crossplane
- **Week 3**: Implement data synchronization
- **Week 4**: Test with non-critical application
- **Week 5**: Production rollout for chores-tracker
- **Week 6**: Extend to remaining applications

## Conclusion

This custom blue/green strategy provides the flexibility needed for comprehensive testing while maintaining compatibility with our GitOps workflow. By using separate RDS instances and managing synchronization ourselves, we avoid the limitations of AWS RDS Blue/Green deployments while achieving our zero-downtime deployment goals.