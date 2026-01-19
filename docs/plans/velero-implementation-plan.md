# Velero Backup and Restore Implementation Plan

## Overview

Implement Velero backup and restore solution to solve two critical issues during cluster shutdown/restart:
1. **CNI IP exhaustion** - Velero restores resources cleanly, avoiding stale CNI allocations
2. **Manual app redeployment** - One-command restore instead of enabling apps one-by-one

### Architecture: Velero + GitOps Integration

**Approach A: Velero Restores Everything, ArgoCD Reconciles**

```
Pre-Shutdown:  Velero backup (resources + PVC data) → S3
Post-Restart:  CNI cleanup → Velero restore all → ArgoCD sees resources match Git → Synced
```

Key points:
- Velero backs up complete namespaces (deployments, services, PVCs, and PVC data via Kopia)
- On restore, `--existing-resource-policy=update` handles any resources ArgoCD created first
- Since backed-up resources originated from Git, restored state matches Git = no ArgoCD drift
- Git remains source of truth; Velero provides data persistence

### Configuration Choices
- **Storage**: Keep local-path + Kopia file-level backup
- **Schedule**: Manual backup before shutdown only
- **Retention**: 7 days
- **Estimated AWS Cost**: $0.50 - $2.00/month

## Success Criteria

- [ ] Velero deployed and operational via GitOps
- [ ] S3 bucket `asela-velero-backups` created with 7-day lifecycle
- [ ] IAM user with minimal S3 permissions
- [ ] Pre-shutdown backup script that captures all stateful namespaces
- [ ] Post-restart restore script with integrated CNI cleanup
- [ ] Recovery documentation updated
- [ ] End-to-end test completed successfully

## Research Findings

### Relevant Files
| File | Purpose |
|------|---------|
| `terraform/roots/asela-cluster/vault-kms.tf` | Pattern for AWS IAM + K8s secret |
| `base-apps/mysql-rds-backup/backup-cronjob.yaml` | Pattern for backup jobs |
| `recovery/CLUSTER-RECOVERY.md` | Current recovery documentation |
| `base-apps/postgresql/pvc.yaml` | Example PVC (local-path) |

### PVCs to Backup
| Namespace | PVC Name | Size |
|-----------|----------|------|
| postgresql | postgresql-pvc | 10Gi |
| n8n | n8n-pvc | 5Gi |
| vault | vault-data | 1Gi |
| logging | prometheus-pvc | 50Gi |
| logging | grafana-pvc | 10Gi |

### Existing Patterns
- AWS resources via Terraform with IAM users and least-privilege policies
- ArgoCD applications with auto-sync, prune, and selfHeal
- S3 bucket naming: `asela-*` (e.g., `asela-terraform-states`, `asela-mysql-backups`)
- Kubernetes secrets created by Terraform for AWS credentials

---

## Implementation

### Phase 1: AWS Infrastructure (Terraform)

#### Task 1.1: Create S3 Bucket for Velero Backups

**Files:** `terraform/roots/asela-cluster/velero-s3.tf` (new)

**Steps:**
1. Create S3 bucket resource `asela-velero-backups`
2. Enable versioning for backup integrity
3. Add lifecycle rule to delete objects after 7 days
4. Enable AES256 server-side encryption
5. Block all public access
6. Add standard tags (Name, Purpose, ManagedBy)

**Code:**
```hcl
resource "aws_s3_bucket" "velero_backups" {
  bucket = "asela-velero-backups"

  tags = {
    Name      = "asela-velero-backups"
    Purpose   = "Velero-Kubernetes-Backups"
    ManagedBy = "Terraform"
  }
}

resource "aws_s3_bucket_versioning" "velero_backups" {
  bucket = aws_s3_bucket.velero_backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "velero_backups" {
  bucket = aws_s3_bucket.velero_backups.id

  rule {
    id     = "expire-old-backups"
    status = "Enabled"

    expiration {
      days = 7
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "velero_backups" {
  bucket = aws_s3_bucket.velero_backups.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "velero_backups" {
  bucket = aws_s3_bucket.velero_backups.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
```

**Testing:**
- [ ] `terraform plan` shows bucket creation with all configurations
- [ ] `terraform apply` succeeds
- [ ] `aws s3 ls s3://asela-velero-backups` returns empty bucket
- [ ] Bucket has versioning, encryption, and lifecycle policy in AWS console

---

#### Task 1.2: Create IAM User and Policy for Velero

**Files:** `terraform/roots/asela-cluster/velero-s3.tf` (continue)

**Steps:**
1. Create IAM user `velero-backup-user` in `/system/` path
2. Create IAM policy with minimal S3 permissions for velero bucket
3. Attach policy to user
4. Create access key for programmatic access

**Code:**
```hcl
resource "aws_iam_user" "velero" {
  name = "velero-backup-user"
  path = "/system/"

  tags = {
    Name      = "velero-backup-user"
    Purpose   = "Velero-Kubernetes-Backups"
    ManagedBy = "Terraform"
  }
}

resource "aws_iam_user_policy" "velero" {
  name = "velero-s3-backup-policy"
  user = aws_iam_user.velero.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "VeleroS3Access"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = [
          aws_s3_bucket.velero_backups.arn,
          "${aws_s3_bucket.velero_backups.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_access_key" "velero" {
  user = aws_iam_user.velero.name
}
```

**Testing:**
- [ ] IAM user `velero-backup-user` appears in AWS console under `/system/`
- [ ] Policy attached with S3 permissions scoped to velero bucket only
- [ ] Access key created successfully

---

#### Task 1.3: Create Kubernetes Secret for Velero Credentials

**Files:** `terraform/roots/asela-cluster/velero-s3.tf` (continue)

**Steps:**
1. Create Kubernetes secret in `velero` namespace
2. Store AWS credentials in Velero's expected format
3. Add outputs for verification

**Code:**
```hcl
resource "kubernetes_namespace" "velero" {
  metadata {
    name = "velero"
  }
}

resource "kubernetes_secret" "velero_credentials" {
  metadata {
    name      = "velero-aws-credentials"
    namespace = kubernetes_namespace.velero.metadata[0].name
  }

  data = {
    cloud = <<-EOF
      [default]
      aws_access_key_id=${aws_iam_access_key.velero.id}
      aws_secret_access_key=${aws_iam_access_key.velero.secret}
    EOF
  }

  type = "Opaque"
}

# Outputs
output "velero_s3_bucket" {
  description = "S3 bucket for Velero backups"
  value       = aws_s3_bucket.velero_backups.id
}

output "velero_iam_user_arn" {
  description = "ARN of the Velero IAM user"
  value       = aws_iam_user.velero.arn
}
```

**Testing:**
- [ ] Namespace `velero` created
- [ ] Secret `velero-aws-credentials` exists in velero namespace
- [ ] Secret contains properly formatted AWS credentials file

---

### Phase 2: Velero Deployment (GitOps)

#### Task 2.1: Create Velero ArgoCD Application

**Files:** `base-apps/velero.yaml` (new)

**Steps:**
1. Create ArgoCD Application manifest
2. Point to `base-apps/velero/` directory
3. Target namespace: `velero`
4. Enable auto-sync with prune and selfHeal

**Code:**
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: velero
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/velero
  destination:
    server: https://kubernetes.default.svc
    namespace: velero
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

**Testing:**
- [ ] Application appears in ArgoCD UI
- [ ] Status shows "Synced" after manifests are added

---

#### Task 2.2: Create Velero Helm Chart Configuration

**Files:** `base-apps/velero/Chart.yaml` (new)

**Steps:**
1. Create Helm chart dependency on velero/velero
2. Specify chart version

**Code:**
```yaml
apiVersion: v2
name: velero
description: Velero backup and restore for Kubernetes
type: application
version: 1.0.0
appVersion: "1.15.0"

dependencies:
  - name: velero
    version: 8.0.0
    repository: https://vmware-tanzu.github.io/helm-charts
```

**Testing:**
- [ ] Chart.yaml is valid YAML
- [ ] Dependency version exists in Velero Helm repo

---

#### Task 2.3: Create Velero Helm Values

**Files:** `base-apps/velero/values.yaml` (new)

**Steps:**
1. Configure AWS provider plugin
2. Set S3 backup location
3. Enable Kopia for file-level PVC backup
4. Reference existing credentials secret
5. Configure node-agent DaemonSet for PVC backups
6. Set resource limits

**Code:**
```yaml
velero:
  # Use existing secret created by Terraform
  credentials:
    useSecret: true
    existingSecret: velero-aws-credentials

  # AWS S3 configuration
  configuration:
    backupStorageLocation:
      - name: default
        provider: aws
        bucket: asela-velero-backups
        config:
          region: us-east-2

    # Enable Kopia for file-level PVC backup
    uploaderType: kopia

    # Default backup TTL (7 days)
    defaultBackupTTL: 168h

    # Features
    features: EnableCSI

  # Install AWS plugin
  initContainers:
    - name: velero-plugin-for-aws
      image: velero/velero-plugin-for-aws:v1.11.0
      volumeMounts:
        - mountPath: /target
          name: plugins

  # Node agent for PVC backups (runs on all nodes)
  deployNodeAgent: true

  nodeAgent:
    resources:
      requests:
        cpu: 100m
        memory: 256Mi
      limits:
        cpu: 500m
        memory: 512Mi

  # Velero server resources
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi
```

**Testing:**
- [ ] `kubectl get pods -n velero` shows velero and node-agent pods running
- [ ] `kubectl get backupstoragelocations -n velero` shows status `Available`
- [ ] `velero version` shows client and server versions

---

### Phase 3: Backup and Restore Scripts

#### Task 3.1: Create Pre-Shutdown Backup Script

**Files:** `recovery/pre-shutdown-backup.sh` (new)

**Steps:**
1. Add script header and safety checks
2. Verify Velero is healthy
3. Create backup of stateful namespaces with PVC data
4. Wait for backup completion
5. Display backup status and instructions

**Code:**
```bash
#!/bin/bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

BACKUP_NAME="pre-shutdown-$(date +%Y%m%d-%H%M%S)"
NAMESPACES="vault,postgresql,n8n,logging"

echo -e "${YELLOW}=== Velero Pre-Shutdown Backup ===${NC}"
echo "Backup name: ${BACKUP_NAME}"
echo "Namespaces: ${NAMESPACES}"
echo ""

# Check Velero is running
echo -e "${YELLOW}Checking Velero status...${NC}"
if ! kubectl get deployment velero -n velero &>/dev/null; then
    echo -e "${RED}ERROR: Velero is not deployed${NC}"
    exit 1
fi

# Check backup storage location is available
BSL_STATUS=$(kubectl get backupstoragelocations -n velero -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "Unknown")
if [ "$BSL_STATUS" != "Available" ]; then
    echo -e "${RED}ERROR: Backup storage location not available (status: ${BSL_STATUS})${NC}"
    exit 1
fi
echo -e "${GREEN}Velero is healthy, backup storage available${NC}"

# Create backup
echo ""
echo -e "${YELLOW}Creating backup...${NC}"
velero backup create "${BACKUP_NAME}" \
    --include-namespaces "${NAMESPACES}" \
    --default-volumes-to-fs-backup=true \
    --wait

# Check backup status
BACKUP_STATUS=$(velero backup get "${BACKUP_NAME}" -o jsonpath='{.status.phase}')
if [ "$BACKUP_STATUS" == "Completed" ]; then
    echo ""
    echo -e "${GREEN}=== Backup Completed Successfully ===${NC}"
    echo ""
    velero backup describe "${BACKUP_NAME}" --details
    echo ""
    echo -e "${GREEN}You can now safely shut down the cluster.${NC}"
    echo -e "${YELLOW}To restore after restart, run:${NC}"
    echo "  ./recovery/post-restart-restore.sh ${BACKUP_NAME}"
else
    echo -e "${RED}ERROR: Backup failed with status: ${BACKUP_STATUS}${NC}"
    velero backup logs "${BACKUP_NAME}"
    exit 1
fi
```

**Testing:**
- [ ] Script is executable (`chmod +x`)
- [ ] Runs without errors when Velero is healthy
- [ ] Creates backup with all specified namespaces
- [ ] PVC data is included (check with `velero backup describe --details`)

---

#### Task 3.2: Create Post-Restart Restore Script

**Files:** `recovery/post-restart-restore.sh` (new)

**Steps:**
1. Add script header and argument parsing
2. Clean CNI IP allocations on all nodes
3. Wait for Velero to be ready
4. List available backups if none specified
5. Perform restore with `--existing-resource-policy=update`
6. Wait for completion and verify

**Code:**
```bash
#!/bin/bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

BACKUP_NAME="${1:-}"
SSH_KEY="${SSH_KEY:-~/.ssh/ari_sela_key}"
NODES=("10.0.1.50" "10.0.1.51" "10.0.1.52")

echo -e "${YELLOW}=== Velero Post-Restart Recovery ===${NC}"
echo ""

# Step 1: Clean CNI on all nodes
echo -e "${YELLOW}Step 1: Cleaning CNI IP allocations on all nodes...${NC}"
for node in "${NODES[@]}"; do
    echo "  Cleaning node ${node}..."
    ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no "asela@${node}" \
        "sudo rm -rf /var/lib/cni/networks/cbr0 && sudo mkdir -p /var/lib/cni/networks/cbr0" \
        2>/dev/null || echo "  Warning: Could not clean ${node}"
done
echo -e "${GREEN}CNI cleanup complete${NC}"
echo ""

# Step 2: Wait for cluster to be ready
echo -e "${YELLOW}Step 2: Waiting for cluster nodes to be ready...${NC}"
kubectl wait --for=condition=Ready nodes --all --timeout=300s
echo -e "${GREEN}All nodes ready${NC}"
echo ""

# Step 3: Wait for Velero to be ready
echo -e "${YELLOW}Step 3: Waiting for Velero to be ready...${NC}"
kubectl wait --for=condition=available deployment/velero -n velero --timeout=300s
echo -e "${GREEN}Velero is ready${NC}"
echo ""

# Step 4: List backups if none specified
if [ -z "$BACKUP_NAME" ]; then
    echo -e "${YELLOW}Available backups:${NC}"
    velero backup get
    echo ""
    echo -e "${YELLOW}Enter backup name to restore (or 'latest' for most recent):${NC}"
    read -r BACKUP_NAME

    if [ "$BACKUP_NAME" == "latest" ]; then
        BACKUP_NAME=$(velero backup get -o jsonpath='{.items[0].metadata.name}')
        echo "Using latest backup: ${BACKUP_NAME}"
    fi
fi

# Verify backup exists
if ! velero backup get "${BACKUP_NAME}" &>/dev/null; then
    echo -e "${RED}ERROR: Backup '${BACKUP_NAME}' not found${NC}"
    exit 1
fi

# Step 5: Perform restore
echo ""
echo -e "${YELLOW}Step 4: Restoring from backup '${BACKUP_NAME}'...${NC}"
RESTORE_NAME="restore-${BACKUP_NAME}-$(date +%H%M%S)"

velero restore create "${RESTORE_NAME}" \
    --from-backup "${BACKUP_NAME}" \
    --existing-resource-policy=update \
    --wait

# Check restore status
RESTORE_STATUS=$(velero restore get "${RESTORE_NAME}" -o jsonpath='{.status.phase}')
if [ "$RESTORE_STATUS" == "Completed" ] || [ "$RESTORE_STATUS" == "PartiallyFailed" ]; then
    echo ""
    echo -e "${GREEN}=== Restore Completed ===${NC}"
    velero restore describe "${RESTORE_NAME}"

    if [ "$RESTORE_STATUS" == "PartiallyFailed" ]; then
        echo ""
        echo -e "${YELLOW}Warning: Some items failed to restore. Check logs:${NC}"
        echo "  velero restore logs ${RESTORE_NAME}"
    fi
else
    echo -e "${RED}ERROR: Restore failed with status: ${RESTORE_STATUS}${NC}"
    velero restore logs "${RESTORE_NAME}"
    exit 1
fi

# Step 6: Verify applications
echo ""
echo -e "${YELLOW}Step 5: Verifying applications...${NC}"
sleep 10  # Give pods time to start

echo "Checking pod status:"
kubectl get pods -n vault
kubectl get pods -n postgresql
kubectl get pods -n n8n
kubectl get pods -n logging

echo ""
echo -e "${YELLOW}Checking ArgoCD application status:${NC}"
kubectl get applications -n argo-cd

echo ""
echo -e "${GREEN}=== Recovery Complete ===${NC}"
echo ""
echo "Next steps:"
echo "1. Verify Vault is unsealed: kubectl exec -n vault vault-0 -- vault status"
echo "2. Check External Secrets: kubectl get externalsecrets --all-namespaces"
echo "3. Verify application data integrity"
```

**Testing:**
- [ ] Script is executable
- [ ] CNI cleanup runs on all nodes
- [ ] Lists backups when no argument provided
- [ ] Restore completes successfully
- [ ] Applications come up healthy after restore

---

#### Task 3.3: Update Recovery Documentation

**Files:** `recovery/CLUSTER-RECOVERY.md`

**Steps:**
1. Add "Pre-Shutdown Checklist" section at the top
2. Update "Quick Recovery Checklist" to use Velero
3. Add Velero troubleshooting section
4. Keep manual recovery steps as fallback

**Changes to add:**

```markdown
## Pre-Shutdown Checklist

Before shutting down the cluster, create a Velero backup:

\`\`\`bash
# Create backup of all stateful namespaces
./recovery/pre-shutdown-backup.sh

# Verify backup completed
velero backup get
\`\`\`

The backup includes: vault, postgresql, n8n, logging namespaces with all PVC data.

---

## Quick Recovery Checklist (with Velero)

After server restart, run the automated recovery:

\`\`\`bash
# Run full recovery (cleans CNI, restores from backup)
./recovery/post-restart-restore.sh

# Or specify a specific backup
./recovery/post-restart-restore.sh pre-shutdown-20260119-143022
\`\`\`

---

## Velero Troubleshooting

### Check Velero Status
\`\`\`bash
velero version
kubectl get backupstoragelocations -n velero
kubectl get pods -n velero
\`\`\`

### View Backup Details
\`\`\`bash
velero backup get
velero backup describe <backup-name> --details
velero backup logs <backup-name>
\`\`\`

### View Restore Details
\`\`\`bash
velero restore get
velero restore describe <restore-name>
velero restore logs <restore-name>
\`\`\`

### Manual Velero Commands
\`\`\`bash
# Create backup manually
velero backup create my-backup \
  --include-namespaces vault,postgresql,n8n,logging \
  --default-volumes-to-fs-backup=true

# Restore manually
velero restore create --from-backup my-backup \
  --existing-resource-policy=update

# Delete old backups
velero backup delete <backup-name>
\`\`\`
```

**Testing:**
- [ ] Documentation renders correctly in markdown
- [ ] All commands in documentation work as written
- [ ] Clear flow from pre-shutdown to post-restart

---

### Phase 4: Testing and Validation

#### Task 4.1: Terraform Apply and Verification

**Steps:**
1. Run `terraform init` (if new providers needed)
2. Run `terraform plan` and review changes
3. Run `terraform apply`
4. Verify all resources created

**Testing:**
- [ ] S3 bucket exists with correct configuration
- [ ] IAM user and policy created
- [ ] Kubernetes secret exists in velero namespace

---

#### Task 4.2: Velero Deployment Verification

**Steps:**
1. Commit and push velero application files
2. Wait for ArgoCD to sync
3. Verify Velero pods are running
4. Verify backup storage location is available

**Commands:**
```bash
# Check pods
kubectl get pods -n velero

# Check backup storage
velero backup-location get

# Test velero CLI
velero version
```

**Testing:**
- [ ] velero pod is Running
- [ ] node-agent pods running on all nodes
- [ ] Backup storage location shows "Available"

---

#### Task 4.3: End-to-End Backup Test

**Steps:**
1. Run pre-shutdown backup script
2. Verify backup completed
3. Check S3 bucket contains backup data

**Commands:**
```bash
./recovery/pre-shutdown-backup.sh
velero backup describe pre-shutdown-* --details
aws s3 ls s3://asela-velero-backups/backups/ --recursive
```

**Testing:**
- [ ] Backup shows "Completed" status
- [ ] All 4 namespaces included in backup
- [ ] PVC data uploaded to S3 (check kopia/ directory)

---

#### Task 4.4: End-to-End Restore Test (Non-Destructive)

**Steps:**
1. Create a test namespace with sample data
2. Include in a test backup
3. Delete the test data
4. Restore and verify

**Commands:**
```bash
# Create test
kubectl create ns velero-test
kubectl run test-pod -n velero-test --image=nginx
kubectl exec -n velero-test test-pod -- sh -c 'echo "test data" > /tmp/test.txt'

# Backup
velero backup create test-backup --include-namespaces velero-test

# Delete
kubectl delete ns velero-test

# Restore
velero restore create --from-backup test-backup

# Verify
kubectl get pods -n velero-test
```

**Testing:**
- [ ] Test namespace restored
- [ ] No impact on production namespaces

---

## File Summary

| File | Type | Description |
|------|------|-------------|
| `terraform/roots/asela-cluster/velero-s3.tf` | New | S3 bucket, IAM user, K8s secret |
| `base-apps/velero.yaml` | New | ArgoCD Application |
| `base-apps/velero/Chart.yaml` | New | Helm chart definition |
| `base-apps/velero/values.yaml` | New | Velero configuration |
| `recovery/pre-shutdown-backup.sh` | New | Backup script |
| `recovery/post-restart-restore.sh` | New | Restore script with CNI cleanup |
| `recovery/CLUSTER-RECOVERY.md` | Update | Add Velero sections |

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Kopia backup slow for large PVCs | Prometheus 50Gi may take 10-30 min | Document expected duration; can exclude logging namespace if not critical |
| S3 credentials in cluster | Security exposure if cluster compromised | Credentials scoped to single bucket only; consider IRSA in future |
| Restore order dependencies | Vault must be ready before ExternalSecrets work | Vault auto-unseals via KMS; script waits for pods |
| ArgoCD conflicts during restore | Resources might drift | `--existing-resource-policy=update` handles this |
| CNI still exhausted after restore | Pods stuck in ContainerCreating | CNI cleanup integrated into restore script |

---

## Estimated Timeline

| Phase | Tasks | Effort |
|-------|-------|--------|
| Phase 1: AWS Infrastructure | 1.1-1.3 | Terraform apply |
| Phase 2: Velero Deployment | 2.1-2.3 | Git commit + ArgoCD sync |
| Phase 3: Scripts | 3.1-3.3 | Script creation |
| Phase 4: Testing | 4.1-4.4 | Validation |

---

## References

- [Velero Documentation](https://velero.io/docs/)
- [Velero AWS Plugin](https://github.com/vmware-tanzu/velero-plugin-for-aws)
- [Velero Helm Chart](https://github.com/vmware-tanzu/helm-charts/tree/main/charts/velero)
- [File System Backup with Kopia](https://velero.io/docs/main/file-system-backup/)
