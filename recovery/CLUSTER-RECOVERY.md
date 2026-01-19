# Cluster Recovery Guide

This document describes how to backup and restore the k3s cluster after server restarts.

## Quick Recovery Checklist

After server restart, run these steps in order:

```bash
# 1. Verify nodes are ready
kubectl get nodes

# 2. Check for CNI issues (if pods stuck in ContainerCreating)
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.50
sudo ls /var/lib/cni/networks/cbr0/ | wc -l  # Should be < 100
# If IP exhaustion: sudo rm -rf /var/lib/cni/networks/cbr0 && sudo mkdir -p /var/lib/cni/networks/cbr0

# 3. Unseal Vault
./recovery/restore-vault-secrets.sh

# 4. Verify applications
kubectl get applications -n argo-cd
kubectl get externalsecrets --all-namespaces
```

## Understanding the Problem

The k3s cluster loses state in several areas after restart:

1. **Vault Seal State**: Vault seals itself on restart and needs to be unsealed
2. **Vault Secrets**: If Vault PVC is lost, all secrets need to be repopulated
3. **CNI IP Allocations**: Flannel can leak IPs causing IP exhaustion
4. **Pod States**: Pods may become orphaned and need cleanup

## Recovery Scripts

### 1. Vault Unseal & Secrets Restoration
```bash
./recovery/restore-vault-secrets.sh
```
This script:
- Checks if Vault is sealed and unseals it
- Configures Kubernetes auth if needed
- Populates all required secrets
- Restarts external-secrets to sync

### 2. Full Cluster Recovery
```bash
./recovery/full-cluster-recovery.sh
```
This script:
- Checks node health
- Cleans up CNI if needed
- Unseals Vault
- Restores secrets
- Verifies all applications

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

### Step 4: Unseal Vault
```bash
# Check status
kubectl exec -n vault vault-0 -- vault status

# If sealed, unseal with 2 keys
kubectl exec -n vault vault-0 -- vault operator unseal 4jwSTxUfSkGFy9bJ9Bn+FRLX4mHxW6Gj6gLiYcKpPjzq
kubectl exec -n vault vault-0 -- vault operator unseal d8jwx3Ie6zsNi2OSse6kDOXzmeWLOPnf5egOcf6qns9Z
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

## Backup Strategy

### What Gets Backed Up Automatically

1. **Git Repository**: All ArgoCD applications and configs are in git
2. **Terraform State**: Stored in S3 bucket `asela-terraform-states`
3. **AWS RDS**: MySQL database is managed by AWS

### What Needs Manual Backup

1. **Vault Secrets**: Backed up in `recovery/restore-vault-secrets.sh`
2. **Vault Unseal Keys**: Stored in `recovery/vault-credentials.txt`
3. **PVC Data**: PostgreSQL, Vault data volumes

### Recommended: PVC Backup with Velero

For production-grade backup, consider installing Velero:

```bash
# Install Velero with AWS provider
velero install \
  --provider aws \
  --plugins velero/velero-plugin-for-aws:v1.7.0 \
  --bucket asela-velero-backups \
  --backup-location-config region=us-east-2 \
  --snapshot-location-config region=us-east-2 \
  --secret-file ./credentials-velero

# Create scheduled backup
velero schedule create daily-backup --schedule="0 2 * * *" --include-namespaces vault,postgresql
```

## Prevention: Auto-Unseal Vault

To avoid manual unsealing, consider migrating to auto-unseal:

### Option 1: AWS KMS Auto-Unseal
```hcl
seal "awskms" {
  region     = "us-east-2"
  kms_key_id = "your-kms-key-id"
}
```

### Option 2: Transit Auto-Unseal
Use another Vault instance for auto-unsealing.

## Cluster Nodes

| Node | IP | Role |
|------|------|------|
| k3s-control-01 | 10.0.1.50 | Control Plane |
| k3s-worker-01 | 10.0.1.51 | Worker |
| k3s-worker-02 | 10.0.1.52 | Worker |

SSH access: `ssh -i ~/.ssh/ari_sela_key asela@<IP>`

## Important Files

| File | Purpose |
|------|---------|
| `recovery/vault-credentials.txt` | Vault unseal keys and root token |
| `recovery/restore-vault-secrets.sh` | Script to restore all Vault secrets |
| `recovery/full-cluster-recovery.sh` | Complete cluster recovery script |
| `.gitignore` | Excludes recovery files from git |

## Troubleshooting

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
