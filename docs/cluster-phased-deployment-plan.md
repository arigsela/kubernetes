# Kubernetes Cluster Phased Deployment Implementation Plan

**Status**: Phase 6 In Progress 🔄
**Last Updated**: 2025-11-05
**Branch**: maintenance-mode
**Overall Progress**: 83% (Phase 5 complete - observability deployed, Phase 6 in progress)

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

### Phase 5: Observability (COMPLETED ✅)
**Goal**: Enable logging and monitoring infrastructure
**Status**: ✅ Complete (Core stack deployed, k8s-monitor disabled pending secrets)
**Duration**: ~1 hour
**Started**: 2025-11-05
**Completed**: 2025-11-05
**Dependencies**: Phase 1 (nginx-ingress for dashboards), Phase 2 (AWS S3 for Loki, Crossplane)

#### Applications
1. **logging** - Loki, Prometheus, and Alloy (log collection DaemonSet)
2. **loki-aws-infrastructure** - AWS resources for Loki S3 backend (bucket, IAM, lifecycle policies)
3. **k8s-monitor** - AI-powered Kubernetes monitoring agent (DISABLED - pending Vault secrets)

#### Prerequisites
- [x] S3 bucket created for Loki storage via Crossplane ✅
- [x] AWS credentials configured for Loki ✅
- [x] Crossplane AWS providers healthy ✅
- [ ] Ingress available for Grafana dashboard (pending future deployment)
- [x] Storage for Prometheus metrics (using in-cluster storage) ✅

#### Implementation Tasks

##### 1. Enable loki-aws-infrastructure Application (COMPLETED ✅)
**Status**: ✅ Complete

- [x] Renamed `base-apps/loki-aws-infrastructure.yaml.disabled` to `.yaml`
- [x] Committed and pushed changes to GitHub
- [x] ArgoCD synced application automatically
- [x] Verified application Synced and Healthy

**AWS Resources Provisioned via Crossplane**:
- **S3 Bucket**: `asela-loki-logs` (us-east-2)
  - Encryption: AES256
  - Versioning: Enabled
  - Lifecycle Policy: Delete logs after 30 days
  - Public Access: Blocked
- **IAM User**: `loki-s3-user`
  - Purpose: Loki authentication to S3
  - Permissions: PutObject, GetObject, ListBucket, DeleteObject on asela-loki-logs
- **IAM Policy**: `loki-s3-access-policy`
  - Attached to loki-s3-user
  - Scoped to asela-loki-logs bucket only

**Crossplane Resources Status**:
- Bucket: READY=True, SYNCED=True ✅
- IAM User: READY=True, SYNCED=True ✅
- IAM Policy: READY=True, SYNCED=True ✅
- UserPolicyAttachment: READY=True, SYNCED=True ✅

##### 2. Enable logging Application (COMPLETED ✅)
**Status**: ✅ Complete

- [x] Renamed `base-apps/logging.yaml.disabled` to `.yaml`
- [x] Committed and pushed changes
- [x] ArgoCD synced application
- [x] Verified all pods Running and Healthy

**Deployed Components**:
1. **Loki** (Log Aggregation System)
   - Version: 3.3.1
   - Deployment: 1 replica (single pod)
   - Storage: S3 backend at s3://asela-loki-logs
   - Retention: 30 days (enforced by S3 lifecycle policy)
   - API: http://loki.logging.svc.cluster.local:3100

2. **Prometheus** (Metrics Collection)
   - Version: v3.1.0
   - Deployment: 1 replica
   - Scrape Interval: 15s
   - Storage: In-cluster persistent volume
   - API: http://prometheus.logging.svc.cluster.local:9090
   - Targets: 25+ active scrape targets
     - kubernetes-nodes (3 worker nodes)
     - kubernetes-pods (cert-manager, crossplane providers, prometheus)
     - kubernetes-apiservers
     - kubernetes-service-endpoints (CoreDNS)

3. **Alloy** (Log Collection Agent)
   - Deployment: DaemonSet (1 pod per node)
   - Pods: 4/4 Running (master + 3 workers)
   - Function: Collects logs from all pods, forwards to Loki
   - Successor to: Promtail

**Pod Status**:
- loki-65f558cbd6-9fl45: Running (1/1) in logging namespace
- prometheus-5bc8bfdc9-7qzp7: Running (1/1) in logging namespace
- alloy-xxxxx (4 pods): Running (1/1) on each node

##### 3. Enable k8s-monitor Application (DISABLED ⚠️)
**Status**: ⚠️ Disabled (pending Vault secret population)

**Initial Deployment Attempt**:
- [x] Renamed `base-apps/k8s-monitor.yaml.disabled` to `.yaml`
- [x] Committed and pushed changes
- [x] ArgoCD synced application
- [x] Created Vault Kubernetes auth role for k8s-monitor namespace
- [x] Created Vault policy for k8s-monitor secret access

**Vault Configuration Created**:
```bash
# Created Kubernetes auth role
vault write auth/kubernetes/role/k8s-monitor \
    bound_service_account_names=default \
    bound_service_account_namespaces=k8s-monitor \
    policies=k8s-monitor \
    ttl=1h

# Created Vault policy
path "k8s-secrets/data/k8s-monitor" {
  capabilities = ["read"]
}
```

**Issue Encountered**:
- Pod stuck in CreateContainerConfigError
- Error: "secret 'k8s-monitor-secrets' not found"
- Root cause: ExternalSecret could not sync - Vault path `k8s-secrets/k8s-monitor` does not exist
- Required secrets (7 values):
  - anthropic-api-key
  - github-token
  - slack-bot-token
  - slack-channel
  - chores-tracker-api-base-url
  - chores-tracker-monitoring-username
  - chores-tracker-monitoring-password

**Resolution**:
- [x] User decision: Disable k8s-monitor until secrets can be populated
- [x] Renamed `base-apps/k8s-monitor.yaml` to `base-apps/k8s-monitor.yaml.disabled`
- [x] Committed and pushed changes
- [x] ArgoCD pruned k8s-monitor resources

**Status**: Infrastructure ready, application disabled pending secret configuration

##### 4. Verify Observability Stack Health (COMPLETED ✅)
**Status**: ✅ Complete

- [x] Verified ArgoCD applications Synced and Healthy
  - loki-aws-infrastructure: Synced, Healthy ✅
  - logging: Synced, Healthy ✅
  - k8s-monitor: Disabled (pruned by ArgoCD) ⚠️

- [x] Verified Loki receiving logs
  - Queried Loki API: `http://loki.logging.svc.cluster.local:3100/loki/api/v1/query`
  - Response: `{"status":"success"}` ✅
  - Logs showing "entry too far behind" warnings (expected for old pod logs)

- [x] Verified Prometheus collecting metrics
  - Queried Prometheus API: `http://prometheus.logging.svc.cluster.local:9090/api/v1/targets`
  - 25+ active scrape targets across cluster ✅
  - All kubernetes-nodes targets showing "up" status = 1 (healthy)
  - Metrics being collected from:
    - 3 worker nodes (k3s-worker-1, k3s-worker-2, k3s-worker-3)
    - cert-manager pods
    - crossplane provider pods
    - prometheus self-monitoring
    - CoreDNS service endpoints

- [x] Verified log collection agents operational
  - 4 Alloy pods running (1 per node)
  - All pods in Running state (1/1 Ready)
  - Logs flowing from all cluster nodes to Loki

#### Success Criteria
- [x] Logs flowing to Loki and queryable ✅
- [x] Metrics being collected by Prometheus ✅
- [x] S3 backend configured with lifecycle policies ✅
- [x] No excessive resource usage ✅
- [ ] Grafana accessible and showing data (pending future deployment)
- [ ] k8s-monitor operational (disabled pending secrets)

#### Phase 5 Final Status

| Application | Sync Status | Health Status | Pods Ready | Namespace |
|------------|-------------|---------------|------------|-----------|
| loki-aws-infrastructure | Synced | Healthy | - | loki |
| logging | Synced | Healthy | 6/6* | logging |
| k8s-monitor | Disabled | - | - | - |

*Logging pod breakdown: 1 Loki, 1 Prometheus, 4 Alloy DaemonSet pods

**AWS Resources**:
- S3 Bucket `asela-loki-logs`: Active, 30-day lifecycle policy configured
- IAM User `loki-s3-user`: Active with scoped permissions
- IAM Policy `loki-s3-access-policy`: Attached and active

**Vault Resources Created for Future Use**:
- Kubernetes auth role `k8s-monitor`: Configured for k8s-monitor namespace
- Vault policy `k8s-monitor`: Read access to k8s-secrets/data/k8s-monitor path

**Notes**:
- k8s-monitor application infrastructure is ready but disabled pending secret population
- When secrets are added to Vault path `k8s-secrets/k8s-monitor`, the application can be re-enabled by renaming `k8s-monitor.yaml.disabled` to `k8s-monitor.yaml`
- Core observability stack (Loki + Prometheus + Alloy) is fully operational

**Phase 5 Complete** ✅

---

### Phase 6: Application Workloads (IN PROGRESS 🔄)
**Goal**: Deploy application services
**Status**: 🔄 In Progress
**Started**: 2025-11-05
**Dependencies**: All previous phases (databases, ingress, secrets, monitoring)

#### Applications
1. **chores-tracker** - Main chores tracker application (if monolithic)
2. **chores-tracker-backend** - Backend API service
3. **chores-tracker-frontend** - Frontend UI service
4. **n8n** - Workflow automation platform
5. **oncall-agent** - On-call management service
6. **whoami-test** - Test application

#### Prerequisites
- [x] Databases (MySQL RDS for chores-tracker) healthy ✅
- [x] nginx-ingress controller operational ✅
- [ ] Vault secrets configured for each application
- [ ] SecretStore created in each application namespace
- [ ] ExternalSecrets created for database credentials
- [ ] Ingress rules configured
- [ ] TLS certificates available

#### Implementation Tasks

##### 1. Fix nginx-ingress Controller Deployment (COMPLETED ✅)
**Issue**: nginx-ingress pods couldn't schedule due to Traefik port conflicts
**Status**: ✅ Resolved (2025-11-05)

**Problem Details**:
- nginx-ingress controller DaemonSet pods stuck in Pending
- ArgoCD webhook validation failing: "failed calling webhook 'validate.nginx.ingress.kubernetes.io': no endpoints available"
- Root cause: K3s's built-in Traefik occupying ports 80/443

**Solution Implemented**:
- [x] Removed K3s Traefik deployment and service (user requirement: "we dont want traefik, we want nginx")
- [x] Deleted nginx-ingress HelmChart to trigger reinstall
- [x] ArgoCD auto-recreated HelmChart via GitOps
- [x] Verified all 4 nginx-ingress controller pods Running (1 per node)
- [x] ValidatingWebhookConfiguration recreated successfully
- [x] Verified ArgoCD application showing Healthy/Synced

**Commands Used**:
```bash
# Remove Traefik
kubectl delete deployment traefik -n kube-system
kubectl delete svc traefik -n kube-system

# Trigger nginx-ingress reinstall
kubectl delete helmchart ingress-nginx -n kube-system
# ArgoCD automatically recreates it
```

**Result**:
- All 4 nginx-ingress controller pods: Running and Ready (1/1)
- Ports 80/443 now available for nginx-ingress
- ArgoCD sync working without webhook errors
- Traefik completely removed from cluster ✅

##### 2. Deploy chores-tracker-backend (COMPLETED ✅)
**Status**: ✅ Complete (2025-11-05)

- [x] Enable chores-tracker-backend.yaml (renamed from .disabled)
- [x] Application synced successfully via ArgoCD
- [x] Deployment created: 2 pods running
- [x] Service created: ClusterIP on port 80
- [x] Health checks passing (HTTP 200 OK on /health endpoint)
- [x] Pods scheduled successfully across cluster

**Pod Status**:
- chores-tracker-backend-5bb4ccd766-thlk7: Running (1/1)
- chores-tracker-backend-5bb4ccd766-wgmck: Running (1/1)

**Service Details**:
- Name: chores-tracker-backend
- Type: ClusterIP
- ClusterIP: 10.43.1.201
- Port: 80/TCP
- Namespace: chores-tracker-backend

##### 3. Restore MySQL Database (COMPLETED ✅)
**Status**: ✅ Complete (2025-11-05)

**Issue**:
- chores-db database was empty after RDS migration
- Application required historical data to be restored from backup

**Backup Details**:
- Backup file: `backups/mysql_backup_20251029_020017.sql`
- Created: October 29, 2025 02:00 AM
- Size: ~500KB containing full application schema and data

**Technical Challenge**:
- Backup contained MySQL system tables (mysql.user, mysql.db, mysql.tables_priv)
- These tables would override existing RDS mysqladmin user credentials
- Standard `mysql < backup.sql` restore would fail and corrupt database access

**Solution Implemented**:

1. **Created Filtered Backup**:
   - Extracted only application database (chores-db) from full backup
   - Excluded all MySQL system tables to preserve RDS credentials
   - Filtered backup contained only 7 application tables

2. **Prepared Kubernetes Restore Job**:
   - Created ConfigMap with filtered SQL statements
   - Deployed restore job pod with MariaDB 10.6 image
   - Job had service account access to database credentials

3. **Executed Restore with Foreign Key Handling**:
   - Used `SET FOREIGN_KEY_CHECKS=0` to handle table dependencies
   - Tables restored in proper order for referential integrity
   - Applied after restore: `SET FOREIGN_KEY_CHECKS=1`

4. **Data Restored Successfully**:
   - **8 users**: asela, diana, Makoto, Eli (family members) + 4 monitoring/admin users
   - **2 chores**: test health check chores
   - **2 families**: "Sela family" and "Monitoring & Health Checks"
   - **All related tables**: activities, alembic_version, chore_assignments, reward_adjustments, families, users, chores

**Verification**:
- [x] Database queries confirmed all data present and intact
- [x] Schema validation passed
- [x] Application able to query all tables successfully
- [x] No referential integrity issues

**Follow-up Optimization** (applied same day):
- Reduced liveness probe delay from 300s to 30s for faster pod readiness
- Reduced startup probe delay from 360s to 60s for quicker application availability
- Pods now reach Ready state in ~2 minutes instead of 6-7 minutes

**Related Configuration Files**:
- `base-apps/chores-tracker-backend/deployments.yaml` (health probe settings)
- Backup stored in `backups/mysql_backup_20251029_020017.sql` (version control)

##### 4. Remaining Implementation Tasks
- [ ] Enable chores-tracker-frontend.yaml
- [ ] Verify frontend connectivity to backend (database now restored with application data)
- [ ] Enable chores-tracker.yaml (if separate component)
- [ ] Configure Ingress resources for external access
- [ ] Test chores-tracker via Ingress
- [ ] Enable n8n.yaml
- [ ] Configure n8n database and workflows
- [ ] Enable oncall-agent.yaml
- [ ] Enable whoami-test.yaml for ingress testing
- [ ] Verify all applications healthy

#### Success Criteria
- [x] nginx-ingress controller fully operational ✅
- [x] chores-tracker-backend pods running ✅
- [x] Backend health checks passing ✅
- [ ] Applications accessible via Ingress
- [ ] TLS certificates working
- [ ] Frontend-to-backend connectivity confirmed
- [ ] No errors in application logs

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

### Issue 9: nginx-ingress Controller Not Deploying After Traefik Removal
**Status**: ✅ Resolved (Phase 6 - 2025-11-05)
**Problem**: nginx-ingress HelmChart existed and showed as deployed, but no pods were running in ingress-nginx namespace
**Root Cause**: Helm release was deployed before Traefik removal, but actual pods were deleted during Traefik cleanup. HelmChart status showed "Complete" but resources were missing.
**Investigation**:
- HelmChart `ingress-nginx` existed in kube-system namespace
- Helm install job showed "Complete" status from 33 hours ago
- No resources existed in ingress-nginx namespace
- ArgoCD showed application as Healthy/Synced but ValidatingWebhookConfiguration had no endpoints
**Solution**:
- Deleted HelmChart to trigger fresh installation: `kubectl delete helmchart ingress-nginx -n kube-system`
- ArgoCD automatically recreated HelmChart via GitOps (selfHeal enabled)
- New Helm install job created nginx-ingress resources successfully
- All 4 DaemonSet pods deployed and running
**Impact**:
- nginx-ingress controller fully operational with 4/4 pods running
- ValidatingWebhookConfiguration recreated and functional
- ArgoCD can now sync Ingress resources without webhook errors
- Ports 80/443 available for nginx-ingress (Traefik fully removed)
**Files**: `base-apps/nginx-ingress.yaml`, `base-apps/nginx-ingress/nginx-ingress-controller.yaml`

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

**Phases Completed**: 5 / 6 (Phase 6 in progress)
**Applications Enabled**: 12 / 19 (added loki-aws-infrastructure, logging; k8s-monitor disabled)
**Applications Created**: 13
**Issues Resolved**: 9 (Vault affinity, cert-manager CRDs, nginx-ingress ports, External Secrets Operator missing, duplicate SecretStore, duplicate ProviderConfig, wrong credentials namespace, MySQL connection refused, nginx-ingress not deploying after Traefik removal)

### Phase Completion Dates
- Phase 0 (Baseline): 2025-11-03 ✅
- Phase 1 (Core Infra): 2025-11-03 ✅
- Phase 2 (Cloud Integration & Secrets): 2025-11-03 ✅
- Phase 3 (External Secrets & TLS): TBD (skipped - functionality covered in Phase 2)
- Phase 4 (Data Layer): 2025-11-04 ✅
- Phase 5 (Observability): 2025-11-05 ✅
- Phase 6 (Applications): Started 2025-11-05 (in progress)

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
