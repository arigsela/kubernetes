# Post-Restart Recovery Walkthrough

This document provides a detailed step-by-step guide for recovering the K3s cluster after the physical host restarts.

---

## Prerequisites

Before shutting down, ensure you have:
- [ ] Run `./recovery/pre-shutdown-backup.sh` to create a Velero backup
- [ ] Noted the backup name (e.g., `pre-shutdown-20260120-143022`)

---

## Phase 1: Physical Host and VM Startup

### Step 1.1: Start the Physical Host
Power on the physical machine that hosts the K3s cluster VMs.

### Step 1.2: Verify VMs are Running
```bash
# Check VM status (run on physical host)
virsh list --all
```

Expected output - all VMs should be running:
```
 Id   Name             State
--------------------------------
 1    k3s-control-01   running
 2    k3s-worker-01    running
 3    k3s-worker-02    running
```

If VMs are not running:
```bash
virsh start k3s-control-01
virsh start k3s-worker-01
virsh start k3s-worker-02
```

### Step 1.3: Wait for VMs to Boot (1-2 minutes)
```bash
# Test SSH connectivity to control plane
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.50 "echo 'Control plane accessible'"
```

---

## Phase 2: Verify K3s Cluster Health

### Step 2.1: Check Node Status
```bash
kubectl get nodes
```

Expected output:
```
NAME             STATUS   ROLES                  AGE    VERSION
k3s-control-01   Ready    control-plane,master   XXd    v1.33.5+k3s1
k3s-worker-01    Ready    <none>                 XXd    v1.33.5+k3s1
k3s-worker-02    Ready    <none>                 XXd    v1.33.5+k3s1
```

**If nodes show NotReady**, wait 1-2 minutes and retry. K3s needs time to initialize.

### Step 2.2: Check System Pods
```bash
kubectl get pods -n kube-system
```

Wait until all pods show `Running` or `Completed` status.

---

## Phase 3: Clean CNI IP Allocations

This step prevents the IP exhaustion issue that causes pods to get stuck in `ContainerCreating`.

### Step 3.1: Clean CNI on Control Plane
```bash
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.50 << 'EOF'
echo "Cleaning CNI on k3s-control-01..."
sudo rm -rf /var/lib/cni/networks/cbr0
sudo mkdir -p /var/lib/cni/networks/cbr0
echo "Done. IP count: $(ls /var/lib/cni/networks/cbr0/ 2>/dev/null | wc -l)"
EOF
```

### Step 3.2: Clean CNI on Worker 1
```bash
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.51 << 'EOF'
echo "Cleaning CNI on k3s-worker-01..."
sudo rm -rf /var/lib/cni/networks/cbr0
sudo mkdir -p /var/lib/cni/networks/cbr0
echo "Done. IP count: $(ls /var/lib/cni/networks/cbr0/ 2>/dev/null | wc -l)"
EOF
```

### Step 3.3: Clean CNI on Worker 2
```bash
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.52 << 'EOF'
echo "Cleaning CNI on k3s-worker-02..."
sudo rm -rf /var/lib/cni/networks/cbr0
sudo mkdir -p /var/lib/cni/networks/cbr0
echo "Done. IP count: $(ls /var/lib/cni/networks/cbr0/ 2>/dev/null | wc -l)"
EOF
```

---

## Phase 4: Wait for Core Services

### Step 4.1: Wait for ArgoCD
```bash
echo "Waiting for ArgoCD..."
kubectl wait --for=condition=available deployment/argocd-server -n argo-cd --timeout=300s
echo "ArgoCD is ready"
```

### Step 4.2: Verify ArgoCD Applications are Syncing
```bash
kubectl get applications -n argo-cd
```

Applications should show `Synced` status. Some may show `Degraded` health until Vault is ready.

### Step 4.3: Wait for Vault Auto-Unseal
```bash
echo "Waiting for Vault pod..."
kubectl wait --for=condition=ready pod/vault-0 -n vault --timeout=300s

echo "Checking Vault seal status..."
kubectl exec -n vault vault-0 -- vault status
```

Expected output should show:
```
Seal Type          awskms
Sealed             false    <-- This confirms auto-unseal worked
```

### Step 4.4: Wait for External Secrets Operator
```bash
echo "Waiting for External Secrets..."
kubectl wait --for=condition=available deployment/external-secrets -n external-secrets --timeout=300s
```

### Step 4.5: Wait for Velero
```bash
echo "Waiting for Velero..."
kubectl wait --for=condition=available deployment/velero -n velero --timeout=300s

echo "Waiting for node-agent pods..."
kubectl wait --for=condition=ready pod -l name=node-agent -n velero --timeout=120s

echo "Checking backup storage location..."
kubectl get backupstoragelocations -n velero
```

The backup storage location should show `Phase: Available`.

---

## Phase 5: Restore from Velero Backup

### Step 5.1: List Available Backups
```bash
velero backup get
```

Example output:
```
NAME                          STATUS      ERRORS   WARNINGS   CREATED                         EXPIRES
pre-shutdown-20260120-143022  Completed   0        0          2026-01-20 14:30:22 -0500 EST   6d
```

### Step 5.2: Choose Backup and Create Restore
```bash
# Replace with your actual backup name
BACKUP_NAME="pre-shutdown-20260120-143022"

velero restore create "restore-$(date +%Y%m%d-%H%M%S)" \
  --from-backup $BACKUP_NAME \
  --existing-resource-policy=update \
  --wait
```

### Step 5.3: Check Restore Status
```bash
velero restore get
```

Expected: `Completed` or `PartiallyFailed` (some warnings are normal).

### Step 5.4: View Restore Details (if needed)
```bash
# Replace with actual restore name from previous command
velero restore describe <restore-name> --details
```

---

## Phase 6: Verify Application Health

### Step 6.1: Check All ArgoCD Applications
```bash
kubectl get applications -n argo-cd
```

All applications should show:
- `SYNC STATUS: Synced`
- `HEALTH STATUS: Healthy` (or `Progressing` if still starting)

### Step 6.2: Check External Secrets are Syncing
```bash
kubectl get externalsecrets --all-namespaces
```

All should show `SecretSynced = True`.

### Step 6.3: Check All Pods
```bash
kubectl get pods --all-namespaces | grep -v Running | grep -v Completed
```

This shows any pods that are not healthy. Some may need a few minutes to start.

### Step 6.4: Verify Critical Applications

**Vault:**
```bash
kubectl exec -n vault vault-0 -- vault status
```

**PostgreSQL:**
```bash
kubectl get pods -n postgresql
```

**n8n:**
```bash
kubectl get pods -n n8n
```

**Grafana/Prometheus:**
```bash
kubectl get pods -n logging
```

---

## Phase 7: Final Verification

### Step 7.1: Test Application Access
- Grafana: https://grafana.ari-sela.com
- n8n: https://n8n.ari-sela.com
- Vault: https://vault.ari-sela.com (internal)

### Step 7.2: Verify Data Integrity
Check that your data was restored:
- n8n workflows are present
- Grafana dashboards are present
- Prometheus metrics are available

---

## Quick Reference: Automated Script

Instead of running all steps manually, you can use the automated script:

```bash
./recovery/post-restart-restore.sh [backup-name]
```

This script performs all of Phase 3-6 automatically.

---

## Troubleshooting

### Pods Stuck in ContainerCreating
CNI issue - re-run Phase 3 (CNI cleanup).

### Vault Still Sealed
Check AWS KMS connectivity:
```bash
kubectl logs -n vault vault-0 | grep -i kms
kubectl get secret vault-kms-credentials -n vault
```

### External Secrets Not Syncing
Restart the operator:
```bash
kubectl rollout restart deployment external-secrets -n external-secrets
```

### ArgoCD Application Not Syncing
Force refresh:
```bash
kubectl annotate application <app-name> -n argo-cd argocd.argoproj.io/refresh=normal --overwrite
```

### Velero Restore Errors
View detailed logs:
```bash
velero restore logs <restore-name>
```

---

## Timeline Estimate

| Phase | Duration |
|-------|----------|
| Phase 1: Physical/VM Startup | 2-5 minutes |
| Phase 2: K3s Health Check | 1-2 minutes |
| Phase 3: CNI Cleanup | 1 minute |
| Phase 4: Core Services Ready | 3-5 minutes |
| Phase 5: Velero Restore | 2-5 minutes |
| Phase 6: Application Verification | 2-3 minutes |
| **Total** | **~15-20 minutes** |

---

## Checklist Summary

- [ ] Physical host powered on
- [ ] All 3 VMs running
- [ ] All 3 K3s nodes Ready
- [ ] CNI cleaned on all nodes
- [ ] ArgoCD ready
- [ ] Vault auto-unsealed
- [ ] External Secrets operator ready
- [ ] Velero ready
- [ ] Backup restored
- [ ] All applications healthy
- [ ] Data verified
