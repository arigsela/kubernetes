#!/usr/bin/env bash
# k3s-restore.sh — restore the k3s server state from a k3s-backup.sh artifact (SPEC.md §T.2)
#
# k3s migrates the SQLite schema one-way on a minor upgrade and does not support
# downgrade, so this script is the rollback path for every hop in the §T walk. It is
# also the thing §V.16 refuses to take on faith: a restore that has not been shown to
# bring the API back at the expected version does not count as a restore, and an
# artifact that has never been through this script does not count as a backup (§V.1).
#
# Usage:
#   k3s-restore.sh --artifact <file.tar.gz> --expect-version v1.33.5+k3s1
#                  [--data-dir DIR] [--skip-service] [--verify-cmd CMD] [--no-verify]
#
#   --skip-service   restore the data tree only; the caller stops/starts/installs k3s.
#                    Used by the containerised drill, where docker owns the lifecycle.
#   --verify-cmd     command whose output must contain --expect-version.
#   --no-verify      data-only restore with no version assertion. Never use this on a
#                    real rollback — it is here so the drill can assert §V.16 itself.

set -euo pipefail

DATA_DIR="${K3S_DATA_DIR:-/var/lib/rancher/k3s}"
K3S_SERVICE="${K3S_SERVICE:-k3s}"
ARTIFACT=""
EXPECT_VERSION=""
VERIFY_CMD=""
SKIP_SERVICE=0
NO_VERIFY=0

die() { echo "k3s-restore: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --artifact)       ARTIFACT="${2:-}"; shift 2 ;;
    --data-dir)       DATA_DIR="${2:-}"; shift 2 ;;
    --expect-version) EXPECT_VERSION="${2:-}"; shift 2 ;;
    --verify-cmd)     VERIFY_CMD="${2:-}"; shift 2 ;;
    --skip-service)   SKIP_SERVICE=1; shift ;;
    --no-verify)      NO_VERIFY=1; shift ;;
    -h|--help)        sed -n '2,20p' "$0" >&2; exit 2 ;;
    *)                die "unknown argument: $1" ;;
  esac
done

[ -n "$ARTIFACT" ]       || die "--artifact is required"
[ -n "$EXPECT_VERSION" ] || die "--expect-version is required (§V.16)"
[ -f "$ARTIFACT" ]       || die "artifact not found: $ARTIFACT"

# --- integrity gate: a corrupt artifact must fail loudly, not restore quietly --------
CHECKSUM_FILE="$ARTIFACT.sha256"
[ -f "$CHECKSUM_FILE" ] || die "no checksum manifest at $CHECKSUM_FILE — this artifact
     cannot be verified, so per §V.1 it is not a backup"

ARTIFACT_DIR="$(cd "$(dirname "$ARTIFACT")" && pwd -P)"
ARTIFACT_NAME="$(basename "$ARTIFACT")"
if command -v sha256sum >/dev/null 2>&1; then
  CHECK="sha256sum -c"
else
  CHECK="shasum -a 256 -c"
fi
if ! ( cd "$ARTIFACT_DIR" && $CHECK "$ARTIFACT_NAME.sha256" >/dev/null 2>&1 ); then
  die "checksum mismatch for $ARTIFACT_NAME — artifact is corrupt or truncated, refusing
     to restore from it"
fi
echo "k3s-restore: checksum OK for $ARTIFACT_NAME"

# --- stop k3s before touching its data dir ------------------------------------------
if [ "$SKIP_SERVICE" -eq 0 ]; then
  echo "k3s-restore: stopping ${K3S_SERVICE}..."
  systemctl stop "$K3S_SERVICE" || true
fi

# Preserve whatever is currently there. A rollback that destroys the failed state also
# destroys the evidence needed to work out why the hop failed.
if [ -d "$DATA_DIR/server" ]; then
  ASIDE="${DATA_DIR}.pre-restore.$(date -u +%Y%m%dT%H%M%SZ)"
  echo "k3s-restore: moving existing data dir aside -> $ASIDE"
  mv "$DATA_DIR" "$ASIDE"
fi

mkdir -p "$DATA_DIR"
tar -xzf "$ARTIFACT" -C "$DATA_DIR"

# An online-mode artifact carries `state.db.backup` (a consistent sqlite3 .backup
# snapshot) and deliberately no `state.db`. Promote it, or k3s starts with no datastore.
SNAPSHOT="$DATA_DIR/server/db/state.db.backup"
LIVE_DB="$DATA_DIR/server/db/state.db"
if [ -f "$SNAPSHOT" ] && [ ! -f "$LIVE_DB" ]; then
  echo "k3s-restore: promoting sqlite snapshot -> server/db/state.db"
  # The snapshot is standalone and already checkpointed. Any -wal/-shm here belongs to a
  # different database — from a pre-§B.1 artifact or a failed prior restore — and SQLite
  # would replay it over the snapshot. Clear them (§V.15).
  rm -f "$DATA_DIR/server/db/state.db-wal" "$DATA_DIR/server/db/state.db-shm"
  mv "$SNAPSHOT" "$LIVE_DB"
fi
[ -f "$LIVE_DB" ] || die "restored tree has no server/db/state.db — artifact is not a
     usable k3s datastore backup"

# --- reinstall the pinned version and start ------------------------------------------
if [ "$SKIP_SERVICE" -eq 0 ]; then
  if [ -n "${INSTALL_K3S_VERSION:-}" ]; then
    echo "k3s-restore: reinstalling k3s ${INSTALL_K3S_VERSION}..."
    curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="$INSTALL_K3S_VERSION" sh -
  else
    echo "k3s-restore: INSTALL_K3S_VERSION unset — starting the installed binary as-is" >&2
  fi
  systemctl start "$K3S_SERVICE"
fi

# --- §V.16: prove the API came back at the expected version --------------------------
if [ "$NO_VERIFY" -eq 1 ]; then
  echo "k3s-restore: data restored to $DATA_DIR (verification skipped by request)"
  echo "k3s-restore: §V.16 NOT satisfied — caller must assert the server version" >&2
  exit 0
fi

if [ -z "$VERIFY_CMD" ]; then
  VERIFY_CMD="k3s kubectl version 2>/dev/null || kubectl version"
fi

echo "k3s-restore: verifying server version (§V.16)..."
OUTPUT="$(eval "$VERIFY_CMD" 2>&1 || true)"
if ! printf '%s' "$OUTPUT" | grep -qF -- "$EXPECT_VERSION"; then
  die "§V16 violation: expected server version '$EXPECT_VERSION' after restore, got:
$OUTPUT
     The data may be restored but the cluster is NOT proven recovered."
fi

echo "k3s-restore: OK — API responding at $EXPECT_VERSION, restore proven (§V.16)"
