#!/usr/bin/env bash
# build-model-image.sh — model weights as OCI images for Kubernetes image volumes
# (GA in 1.36; docs/plans/k8s-136-features-implementation-plan.md, Phase 4, Task 4.1)
#
#   build-model-image.sh <qwen|nomic-embed-text> [--push] [--no-cache]
#
# Without --push: builds a local OCI archive and prints its manifest digest. The build is
# reproducible (pinned sources, SOURCE_DATE_EPOCH, rewritten timestamps, no attestations), so
# the same inputs give the same digest: run it twice with --no-cache to check.
# With --push: also creates the ECR repo if missing (IMMUTABLE tags) and pushes. A tag that
# already exists is never overwritten: same digest means already pushed (skip), a different
# digest is an error (the inputs changed: bump the tag). Prints the repo@sha256:... reference
# to pin in the Deployment.
#
# Sources are pinned and verified, so an upstream change can never slip into an image:
#   qwen              Hugging Face unsloth/Qwen3.5-0.8B-GGUF at a fixed commit; every file's
#                     sha256 checked after download (the same bytes the qwen PVC held)
#   nomic-embed-text  Ollama tag v1.5, model digest asserted in the Dockerfile (== the digest
#                     the ollama PVC held; kagent's stored embeddings depend on it)
#
# Needs: docker buildx (a builder that can export OCI), aws CLI with ECR push rights (--push).
set -euo pipefail

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "build-model-image: $*" >&2; exit 2; }

MODEL="${1:-}"; shift || true
PUSH=0; NOCACHE=()
for a in "$@"; do
  case "$a" in
    --push) PUSH=1 ;;
    --no-cache) NOCACHE=(--no-cache) ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $a" ;;
  esac
done

REGION=us-east-2
REGISTRY="852893458518.dkr.ecr.${REGION}.amazonaws.com"
PLATFORM=linux/amd64                  # the cluster's nodes; the content itself is CPU-neutral
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="${MODEL_CACHE:-$HOME/.cache/homelab-models}"
export SOURCE_DATE_EPOCH=0            # fixed timestamps: part of what makes the digest stable

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

BUILD_ARGS=()
case "$MODEL" in
  qwen)
    REPO=models/qwen3.5-0.8b
    HF_REPO=unsloth/Qwen3.5-0.8B-GGUF
    HF_REV=6ab461498e2023f6e3c1baea90a8f0fe38ab64d0
    TAG=hf-6ab4614-ud-q4-k-xl        # immutable: change it whenever HF_REV or FILES change
    FILES="Qwen3.5-0.8B-UD-Q4_K_XL.gguf 3177ebd67afe4438374da19e690bc1b98756f7e0fea9240e1be404336156a7b5
mmproj-F16.gguf 56e4c6cfe73b0c82e3e82bc518d7591997e61d81f723fc41a586f4fa69ea2453"
    CONTEXT="$CACHE/qwen/$HF_REV"
    mkdir -p "$CONTEXT"
    while read -r f want; do
      if [ ! -f "$CONTEXT/$f" ]; then
        echo "downloading $f @ $HF_REV ..."
        curl -fL --retry 3 -o "$CONTEXT/$f.part" "https://huggingface.co/$HF_REPO/resolve/$HF_REV/$f"
        mv "$CONTEXT/$f.part" "$CONTEXT/$f"
      fi
      got=$(sha256 "$CONTEXT/$f")
      [ "$got" = "$want" ] || { rm -f "$CONTEXT/$f"; die "$f sha256 $got != pinned $want (deleted; re-run)"; }
      echo "verified  $f  $got"
    done <<< "$FILES"
    ;;
  nomic-embed-text)
    REPO=models/nomic-embed-text
    TAG=v1.5
    BUILD_ARGS=(--build-arg MODEL_TAG=v1.5
                --build-arg MODEL_DIGEST=sha256:970aa74c0a90ef7482477cf803618e776e173c007bf957f635f1015bfcfef0e6)
    CONTEXT="$REPO_ROOT/models/nomic-embed-text"
    ;;
  -h|--help|"") usage; [ -n "$MODEL" ] && exit 0 || exit 2 ;;
  *) die "unknown model: $MODEL (qwen | nomic-embed-text)" ;;
esac
DOCKERFILE="$REPO_ROOT/models/$MODEL/Dockerfile"
REF="$REGISTRY/$REPO:$TAG"
# Uncompressed: GGUF barely compresses, and the kubelet then skips a decompression pass.
OUT_OPTS="oci-mediatypes=true,compression=uncompressed,force-compression=true,rewrite-timestamp=true"

build() {  # build <output spec> <metadata file>
  docker buildx build --platform "$PLATFORM" -f "$DOCKERFILE" ${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"} ${NOCACHE[@]+"${NOCACHE[@]}"} \
    --provenance=false --sbom=false --output "$1" --metadata-file "$2" "$CONTEXT" >&2 \
    || die "docker buildx build failed (output above)"
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["containerimage.digest"])' "$2"
}

mkdir -p "$CACHE"
META="$CACHE/$MODEL.metadata.json"
DIGEST=$(build "type=oci,dest=$CACHE/$MODEL.oci.tar,$OUT_OPTS" "$META")
echo "built     $REF  $DIGEST"

if [ "$PUSH" -eq 0 ]; then
  echo "(local build only: $CACHE/$MODEL.oci.tar; re-run with --push to publish)"
  exit 0
fi

aws ecr describe-repositories --region "$REGION" --repository-names "$REPO" >/dev/null 2>&1 || {
  echo "creating ECR repository $REPO (IMMUTABLE tags)"
  aws ecr create-repository --region "$REGION" --repository-name "$REPO" \
    --image-tag-mutability IMMUTABLE >/dev/null
}
EXISTING=$(aws ecr describe-images --region "$REGION" --repository-name "$REPO" \
             --image-ids imageTag="$TAG" --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || true)
if [ -n "$EXISTING" ] && [ "$EXISTING" != "None" ]; then
  [ "$EXISTING" = "$DIGEST" ] || die "$REF already exists as $EXISTING, built $DIGEST: inputs changed, bump TAG"
  echo "already pushed (same digest)"
else
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
  # unpack=false: the docker driver's image exporter would otherwise unpack into the local
  # store, which conflicts with rewrite-timestamp. Nothing needs the image locally.
  PUSHED=$(build "type=image,name=$REF,push=true,unpack=false,$OUT_OPTS" "$CACHE/$MODEL.push.metadata.json")
  [ "$PUSHED" = "$DIGEST" ] || die "pushed digest $PUSHED != built $DIGEST (build not reproducible?)"
  echo "pushed    $REF"
fi
echo
echo "pin this:  $REGISTRY/$REPO@$DIGEST"
