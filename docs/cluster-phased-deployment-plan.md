# Kubernetes Cluster Phased Deployment Implementation Plan

**Status**: Phase 4 Complete ✅
**Last Updated**: 2025-11-04
**Branch**: maintenance-mode
**Overall Progress**: 67% (Phase 4 of 6 complete)

---

## Overview

After recovering from cluster stability issues, this plan implements a controlled, incremental deployment of all applications to prevent cluster overload and ensure stability at each phase.

### Context
- Previous issue: Deploying all applications simultaneously overwhelmed the cluster
- Solution: Created all ArgoCD Application manifests as `.disabled` files
- Approach: Enable applications in phases based on dependencies
- Total Applications: 19 (6 pre-existing, 13 newly created)

### Goals
1. Deploy applications incrementally without overwhelming cluster resources
2. Validate health and stability at each phase before proceeding
3. Resolve dependency issues (node affinity, CRDs, port conflicts) as they arise
4. Document all configuration decisions and credentials securely

---

## Phased Implementation Plan

### Phase 0: Baseline (COMPLETED ✅)
**Goal**: Establish stable ArgoCD foundation
**Status**: ✅ Complete

#### Tasks
- [x] Comment out all base-apps applications
- [x] Run terraform apply to deploy only ArgoCD
- [x] Verify ArgoCD deployment health
- [x] Update master-app to track `maintenance-mode` branch
- [x] Sanity check cluster resource utilization

#### Results
- ArgoCD v3.1.9 deployed and healthy
- Cluster stable with low resource usage
- All nodes (master + 3 workers) operational

---

### Phase 1: Core Infrastructure (COMPLETED ✅)
**Goal**: Deploy foundation services for TLS, ingress, and secrets
**Status**: ✅ Complete
**Duration**: ~2 hours

#### Applications
1. **cert-manager** - TLS certificate management
2. **nginx-ingress** - Ingress controller for external access
3. **vault** - Secret management system

#### Implementation Tasks

##### 1. Create Missing Application Manifests
- [x] Create 13 new ArgoCD Application manifests as `.disabled`
- [x] All manifests track `maintenance-mode` branch
- [x] All manifests use auto-sync with prune and selfHeal

**Applications Created**:
- vault.yaml.disabled
- nginx-ingress.yaml.disabled
- logging.yaml.disabled
- loki-aws-infrastructure.yaml.disabled
- k8s-monitor.yaml.disabled
- ecr-auth.yaml.disabled
- n8n.yaml.disabled
- chores-tracker.yaml.disabled
- chores-tracker-backend.yaml.disabled
- chores-tracker-frontend.yaml.disabled
- oncall-agent.yaml.disabled
- whoami-test.yaml.disabled
- cert-manager-config.yaml.disabled (for ClusterIssuers)

##### 2. Enable Phase 1 Applications
- [x] Rename `.disabled` to `.yaml` for cert-manager, nginx-ingress, vault
- [x] Commit and push changes to GitHub
- [x] Monitor ArgoCD sync status

##### 3. Fix Vault Node Affinity Issue
**Problem**: Vault pods couldn't schedule - required `workload=infra` node label
**Root Cause**: `base-apps/vault/statefulsets.yaml:125-126` has nodeSelector requirement
**Solution**:
- [x] Label k3s-worker-1 with `workload=infra`
- [x] Verify vault pods scheduled successfully

**Command Used**:
```bash
kubectl label node k3s-worker-1 workload=infra
```

**Result**: Both vault pods (vault-0, vault-agent-injector) running on k3s-worker-1

##### 4. Fix cert-manager CRD Installation
**Problem**: cert-manager CRDs not installed, ClusterIssuers failing
**Root Cause**: Directory only contained ClusterIssuer YAMLs, not cert-manager deployment
**Solution**:
- [x] Update cert-manager.yaml to use Jetstack Helm chart v1.18.2
- [x] Enable `installCRDs: true` parameter
- [x] Add nodeSelector for `workload=infra` on all components
- [x] Create separate cert-manager-config.yaml.disabled for ClusterIssuers
- [x] Commit and force-push updated configuration

**Helm Chart Configuration**:
```yaml
source:
  repoURL: https://charts.jetstack.io
  chart: cert-manager
  targetRevision: v1.18.2
  helm:
    parameters:
      - name: installCRDs
        value: "true"
    values: |
      nodeSelector:
        workload: infra
      webhook:
        nodeSelector:
          workload: infra
      cainjector:
        nodeSelector:
          workload: infra
```

**Result**: All 6 cert-manager CRDs installed, 3 pods running on k3s-worker-1

##### 5. Resolve nginx-ingress Port Conflict
**Problem**: nginx-ingress pods stuck in Pending - port 80/443 conflicts
**Root Cause**: k3s ships with Traefik ingress controller using hostNetwork ports 80/443
**Investigation**:
- nginx-ingress deployed to `ingress-nginx` namespace (not `nginx-ingress`)
- HelmChart created by k3s Helm controller
- 4 DaemonSet pods pending due to port conflicts on all nodes
- Error: "node(s) didn't have free ports for the requested pod ports"

**Solution**:
- [x] Delete Traefik HelmCharts (traefik, traefik-crd)
- [x] Verify Traefik deployment and services removed
- [x] Verify nginx-ingress pods scheduled

**Commands Used**:
```bash
kubectl delete helmchart traefik traefik-crd -n kube-system
```

**Result**: All 4 nginx-ingress controller pods running (1 per node) using hostNetwork

##### 6. Initialize and Unseal Vault
**Task**: Initialize Vault and retrieve credentials
**Status**: ✅ Complete

- [x] Initialize Vault with 5 key shares, threshold of 3
- [x] Retrieve and securely store unseal keys and root token
- [x] Unseal Vault using 3 keys
- [x] Verify vault-0 pod healthy (1/1 Ready)

**Vault Details**:
- Version: 1.18.1
- Cluster: vault-cluster-d4523dc1
- Storage: file (local PVC)
- HA: Disabled (single instance)

**Credentials**: Stored securely in password manager ✅

#### Phase 1 Final Status

| Application | Sync Status | Health Status | Pods Ready | Location |
|------------|-------------|---------------|------------|----------|
| cert-manager | Synced | Healthy | 3/3 | k3s-worker-1 |
| nginx-ingress | Synced | Healthy | 4/4 | All nodes |
| vault | Synced | Healthy | 2/2 | k3s-worker-1 |

**Phase 1 Complete** ✅

---

### Phase 2: Cloud Integration & Secrets (COMPLETED ✅)
**Goal**: Enable cloud provider integration and secret synchronization
**Status**: ✅ Complete
**Duration**: ~1 hour
**Dependencies**: Phase 1 (Vault must be healthy)

#### Applications
1. **crossplane** - Cloud resource provisioning
2. **crossplane-aws-provider** - AWS provider for Crossplane
3. **crossplane-config** - Crossplane configuration
4. **external-secrets** - External Secrets Operator for Vault integration
5. **ecr-auth** - ECR authentication helper

#### Prerequisites
- [x] Vault fully operational and unsealed ✅
- [x] Vault KV v2 secrets engine enabled at `k8s-secrets` path ✅
- [x] Kubernetes auth method configured in Vault ✅
- [x] AWS credentials stored in Vault for Crossplane ✅
  - Stored at: `k8s-secrets/crossplane-system/aws-credentials`
  - Access Key ID: AKIA4NFDJMBLDENYEH5Z (us-east-2)
- [x] AWS credentials stored in Vault for ECR Auth ✅
  - Stored at: `k8s-secrets/ecr-auth`
  - Region: us-east-2
- [x] Vault policies created ✅
  - Policy `crossplane-system`: read/list on `k8s-secrets/data/crossplane-system/*`
  - Policy `ecr-credentials-sync`: read/list on `k8s-secrets/data/ecr-auth`
- [x] Kubernetes auth roles configured ✅
  - Role `crossplane-system`: bound to `default` SA in `crossplane-system` namespace
  - Role `ecr-credentials-sync`: bound to `ecr-credentials-sync` SA in `kube-system` namespace

#### Implementation Tasks
- [x] Enable crossplane.yaml (renamed from .disabled)
- [x] Wait for Crossplane CRDs to be installed
- [x] Enable crossplane-aws-provider.yaml
- [x] Configure AWS provider with credentials
- [x] Enable crossplane-config.yaml
- [x] Deploy External Secrets Operator (v0.11.0)
- [x] Enable ecr-auth.yaml for ECR image pulling
- [x] Fix duplicate SecretStore definition in ecr-auth
- [x] Verify all pods healthy
- [x] Verify ExternalSecret syncing from Vault

#### Success Criteria
- [x] Crossplane controller running and healthy
- [x] AWS provider authenticated and ready
- [x] External Secrets Operator deployed and functional
- [x] ECR authentication working for image pulls
- [x] ExternalSecrets syncing from Vault successfully

#### Phase 2 Final Status

| Application | Sync Status | Health Status | Pods Ready | Namespace |
|------------|-------------|---------------|------------|-----------|
| crossplane | Synced | Healthy | 1/1 | crossplane-system |
| crossplane-aws-provider | Synced | Healthy | 2/2 | crossplane-system |
| crossplane-config | Synced | Healthy | - | crossplane-system |
| external-secrets | Synced | Healthy | 3/3 | external-secrets |
| ecr-auth | Synced | Healthy | - | kube-system |

**External Secrets Resources**:
- ExternalSecret `ecr-auth-secrets`: SecretSynced=True, Ready=True
- SecretStore `vault-backend`: Status=Valid, Ready=True
- Secret `aws-credentials` created with AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION
- CronJob `ecr-credentials-sync` scheduled hourly (0 * * * *)

**Issues Resolved During Phase 2**:
1. **Missing External Secrets Operator**: Discovered ECR auth required External Secrets Operator CRDs. Created `base-apps/external-secrets.yaml` deploying Helm chart v0.11.0.
2. **Duplicate SecretStore Warning**: SecretStore was defined in both `external-secret.yaml` and `secret-store.yaml`. Removed duplicate from `external-secret.yaml`, keeping single source of truth in `secret-store.yaml`.

**Phase 2 Complete** ✅

---

### Phase 3: External Secrets & TLS (PENDING ⬜)
**Goal**: Enable secret synchronization from Vault and TLS certificate issuance
**Status**: ⬜ Not Started
**Dependencies**: Phase 1 (Vault + cert-manager), Phase 2 (Crossplane)

#### Applications
1. **cert-manager-config** - ClusterIssuers for Let's Encrypt
2. **external-secrets-operator** - (if exists) Vault secret sync

#### Prerequisites
- [ ] Vault unsealed and accessible
- [ ] cert-manager CRDs installed and healthy
- [ ] Route 53 credentials configured for DNS-01 challenges
- [ ] Kubernetes auth roles created in Vault for each namespace

#### Implementation Tasks
- [ ] Configure Vault Kubernetes auth method
- [ ] Create Vault policies for application namespaces
- [ ] Create Vault roles for each namespace
- [ ] Enable cert-manager-config.yaml
- [ ] Verify ClusterIssuers created (letsencrypt-prod, letsencrypt-staging, letsencrypt-route53)
- [ ] Test certificate issuance with a simple Ingress

#### Success Criteria
- ClusterIssuers showing Ready=True
- Test certificate issued successfully
- Vault authentication from pods working

---

### Phase 4: Data Layer (COMPLETED ✅)
**Goal**: Deploy database services (migrated to AWS RDS for MySQL)
**Status**: ✅ Complete
**Duration**: ~3 hours
**Dependencies**: Phase 1 (Vault), Phase 2 (Crossplane for RDS provisioning)

#### Applications
1. **postgresql** - PostgreSQL database (pending deployment)
2. **crossplane-mysql** - Crossplane MySQL provider configuration for AWS RDS
3. **chores-tracker-backend** - Crossplane MySQL resources (Database, User, Grant)

#### Architecture Decision
**Decision**: Migrated from in-cluster MySQL StatefulSet to AWS RDS MySQL
**Rationale**:
- Better reliability and managed backups
- Reduced cluster resource usage
- Professional-grade database service
- Automatic OS and engine patching

**RDS Configuration**:
- Instance: db.t4g.micro (ARM Graviton2, ~$12/month)
- Engine: MySQL 8.0.39
- Storage: 20GB gp3, encrypted
- Endpoint: asela-cluster-mysql.c9ke2o0oe6ir.us-east-2.rds.amazonaws.com:3306
- Initial database: n8n
- Provisioned via Terraform in `terraform/roots/asela-cluster/rds.tf`

#### Prerequisites
- [x] Vault unsealed with RDS credentials stored
- [x] RDS MySQL instance provisioned via Terraform
- [x] RDS credentials stored in Vault at `k8s-secrets/mysql-credentials`
- [x] Vault Kubernetes auth role created for crossplane-system namespace
- [x] SecretStore configurations created in crossplane-system namespace
- [x] Crossplane MySQL SQL Provider v0.9.0 installed

#### Implementation Tasks

##### 1. Configure Vault Secret Storage for RDS
- [x] Store RDS credentials in Vault KV v2 engine
- [x] Path: `k8s-secrets/mysql-credentials`
- [x] Keys: `endpoint`, `port`, `DB_USER`, `DB_PASSWORD`
- [x] Endpoint: `asela-cluster-mysql.c9ke2o0oe6ir.us-east-2.rds.amazonaws.com`
- [x] Port: `3306`
- [x] Username: `mysqladmin`

##### 2. Create Crossplane MySQL Application
- [x] Created `base-apps/crossplane-mysql.yaml` ArgoCD application
- [x] Application tracks `maintenance-mode` branch
- [x] Path: `base-apps/crossplane-mysql`
- [x] Namespace: crossplane-system
- [x] Auto-sync enabled with prune and selfHeal

##### 3. Configure External Secrets for RDS
- [x] Created `base-apps/crossplane-mysql/secret-store.yaml`
  - SecretStore in crossplane-system namespace
  - Vault server: `http://vault.vault.svc.cluster.local:8200`
  - KV v2 path: `k8s-secrets`
  - Kubernetes auth with role: `crossplane-system`
- [x] Created `base-apps/crossplane-mysql/external-secret.yaml`
  - ExternalSecret syncs RDS credentials from Vault
  - Target secret: `mysql-credentials` in crossplane-system
  - Refresh interval: 15s
  - Syncs: endpoint, port, username, password

##### 4. Configure Crossplane MySQL Provider
**Issue Encountered**: Duplicate ProviderConfig conflict
- ProviderConfig was managed by TWO ArgoCD applications (crossplane-config AND crossplane-mysql)
- ArgoCD SharedResourceWarning prevented updates from taking effect
- Initial configuration pointed to wrong namespace (mysql) with old in-cluster credentials

**Resolution**:
- [x] Deleted duplicate ProviderConfig from `base-apps/crossplane-config/mysql-provider-config.yaml`
- [x] Updated `base-apps/crossplane-mysql/provider-config.yaml` to use crossplane-system namespace
- [x] Changed TLS setting from "skip-verify" to "preferred" for better security
- [x] Committed changes (commits: 13404e7, fac2387)

**Final ProviderConfig**:
```yaml
apiVersion: mysql.sql.crossplane.io/v1alpha1
kind: ProviderConfig
metadata:
  name: mysql-provider
spec:
  credentials:
    source: MySQLConnectionSecret
    connectionSecretRef:
      namespace: crossplane-system
      name: mysql-credentials
  tls: preferred
```

##### 5. Provision chores-tracker-backend MySQL Resources
- [x] Created namespace: `chores-tracker-backend`
- [x] Applied Crossplane resources from `base-apps/chores-tracker-backend/crossplane_resources.yaml`

**Resources Created**:
1. **Database**: chores-db
   - Status: READY=True, SYNCED=True ✅
   - Successfully created on RDS instance
2. **User**: chores-user
   - Status: Waiting for password secret
   - Requires: `chores-tracker-backend-secrets` secret with `DB_PASSWORD` key
   - Will be provisioned when application is deployed
3. **Grant**: chores-db-grant
   - Status: Waiting for User resource
   - Privileges: ALL on chores-db for chores-user
   - Will be provisioned after User is created

##### 6. Remove Legacy In-Cluster MySQL
- [x] Deleted mysql-application from ArgoCD cluster
- [x] Renamed `base-apps/mysql.yaml` to `mysql.yaml.disabled`
- [x] Committed changes (commit: 13b84ba)
- [x] Verified mysql namespace resources cleaned up by ArgoCD prune

#### Success Criteria
- [x] RDS MySQL instance accessible from cluster
- [x] Crossplane MySQL Provider configured and authenticated
- [x] ExternalSecret syncing RDS credentials from Vault successfully
- [x] chores-db database created on RDS (READY=True, SYNCED=True)
- [x] ProviderConfig conflict resolved (no ArgoCD SharedResourceWarning)
- [x] Old in-cluster MySQL application removed
- [ ] User and Grant resources provisioned (pending chores-tracker-backend deployment)

#### Phase 4 Final Status

| Application | Sync Status | Health Status | Resources Ready | Namespace |
|------------|-------------|---------------|-----------------|-----------|
| crossplane-mysql | Synced | Healthy | 3/3 | crossplane-system |

**Crossplane Resources**:
- ProviderConfig `mysql-provider`: Configured, pointing to RDS
- ExternalSecret `mysql-credentials`: SecretSynced=True, Ready=True
- SecretStore `vault-backend`: Status=Valid, Ready=True
- Database `chores-db`: READY=True, SYNCED=True ✅
- User `chores-user`: Waiting for password secret (pending)
- Grant `chores-db-grant`: Waiting for User (pending)

**Issues Resolved During Phase 4**:
1. **Duplicate ProviderConfig Conflict**: Identified ArgoCD SharedResourceWarning caused by two applications managing the same ProviderConfig. Removed duplicate from crossplane-config, kept single source in crossplane-mysql.
2. **Wrong Credentials Namespace**: ProviderConfig was pointing to mysql namespace with old in-cluster credentials. Updated to crossplane-system namespace with RDS credentials.
3. **Connection Refused Error**: Initial connection attempts failed with "dial tcp 10.43.227.221:3306: connect: connection refused". Root cause was ProviderConfig using old in-cluster MySQL service IP. Fixed by updating to RDS endpoint.
4. **TLS Security**: Changed TLS configuration from "skip-verify" to "preferred" for better security posture.

**Pending Tasks**:
- Deploy chores-tracker-backend application to create password secret
- Verify User and Grant resources complete provisioning
- Test chores-tracker-backend connectivity to RDS database

**Phase 4 Complete** ✅

---

### Phase 5: Observability (PENDING ⬜)
**Goal**: Enable logging and monitoring infrastructure
**Status**: ⬜ Not Started
**Dependencies**: Phase 1 (nginx-ingress for dashboards), Phase 2 (AWS S3 for Loki)

#### Applications
1. **logging** - Logging infrastructure (likely Promtail/Loki)
2. **loki-aws-infrastructure** - Loki with S3 backend
3. **k8s-monitor** - Kubernetes monitoring (likely Prometheus/Grafana)

#### Prerequisites
- [ ] S3 bucket created for Loki storage (via Crossplane or manual)
- [ ] AWS credentials configured for Loki
- [ ] Ingress available for Grafana dashboard
- [ ] Storage for Prometheus metrics

#### Implementation Tasks
- [ ] Enable loki-aws-infrastructure.yaml
- [ ] Verify Loki can write to S3
- [ ] Enable logging.yaml (Promtail)
- [ ] Verify log collection working
- [ ] Enable k8s-monitor.yaml
- [ ] Access Grafana dashboard via Ingress
- [ ] Import default Kubernetes dashboards
- [ ] Configure alerting rules

#### Success Criteria
- Logs flowing to Loki and queryable
- Metrics being collected by Prometheus
- Grafana accessible and showing data
- No excessive resource usage

---

### Phase 6: Application Workloads (PENDING ⬜)
**Goal**: Deploy application services
**Status**: ⬜ Not Started
**Dependencies**: All previous phases (databases, ingress, secrets, monitoring)

#### Applications
1. **chores-tracker** - Main chores tracker application (if monolithic)
2. **chores-tracker-backend** - Backend API service
3. **chores-tracker-frontend** - Frontend UI service
4. **n8n** - Workflow automation platform
5. **oncall-agent** - On-call management service
6. **whoami-test** - Test application

#### Prerequisites
- [ ] Databases (MySQL for chores-tracker) healthy
- [ ] Vault secrets configured for each application
- [ ] SecretStore created in each application namespace
- [ ] ExternalSecrets created for database credentials
- [ ] Ingress rules configured
- [ ] TLS certificates available

#### Implementation Tasks
- [ ] Enable chores-tracker-backend.yaml
- [ ] Verify database connectivity
- [ ] Enable chores-tracker-frontend.yaml
- [ ] Enable chores-tracker.yaml (if separate component)
- [ ] Test chores-tracker via Ingress
- [ ] Enable n8n.yaml
- [ ] Configure n8n workflows
- [ ] Enable oncall-agent.yaml
- [ ] Enable whoami-test.yaml for ingress testing
- [ ] Verify all applications healthy

#### Success Criteria
- All application pods running
- Applications accessible via Ingress
- TLS certificates working
- Database connectivity confirmed
- No errors in logs

---

## Technical Notes

### Infrastructure Configuration

**Cluster Details**:
- Platform: k3s v1.33.4+k3s1
- Nodes: 4 (1 master, 3 workers)
  - k3s-master-new (10.0.1.115)
  - k3s-worker-1 (10.0.1.111) - labeled `workload=infra`
  - k3s-worker-2 (10.0.1.112)
  - k3s-worker-3 (10.0.1.113)
- GitOps: ArgoCD v3.1.9 (Helm chart v9.0.5)
- Git Branch: `maintenance-mode`
- Repository: https://github.com/arigsela/kubernetes

**ArgoCD Configuration**:
- Master App Pattern: Watches `base-apps/` directory
- Auto-sync: Enabled with prune and selfHeal
- Namespace Creation: Automatic via `CreateNamespace=true`

**Traefik Removal**:
- k3s default Traefik ingress controller has been removed
- nginx-ingress now handles all ingress traffic on ports 80/443
- All nodes have nginx-ingress controller pod (DaemonSet)

### Node Selectors & Affinity

Applications requiring `workload=infra` label:
- cert-manager (main, webhook, cainjector)
- vault (vault-0, agent-injector)

Current infra node: **k3s-worker-1**

### Vault Configuration

**Access**:
- Internal: `http://vault.vault.svc.cluster.local:8200`
- Pod: vault-0 in vault namespace

**Auth Methods**:
- Token (root token available)
- Kubernetes (to be configured in Phase 3)

**Storage**:
- Backend: file (local PVC)
- Path: /vault/data
- KV Engine: v2 at path `k8s-secrets` (to be configured)

**Unseal Process**:
- Requires 3 of 5 unseal keys
- Manual unseal required after pod restart
- Keys stored securely in password manager

### cert-manager Configuration

**CRDs Installed**:
- certificaterequests.cert-manager.io
- certificates.cert-manager.io
- challenges.acme.cert-manager.io
- clusterissuers.cert-manager.io
- issuers.cert-manager.io
- orders.acme.cert-manager.io

**ClusterIssuers** (to be deployed in Phase 3):
- letsencrypt-prod - Production Let's Encrypt
- letsencrypt-staging - Staging Let's Encrypt
- letsencrypt-route53 - Route 53 DNS-01 challenge

### nginx-ingress Configuration

**Deployment Method**: k3s HelmChart CRD
**Chart**: kubernetes/ingress-nginx v4.11.2
**Type**: DaemonSet (1 pod per node)
**Network**: hostNetwork=true (ports 80/443)
**Service Type**: ClusterIP

**Configuration Highlights**:
- Timeout configurations: 30s for proxy timeouts
- Keepalive connections: 100
- TLS: TLSv1.2 and TLSv1.3
- Trusted proxies: Cloudflare IP ranges configured
- Real IP headers: X-Forwarded-For

---

## Known Issues & Resolutions

### Issue 1: Vault Node Affinity
**Status**: ✅ Resolved
**Problem**: Vault pods stuck in Pending
**Root Cause**: Missing `workload=infra` node label
**Solution**: Labeled k3s-worker-1
**File**: `base-apps/vault/statefulsets.yaml:125-126`

### Issue 2: cert-manager CRD Missing
**Status**: ✅ Resolved
**Problem**: ClusterIssuers couldn't be created
**Root Cause**: Only ClusterIssuer YAMLs existed, no cert-manager deployment
**Solution**: Switched to Jetstack Helm chart with installCRDs=true
**Files**:
- `base-apps/cert-manager.yaml` (updated)
- `base-apps/cert-manager-config.yaml.disabled` (created)

### Issue 3: nginx-ingress Port Conflict
**Status**: ✅ Resolved
**Problem**: nginx-ingress pods couldn't schedule
**Root Cause**: Traefik using hostNetwork ports 80/443
**Solution**: Removed Traefik HelmCharts
**Impact**: No default k3s ingress controller, nginx-ingress is now primary

### Issue 4: Missing External Secrets Operator
**Status**: ✅ Resolved
**Problem**: ECR auth application couldn't sync - ExternalSecret and SecretStore CRDs missing
**Root Cause**: External Secrets Operator not deployed, only ecr-auth application existed
**Solution**: Created `base-apps/external-secrets.yaml` deploying Helm chart v0.11.0 from https://charts.external-secrets.io
**Impact**: Enabled Vault secret synchronization for all applications
**Files**: `base-apps/external-secrets.yaml` (created), `base-apps/ecr-auth/external-secret.yaml` (now functional)

### Issue 5: Duplicate SecretStore Definition
**Status**: ✅ Resolved
**Problem**: ArgoCD warning "Resource external-secrets.io/SecretStore/kube-system/vault-backend appeared 2 times"
**Root Cause**: SecretStore defined in both `external-secret.yaml` and `secret-store.yaml`
**Solution**: Removed duplicate SecretStore from `external-secret.yaml:1-18`, kept canonical definition in `secret-store.yaml`
**Impact**: Cleaner resource organization, follows separation of concerns pattern
**Files**: `base-apps/ecr-auth/external-secret.yaml` (modified)

### Issue 6: Duplicate Crossplane ProviderConfig Conflict
**Status**: ✅ Resolved (Phase 4)
**Problem**: ArgoCD SharedResourceWarning - ProviderConfig managed by two applications (crossplane-config AND crossplane-mysql)
**Root Cause**: Same ProviderConfig resource defined in both applications, ArgoCD preventing updates
**Solution**: Removed duplicate from `base-apps/crossplane-config/mysql-provider-config.yaml`, kept single source in `base-apps/crossplane-mysql/provider-config.yaml`
**Impact**: Enabled Crossplane MySQL Provider to connect to RDS successfully
**Files**:
- `base-apps/crossplane-config/mysql-provider-config.yaml` (deleted)
- `base-apps/crossplane-mysql/provider-config.yaml` (updated)

### Issue 7: Crossplane MySQL Wrong Credentials Namespace
**Status**: ✅ Resolved (Phase 4)
**Problem**: ProviderConfig pointing to mysql namespace with old in-cluster MySQL credentials
**Root Cause**: Configuration not updated after RDS migration
**Solution**: Updated ProviderConfig to use crossplane-system namespace where RDS credentials are synced
**Impact**: Fixed "connection refused" errors, enabled successful RDS connection
**File**: `base-apps/crossplane-mysql/provider-config.yaml:7-9`

### Issue 8: Crossplane MySQL Connection Refused
**Status**: ✅ Resolved (Phase 4)
**Problem**: Error "dial tcp 10.43.227.221:3306: connect: connection refused"
**Root Cause**: ProviderConfig using credentials that pointed to old in-cluster MySQL service IP
**Solution**: Fixed ProviderConfig namespace reference to use RDS credentials from crossplane-system
**Impact**: Database resource successfully created on RDS (READY=True, SYNCED=True)
**File**: `base-apps/crossplane-mysql/provider-config.yaml`

---

## Rollback Procedures

### Phase 1 Rollback
If Phase 1 needs to be rolled back:

```bash
# Disable applications
mv base-apps/cert-manager.yaml base-apps/cert-manager.yaml.disabled
mv base-apps/nginx-ingress.yaml base-apps/nginx-ingress.yaml.disabled
mv base-apps/vault.yaml base-apps/vault.yaml.disabled

# Commit and push
git add base-apps/*.disabled
git commit -m "rollback: Disable Phase 1 applications"
git push origin maintenance-mode

# ArgoCD will auto-prune the resources
```

### General Rollback Strategy
1. Rename `.yaml` to `.yaml.disabled`
2. Commit and push to git
3. ArgoCD auto-prune will delete resources
4. Verify pods terminated: `kubectl get pods -A`

---

## Next Steps

### Immediate Actions
1. ✅ Document Vault credentials in secure location
2. ⬜ Plan Phase 2: Crossplane and AWS integration
3. ⬜ Review Vault policies needed for each namespace
4. ⬜ Prepare Kubernetes auth configuration for Vault

### Before Phase 2
- [ ] Verify Phase 1 stability over 24 hours
- [ ] Check cluster resource utilization
- [ ] Review AWS credentials and permissions needed
- [ ] Confirm Crossplane configuration requirements

### Phase 2 Prerequisites Checklist
- [ ] Vault healthy and unsealed
- [ ] AWS access keys available
- [ ] Crossplane AWS provider version confirmed
- [ ] S3 bucket for Crossplane state (if needed)

---

## Progress Tracking

**Phases Completed**: 4 / 6
**Applications Enabled**: 9 / 19 (added crossplane-mysql)
**Applications Created**: 13
**Issues Resolved**: 8 (Vault affinity, cert-manager CRDs, nginx-ingress ports, External Secrets Operator missing, duplicate SecretStore, duplicate ProviderConfig, wrong credentials namespace, MySQL connection refused)

### Phase Completion Dates
- Phase 0 (Baseline): 2025-11-03 ✅
- Phase 1 (Core Infra): 2025-11-03 ✅
- Phase 2 (Cloud Integration & Secrets): 2025-11-03 ✅
- Phase 3 (External Secrets & TLS): TBD (skipped - functionality covered in Phase 2)
- Phase 4 (Data Layer): 2025-11-04 ✅
- Phase 5 (Observability): TBD
- Phase 6 (Applications): TBD

---

## References

### Key Files
- Master App: `terraform/modules/application-sets/application-sets.tf`
- Phase 1 Apps: `base-apps/cert-manager.yaml`, `base-apps/nginx-ingress.yaml`, `base-apps/vault.yaml`
- Vault StatefulSet: `base-apps/vault/statefulsets.yaml`
- nginx-ingress HelmChart: `base-apps/nginx-ingress/nginx-ingress-controller.yaml`

### Documentation
- CLAUDE.md: Repository GitOps workflow documentation
- ArgoCD: https://argo-cd.readthedocs.io/
- cert-manager: https://cert-manager.io/docs/
- Vault: https://developer.hashicorp.com/vault/docs

---

**Last Updated**: 2025-11-04
**Document Version**: 1.1
**Maintained By**: Infrastructure Team

## Changelog

### Version 1.1 - 2025-11-04
- **Phase 4 Completed**: Migrated from in-cluster MySQL to AWS RDS MySQL 8.0.39
- **Added crossplane-mysql application**: Crossplane MySQL provider configuration for RDS
- **Documented RDS architecture decision**: Cost-effective db.t4g.micro instance
- **Resolved 3 critical issues**: Duplicate ProviderConfig conflict, wrong credentials namespace, MySQL connection refused
- **Updated progress**: 67% complete (4 of 6 phases)
