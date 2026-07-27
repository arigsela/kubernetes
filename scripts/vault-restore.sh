#!/usr/bin/env bash
# vault-restore.sh — restore Vault's `file` storage from a vault-backup.sh artifact
# (SPEC.md §T.35, §V.28)
#
# §V.28 will not take a Vault backup on faith: restoring the bytes proves nothing unless
# Vault then unseals and a real secret reads back. This script gates on the checksum,
# refuses artifacts branded INCONSISTENT unless the operator says so out loud, and — by
# default — requires a verification command to demonstrate recovery.
#
# ⚠ awskms: the restored bytes are ciphertext. Without the KMS key
# (alias/vault-auto-unseal) they cannot be decrypted, no matter how good the artifact is.
#
# Usage:
#   vault-restore.sh --artifact <file.tar.gz> [--data-dir DIR] [--verify-cmd CMD]
#                    [--no-verify] [--accept-inconsistent]

set -euo pipefail

ARTIFACT=""
DATA_DIR=""
VERIFY_CMD=""
NO_VERIFY=0
ACCEPT_INCONSISTENT=0

die() { echo "vault-restore: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --artifact)            ARTIFACT="${2:-}"; shift 2 ;;
    --data-dir)            DATA_DIR="${2:-}"; shift 2 ;;
    --verify-cmd)          VERIFY_CMD="${2:-}"; shift 2 ;;
    --no-verify)           NO_VERIFY=1; shift ;;
    --accept-inconsistent) ACCEPT_INCONSISTENT=1; shift ;;
    -h|--help)             sed -n '2,18p' "$0" >&2; exit 2 ;;
    *)                     die "unknown argument: $1" ;;
  esac
done

[ -n "$ARTIFACT" ] || die "--artifact is required"
[ -f "$ARTIFACT" ] || die "artifact not found: $ARTIFACT"
[ -n "$DATA_DIR" ] || die "--data-dir is required"

ARTIFACT_NAME="$(basename "$ARTIFACT")"

# --- refuse a knowingly-torn artifact unless acknowledged ---------------------------
case "$ARTIFACT_NAME" in
  *INCONSISTENT*)
    if [ "$ACCEPT_INCONSISTENT" -eq 0 ]; then
      die "artifact '$ARTIFACT_NAME' is branded INCONSISTENT — it was taken while Vault
     was running, so it may contain a partially-written file. Pass --accept-inconsistent
     to restore from it anyway, understanding it may not produce a working Vault."
    fi
    echo "vault-restore: ⚠ restoring from an INCONSISTENT artifact by explicit request" >&2
    ;;
esac

# --- integrity gate -----------------------------------------------------------------
CHECKSUM_FILE="$ARTIFACT.sha256"
[ -f "$CHECKSUM_FILE" ] || die "no checksum manifest at $CHECKSUM_FILE — unverifiable,
     so per §V.28 this is not a backup"

ARTIFACT_DIR="$(cd "$(dirname "$ARTIFACT")" && pwd -P)"
if command -v sha256sum >/dev/null 2>&1; then CHECK="sha256sum -c"; else CHECK="shasum -a 256 -c"; fi
if ! ( cd "$ARTIFACT_DIR" && $CHECK "$ARTIFACT_NAME.sha256" >/dev/null 2>&1 ); then
  die "checksum mismatch for $ARTIFACT_NAME — artifact is corrupt or truncated, refusing
     to restore from it"
fi
echo "vault-restore: checksum OK for $ARTIFACT_NAME"

# Preserve any existing tree; a failed restore should not also destroy the evidence.
if [ -d "$DATA_DIR" ] && [ -n "$(ls -A "$DATA_DIR" 2>/dev/null)" ]; then
  ASIDE="${DATA_DIR}.pre-restore.$(date -u +%Y%m%dT%H%M%SZ)"
  echo "vault-restore: moving existing data dir aside -> $ASIDE"
  mv "$DATA_DIR" "$ASIDE"
fi

mkdir -p "$DATA_DIR"
tar -xzf "$ARTIFACT" -C "$DATA_DIR"

[ -d "$DATA_DIR/core" ] || die "restored tree has no core/ — this does not look like a
     Vault file-storage backup"

echo "vault-restore: data restored to $DATA_DIR"

# --- §V.28: prove recovery, do not assume it ----------------------------------------
if [ "$NO_VERIFY" -eq 1 ]; then
  echo "vault-restore: §V.28 NOT satisfied — caller must prove unseal + secret read" >&2
  exit 0
fi

[ -n "$VERIFY_CMD" ] || die "--verify-cmd is required unless --no-verify is given.
     §V.28 requires proof that Vault unseals and a known secret reads back; restoring
     bytes is not recovery."

echo "vault-restore: verifying unseal + secret read (§V.28)..."
if ! eval "$VERIFY_CMD"; then
  die "§V28 violation: verification command failed. Data is on disk but Vault is NOT
     proven recovered."
fi

echo "vault-restore: OK — Vault unsealed and secret read back, restore proven (§V.28)"
