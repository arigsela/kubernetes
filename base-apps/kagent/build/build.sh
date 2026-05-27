#!/usr/bin/env bash
# Build & push a custom kagent UI image that uses Node 20 LTS instead of Node 24,
# so the Next.js process doesn't SIGILL on older x86-64 CPUs (the HP server's CPU
# lacks the x86-64-v2 instruction baseline that Node 24 requires).
#
# See: docs/superpowers/specs/2026-05-09-kagent-ui-custom-image-design.md
# Upstream issue: https://github.com/kagent-dev/kagent/issues/1505

set -euo pipefail

# --- config ---
KAGENT_VERSION="0.9.4"
ECR_REGISTRY="852893458518.dkr.ecr.us-east-2.amazonaws.com"
ECR_REGION="us-east-2"
IMAGE_NAME="kagent-ui"
IMAGE_TAG="${KAGENT_VERSION}-node20"
PLATFORM="linux/amd64"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "==> Building $ECR_REGISTRY/$IMAGE_NAME:$IMAGE_TAG"
echo "==> Working in $WORK_DIR"

# --- 1. Clone upstream at the pinned tag ---
echo "==> Cloning kagent-dev/kagent at v${KAGENT_VERSION}"
git clone --depth 1 --branch "v${KAGENT_VERSION}" \
  https://github.com/kagent-dev/kagent.git "$WORK_DIR/kagent"

# --- 2. Swap in the patched Dockerfile ---
echo "==> Applying patched Dockerfile"
cp "$SCRIPT_DIR/Dockerfile" "$WORK_DIR/kagent/ui/Dockerfile"

# --- (optional) Risk A/B contingency: patch nginx.conf / supervisord.conf if needed ---
# Enable these by un-commenting and dropping the corresponding .patch file in this dir.
# if [[ -f "$SCRIPT_DIR/nginx.conf.patch" ]]; then
#   echo "==> Applying nginx.conf patch"
#   patch "$WORK_DIR/kagent/ui/conf/nginx.conf" < "$SCRIPT_DIR/nginx.conf.patch"
# fi
# if [[ -f "$SCRIPT_DIR/supervisord.conf.patch" ]]; then
#   echo "==> Applying supervisord.conf patch"
#   patch "$WORK_DIR/kagent/ui/conf/supervisord.conf" < "$SCRIPT_DIR/supervisord.conf.patch"
# fi

# --- 3. ECR login ---
echo "==> Logging in to ECR"
aws ecr get-login-password --region "$ECR_REGION" \
  | docker login --username AWS --password-stdin "$ECR_REGISTRY"

# --- 4. Build & push (single-platform amd64) ---
echo "==> Building and pushing $PLATFORM image"
docker buildx build \
  --platform "$PLATFORM" \
  --tag "$ECR_REGISTRY/$IMAGE_NAME:$IMAGE_TAG" \
  --push \
  "$WORK_DIR/kagent/ui"

echo ""
echo "==> Done. Pushed: $ECR_REGISTRY/$IMAGE_NAME:$IMAGE_TAG"
