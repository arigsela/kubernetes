#!/bin/bash
set -e

# MySQL Backup Docker Image - Build and Push Script
# This script builds and pushes the MySQL backup image to ECR

# Configuration
IMAGE_NAME="mysql-backup"
VERSION="1.0.0"
ECR_REGISTRY="852893458518.dkr.ecr.us-east-2.amazonaws.com"
AWS_REGION="us-east-2"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=========================================="
echo "MySQL Backup Image Build & Push"
echo -e "==========================================${NC}"
echo ""

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "${SCRIPT_DIR}"

# Step 1: Build
echo -e "${GREEN}[1/6]${NC} Building Docker image..."
docker build -f backup-dockerfile -t ${IMAGE_NAME}:${VERSION} .
echo ""

# Step 2: Tag for ECR
echo -e "${GREEN}[2/6]${NC} Tagging image for ECR..."
docker tag ${IMAGE_NAME}:${VERSION} ${ECR_REGISTRY}/${IMAGE_NAME}:${VERSION}
docker tag ${IMAGE_NAME}:${VERSION} ${ECR_REGISTRY}/${IMAGE_NAME}:latest
echo ""

# Step 3: Authenticate with ECR
echo -e "${GREEN}[3/6]${NC} Authenticating with ECR..."
aws ecr get-login-password --region ${AWS_REGION} | \
  docker login --username AWS --password-stdin ${ECR_REGISTRY}
echo ""

# Step 4: Create ECR repository (if it doesn't exist)
echo -e "${GREEN}[4/6]${NC} Creating ECR repository (if needed)..."
aws ecr create-repository \
  --repository-name ${IMAGE_NAME} \
  --region ${AWS_REGION} \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256 2>/dev/null || echo "Repository already exists"
echo ""

# Step 5: Push to ECR
echo -e "${GREEN}[5/6]${NC} Pushing images to ECR..."
docker push ${ECR_REGISTRY}/${IMAGE_NAME}:${VERSION}
docker push ${ECR_REGISTRY}/${IMAGE_NAME}:latest
echo ""

# Step 6: Verify
echo -e "${GREEN}[6/6]${NC} Verifying image in ECR..."
aws ecr describe-images \
  --repository-name ${IMAGE_NAME} \
  --region ${AWS_REGION} \
  --query 'sort_by(imageDetails,& imagePushedAt)[-5:].[imageTags[0],imagePushedAt,imageSizeInBytes]' \
  --output table
echo ""

echo -e "${BLUE}=========================================="
echo "✓ Build and push completed successfully!"
echo -e "==========================================${NC}"
echo ""
echo "Image locations:"
echo "  ${ECR_REGISTRY}/${IMAGE_NAME}:${VERSION}"
echo "  ${ECR_REGISTRY}/${IMAGE_NAME}:latest"
echo ""
echo "Next steps:"
echo "  1. Update backup-cronjob.yaml with this image"
echo "  2. Commit and push to trigger ArgoCD sync"
echo ""
