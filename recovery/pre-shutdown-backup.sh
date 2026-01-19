#!/bin/bash
set -euo pipefail

# =============================================================================
# Velero Pre-Shutdown Backup Script
# =============================================================================
# Creates a Velero backup of all stateful namespaces before cluster shutdown.
# This ensures PVC data is safely stored in S3 for restore after restart.
#
# Usage: ./pre-shutdown-backup.sh
# =============================================================================

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

BACKUP_NAME="pre-shutdown-$(date +%Y%m%d-%H%M%S)"
NAMESPACES="vault,postgresql,n8n,logging"

echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           Velero Pre-Shutdown Backup                          ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "Backup name: ${BACKUP_NAME}"
echo "Namespaces:  ${NAMESPACES}"
echo ""

# -----------------------------------------------------------------------------
# Step 1: Check Velero is running
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[1/4] Checking Velero deployment...${NC}"
if ! kubectl get deployment velero -n velero &>/dev/null; then
    echo -e "${RED}ERROR: Velero is not deployed in the cluster${NC}"
    echo "Please ensure Velero is installed before running backups."
    exit 1
fi

VELERO_READY=$(kubectl get deployment velero -n velero -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
if [ "$VELERO_READY" != "1" ]; then
    echo -e "${RED}ERROR: Velero deployment is not ready (replicas: ${VELERO_READY})${NC}"
    kubectl get pods -n velero
    exit 1
fi
echo -e "${GREEN}✓ Velero deployment is ready${NC}"

# -----------------------------------------------------------------------------
# Step 2: Check backup storage location is available
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[2/4] Checking backup storage location...${NC}"
BSL_STATUS=$(kubectl get backupstoragelocations -n velero -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "Unknown")
if [ "$BSL_STATUS" != "Available" ]; then
    echo -e "${RED}ERROR: Backup storage location not available (status: ${BSL_STATUS})${NC}"
    echo ""
    echo "Troubleshooting:"
    echo "  kubectl get backupstoragelocations -n velero"
    echo "  kubectl describe backupstoragelocation -n velero"
    exit 1
fi
echo -e "${GREEN}✓ Backup storage location is available (S3)${NC}"

# -----------------------------------------------------------------------------
# Step 3: Check node-agent is running (required for PVC backups)
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[3/4] Checking node-agent DaemonSet...${NC}"
NODE_AGENT_DESIRED=$(kubectl get daemonset node-agent -n velero -o jsonpath='{.status.desiredNumberScheduled}' 2>/dev/null || echo "0")
NODE_AGENT_READY=$(kubectl get daemonset node-agent -n velero -o jsonpath='{.status.numberReady}' 2>/dev/null || echo "0")
if [ "$NODE_AGENT_READY" != "$NODE_AGENT_DESIRED" ] || [ "$NODE_AGENT_DESIRED" == "0" ]; then
    echo -e "${YELLOW}WARNING: node-agent DaemonSet not fully ready (${NODE_AGENT_READY}/${NODE_AGENT_DESIRED})${NC}"
    echo "PVC backups may not work correctly."
    echo ""
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo -e "${GREEN}✓ node-agent DaemonSet is ready (${NODE_AGENT_READY}/${NODE_AGENT_DESIRED} nodes)${NC}"
fi

# -----------------------------------------------------------------------------
# Step 4: Create backup
# -----------------------------------------------------------------------------
echo ""
echo -e "${YELLOW}[4/4] Creating backup...${NC}"
echo "This may take several minutes depending on PVC data size."
echo ""

velero backup create "${BACKUP_NAME}" \
    --include-namespaces "${NAMESPACES}" \
    --default-volumes-to-fs-backup=true \
    --wait

# Check backup status
BACKUP_STATUS=$(velero backup get "${BACKUP_NAME}" -o jsonpath='{.status.phase}')

if [ "$BACKUP_STATUS" == "Completed" ]; then
    echo ""
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║           Backup Completed Successfully!                      ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    # Show backup details
    velero backup describe "${BACKUP_NAME}" --details

    echo ""
    echo -e "${GREEN}You can now safely shut down the cluster.${NC}"
    echo ""
    echo -e "${YELLOW}To restore after restart, run:${NC}"
    echo -e "  ${BLUE}./recovery/post-restart-restore.sh ${BACKUP_NAME}${NC}"
    echo ""
    echo -e "${YELLOW}Or to restore from the latest backup:${NC}"
    echo -e "  ${BLUE}./recovery/post-restart-restore.sh${NC}"

elif [ "$BACKUP_STATUS" == "PartiallyFailed" ]; then
    echo ""
    echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║           Backup Partially Failed                             ║${NC}"
    echo -e "${YELLOW}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "Some items failed to backup. Review the logs:"
    echo "  velero backup logs ${BACKUP_NAME}"
    echo ""
    velero backup describe "${BACKUP_NAME}"
    exit 1

else
    echo ""
    echo -e "${RED}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║           Backup Failed                                       ║${NC}"
    echo -e "${RED}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "Backup status: ${BACKUP_STATUS}"
    echo ""
    echo "View logs for details:"
    echo "  velero backup logs ${BACKUP_NAME}"
    exit 1
fi
