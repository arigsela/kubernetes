#!/usr/bin/env bash
# build-agentgateway-image.sh — the agentgateway MCP adapter as an OCI image (§T.133)
#
#   build-agentgateway-image.sh [--push] [--no-cache]
#
# base-apps/kagent/{agent-docs,brave-search,implementer}-mcp.yaml run agentgateway around a
# stdio MCP server: an init container runs `--copy-self` into an emptyDir and the server's own
# container runs the copy. --copy-self exists in musl builds only, and upstream stopped
# publishing musl images after v1.3.1 (agentgateway#2574). The release binaries are still
# static musl, so this packages one FROM scratch (images/agentgateway/Dockerfile).
#
# Same contract as build-model-image.sh. The version and the binary's sha256 are pinned below
# and checked after download. The build is reproducible (SOURCE_DATE_EPOCH, rewritten
# timestamps, no attestations), so the same inputs give the same digest. Without --push it
# builds a local OCI archive and prints the digest. With --push it pushes, never overwriting
# an existing tag (same digest: skip; different digest: error), and prints the reference to
# pin. The ECR repository comes from Terraform (terraform/roots/asela-cluster/ecr.tf); this
# script never creates it.
#
# Bumping: Renovate edits AGW_VERSION only, and tests/agent-identity fails until the rest
# follows. Set AGW_SHA256 from the release's agentgateway-linux-amd64.sha256, run with --push,
# and pin the printed reference in the three Deployments' init containers.
#
# Needs: curl, docker buildx (a builder that can export OCI), aws CLI with ECR push rights (--push).
set -euo pipefail

usage() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "build-agentgateway-image: $*" >&2; exit 2; }

PUSH=0; NOCACHE=()
for a in "$@"; do
  case "$a" in
    --push) PUSH=1 ;;
    --no-cache) NOCACHE=(--no-cache) ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $a" ;;
  esac
done

# renovate: datasource=github-releases depName=agentgateway/agentgateway
AGW_VERSION=v1.6.0
# sha256 of the release asset agentgateway-linux-amd64 (its .sha256 asset says the same)
AGW_SHA256=496ed27e6a96b55a9ad7cabe5c2be7172511f44a3cb113e533d22bf93a7f079d

REGION=us-east-2
REGISTRY="852893458518.dkr.ecr.${REGION}.amazonaws.com"
REPO=agentgateway
TAG="$AGW_VERSION"                    # immutable: a Dockerfile change alone needs a new tag (e.g. -r2)
PLATFORM=linux/amd64                  # the cluster's nodes
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="${IMAGE_CACHE:-$HOME/.cache/homelab-images}/agentgateway"
CONTEXT="$CACHE/$AGW_VERSION"
URL="https://github.com/agentgateway/agentgateway/releases/download/$AGW_VERSION/agentgateway-linux-amd64"
DOCKERFILE="$REPO_ROOT/images/agentgateway/Dockerfile"
REF="$REGISTRY/$REPO:$TAG"
export SOURCE_DATE_EPOCH=0            # fixed timestamps: part of what makes the digest stable

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

mkdir -p "$CONTEXT"
BIN="$CONTEXT/agentgateway"
if [ ! -f "$BIN" ]; then
  echo "downloading agentgateway $AGW_VERSION ..."
  rm -f "$BIN.part"
  curl -fL --retry 3 -o "$BIN.part" "$URL" || { rm -f "$BIN.part"; die "download failed: $URL"; }
  mv "$BIN.part" "$BIN"
fi
got=$(sha256 "$BIN")
[ "$got" = "$AGW_SHA256" ] || { rm -f "$BIN"; die "agentgateway-linux-amd64 sha256 $got != pinned $AGW_SHA256 (deleted; re-run)"; }
echo "verified  agentgateway $AGW_VERSION  $got"

# gzip: the binary compresses to about a third, and every node pulls it.
OUT_OPTS="oci-mediatypes=true,compression=gzip,force-compression=true,rewrite-timestamp=true"

build() {  # build <output spec> <metadata file>
  docker buildx build --platform "$PLATFORM" -f "$DOCKERFILE" ${NOCACHE[@]+"${NOCACHE[@]}"} \
    --provenance=false --sbom=false --output "$1" --metadata-file "$2" "$CONTEXT" >&2 \
    || die "docker buildx build failed (output above)"
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["containerimage.digest"])' "$2"
}

META="$CACHE/$AGW_VERSION.metadata.json"
DIGEST=$(build "type=oci,dest=$CACHE/$AGW_VERSION.oci.tar,$OUT_OPTS" "$META")
echo "built     $REF  $DIGEST"

if [ "$PUSH" -eq 0 ]; then
  echo "(local build only: $CACHE/$AGW_VERSION.oci.tar; re-run with --push to publish)"
  exit 0
fi

aws ecr describe-repositories --region "$REGION" --repository-names "$REPO" >/dev/null 2>&1 \
  || die "ECR repository $REPO not found: it comes from terraform/roots/asela-cluster/ecr.tf (applied by Atlantis)"
EXISTING=$(aws ecr describe-images --region "$REGION" --repository-name "$REPO" \
             --image-ids imageTag="$TAG" --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || true)
if [ -n "$EXISTING" ] && [ "$EXISTING" != "None" ]; then
  [ "$EXISTING" = "$DIGEST" ] || die "$REF already exists as $EXISTING, built $DIGEST: inputs changed, bump TAG"
  echo "already pushed (same digest)"
else
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
  # unpack=false: the docker driver's image exporter would otherwise unpack into the local
  # store, which conflicts with rewrite-timestamp. Nothing needs the image locally.
  PUSHED=$(build "type=image,name=$REF,push=true,unpack=false,$OUT_OPTS" "$CACHE/$AGW_VERSION.push.metadata.json")
  [ "$PUSHED" = "$DIGEST" ] || die "pushed digest $PUSHED != built $DIGEST (build not reproducible?)"
  echo "pushed    $REF"
fi
echo
echo "pin this:  $REF@$DIGEST"
