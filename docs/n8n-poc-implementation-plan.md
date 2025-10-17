# n8n Workflow Automation - POC Implementation Plan

**Document Version:** 1.0
**Created:** 2025-10-17
**Last Updated:** 2025-10-17
**Status:** Planning - Phase 0
**Cluster:** K3s Homelab
**Type:** Proof of Concept (Ephemeral)

### Component Versions
- **n8n:** latest (n8nio/n8n:latest)
- **PostgreSQL:** 15.3 or later

**Important Notes:**
- ⚠️ **POC Only**: No persistent storage - all data lost on pod restart
- ⚠️ **Internal Access Only**: No external ingress - accessed via port-forward or NodePort
- ⚠️ **n8n v1.0+ requires PostgreSQL** - MySQL is not supported
- ✅ **GitOps**: All resources managed via ArgoCD
- ✅ **Vault Integration**: Secrets managed via External Secrets Operator

---

## Table of Contents
1. [Executive Summary](#executive-summary)
2. [Architecture Overview](#architecture-overview)
3. [Prerequisites](#prerequisites)
4. [Implementation Phases](#implementation-phases)
5. [Configuration Details](#configuration-details)
6. [Testing & Validation](#testing--validation)
7. [Access Methods](#access-methods)
8. [Cleanup & Removal](#cleanup--removal)

---

## Executive Summary

### Objective
Deploy n8n workflow automation platform as a **Proof of Concept** in the K3s homelab cluster with ephemeral storage for testing and evaluation purposes.

### Key Benefits
- **Workflow Automation**: Visual workflow builder for integrating 400+ services
- **Self-Hosted**: Full control over data and workflows
- **Fast Setup**: Minimal configuration for POC evaluation (~1.5-2 hours)
- **GitOps Ready**: Managed via ArgoCD for easy deployment
- **Low Resources**: ~1Gi memory, ~750m CPU footprint

### POC Limitations
- ⚠️ **No Persistence**: All workflows, credentials, and data lost on pod restart
- ⚠️ **Internal Only**: No external HTTPS access (accessed via kubectl port-forward)
- ⚠️ **Single Instance**: No high availability or redundancy
- ⚠️ **Ephemeral Database**: PostgreSQL data also non-persistent

### Success Criteria
- ✅ n8n web interface accessible via port-forward
- ✅ PostgreSQL database connection working
- ✅ Can create and execute basic workflows
- ✅ Webhook triggers functional
- ✅ Credentials can be stored and used
- ✅ ArgoCD auto-sync enabled for all components

### Migration Path (POC → Production)
If POC is successful, production deployment would add:
1. Persistent volumes for both n8n and PostgreSQL
2. External ingress with TLS (cert-manager integration)
3. Automated backups
4. Resource scaling and HA configuration
5. Monitoring and alerting integration

---

## Architecture Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         K3s Cluster                             │
│                                                                  │
│  ┌────────────────────────────────────────────────────────┐    │
│  │              n8n Namespace                              │    │
│  │                                                          │    │
│  │  ┌──────────────────┐         ┌──────────────────┐     │    │
│  │  │   n8n Pod        │         │  PostgreSQL Pod  │     │    │
│  │  │                  │         │                  │     │    │
│  │  │  Port: 5678      │────────▶│  Port: 5432      │     │    │
│  │  │  (HTTP)          │         │  (ephemeral)     │     │    │
│  │  │                  │         │                  │     │    │
│  │  │  Secrets from    │         │  Secrets from    │     │    │
│  │  │  Vault           │         │  Vault           │     │    │
│  │  └──────────────────┘         └──────────────────┘     │    │
│  │           │                             │               │    │
│  │           │                             │               │    │
│  │  ┌────────▼─────────────────────────────▼──────┐       │    │
│  │  │      External Secrets Operator       │       │    │
│  │  │      (pulls from Vault)               │       │    │
│  │  └──────────────────────────────────────────────┘       │    │
│  │                                                          │    │
│  └────────────────────────────────────────────────────────┘    │
│                           │                                      │
│                           │ kubectl port-forward                │
└───────────────────────────┼──────────────────────────────────────┘
                            │
                            ▼
                   Developer Workstation
                   http://localhost:5678
```

### Component Flow

1. **PostgreSQL Pod** (ephemeral storage)
   - Stores n8n data (workflows, credentials, executions)
   - Credentials from Vault via External Secrets
   - No persistent volume - data lost on restart

2. **n8n Pod** (ephemeral storage)
   - Connects to PostgreSQL for data storage
   - Workflow execution engine
   - Web UI on port 5678
   - Secrets (encryption key, DB creds) from Vault

3. **External Secrets Operator**
   - Syncs secrets from Vault to Kubernetes secrets
   - Separate SecretStores for postgresql and n8n namespaces

4. **Access Method**
   - kubectl port-forward for local access
   - Alternative: NodePort service for stable internal IP

---

## Prerequisites

### Required Infrastructure
- ✅ K3s cluster running
- ✅ ArgoCD installed and operational
- ✅ Vault deployed with Kubernetes auth enabled
- ✅ External Secrets Operator installed
- ✅ kubectl access to cluster

### Vault Configuration Required
The following secrets must be created in Vault:

**Path:** `k8s-secrets/postgresql`
```json
{
  "root-password": "<random-password>",
  "database-name": "n8n",
  "n8n-user": "n8n",
  "n8n-password": "<random-password>"
}
```

**Path:** `k8s-secrets/n8n`
```json
{
  "encryption-key": "<32-character-random-string>",
  "db-host": "postgresql.postgresql.svc.cluster.local",
  "db-port": "5432",
  "db-name": "n8n",
  "db-user": "n8n",
  "db-password": "<same-as-n8n-password-above>",
  "webhook-url": "http://n8n.n8n.svc.cluster.local:5678"
}
```

**Vault Roles Required:**
- `postgresql` - Read access to `k8s-secrets/postgresql`
- `n8n` - Read access to `k8s-secrets/n8n`

### Resources Required
- **Memory**: ~1Gi total (500Mi PostgreSQL + 500Mi n8n)
- **CPU**: ~750m total (250m PostgreSQL + 500m n8n)
- **Storage**: None (ephemeral only)

---

## Implementation Phases

### Phase 0: Preparation ⬜ (0/3 tasks)
**Objective:** Prepare Vault secrets and validate prerequisites

#### Subphase 0.1: Generate Secrets ⬜
**Tasks:**
- ⬜ Generate random PostgreSQL root password
- ⬜ Generate random n8n database user password
- ⬜ Generate 32-character encryption key for n8n

**Commands:**
```bash
# Generate passwords (32 chars each)
openssl rand -base64 24

# Generate n8n encryption key (32 chars)
openssl rand -hex 16
```

#### Subphase 0.2: Configure Vault Secrets ⬜
**Tasks:**
- ⬜ Create `k8s-secrets/postgresql` secret in Vault
- ⬜ Create `k8s-secrets/n8n` secret in Vault
- ⬜ Verify secrets are readable

**Vault Commands:**
```bash
# Write PostgreSQL secrets
vault kv put k8s-secrets/postgresql \
  root-password="<generated-password>" \
  database-name="n8n" \
  n8n-user="n8n" \
  n8n-password="<generated-password>"

# Write n8n secrets
vault kv put k8s-secrets/n8n \
  encryption-key="<generated-32-char-key>" \
  db-host="postgresql.postgresql.svc.cluster.local" \
  db-port="5432" \
  db-name="n8n" \
  db-user="n8n" \
  db-password="<same-as-above>" \
  webhook-url="http://n8n.n8n.svc.cluster.local:5678"

# Verify
vault kv get k8s-secrets/postgresql
vault kv get k8s-secrets/n8n
```

#### Subphase 0.3: Configure Vault Roles ⬜
**Tasks:**
- ⬜ Create/verify Vault role for postgresql namespace
- ⬜ Create/verify Vault role for n8n namespace
- ⬜ Test role access with sample ServiceAccount

**Testing:**
```bash
# Test after Phase 1.2 completes
kubectl exec -n postgresql -it <postgresql-pod> -- env | grep POSTGRES
kubectl exec -n n8n -it <n8n-pod> -- env | grep N8N
```

---

### Phase 1: PostgreSQL Deployment ⬜ (0/12 tasks)
**Objective:** Deploy ephemeral PostgreSQL database for n8n

**Completion:** 0/12 tasks (0%)

#### Subphase 1.1: Create PostgreSQL Manifests ⬜
**Tasks:**
- ⬜ Create `base-apps/postgresql/` directory
- ⬜ Create `deployments.yaml` with PostgreSQL 15 container
- ⬜ Create `services.yaml` with ClusterIP service
- ⬜ Create `secret-store.yaml` for Vault integration
- ⬜ Create `external-secrets.yaml` for DB credentials

**Files to Create:**
```
base-apps/
├── postgresql/
│   ├── deployments.yaml       # PostgreSQL deployment (no PVC)
│   ├── services.yaml          # ClusterIP on port 5432
│   ├── secret-store.yaml      # Vault SecretStore
│   └── external-secrets.yaml  # DB credentials from Vault
└── postgresql.yaml            # ArgoCD Application
```

**Deployment Configuration:**
```yaml
# Key settings for deployments.yaml
image: postgres:15.3
env:
  - POSTGRES_DB: (from secret)
  - POSTGRES_USER: (from secret)
  - POSTGRES_PASSWORD: (from secret)
  - PGDATA: /var/lib/postgresql/data/pgdata
resources:
  requests:
    memory: 500Mi
    cpu: 250m
  limits:
    memory: 1Gi
    cpu: 500m
# NO volumeMounts - ephemeral storage only
```

#### Subphase 1.2: Deploy PostgreSQL via ArgoCD ⬜
**Tasks:**
- ⬜ Create `base-apps/postgresql.yaml` ArgoCD Application
- ⬜ Commit and push to Git repository
- ⬜ Verify ArgoCD sync status
- ⬜ Verify postgresql namespace created
- ⬜ Verify pod is running and healthy

**ArgoCD Application Configuration:**
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: postgresql
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/postgresql
  destination:
    server: https://kubernetes.default.svc
    namespace: postgresql
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

**Validation Commands:**
```bash
# Check ArgoCD sync
kubectl get applications -n argo-cd postgresql

# Check pod status
kubectl get pods -n postgresql

# Check service
kubectl get svc -n postgresql

# Test database connection
kubectl exec -n postgresql -it postgresql-<pod-id> -- psql -U n8n -d n8n -c '\l'
```

#### Subphase 1.3: Verify PostgreSQL Functionality ⬜
**Tasks:**
- ⬜ Verify secrets are populated from Vault
- ⬜ Test database connection internally
- ⬜ Verify service DNS resolution

**Testing:**
```bash
# Check secrets
kubectl get secrets -n postgresql
kubectl describe secret postgresql-credentials -n postgresql

# Test DB connection from another pod
kubectl run -n postgresql pg-test --rm -it --image=postgres:15.3 -- \
  psql -h postgresql.postgresql.svc.cluster.local -U n8n -d n8n

# Verify DNS
kubectl run -n postgresql dns-test --rm -it --image=busybox -- \
  nslookup postgresql.postgresql.svc.cluster.local
```

---

### Phase 2: n8n Application Deployment ⬜ (0/11 tasks)
**Objective:** Deploy n8n with connection to PostgreSQL

**Completion:** 0/11 tasks (0%)

#### Subphase 2.1: Create n8n Manifests ⬜
**Tasks:**
- ⬜ Create `base-apps/n8n/` directory
- ⬜ Create `deployments.yaml` with n8n container
- ⬜ Create `services.yaml` with ClusterIP service
- ⬜ Create `secret-store.yaml` for Vault integration
- ⬜ Create `external-secrets.yaml` for n8n credentials

**Files to Create:**
```
base-apps/
├── n8n/
│   ├── deployments.yaml       # n8n deployment (no PVC)
│   ├── services.yaml          # ClusterIP on port 5678
│   ├── secret-store.yaml      # Vault SecretStore
│   └── external-secrets.yaml  # n8n credentials from Vault
└── n8n.yaml                   # ArgoCD Application
```

**Deployment Configuration:**
```yaml
# Key settings for deployments.yaml
image: n8nio/n8n:latest
env:
  - DB_TYPE: postgresdb
  - DB_POSTGRESDB_HOST: postgresql.postgresql.svc.cluster.local
  - DB_POSTGRESDB_PORT: 5432
  - DB_POSTGRESDB_DATABASE: (from secret)
  - DB_POSTGRESDB_USER: (from secret)
  - DB_POSTGRESDB_PASSWORD: (from secret)
  - N8N_ENCRYPTION_KEY: (from secret)
  - WEBHOOK_URL: (from secret)
  - N8N_PROTOCOL: http
  - N8N_PORT: 5678
  - N8N_LOG_LEVEL: info
resources:
  requests:
    memory: 500Mi
    cpu: 500m
  limits:
    memory: 1Gi
    cpu: 1000m
# NO volumeMounts - ephemeral storage only
```

#### Subphase 2.2: Deploy n8n via ArgoCD ⬜
**Tasks:**
- ⬜ Create `base-apps/n8n.yaml` ArgoCD Application
- ⬜ Commit and push to Git repository
- ⬜ Verify ArgoCD sync status
- ⬜ Verify n8n namespace created
- ⬜ Verify pod is running and healthy

**ArgoCD Application Configuration:**
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: n8n
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/n8n
  destination:
    server: https://kubernetes.default.svc
    namespace: n8n
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

**Validation Commands:**
```bash
# Check ArgoCD sync
kubectl get applications -n argo-cd n8n

# Check pod status
kubectl get pods -n n8n
kubectl logs -n n8n -l app=n8n --tail=50

# Check service
kubectl get svc -n n8n

# Check database connection from n8n logs
kubectl logs -n n8n -l app=n8n | grep -i "database\|postgres\|connection"
```

#### Subphase 2.3: Verify n8n Functionality ⬜
**Tasks:**
- ⬜ Verify secrets are populated from Vault
- ⬜ Verify n8n connects to PostgreSQL successfully
- ⬜ Check n8n logs for startup errors

**Testing:**
```bash
# Check secrets
kubectl get secrets -n n8n
kubectl describe secret n8n-secrets -n n8n

# Check n8n startup logs
kubectl logs -n n8n -l app=n8n --tail=100

# Verify database tables were created
kubectl exec -n postgresql -it postgresql-<pod-id> -- \
  psql -U n8n -d n8n -c '\dt'
```

---

### Phase 3: Access Configuration ⬜ (0/5 tasks)
**Objective:** Configure internal access to n8n web interface

**Completion:** 0/5 tasks (0%)

#### Subphase 3.1: Configure Port-Forward Access ⬜
**Tasks:**
- ⬜ Set up port-forward to n8n service
- ⬜ Verify web interface loads at http://localhost:5678
- ⬜ Document access instructions

**Port-Forward Setup:**
```bash
# Forward local port 5678 to n8n service
kubectl port-forward -n n8n svc/n8n 5678:5678

# Access n8n
# Open browser to: http://localhost:5678
```

**Alternative: NodePort Service (Optional):**
```yaml
# Modify services.yaml to use NodePort instead of ClusterIP
apiVersion: v1
kind: Service
metadata:
  name: n8n
  namespace: n8n
spec:
  type: NodePort
  ports:
  - port: 5678
    targetPort: 5678
    nodePort: 30678  # Accessible at http://<node-ip>:30678
  selector:
    app: n8n
```

#### Subphase 3.2: Initial n8n Setup ⬜
**Tasks:**
- ⬜ Access n8n web interface
- ⬜ Complete initial setup wizard (create admin user)
- ⬜ Verify dashboard loads correctly

**Setup Steps:**
1. Access http://localhost:5678
2. Create owner account (email/password)
3. Skip usage/telemetry prompts (or configure as desired)
4. Verify you reach the main n8n dashboard

---

### Phase 4: Testing & Validation ⬜ (0/6 tasks)
**Objective:** Validate n8n functionality end-to-end

**Completion:** 0/6 tasks (0%)

#### Subphase 4.1: Basic Workflow Testing ⬜
**Tasks:**
- ⬜ Create a simple test workflow
- ⬜ Execute workflow manually
- ⬜ Verify execution completes successfully

**Test Workflow:**
```
1. Create new workflow
2. Add "Manual Trigger" node
3. Add "Set" node (set a variable)
4. Add "HTTP Request" node (GET request to httpbin.org/get)
5. Save and execute manually
6. Verify execution shows success
```

#### Subphase 4.2: Webhook Testing ⬜
**Tasks:**
- ⬜ Create workflow with webhook trigger
- ⬜ Activate workflow
- ⬜ Test webhook with curl from within cluster
- ⬜ Verify webhook execution logs

**Webhook Test:**
```bash
# Get webhook URL from n8n workflow
# Test from a pod in the cluster
kubectl run -n n8n curl-test --rm -it --image=curlimages/curl -- \
  curl -X POST http://n8n.n8n.svc.cluster.local:5678/webhook/<webhook-id> \
  -H "Content-Type: application/json" \
  -d '{"test": "data"}'
```

#### Subphase 4.3: Credential Storage Testing ⬜
**Tasks:**
- ⬜ Create a test credential in n8n
- ⬜ Use credential in a workflow
- ⬜ Verify credential persists (until pod restart)

**Test Steps:**
1. Go to Credentials > Add Credential
2. Create a basic auth credential
3. Use in HTTP Request node
4. Verify credential is encrypted in database

---

## Configuration Details

### PostgreSQL Configuration

**Namespace:** postgresql

**Key Environment Variables:**
- `POSTGRES_DB`: n8n (from Vault)
- `POSTGRES_USER`: n8n (from Vault)
- `POSTGRES_PASSWORD`: <from Vault>
- `PGDATA`: /var/lib/postgresql/data/pgdata

**Service:**
- Name: postgresql
- Type: ClusterIP
- Port: 5432
- DNS: postgresql.postgresql.svc.cluster.local

**Resource Limits:**
- Memory: 500Mi request, 1Gi limit
- CPU: 250m request, 500m limit

**Storage:**
- None (ephemeral) - uses emptyDir or container filesystem

---

### n8n Configuration

**Namespace:** n8n

**Key Environment Variables:**
- `DB_TYPE`: postgresdb
- `DB_POSTGRESDB_HOST`: postgresql.postgresql.svc.cluster.local
- `DB_POSTGRESDB_PORT`: 5432
- `DB_POSTGRESDB_DATABASE`: n8n (from Vault)
- `DB_POSTGRESDB_USER`: n8n (from Vault)
- `DB_POSTGRESDB_PASSWORD`: <from Vault>
- `N8N_ENCRYPTION_KEY`: <from Vault>
- `WEBHOOK_URL`: http://n8n.n8n.svc.cluster.local:5678
- `N8N_PROTOCOL`: http
- `N8N_PORT`: 5678
- `N8N_LOG_LEVEL`: info

**Service:**
- Name: n8n
- Type: ClusterIP (or NodePort for alternative access)
- Port: 5678
- DNS: n8n.n8n.svc.cluster.local

**Resource Limits:**
- Memory: 500Mi request, 1Gi limit
- CPU: 500m request, 1000m limit

**Storage:**
- None (ephemeral) - workflow data in PostgreSQL only

---

### Vault Secret Structure

**PostgreSQL Secrets** (`k8s-secrets/postgresql`):
```json
{
  "root-password": "<random-32-chars>",
  "database-name": "n8n",
  "n8n-user": "n8n",
  "n8n-password": "<random-32-chars>"
}
```

**n8n Secrets** (`k8s-secrets/n8n`):
```json
{
  "encryption-key": "<32-char-hex-string>",
  "db-host": "postgresql.postgresql.svc.cluster.local",
  "db-port": "5432",
  "db-name": "n8n",
  "db-user": "n8n",
  "db-password": "<same-as-above>",
  "webhook-url": "http://n8n.n8n.svc.cluster.local:5678"
}
```

---

## Testing & Validation

### Health Checks

**PostgreSQL Health:**
```bash
# Check pod is running
kubectl get pods -n postgresql

# Check database is accessible
kubectl exec -n postgresql -it <pod-name> -- psql -U n8n -d n8n -c 'SELECT version();'

# Check tables created by n8n
kubectl exec -n postgresql -it <pod-name> -- psql -U n8n -d n8n -c '\dt'
```

**n8n Health:**
```bash
# Check pod is running
kubectl get pods -n n8n

# Check logs for successful startup
kubectl logs -n n8n -l app=n8n --tail=50

# Check database connection in logs
kubectl logs -n n8n -l app=n8n | grep -i postgres

# Access health endpoint (if port-forward active)
curl http://localhost:5678/healthz
```

### Functional Tests

**Test 1: Manual Workflow Execution**
1. Create workflow with Manual Trigger + HTTP Request node
2. Execute manually
3. Verify execution succeeds in execution log

**Test 2: Webhook Trigger**
1. Create workflow with Webhook Trigger
2. Activate workflow
3. Send POST request to webhook URL
4. Verify workflow executes

**Test 3: Credential Management**
1. Add HTTP Header Auth credential
2. Use in HTTP Request node
3. Verify credential works and is encrypted

**Test 4: Data Persistence (within session)**
1. Create and save workflow
2. Reload browser
3. Verify workflow still exists

**Test 5: Pod Restart Behavior (ephemeral warning)**
1. Note current workflows
2. Restart n8n pod: `kubectl rollout restart -n n8n deployment/n8n`
3. Verify all workflows are LOST (expected for POC)

---

## Access Methods

### Method 1: kubectl port-forward (Recommended for POC)

**Setup:**
```bash
kubectl port-forward -n n8n svc/n8n 5678:5678
```

**Access:**
- URL: http://localhost:5678
- Pros: Simple, secure (only local access)
- Cons: Must keep terminal open, breaks if network disconnects

**Permanent Setup (systemd service example):**
```bash
# Create systemd service for auto port-forward
sudo tee /etc/systemd/system/n8n-port-forward.service << EOF
[Unit]
Description=n8n Port Forward
After=network.target

[Service]
Type=simple
User=$USER
ExecStart=/usr/local/bin/kubectl port-forward -n n8n svc/n8n 5678:5678
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable n8n-port-forward
sudo systemctl start n8n-port-forward
```

### Method 2: NodePort Service

**Setup:**
Modify `base-apps/n8n/services.yaml`:
```yaml
apiVersion: v1
kind: Service
metadata:
  name: n8n
  namespace: n8n
spec:
  type: NodePort
  ports:
  - port: 5678
    targetPort: 5678
    nodePort: 30678  # Static port (30000-32767 range)
  selector:
    app: n8n
```

**Access:**
- URL: http://<any-node-ip>:30678
- Pros: Stable, always accessible from network
- Cons: Port exposed on all nodes, no TLS

**Find Node IP:**
```bash
kubectl get nodes -o wide
```

### Method 3: ClusterIP + kubectl proxy (Alternative)

**Setup:**
```bash
kubectl proxy --port=8080
```

**Access:**
- URL: http://localhost:8080/api/v1/namespaces/n8n/services/n8n:5678/proxy/
- Pros: Works through Kubernetes API
- Cons: Long URL, must keep proxy running

---

## Cleanup & Removal

### Remove n8n POC

**Quick Removal:**
```bash
# Delete ArgoCD applications
kubectl delete application -n argo-cd n8n
kubectl delete application -n argo-cd postgresql

# Delete namespaces (if ArgoCD doesn't clean up)
kubectl delete namespace n8n
kubectl delete namespace postgresql
```

**Vault Cleanup:**
```bash
# Remove secrets from Vault
vault kv delete k8s-secrets/postgresql
vault kv delete k8s-secrets/n8n

# Optionally remove Vault roles
vault delete auth/kubernetes/role/postgresql
vault delete auth/kubernetes/role/n8n
```

**Git Cleanup:**
```bash
# Remove files
rm -rf base-apps/n8n/
rm -rf base-apps/postgresql/
rm base-apps/n8n.yaml
rm base-apps/postgresql.yaml

# Commit removal
git add -A
git commit -m "Remove n8n POC deployment"
git push origin main
```

**Verify Cleanup:**
```bash
# Check namespaces removed
kubectl get namespaces | grep -E 'n8n|postgresql'

# Check ArgoCD applications removed
kubectl get applications -n argo-cd | grep -E 'n8n|postgresql'

# Check no lingering resources
kubectl get all -n n8n
kubectl get all -n postgresql
```

---

## Troubleshooting

### n8n Pod Won't Start

**Symptom:** Pod in CrashLoopBackOff or Error state

**Diagnosis:**
```bash
# Check pod events
kubectl describe pod -n n8n <pod-name>

# Check logs
kubectl logs -n n8n <pod-name>

# Common issues to look for:
# - "Unable to connect to database"
# - "N8N_ENCRYPTION_KEY not set"
# - "Invalid database credentials"
```

**Solutions:**
1. **Database Connection Issues:**
   ```bash
   # Verify PostgreSQL is running
   kubectl get pods -n postgresql

   # Test connection from n8n namespace
   kubectl run -n n8n pg-test --rm -it --image=postgres:15.3 -- \
     psql -h postgresql.postgresql.svc.cluster.local -U n8n -d n8n
   ```

2. **Missing Secrets:**
   ```bash
   # Check secrets exist
   kubectl get secrets -n n8n
   kubectl describe secret n8n-secrets -n n8n

   # Check External Secrets status
   kubectl get externalsecrets -n n8n
   kubectl describe externalsecret -n n8n n8n-secrets
   ```

3. **Encryption Key Issues:**
   ```bash
   # Verify encryption key is 32 characters
   kubectl get secret -n n8n n8n-secrets -o jsonpath='{.data.encryption-key}' | base64 -d | wc -c
   # Should output: 32
   ```

### PostgreSQL Connection Failures

**Symptom:** n8n logs show "ECONNREFUSED" or "Connection refused"

**Diagnosis:**
```bash
# Check PostgreSQL pod status
kubectl get pods -n postgresql
kubectl logs -n postgresql <pod-name>

# Check PostgreSQL service
kubectl get svc -n postgresql

# Test DNS resolution from n8n namespace
kubectl run -n n8n dns-test --rm -it --image=busybox -- \
  nslookup postgresql.postgresql.svc.cluster.local
```

**Solutions:**
1. **Service not found:**
   - Verify service exists: `kubectl get svc -n postgresql postgresql`
   - Check service selector matches pod labels

2. **PostgreSQL not accepting connections:**
   ```bash
   # Check PostgreSQL is listening
   kubectl exec -n postgresql <pod-name> -- netstat -tlnp | grep 5432

   # Check PostgreSQL logs for errors
   kubectl logs -n postgresql <pod-name> | grep -i error
   ```

### Web Interface Not Accessible

**Symptom:** Cannot access http://localhost:5678

**Diagnosis:**
```bash
# Verify port-forward is running
ps aux | grep 'port-forward'

# Check n8n pod is running
kubectl get pods -n n8n

# Check n8n logs
kubectl logs -n n8n -l app=n8n
```

**Solutions:**
1. **Port-forward not running:**
   ```bash
   kubectl port-forward -n n8n svc/n8n 5678:5678
   ```

2. **n8n not listening on port:**
   ```bash
   # Check n8n logs for port binding
   kubectl logs -n n8n -l app=n8n | grep -i "listening\|port"

   # Should see: "Editor is now accessible via: http://localhost:5678"
   ```

3. **Firewall blocking local port:**
   ```bash
   # Check if port is in use
   lsof -i :5678

   # Try different local port
   kubectl port-forward -n n8n svc/n8n 8080:5678
   # Access at http://localhost:8080
   ```

### Workflows Lost After Restart

**Symptom:** All workflows disappear after pod restart

**Expected Behavior:** ⚠️ This is EXPECTED for POC deployment (no persistent storage)

**Explanation:**
- POC uses ephemeral storage only
- All workflow data stored in PostgreSQL
- PostgreSQL also uses ephemeral storage
- Pod restarts = data loss

**Solution (for production):**
- Add PersistentVolumeClaims to both n8n and PostgreSQL
- See "Migration Path (POC → Production)" section above

### External Secrets Not Syncing

**Symptom:** Secrets not populated from Vault

**Diagnosis:**
```bash
# Check ExternalSecret status
kubectl get externalsecrets -n n8n
kubectl describe externalsecret -n n8n n8n-secrets

# Check SecretStore status
kubectl get secretstore -n n8n
kubectl describe secretstore -n n8n vault-backend

# Check External Secrets Operator logs
kubectl logs -n external-secrets-system -l app.kubernetes.io/name=external-secrets
```

**Solutions:**
1. **Vault role not configured:**
   ```bash
   # Verify Vault role exists
   vault read auth/kubernetes/role/n8n

   # Create role if missing
   vault write auth/kubernetes/role/n8n \
     bound_service_account_names=default \
     bound_service_account_namespaces=n8n \
     policies=n8n-policy \
     ttl=1h
   ```

2. **Secrets not in Vault:**
   ```bash
   # Verify secrets exist
   vault kv get k8s-secrets/n8n
   vault kv get k8s-secrets/postgresql
   ```

3. **Wrong path or keys:**
   - Check ExternalSecret `remoteRef.key` matches Vault path
   - Check `property` names match Vault secret keys

---

## Migration Path: POC to Production

If POC is successful, upgrade to production with these changes:

### 1. Add Persistent Storage

**PostgreSQL:**
```yaml
# Add to deployments.yaml
volumeMounts:
  - name: postgres-storage
    mountPath: /var/lib/postgresql/data
volumes:
  - name: postgres-storage
    persistentVolumeClaim:
      claimName: postgresql-pvc

# Create pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgresql-pvc
  namespace: postgresql
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
  storageClassName: local-path  # or your storage class
```

**n8n:**
```yaml
# Add to deployments.yaml
volumeMounts:
  - name: n8n-storage
    mountPath: /home/node/.n8n
volumes:
  - name: n8n-storage
    persistentVolumeClaim:
      claimName: n8n-pvc

# Create pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: n8n-pvc
  namespace: n8n
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 5Gi
  storageClassName: local-path
```

### 2. Add External Ingress with TLS

```yaml
# Create base-apps/n8n/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: n8n-ingress
  namespace: n8n
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - n8n.arigsela.com
    secretName: n8n-tls
  rules:
  - host: n8n.arigsela.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: n8n
            port:
              number: 5678
```

**Update n8n environment variables:**
```yaml
- name: WEBHOOK_URL
  value: "https://n8n.arigsela.com"
- name: N8N_PROTOCOL
  value: "https"
- name: N8N_HOST
  value: "n8n.arigsela.com"
```

### 3. Add Backups

**PostgreSQL Backup CronJob:**
```yaml
# Create base-apps/postgresql/backup-cronjob.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgresql-backup
  namespace: postgresql
spec:
  schedule: "0 2 * * *"  # Daily at 2 AM
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: backup
            image: postgres:15.3
            command:
            - /bin/sh
            - -c
            - |
              pg_dump -h postgresql -U n8n n8n > /backup/n8n-$(date +%Y%m%d-%H%M%S).sql
            env:
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: postgresql-credentials
                  key: n8n-password
            volumeMounts:
            - name: backup-storage
              mountPath: /backup
          volumes:
          - name: backup-storage
            persistentVolumeClaim:
              claimName: postgresql-backup-pvc
          restartPolicy: OnFailure
```

### 4. Scale Resources

**Increase resource limits for production:**
```yaml
# PostgreSQL
resources:
  requests:
    memory: 1Gi
    cpu: 500m
  limits:
    memory: 2Gi
    cpu: 1000m

# n8n
resources:
  requests:
    memory: 1Gi
    cpu: 1000m
  limits:
    memory: 2Gi
    cpu: 2000m
```

### 5. Add Monitoring

**Integrate with Prometheus (if deployed):**
```yaml
# Add to n8n service
metadata:
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/port: "5678"
    prometheus.io/path: "/metrics"
```

---

## Progress Tracking

**Last Updated:** 2025-10-17
**Current Phase:** Phase 0 - Preparation
**Overall Completion:** 0/34 tasks (0%)

### Phase Summary
- ✅ Phase 0: Preparation - 0/3 tasks (0%)
- ⬜ Phase 1: PostgreSQL Deployment - 0/12 tasks (0%)
- ⬜ Phase 2: n8n Deployment - 0/11 tasks (0%)
- ⬜ Phase 3: Access Configuration - 0/5 tasks (0%)
- ⬜ Phase 4: Testing & Validation - 0/6 tasks (0%)

### Next Steps
1. Generate random passwords and encryption key
2. Configure Vault secrets for PostgreSQL and n8n
3. Verify Vault roles for both namespaces
4. Begin Phase 1: PostgreSQL deployment

---

## Document Updates

**Version History:**
- v1.0 (2025-10-17): Initial POC implementation plan created
  - Defined 4 phases with 34 total tasks
  - Documented ephemeral architecture
  - Added troubleshooting guide
  - Included migration path to production
