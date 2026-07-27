#!/usr/bin/env bash
# k3s-backup.sh — consistent backup of the k3s server state (SPEC.md §T.1)
#
# This cluster's datastore is SQLite: the server runs without `--cluster-init`, so there
# is no embedded etcd and therefore no `k3s etcd-snapshot`. k3s also migrates the schema
# one-way on every minor upgrade and does not support downgrade, which makes the artifact
# this script produces the *only* rollback that exists (§V.1).
#
# §V.15 — never tar a live state.db. An in-flight SQLite file copied byte-wise yields a
# torn database that still checksums cleanly, so §V.1 would report "verified" for
# something that cannot restore. Two safe modes instead:
#
#   cold    k3s confirmed stopped, then tar the tree as-is. Guaranteed point-in-time,
#           costs an outage window. Use this before an upgrade hop.
#   online  k3s keeps running; state.db is captured via `sqlite3 .backup` (a consistent
#           snapshot taken through SQLite's own backup API) and the live file is excluded
#           from the archive. Use this for routine/scheduled backups.
#
# §V.21 — the artifact must not be written inside the cluster. `local-path` is the only
# StorageClass here and its volumes live under $K3S_DATA_DIR/storage, so an artifact
# parked there dies with the node it exists to protect.
#
# Usage:
#   k3s-backup.sh --mode cold|online --dest <dir|s3://bucket/prefix> [--dry-run] [--skip-stop]
#
# Runs on the k3s server node (k3s-control-01), not from a workstation.

set -euo pipefail

K3S_DATA_DIR="${K3S_DATA_DIR:-/var/lib/rancher/k3s}"
K3S_SERVICE="${K3S_SERVICE:-k3s}"
# Exits 0 when k3s is still running. Overridable so the guards can be tested off-node.
K3S_STATUS_CMD="${K3S_STATUS_CMD:-systemctl is-active --quiet ${K3S_SERVICE}}"

MODE="cold"
DEST=""
DRY_RUN=0
SKIP_STOP=0

die() { echo "k3s-backup: $*" >&2; exit 1; }

usage() {
  sed -n '2,28p' "$0" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --mode)      MODE="${2:-}"; shift 2 ;;
    --dest)      DEST="${2:-}"; shift 2 ;;
    --dry-run)   DRY_RUN=1; shift ;;
    --skip-stop) SKIP_STOP=1; shift ;;
    -h|--help)   usage ;;
    *)           die "unknown argument: $1" ;;
  esac
done

[ -n "$DEST" ] || die "--dest is required"
case "$MODE" in
  cold|online) ;;
  *) die "--mode must be 'cold' or 'online', got '$MODE'" ;;
esac

# Canonicalise a path that may not exist yet, by resolving its deepest existing parent.
# `readlink -f` is GNU-only; this stays portable to the BSD userland on macOS.
canon() {
  local p="$1" dir suffix=""
  dir="$p"
  while [ ! -d "$dir" ] && [ "$dir" != "/" ] && [ -n "$dir" ]; do
    suffix="/$(basename "$dir")$suffix"
    dir="$(dirname "$dir")"
  done
  ( cd "$dir" 2>/dev/null && printf '%s%s\n' "$(pwd -P)" "$suffix" )
}

# --- §V.21: refuse a destination inside the cluster ---------------------------------
is_s3_dest() { case "$1" in s3://*) return 0 ;; *) return 1 ;; esac; }

if ! is_s3_dest "$DEST"; then
  canon_dest="$(canon "$DEST")"
  canon_data="$(canon "$K3S_DATA_DIR")"
  case "$canon_dest/" in
    "$canon_data"/*)
      die "§V21 violation: destination '$DEST' is inside the k3s data dir ($K3S_DATA_DIR).
     local-path volumes live there, so this artifact would be lost with the node it
     protects. Use an off-cluster path or an s3:// destination."
      ;;
  esac
fi

k3s_running() { eval "$K3S_STATUS_CMD" >/dev/null 2>&1; }

# --- §V.15: cold mode must prove k3s is stopped before archiving state.db -----------
if [ "$MODE" = "cold" ]; then
  if [ "$SKIP_STOP" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
    echo "k3s-backup: stopping ${K3S_SERVICE} for a consistent cold copy..."
    systemctl stop "$K3S_SERVICE"
  fi
  if [ "$DRY_RUN" -eq 0 ] && k3s_running; then
    die "§V15 violation: refusing to archive a live state.db — ${K3S_SERVICE} is still
     running. A byte-wise copy of an in-flight SQLite file is torn but still checksums
     cleanly, which would make §V.1 report a verified backup that cannot restore.
     Stop k3s first, or use --mode online."
  fi
fi

if [ "$MODE" = "online" ]; then
  command -v sqlite3 >/dev/null 2>&1 \
    || die "--mode online needs sqlite3 to take a consistent snapshot; install it or use --mode cold"
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "k3s-backup: dry run OK — mode=$MODE dest=$DEST data=$K3S_DATA_DIR"
  exit 0
fi

[ -d "$K3S_DATA_DIR/server" ] || die "no k3s server dir at $K3S_DATA_DIR/server"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="k3s-backup-$(hostname -s)-${STAMP}.tar.gz"

# s3:// destinations are staged locally first, then uploaded.
if is_s3_dest "$DEST"; then
  STAGE_OUT="$(mktemp -d)"
  OUT_DIR="$STAGE_OUT"
else
  mkdir -p "$DEST"
  OUT_DIR="$DEST"
fi
ARTIFACT="$OUT_DIR/$NAME"

# `storage/` is deliberately excluded: those are local-path PersistentVolumes, not k3s
# state. They are protected separately (CNPG ships to S3 via barman) and including them
# would balloon the artifact this script exists to keep restorable.
#
# `server/kine.sock` is a live unix socket, not state. tar cannot archive a socket and
# warns (and on some versions exits non-zero, which `set -o pipefail` would turn into a
# failed backup). Exclude it explicitly rather than depend on tar's warning behaviour.
case "$MODE" in
  cold)
    tar -czf "$ARTIFACT" --exclude='server/kine.sock' -C "$K3S_DATA_DIR" server
    ;;
  online)
    STAGE="$(mktemp -d)"
    trap 'rm -rf "$STAGE"' EXIT
    # Copy the tree without ever reading the live database. kine runs SQLite in WAL mode,
    # so the datastore is a file *set*: shipping a live -wal/-shm next to the snapshot
    # would have SQLite replay a foreign write-ahead log over it on restore (§B.1).
    tar -cf - \
      --exclude='server/db/state.db' \
      --exclude='server/db/state.db-wal' \
      --exclude='server/db/state.db-shm' \
      --exclude='server/kine.sock' \
      -C "$K3S_DATA_DIR" server | tar -xf - -C "$STAGE"
    mkdir -p "$STAGE/server/db"
    rm -f "$STAGE/server/db/state.db-wal" "$STAGE/server/db/state.db-shm"
    # `.backup` goes through SQLite's own API, checkpointing the WAL into a standalone,
    # self-consistent database file.
    sqlite3 "$K3S_DATA_DIR/server/db/state.db" ".backup '$STAGE/server/db/state.db.backup'"
    tar -czf "$ARTIFACT" -C "$STAGE" server
    ;;
esac

# --- §V.1: the artifact carries its own integrity manifest --------------------------
if command -v sha256sum >/dev/null 2>&1; then
  ( cd "$OUT_DIR" && sha256sum "$NAME" > "$NAME.sha256" )
else
  ( cd "$OUT_DIR" && shasum -a 256 "$NAME" > "$NAME.sha256" )
fi

if is_s3_dest "$DEST"; then
  command -v aws >/dev/null 2>&1 || die "aws CLI not found; cannot ship to $DEST"
  aws s3 cp "$ARTIFACT" "${DEST%/}/$NAME"
  aws s3 cp "$ARTIFACT.sha256" "${DEST%/}/$NAME.sha256"
  rm -rf "$STAGE_OUT"
  echo "k3s-backup: uploaded ${DEST%/}/$NAME"
else
  echo "k3s-backup: wrote $ARTIFACT"
fi

echo "k3s-backup: mode=$MODE — restore with scripts/k3s-restore.sh (§V.16 requires a drill)"
