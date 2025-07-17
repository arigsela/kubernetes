# Authentication Setup

## Overview

This guide covers how to set up secure authentication for your **asela-test3db** MySQL database.

## Database User Configuration

### User Details

| Parameter | Value |
|-----------|-------|
| **Username** | `asela-test3user` |
| **Authentication** | MySQL Native Password |
| **SSL Required** | Yes |

### User Privileges

The database user has been configured with the following privileges:


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    


=== "{{ privilege }}"
    
    **{{ privilege }}**: Administrative privilege for {{ privilege.lower() }} operations
    



## Password Management

### Secure Storage

Database passwords are managed through multiple secure systems:

1. **HashiCorp Vault** (Primary)
2. **Kubernetes Secrets** (Application access)
3. **External Secrets Operator** (Synchronization)

### Password Rotation

!!! warning "Password Rotation"
    Passwords are automatically rotated every 90 days. Applications using connection pooling will reconnect automatically.

#### Manual Password Rotation

If you need to rotate the password manually:

```bash
# Generate new password
NEW_PASSWORD=$(openssl rand -base64 32)

# Update in Vault
vault kv put secret/asela-test3 DB_PASSWORD="$NEW_PASSWORD"

# External Secrets will automatically sync to Kubernetes secret
# Monitor the sync:
kubectl get externalsecret asela-test3-secret -n asela-test3 -w
```

## SSL/TLS Configuration

### Required Settings

All connections to the database **must** use SSL/TLS encryption:

| Setting | Value |
|---------|-------|
| **SSL Mode** | Required |
| **TLS Version** | 1.2+ |
| **Certificate Verification** | Enabled |

### Connection String Examples

=== "Python (PyMySQL)"
    ```python
    import pymysql
    
    connection = pymysql.connect(
        host='mysql.asela-test3.svc.cluster.local',
        user='asela-test3user',
        password=password,
        database='asela-test3db',
        ssl={'ssl_disabled': False},  # SSL required
        ssl_verify_cert=True,
        ssl_verify_identity=True
    )
    ```

=== "Java (JDBC)"
    ```java
    String url = "jdbc:mysql://mysql.asela-test3.svc.cluster.local:3306/asela-test3db" +
                 "?useSSL=true&requireSSL=true&verifyServerCertificate=true";
    
    Connection conn = DriverManager.getConnection(url, "asela-test3user", password);
    ```

=== "Node.js (mysql2)"
    ```javascript
    const connection = mysql.createConnection({
        host: 'mysql.asela-test3.svc.cluster.local',
        user: 'asela-test3user',
        password: password,
        database: 'asela-test3db',
        ssl: {
            rejectUnauthorized: true,
            minVersion: 'TLSv1.2'
        }
    });
    ```

=== "Go"
    ```go
    dsn := fmt.Sprintf("%s:%s@tcp(mysql.asela-test3.svc.cluster.local:3306)/asela-test3db?tls=true&tls-skip-verify=false",
        "asela-test3user", password)
    
    db, err := sql.Open("mysql", dsn)
    ```

## Application Authentication

### Service Account Setup

For applications running in Kubernetes, configure a service account with proper RBAC:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: asela-test3-app
  namespace: asela-test3
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: asela-test3-secret-reader
  namespace: asela-test3
rules:
- apiGroups: [""]
  resources: ["secrets"]
  resourceNames: ["asela-test3-secret"]
  verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: asela-test3-secret-reader
  namespace: asela-test3
subjects:
- kind: ServiceAccount
  name: asela-test3-app
  namespace: asela-test3
roleRef:
  kind: Role
  name: asela-test3-secret-reader
  apiGroup: rbac.authorization.k8s.io
```

### Environment Variables

Configure your application deployment with these environment variables:

```yaml
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      serviceAccountName: asela-test3-app
      containers:
      - name: app
        env:
        - name: DB_HOST
          value: "mysql.asela-test3.svc.cluster.local"
        - name: DB_PORT
          value: "3306"
        - name: DB_NAME
          value: "asela-test3db"
        - name: DB_USER
          value: "asela-test3user"
        - name: DB_PASSWORD
          valueFrom:
            secretKeyRef:
              name: asela-test3-secret
              key: DB_PASSWORD
```

## Vault Integration

### Accessing Secrets

#### Using Vault CLI

```bash
# Authenticate to Vault
vault auth -method=kubernetes role=asela-test3-db-reader

# Read the database password
vault kv get -field=DB_PASSWORD secret/asela-test3
```

#### Using Vault API

```bash
# Get Vault token
VAULT_TOKEN=$(vault write -field=token auth/kubernetes/login \
    role=asela-test3-db-reader \
    jwt=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token))

# Retrieve password
curl -H "X-Vault-Token: $VAULT_TOKEN" \
    https://vault.arigsela.com/v1/secret/data/asela-test3 | \
    jq -r '.data.data.DB_PASSWORD'
```

### Vault Policy

The following Vault policy is configured for your application:

```hcl
# Policy: asela-test3-db-reader
path "secret/data/asela-test3" {
  capabilities = ["read"]
}

path "secret/metadata/asela-test3" {
  capabilities = ["read"]
}
```

## Security Best Practices

### Application Level

1. **Never log passwords** in application logs
2. **Use connection pooling** to minimize connection overhead
3. **Implement query timeouts** to prevent long-running queries
4. **Validate all inputs** to prevent SQL injection
5. **Use prepared statements** for all dynamic queries

### Network Level

1. **Network policies** restrict database access to authorized pods only
2. **Service mesh** (if available) provides additional encryption
3. **Egress filtering** prevents unauthorized external connections

### Monitoring

1. **Failed login attempts** are logged and monitored
2. **Unusual query patterns** trigger alerts
3. **Connection metrics** are tracked in Grafana

## Troubleshooting Authentication

### Common Issues

1. **SSL Certificate Errors**
   ```bash
   # Check SSL configuration
   mysql -h mysql.asela-test3.svc.cluster.local \
         -u asela-test3user -p \
         --ssl-mode=REQUIRED \
         --ssl-verify-server-cert
   ```

2. **Permission Denied**
   ```bash
   # Verify password from secret
   kubectl get secret asela-test3-secret \
     -n asela-test3 \
     -o jsonpath='{.data.DB_PASSWORD}' | base64 -d
   ```

3. **Connection Timeout**
   ```bash
   # Test network connectivity
   kubectl run -it --rm debug --image=busybox --restart=Never -- \
     nc -zv mysql.asela-test3.svc.cluster.local 3306
   ```

### Getting Help

For authentication issues, contact the platform team:

- **Slack**: [Support Channel](https://slack.com/app_redirect?channel=C1234567890)
- **Email**: platform-team@company.com
- **Emergency**: Page the on-call engineer

## Next Steps

- [Connection Guide →](connection.md)
- [Operations Guide →](operations.md)
- [Security Best Practices →](security.md)