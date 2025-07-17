# Troubleshooting Guide

## Overview

This guide helps you diagnose and resolve common issues with your **asela-test3db** MySQL database.

## Quick Diagnostics

### Database Health Check

Run this comprehensive health check script:

```bash
#!/bin/bash
# Database Health Check Script

NAMESPACE="asela-test3"
APP_NAME="asela-test3"
DB_NAME="asela-test3db"

echo "=== Database Health Check ==="
echo "Namespace: $NAMESPACE"
echo "Application: $APP_NAME"
echo "Database: $DB_NAME"
echo

# Check if pods are running
echo "1. Pod Status:"
kubectl get pods -n $NAMESPACE -l app=mysql
echo

# Check service endpoints
echo "2. Service Endpoints:"
kubectl get endpoints mysql -n $NAMESPACE
echo

# Check persistent volumes
echo "3. Storage Status:"
kubectl get pvc -n $NAMESPACE -l app=mysql
echo

# Test database connectivity
echo "4. Database Connectivity:"
kubectl exec -n $NAMESPACE \
  $(kubectl get pods -n $NAMESPACE -l app=mysql -o jsonpath='{.items[0].metadata.name}') -- \
  mysql -uasela-test3user -p -e "SELECT 1 as status;" 2>/dev/null && echo "✓ Database accessible" || echo "✗ Database connection failed"
echo

# Check recent events
echo "5. Recent Events:"
kubectl get events -n $NAMESPACE --sort-by='.lastTimestamp' | tail -5
```

### Connection Test

```bash
# Test internal connectivity
kubectl run mysql-test --image=mysql:8.0 --rm -it --restart=Never -- \
  mysql -h mysql.asela-test3.svc.cluster.local \
        -u asela-test3user \
        -pasela-test3db \
        -e "SELECT NOW() as current_time;"
```

## Common Issues

### 1. Connection Problems

#### Symptoms
- Applications cannot connect to database
- "Connection refused" errors
- Timeout errors

#### Diagnosis

```bash
# Check if MySQL pod is running
kubectl get pods -n asela-test3 -l app=mysql

# Check pod logs for errors
kubectl logs -n asela-test3 -l app=mysql --tail=50

# Test network connectivity
kubectl run nettest --image=busybox --rm -it --restart=Never -- \
  nc -zv mysql.asela-test3.svc.cluster.local 3306

# Check service configuration
kubectl describe service mysql -n asela-test3
```

#### Solutions

=== "Pod Not Running"
    ```bash
    # Check pod status and events
    kubectl describe pod -n asela-test3 -l app=mysql
    
    # Check resource constraints
    kubectl top pod -n asela-test3 -l app=mysql
    
    # Restart if necessary
    kubectl delete pod -n asela-test3 -l app=mysql
    ```

=== "Network Issues"
    ```bash
    # Check network policies
    kubectl get networkpolicies -n asela-test3
    
    # Verify service selector
    kubectl get service mysql -n asela-test3 -o yaml
    
    # Check endpoints
    kubectl get endpoints mysql -n asela-test3
    ```

=== "Authentication Issues"
    ```bash
    # Verify credentials from secret
    kubectl get secret asela-test3-secret \
      -n asela-test3 \
      -o jsonpath='{.data.DB_PASSWORD}' | base64 -d
    
    # Check if user exists in database
    kubectl exec -n asela-test3 \
      $(kubectl get pods -n asela-test3 -l app=mysql -o jsonpath='{.items[0].metadata.name}') -- \
      mysql -u root -p$MYSQL_ROOT_PASSWORD \
      -e "SELECT User, Host FROM mysql.user WHERE User = 'asela-test3user';"
    ```

### 2. Performance Issues

#### Symptoms
- Slow query responses
- High CPU usage
- Memory pressure
- Application timeouts

#### Diagnosis

```sql
-- Check current database status
SHOW STATUS LIKE 'Threads_connected';
SHOW STATUS LIKE 'Queries';
SHOW STATUS LIKE 'Slow_queries';

-- Find slow queries
SELECT 
    DIGEST_TEXT,
    COUNT_STAR as executions,
    ROUND(AVG_TIMER_WAIT/1000000000, 2) as avg_seconds,
    ROUND(SUM_TIMER_WAIT/1000000000, 2) as total_seconds
FROM performance_schema.events_statements_summary_by_digest 
ORDER BY SUM_TIMER_WAIT DESC 
LIMIT 10;

-- Check for blocking queries
SHOW PROCESSLIST;

-- Check InnoDB status
SHOW ENGINE INNODB STATUS\G
```

#### Solutions

=== "High CPU Usage"
    ```sql
    -- Identify expensive queries
    SELECT * FROM sys.statements_with_runtimes_in_95th_percentile;
    
    -- Check for missing indexes
    SELECT * FROM sys.statements_with_full_table_scans;
    
    -- Optimize problematic queries
    EXPLAIN FORMAT=JSON SELECT ...; -- Analyze execution plan
    ```

=== "Memory Issues"
    ```bash
    # Check pod memory usage
    kubectl top pod -n asela-test3 -l app=mysql
    
    # Increase memory limits if needed
    kubectl patch deployment mysql -n asela-test3 -p '{"spec":{"template":{"spec":{"containers":[{"name":"mysql","resources":{"limits":{"memory":"4Gi"}}}]}}}}'
    ```

=== "Lock Contention"
    ```sql
    -- Check for deadlocks
    SHOW ENGINE INNODB STATUS\G
    
    -- Monitor current locks
    SELECT * FROM performance_schema.data_locks;
    
    -- Check lock waits
    SELECT * FROM performance_schema.data_lock_waits;
    ```

### 3. Storage Issues

#### Symptoms
- "Disk full" errors
- Database crashes
- Cannot write to database

#### Diagnosis

```bash
# Check disk usage in pod
kubectl exec -n asela-test3 \
  $(kubectl get pods -n asela-test3 -l app=mysql -o jsonpath='{.items[0].metadata.name}') -- \
  df -h

# Check PVC status
kubectl get pvc -n asela-test3 -l app=mysql

# Monitor disk I/O
kubectl exec -n asela-test3 \
  $(kubectl get pods -n asela-test3 -l app=mysql -o jsonpath='{.items[0].metadata.name}') -- \
  iostat -x 1 5
```

#### Solutions

=== "Disk Full"
    ```bash
    # Clean up logs
    kubectl exec -n asela-test3 \
      $(kubectl get pods -n asela-test3 -l app=mysql -o jsonpath='{.items[0].metadata.name}') -- \
      mysql -u root -p$MYSQL_ROOT_PASSWORD \
      -e "PURGE BINARY LOGS BEFORE DATE_SUB(NOW(), INTERVAL 3 DAY);"
    
    # Remove old slow query logs
    kubectl exec -n asela-test3 \
      $(kubectl get pods -n asela-test3 -l app=mysql -o jsonpath='{.items[0].metadata.name}') -- \
      find /var/log/mysql -name "*.log" -mtime +7 -delete
    ```

=== "Expand Storage"
    ```bash
    # Increase PVC size (requires StorageClass with allowVolumeExpansion)
    kubectl patch pvc mysql-data-asela-test3-0 \
      -n asela-test3 \
      -p '{"spec":{"resources":{"requests":{"storage":"100Gi"}}}}'
    
    # Monitor expansion progress
    kubectl get events -n asela-test3 \
      --field-selector involvedObject.kind=PersistentVolumeClaim
    ```

### 4. Backup and Recovery Issues

#### Symptoms
- Backup jobs failing
- Cannot restore from backup
- Data corruption

#### Diagnosis

```bash
# Check backup job status
kubectl get jobs -n asela-test3 -l app=mysql-backup

# Check backup pod logs
kubectl logs -n asela-test3 -l job-name=mysql-backup-$(date +%Y%m%d)

# Verify backup files
kubectl exec -n asela-test3 \
  $(kubectl get pods -n asela-test3 -l app=mysql-backup -o jsonpath='{.items[0].metadata.name}') -- \
  ls -la /backups/
```

#### Solutions

=== "Backup Failures"
    ```bash
    # Manual backup to test
    kubectl exec -n asela-test3 \
      $(kubectl get pods -n asela-test3 -l app=mysql -o jsonpath='{.items[0].metadata.name}') -- \
      mysqldump -u root -p$MYSQL_ROOT_PASSWORD \
      --single-transaction --routines --triggers asela-test3db > manual-backup.sql
    
    # Check backup storage permissions
    kubectl exec -n asela-test3 \
      $(kubectl get pods -n asela-test3 -l app=mysql-backup -o jsonpath='{.items[0].metadata.name}') -- \
      ls -la /backups/
    ```

=== "Restore Issues"
    ```bash
    # Verify backup integrity
    kubectl exec -n asela-test3 \
      $(kubectl get pods -n asela-test3 -l app=mysql -o jsonpath='{.items[0].metadata.name}') -- \
      mysql -u root -p$MYSQL_ROOT_PASSWORD \
      -e "source /backups/backup-file.sql" --verbose
    
    # Check for corruption
    kubectl exec -n asela-test3 \
      $(kubectl get pods -n asela-test3 -l app=mysql -o jsonpath='{.items[0].metadata.name}') -- \
      mysqlcheck -u root -p$MYSQL_ROOT_PASSWORD --check-upgrade --all-databases
    ```

### 5. Security Issues

#### Symptoms
- Authentication failures
- Permission denied errors
- Suspicious access patterns

#### Diagnosis

```sql
-- Check user privileges
SHOW GRANTS FOR 'asela-test3user'@'%';

-- Review recent connections
SELECT * FROM mysql.general_log 
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
ORDER BY event_time DESC;

-- Check for failed login attempts
SELECT * FROM performance_schema.events_statements_history
WHERE EVENT_NAME = 'statement/sql/error'
AND ERRORS > 0;
```

#### Solutions

=== "Password Issues"
    ```bash
    # Rotate password
    NEW_PASSWORD=$(openssl rand -base64 32)
    
    # Update in Vault
    vault kv put secret/asela-test3 DB_PASSWORD="$NEW_PASSWORD"
    
    # Verify External Secrets sync
    kubectl get externalsecret asela-test3-secret \
      -n asela-test3 -o yaml
    ```

=== "Permission Problems"
    ```sql
    -- Grant necessary permissions
    GRANT SELECT, INSERT, UPDATE, DELETE ON asela-test3db.* 
    TO 'asela-test3user'@'%';
    
    -- Reload privileges
    FLUSH PRIVILEGES;
    
    -- Verify grants
    SHOW GRANTS FOR 'asela-test3user'@'%';
    ```

## Error Code Reference

### Common MySQL Error Codes

| Error Code | Description | Common Cause | Solution |
|------------|-------------|--------------|----------|
| **1045** | Access denied | Wrong credentials | Check username/password |
| **1146** | Table doesn't exist | Missing table | Create table or check name |
| **1205** | Lock wait timeout | Deadlock/long transaction | Optimize queries, reduce transaction time |
| **1213** | Deadlock found | Transaction conflict | Implement retry logic |
| **2003** | Can't connect | Network/service issue | Check connectivity and service |
| **2006** | MySQL server has gone away | Connection timeout | Check wait_timeout setting |

### Kubernetes-Specific Errors

| Error | Description | Solution |
|-------|-------------|----------|
| **CrashLoopBackOff** | Pod keeps restarting | Check pod logs, resource limits |
| **ImagePullBackOff** | Cannot pull image | Check image name, registry access |
| **Pending** | Pod cannot be scheduled | Check resource requests, node capacity |
| **PVC Pending** | Storage not available | Check StorageClass, available storage |

## Log Analysis

### Database Logs

```bash
# View MySQL error log
kubectl logs -n asela-test3 -l app=mysql --tail=100

# Search for specific errors
kubectl logs -n asela-test3 -l app=mysql | grep -i error

# Export logs for analysis
kubectl logs -n asela-test3 -l app=mysql --since=24h > mysql-logs-$(date +%Y%m%d).log
```

### Slow Query Log

```bash
# Enable slow query log
kubectl exec -n asela-test3 \
  $(kubectl get pods -n asela-test3 -l app=mysql -o jsonpath='{.items[0].metadata.name}') -- \
  mysql -u root -p$MYSQL_ROOT_PASSWORD \
  -e "SET GLOBAL slow_query_log = 'ON'; SET GLOBAL long_query_time = 2;"

# View slow queries
kubectl exec -n asela-test3 \
  $(kubectl get pods -n asela-test3 -l app=mysql -o jsonpath='{.items[0].metadata.name}') -- \
  tail -f /var/log/mysql/slow.log
```

## Monitoring and Alerting

### Key Metrics to Monitor

Access your monitoring dashboards:

- [Database Overview](https://grafana.example.com/d/mysql-overview/mysql-database-overview?var-database=asela-test3db&var-namespace=asela-test3)
- [Error Dashboard](https://grafana.example.com/d/mysql-errors/mysql-errors?var-namespace=asela-test3)

### Setting Up Alerts

Configure alerts for these critical metrics:

```yaml
# Example Prometheus alert rules
groups:
- name: mysql-asela-test3
  rules:
  - alert: MySQLDown
    expr: up{job="mysql-asela-test3"} == 0
    for: 1m
    labels:
      severity: critical
    annotations:
      summary: "MySQL database is down"
      
  - alert: MySQLSlowQueries
    expr: rate(mysql_global_status_slow_queries{instance="mysql-asela-test3"}[5m]) > 10
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "High number of slow queries detected"
```

## Getting Help

### Internal Resources

1. **Documentation**: Check other sections of this guide
2. **Monitoring**: Review [Grafana dashboards](https://grafana.example.com)
3. **Logs**: Analyze application and database logs

### External Support

1. **Platform Team**: [Slack Channel](https://slack.com/app_redirect?channel=C1234567890)
2. **On-Call**: Page for critical issues
3. **Vendor Support**: MySQL Enterprise support (if applicable)

### Information to Gather

When requesting support, please provide:

1. **Error messages** and relevant logs
2. **Timeline** of when the issue started
3. **Recent changes** to application or database
4. **Performance metrics** from monitoring dashboards
5. **Steps to reproduce** the issue

## Emergency Procedures

### Database Outage

1. **Assess the situation**: Check pod status and logs
2. **Escalate if needed**: Contact on-call engineer
3. **Communicate**: Update stakeholders via Slack
4. **Document**: Record timeline and actions taken

### Data Corruption

1. **Stop applications** immediately
2. **Take snapshot** if possible
3. **Assess corruption extent**
4. **Initiate recovery** from last known good backup
5. **Validate restored data**

### Security Incident

1. **Isolate the database** (network policies)
2. **Rotate all passwords** immediately
3. **Audit access logs**
4. **Contact security team**
5. **Document incident**

## Next Steps

- [Performance Tuning →](performance.md)
- [Security Best Practices →](security.md)
- [Operations Guide →](operations.md)