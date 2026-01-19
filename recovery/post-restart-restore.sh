#!/bin/bash
set -euo pipefail

# =============================================================================
# Velero Post-Restart Recovery Script
# =============================================================================
# Performs full cluster recovery after restart:
# 1. Cleans CNI IP allocations on all nodes
# 2. Waits for cluster to be ready
# 3. Restores from Velero backup
# 4. Verifies applications are healthy
#
# Usage: ./post-restart-restore.sh [backup-name]
#        If no backup name is provided, lists available backups.
# =============================================================================

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

BACKUP_NAME="${1:-}"
SSH_KEY="${SSH_KEY:-~/.ssh/ari_sela_key}"
SSH_USER="${SSH_USER:-asela}"
NODES=("10.0.1.50" "10.0.1.51" "10.0.1.52")

echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           Velero Post-Restart Recovery                        ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# -----------------------------------------------------------------------------
# Step 1: Clean CNI on all nodes
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[1/5] Cleaning CNI IP allocations on all nodes...${NC}"
echo "This prevents IP exhaustion issues after restart."
echo ""

CNI_CLEANED=0
for node in "${NODES[@]}"; do
    echo -n "  Cleaning node ${node}... "
    if ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${SSH_USER}@${node}" \
        "sudo rm -rf /var/lib/cni/networks/cbr0 && sudo mkdir -p /var/lib/cni/networks/cbr0" 2>/dev/null; then
        echo -e "${GREEN}done${NC}"
        ((CNI_CLEANED++))
    else
        echo -e "${YELLOW}skipped (node unreachable)${NC}"
    fi
done

if [ "$CNI_CLEANED" -eq 0 ]; then
    echo -e "${YELLOW}WARNING: Could not clean CNI on any nodes.${NC}"
    echo "You may need to clean CNI manually if pods get stuck in ContainerCreating."
else
    echo -e "${GREEN}✓ CNI cleanup complete (${CNI_CLEANED}/${#NODES[@]} nodes)${NC}"
fi
echo ""

# -----------------------------------------------------------------------------
# Step 2: Wait for cluster to be ready
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[2/5] Waiting for cluster nodes to be ready...${NC}"
if ! kubectl wait --for=condition=Ready nodes --all --timeout=300s 2>/dev/null; then
    echo -e "${RED}ERROR: Nodes did not become ready within 5 minutes${NC}"
    kubectl get nodes
    exit 1
fi
echo -e "${GREEN}✓ All nodes are ready${NC}"
kubectl get nodes
echo ""

# -----------------------------------------------------------------------------
# Step 3: Wait for Velero to be ready
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[3/5] Waiting for Velero to be ready...${NC}"

# First check if Velero namespace exists
if ! kubectl get namespace velero &>/dev/null; then
    echo -e "${RED}ERROR: Velero namespace does not exist${NC}"
    echo "Velero may not be deployed. Please deploy Velero first."
    exit 1
fi

# Wait for Velero deployment
if ! kubectl wait --for=condition=available deployment/velero -n velero --timeout=300s 2>/dev/null; then
    echo -e "${RED}ERROR: Velero did not become ready within 5 minutes${NC}"
    kubectl get pods -n velero
    exit 1
fi

# Check backup storage location
BSL_STATUS=$(kubectl get backupstoragelocations -n velero -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "Unknown")
if [ "$BSL_STATUS" != "Available" ]; then
    echo -e "${YELLOW}WARNING: Backup storage location status: ${BSL_STATUS}${NC}"
    echo "Waiting for backup storage to become available..."
    sleep 30
    BSL_STATUS=$(kubectl get backupstoragelocations -n velero -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "Unknown")
    if [ "$BSL_STATUS" != "Available" ]; then
        echo -e "${RED}ERROR: Backup storage location still not available${NC}"
        kubectl describe backupstoragelocation -n velero
        exit 1
    fi
fi

echo -e "${GREEN}✓ Velero is ready, backup storage available${NC}"
echo ""

# -----------------------------------------------------------------------------
# Step 4: Select and perform restore
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[4/5] Selecting backup to restore...${NC}"

# If no backup name provided, list available and prompt
if [ -z "$BACKUP_NAME" ]; then
    echo ""
    echo "Available backups:"
    echo "─────────────────────────────────────────────────────────────────"
    velero backup get
    echo "─────────────────────────────────────────────────────────────────"
    echo ""
    echo -e "${YELLOW}Enter backup name to restore (or 'latest' for most recent):${NC}"
    read -r BACKUP_NAME

    if [ -z "$BACKUP_NAME" ]; then
        echo -e "${RED}ERROR: No backup name provided${NC}"
        exit 1
    fi

    if [ "$BACKUP_NAME" == "latest" ]; then
        BACKUP_NAME=$(velero backup get --output json | jq -r '.items | sort_by(.metadata.creationTimestamp) | last | .metadata.name' 2>/dev/null)
        if [ -z "$BACKUP_NAME" ] || [ "$BACKUP_NAME" == "null" ]; then
            echo -e "${RED}ERROR: Could not determine latest backup${NC}"
            exit 1
        fi
        echo "Using latest backup: ${BACKUP_NAME}"
    fi
fi

# Verify backup exists and is completed
BACKUP_STATUS=$(velero backup get "${BACKUP_NAME}" -o jsonpath='{.status.phase}' 2>/dev/null || echo "NotFound")
if [ "$BACKUP_STATUS" == "NotFound" ]; then
    echo -e "${RED}ERROR: Backup '${BACKUP_NAME}' not found${NC}"
    echo ""
    echo "Available backups:"
    velero backup get
    exit 1
fi

if [ "$BACKUP_STATUS" != "Completed" ]; then
    echo -e "${YELLOW}WARNING: Backup status is '${BACKUP_STATUS}' (not Completed)${NC}"
    read -p "Continue with restore anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo ""
echo "Backup details:"
velero backup describe "${BACKUP_NAME}" | head -20
echo ""

# Perform restore
RESTORE_NAME="restore-$(date +%Y%m%d-%H%M%S)"
echo -e "${YELLOW}Creating restore '${RESTORE_NAME}' from backup '${BACKUP_NAME}'...${NC}"
echo "This may take several minutes depending on PVC data size."
echo ""

velero restore create "${RESTORE_NAME}" \
    --from-backup "${BACKUP_NAME}" \
    --existing-resource-policy=update \
    --wait

# Check restore status
RESTORE_STATUS=$(velero restore get "${RESTORE_NAME}" -o jsonpath='{.status.phase}')

if [ "$RESTORE_STATUS" == "Completed" ]; then
    echo -e "${GREEN}✓ Restore completed successfully${NC}"
elif [ "$RESTORE_STATUS" == "PartiallyFailed" ]; then
    echo -e "${YELLOW}⚠ Restore partially failed${NC}"
    echo "Some items may not have been restored. Check logs:"
    echo "  velero restore logs ${RESTORE_NAME}"
else
    echo -e "${RED}✗ Restore failed with status: ${RESTORE_STATUS}${NC}"
    echo "View logs for details:"
    echo "  velero restore logs ${RESTORE_NAME}"
    exit 1
fi

echo ""
velero restore describe "${RESTORE_NAME}"
echo ""

# -----------------------------------------------------------------------------
# Step 5: Verify applications
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[5/5] Verifying applications...${NC}"
echo "Waiting 15 seconds for pods to start..."
sleep 15

echo ""
echo "Vault namespace:"
kubectl get pods -n vault 2>/dev/null || echo "  Namespace not found or no pods"

echo ""
echo "PostgreSQL namespace:"
kubectl get pods -n postgresql 2>/dev/null || echo "  Namespace not found or no pods"

echo ""
echo "n8n namespace:"
kubectl get pods -n n8n 2>/dev/null || echo "  Namespace not found or no pods"

echo ""
echo "Logging namespace:"
kubectl get pods -n logging 2>/dev/null || echo "  Namespace not found or no pods"

echo ""
echo "ArgoCD Applications:"
kubectl get applications -n argo-cd 2>/dev/null || echo "  Could not get applications"

# -----------------------------------------------------------------------------
# Complete
# -----------------------------------------------------------------------------
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           Recovery Complete!                                  ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Verify Vault is unsealed:"
echo "   kubectl exec -n vault vault-0 -- vault status"
echo ""
echo "2. Check External Secrets are syncing:"
echo "   kubectl get externalsecrets --all-namespaces"
echo ""
echo "3. Verify application data integrity:"
echo "   - PostgreSQL: kubectl exec -n postgresql deploy/postgresql -- psql -U n8n -d n8n -c '\\dt'"
echo "   - n8n: Check workflows in n8n UI"
echo ""
echo "4. If any pods are stuck in ContainerCreating, CNI may need additional cleanup:"
echo "   kubectl describe pod <pod-name> -n <namespace>"
