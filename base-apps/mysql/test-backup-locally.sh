#!/bin/bash
# Test MySQL Backup Image Locally

set -e

echo "=========================================="
echo "MySQL Backup Image - Local Test"
echo "=========================================="
echo ""

# Configuration
IMAGE="852893458518.dkr.ecr.us-east-2.amazonaws.com/mysql-backup:latest"
AWS_REGION="us-east-2"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}Step 1: Pulling latest image from ECR...${NC}"
aws ecr get-login-password --region ${AWS_REGION} | \
  docker login --username AWS --password-stdin 852893458518.dkr.ecr.us-east-2.amazonaws.com

docker pull ${IMAGE}
echo ""

echo -e "${BLUE}Step 2: Verifying image contents...${NC}"
echo -e "${GREEN}✓ Checking backup script exists:${NC}"
docker run --rm --entrypoint cat ${IMAGE} /usr/local/bin/backup-script.sh | head -10

echo ""
echo -e "${GREEN}✓ Checking AWS CLI version:${NC}"
docker run --rm --entrypoint aws ${IMAGE} --version

echo ""
echo -e "${GREEN}✓ Checking MySQL client version:${NC}"
docker run --rm --entrypoint mysql ${IMAGE} --version

echo ""
echo -e "${GREEN}✓ Checking mysqldump version:${NC}"
docker run --rm --entrypoint mysqldump ${IMAGE} --version

echo ""
echo -e "${BLUE}Step 3: Testing backup with live credentials...${NC}"
echo -e "${YELLOW}This will attempt a real backup to S3!${NC}"
echo ""

# Get MySQL password from Vault
echo "Fetching MySQL password from Vault..."
MYSQL_PASSWORD=$(vault kv get -field=password k8s-secrets/mysql/credentials 2>/dev/null || echo "")

if [ -z "${MYSQL_PASSWORD}" ]; then
  echo -e "${YELLOW}⚠ Could not fetch MySQL password from Vault${NC}"
  echo "Please enter MySQL root password manually:"
  read -s MYSQL_PASSWORD
fi

# Get AWS credentials from Vault
echo "Fetching AWS credentials from Vault..."
AWS_ACCESS_KEY_ID=$(vault kv get -field=AWS_ACCESS_KEY_ID k8s-secrets/mysql/backup-credentials 2>/dev/null || echo "")
AWS_SECRET_ACCESS_KEY=$(vault kv get -field=AWS_SECRET_ACCESS_KEY k8s-secrets/mysql/backup-credentials 2>/dev/null || echo "")
S3_BUCKET=$(vault kv get -field=S3_BUCKET k8s-secrets/mysql/backup-credentials 2>/dev/null || echo "mysql-backups-asela-cluster")

if [ -z "${AWS_ACCESS_KEY_ID}" ] || [ -z "${AWS_SECRET_ACCESS_KEY}" ]; then
  echo -e "${YELLOW}⚠ Could not fetch AWS credentials from Vault${NC}"
  echo "Please enter AWS credentials manually:"
  read -p "AWS_ACCESS_KEY_ID: " AWS_ACCESS_KEY_ID
  read -s -p "AWS_SECRET_ACCESS_KEY: " AWS_SECRET_ACCESS_KEY
  echo ""
fi

# Determine MySQL host
# If running in Docker, use host.docker.internal to reach host machine
# If you want to test against the cluster, use the actual IP
echo ""
echo "Select MySQL host to test against:"
echo "  1) Local MySQL on host machine (host.docker.internal)"
echo "  2) Kubernetes cluster MySQL (10.0.1.110 - requires network access)"
echo "  3) Custom host"
read -p "Choice [1]: " CHOICE
CHOICE=${CHOICE:-1}

case $CHOICE in
  1)
    MYSQL_HOST="host.docker.internal"
    ;;
  2)
    MYSQL_HOST="10.0.1.110"
    echo "Note: You may need to set up port forwarding:"
    echo "  kubectl port-forward -n mysql svc/mysql 3306:3306"
    ;;
  3)
    read -p "Enter MySQL host: " MYSQL_HOST
    ;;
esac

echo ""
echo -e "${BLUE}Running backup test...${NC}"
echo "  MySQL Host: ${MYSQL_HOST}"
echo "  S3 Bucket: ${S3_BUCKET}"
echo ""

# Run the backup
docker run --rm \
  --add-host=host.docker.internal:host-gateway \
  -e MYSQL_HOST="${MYSQL_HOST}" \
  -e MYSQL_PORT="3306" \
  -e MYSQL_USER="root" \
  -e MYSQL_PASSWORD="${MYSQL_PASSWORD}" \
  -e MYSQL_DATABASE="chores" \
  -e S3_BUCKET="${S3_BUCKET}" \
  -e AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID}" \
  -e AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY}" \
  -e AWS_REGION="us-east-1" \
  -e BACKUP_RETENTION_DAYS="30" \
  ${IMAGE}

echo ""
echo -e "${GREEN}=========================================="
echo "✓ Test completed!"
echo -e "==========================================${NC}"
echo ""
echo "Check S3 bucket for the backup:"
echo "  aws s3 ls s3://${S3_BUCKET}/"
