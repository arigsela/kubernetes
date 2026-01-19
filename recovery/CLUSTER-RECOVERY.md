# Cluster Recovery Guide

This document describes how to backup and restore the k3s cluster after server restarts.

---

## Pre-Shutdown Checklist

**Before shutting down the cluster**, create a Velero backup to preserve PVC data:

```bash
# Create backup of all stateful namespaces (vault, postgresql, n8n, logging)
./recovery/pre-shutdown-backup.sh

# Verify backup completed
velero backup get
```

The backup includes all Kubernetes resources and PVC data for stateful applications.
Backups are stored in S3 (`asela-velero-backups`) and retained for 7 days.

---

## Quick Recovery Checklist (with Velero)

After server restart, run the automated recovery script:

```bash
# Full automated recovery (cleans CNI, restores from backup, verifies apps)
./recovery/post-restart-restore.sh

# Or specify a specific backup
./recovery/post-restart-restore.sh pre-shutdown-20260119-143022
```

This script automatically:
1. Cleans CNI IP allocations on all nodes
2. Waits for cluster and Velero to be ready
3. Restores from the specified (or latest) backup
4. Verifies all applications are healthy

---

## Quick Recovery Checklist (Manual)

If Velero is not available or you prefer manual recovery:

```bash
# 1. Verify nodes are ready
kubectl get nodes

# 2. Check for CNI issues (if pods stuck in ContainerCreating)
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.50
sudo ls /var/lib/cni/networks/cbr0/ | wc -l  # Should be < 100
# If IP exhaustion: sudo rm -rf /var/lib/cni/networks/cbr0 && sudo mkdir -p /var/lib/cni/networks/cbr0

# 3. Verify Vault auto-unsealed (no manual action needed!)
kubectl exec -n vault vault-0 -- vault status

# 4. Verify applications
kubectl get applications -n argo-cd
kubectl get externalsecrets --all-namespaces
```

> **Note**: Vault now uses AWS KMS auto-unseal. No manual unsealing is required after restarts!

---

## Understanding the Problem

The k3s cluster loses state in several areas after restart:

1. **Vault Seal State**: ~~Vault seals itself on restart and needs to be unsealed~~ **RESOLVED** - Vault now uses AWS KMS auto-unseal
2. **Vault Secrets**: If Vault PVC is lost, all secrets need to be repopulated
3. **CNI IP Allocations**: Flannel can leak IPs causing IP exhaustion
4. **Pod States**: Pods may become orphaned and need cleanup
5. **PVC Data**: Local-path storage data may be lost if not backed up - **RESOLVED** with Velero

---

## Velero Backup and Restore

Velero is deployed to backup and restore Kubernetes resources and PVC data.

### Architecture

```
Pre-Shutdown:  Velero backup (resources + PVC data via Kopia) → S3
Post-Restart:  CNI cleanup → Velero restore → ArgoCD reconciles → Cluster healthy
```

### Backup Namespaces

The following namespaces are backed up (contain PVCs with important data):
- `vault` - Vault data and configuration
- `postgresql` - PostgreSQL database (10Gi)
- `n8n` - n8n workflow data (5Gi)
- `logging` - Prometheus metrics (50Gi) and Grafana dashboards (10Gi)

### Pre-Shutdown Backup

```bash
# Run the backup script
./recovery/pre-shutdown-backup.sh

# The script will:
# 1. Verify Velero is healthy
# 2. Check backup storage location (S3)
# 3. Create backup with PVC data
# 4. Wait for completion
# 5. Display restore instructions
```

### Post-Restart Restore

```bash
# Run the restore script (prompts for backup selection)
./recovery/post-restart-restore.sh

# Or restore from a specific backup
./recovery/post-restart-restore.sh pre-shutdown-20260119-143022

# The script will:
# 1. Clean CNI on all nodes
# 2. Wait for cluster to be ready
# 3. Wait for Velero to be ready
# 4. Restore from backup
# 5. Verify applications
```

### Manual Velero Commands

```bash
# List all backups
velero backup get

# View backup details
velero backup describe <backup-name> --details

# View backup logs
velero backup logs <backup-name>

# Create manual backup
velero backup create my-backup \
  --include-namespaces vault,postgresql,n8n,logging \
  --default-volumes-to-fs-backup=true

# Restore from backup
velero restore create --from-backup <backup-name> \
  --existing-resource-policy=update

# List restores
velero restore get

# View restore details
velero restore describe <restore-name>

# Delete old backups
velero backup delete <backup-name>
```

---

## Recovery Scripts

### 1. Velero Pre-Shutdown Backup (Recommended)
```bash
./recovery/pre-shutdown-backup.sh
```
This script:
- Verifies Velero is healthy
- Creates backup of all stateful namespaces with PVC data
- Waits for backup completion
- Displays restore instructions

### 2. Velero Post-Restart Restore (Recommended)
```bash
./recovery/post-restart-restore.sh [backup-name]
```
This script:
- Cleans CNI IP allocations on all nodes
- Waits for cluster and Velero to be ready
- Restores from backup (prompts if no backup specified)
- Verifies all applications

### 3. Vault Secrets Restoration (if Velero unavailable)
```bash
./recovery/restore-vault-secrets.sh
```
This script:
- Verifies Vault is running and auto-unsealed via AWS KMS
- Configures Kubernetes auth if needed
- Populates all required secrets
- Restarts external-secrets to sync

> **Note**: Manual unsealing is no longer required. Vault automatically unseals using AWS KMS.

### 4. Full Cluster Recovery (Legacy)
```bash
./recovery/full-cluster-recovery.sh
```
This script:
- Checks node health
- Cleans up CNI if needed
- Unseals Vault
- Restores secrets
- Verifies all applications

---

## Manual Recovery Steps

### Step 1: Check Node Status
```bash
kubectl get nodes
```
All nodes should be `Ready`. If not, check VM status with `virsh list --all`.

### Step 2: Check Pod Status
```bash
kubectl get pods --all-namespaces | grep -v Running | grep -v Completed
```

### Step 3: Fix CNI IP Exhaustion (if pods stuck in ContainerCreating)
```bash
# SSH to control plane
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.50

# Check IP allocations
ls /var/lib/cni/networks/cbr0/ | wc -l

# If > 200 IPs allocated, clear and recreate
sudo rm -rf /var/lib/cni/networks/cbr0
sudo mkdir -p /var/lib/cni/networks/cbr0

# Exit SSH
exit
```

### Step 4: Verify Vault Auto-Unsealed
```bash
# Check status - should show Sealed: false automatically
kubectl exec -n vault vault-0 -- vault status

# Expected output:
# Seal Type          awskms
# Sealed             false
```

> **Note**: Vault now auto-unseals via AWS KMS. If Vault is still sealed after restart, check:
> - AWS KMS key availability and permissions
> - The `vault-kms-credentials` secret in the vault namespace
> - Vault pod logs: `kubectl logs -n vault vault-0`

### Step 4b: Emergency Manual Recovery (if auto-unseal fails)
Only use this if AWS KMS auto-unseal is not working:
```bash
# Use recovery keys (need 2 of 3) - see recovery/vault-credentials.txt
kubectl exec -n vault vault-0 -- vault operator unseal <RECOVERY_KEY_1>
kubectl exec -n vault vault-0 -- vault operator unseal <RECOVERY_KEY_2>
```

### Step 5: Verify External Secrets
```bash
kubectl get externalsecrets --all-namespaces
```
All should show `SecretSynced = True`.

### Step 6: Verify Applications
```bash
kubectl get applications -n argo-cd
```
All should show `Synced` and `Healthy`.

---

## Backup Strategy

### What Gets Backed Up Automatically

1. **Git Repository**: All ArgoCD applications and configs are in git
2. **Terraform State**: Stored in S3 bucket `asela-terraform-states`
3. **AWS RDS**: MySQL database is managed by AWS with automated backups
4. **Velero Backups**: PVC data backed up to S3 bucket `asela-velero-backups`

### What Needs Manual Backup

1. **Vault Recovery Keys**: Stored in `recovery/vault-credentials.txt` (not in git)

### Velero Backup Details

| Item | Backup Method | Storage | Retention |
|------|---------------|---------|-----------|
| K8s Resources | Velero | S3 | 7 days |
| PVC Data | Velero + Kopia | S3 | 7 days |
| Vault Secrets | Vault + KMS | In-cluster + KMS | Persistent |
| ArgoCD Apps | Git | GitHub | Permanent |
| Terraform State | Terraform | S3 | Versioned |

---

## AWS KMS Auto-Unseal Configuration

Vault is configured to automatically unseal using AWS KMS. This eliminates manual intervention after restarts.

### Current Configuration
- **Seal Type**: awskms
- **KMS Key ID**: `2d982e46-c7bd-4606-a1bf-a470d1c09e07`
- **KMS Alias**: `alias/vault-auto-unseal`
- **AWS Region**: us-east-2

### How It Works
1. Vault encrypts its master key with the AWS KMS key
2. On startup, Vault calls AWS KMS to decrypt the master key
3. Vault automatically unseals without human intervention

### Recovery Keys
With auto-unseal, the original unseal keys become "recovery keys". They are only needed for:
- Regenerating a root token
- Migrating to a different seal type
- Emergency recovery scenarios

Recovery keys are stored in `recovery/vault-credentials.txt`.

### Troubleshooting Auto-Unseal
If Vault fails to auto-unseal:
```bash
# Check Vault logs for KMS errors
kubectl logs -n vault vault-0 | grep -i kms

# Verify KMS credentials secret exists
kubectl get secret vault-kms-credentials -n vault

# Verify AWS IAM permissions
aws kms describe-key --key-id 2d982e46-c7bd-4606-a1bf-a470d1c09e07 --region us-east-2
```

---

## Cluster Nodes

| Node | IP | Role |
|------|------|------|
| k3s-control-01 | 10.0.1.50 | Control Plane |
| k3s-worker-01 | 10.0.1.51 | Worker |
| k3s-worker-02 | 10.0.1.52 | Worker |

SSH access: `ssh -i ~/.ssh/ari_sela_key asela@<IP>`

---

## Important Files

| File | Purpose |
|------|---------|
| `recovery/pre-shutdown-backup.sh` | Create Velero backup before shutdown |
| `recovery/post-restart-restore.sh` | Restore from Velero backup after restart |
| `recovery/vault-credentials.txt` | Vault recovery keys, root token, and KMS config |
| `recovery/restore-vault-secrets.sh` | Script to restore all Vault secrets (legacy) |
| `recovery/full-cluster-recovery.sh` | Complete cluster recovery script (legacy) |
| `terraform/roots/asela-cluster/vault-kms.tf` | Terraform for KMS key and IAM resources |
| `terraform/roots/asela-cluster/velero-s3.tf` | Terraform for Velero S3 bucket and IAM |
| `base-apps/velero/` | Velero Helm chart configuration |
| `.gitignore` | Excludes recovery files from git |

---

## Troubleshooting

### Velero Backup Issues

```bash
# Check Velero pod status
kubectl get pods -n velero

# Check backup storage location
kubectl get backupstoragelocations -n velero
kubectl describe backupstoragelocation default -n velero

# Check node-agent DaemonSet (required for PVC backups)
kubectl get daemonset node-agent -n velero

# View Velero logs
kubectl logs -n velero -l app.kubernetes.io/name=velero

# View node-agent logs
kubectl logs -n velero -l name=node-agent
```

### Velero Restore Issues

```bash
# Check restore status
velero restore get
velero restore describe <restore-name>

# View restore logs
velero restore logs <restore-name>

# Check for partially failed items
velero restore describe <restore-name> --details
```

### Pods Stuck in ContainerCreating
- Check CNI IP allocation (see Step 3)
- Check events: `kubectl describe pod <pod-name> -n <namespace>`

### External Secrets Not Syncing
- Verify Vault is unsealed
- Check SecretStore status: `kubectl get secretstores --all-namespaces`
- Restart external-secrets: `kubectl rollout restart deployment external-secrets -n external-secrets`

### ArgoCD Applications Not Syncing
- Force refresh: `kubectl annotate application <app> -n argo-cd argocd.argoproj.io/refresh=normal --overwrite`
- Check ArgoCD logs: `kubectl logs -n argo-cd -l app.kubernetes.io/name=argocd-application-controller`

### PostgreSQL Connection Issues
- Verify PostgreSQL is running: `kubectl get pods -n postgresql`
- Check database exists: `kubectl exec -n postgresql deploy/postgresql -- psql -U n8n -d postgres -c "\l"`
