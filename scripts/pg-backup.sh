#!/usr/bin/env bash
# pg-backup.sh — operator-independent logical backup of the CNPG database (SPEC.md §T.3)
#
# `postgresql/postgresql-cluster` already ships physical backups to
# s3://mysql-backups-asela-cluster/postgresql/ via barman, and those are healthy. This
# script is not a replacement for them. It exists because §T.9 upgrades the CNPG operator
# itself (1.24.1 -> 1.29.x, for CVE-2026-44477), and a barman restore needs a working
# operator to drive it. A plain pg_dump is the only insurance that survives the operator
# upgrade going wrong.
#
# It matters here more than it would elsewhere: the cluster runs a single instance
# (instances=1) on a node-pinned local-path volume, on the same node as the control
# plane. There is no replica to fail over to (§C, §V.14).
#
# §V.21 — the artifact must not be written inside the cluster.
#
# Usage:
#   pg-backup.sh --dest <dir|s3://bucket/prefix> [--namespace NS] [--cluster NAME]
#                [--database DB] [--exec-cmd CMD] [--dry-run]

set -euo pipefail

NAMESPACE="${PG_NAMESPACE:-postgresql}"
CLUSTER="${PG_CLUSTER:-postgresql-cluster}"
CONTAINER="${PG_CONTAINER:-postgres}"
DATABASE=""
ALL_DATABASES=0
DEST=""
EXEC_CMD=""
DRY_RUN=0

die() { echo "pg-backup: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dest)      DEST="${2:-}"; shift 2 ;;
    --namespace) NAMESPACE="${2:-}"; shift 2 ;;
    --cluster)   CLUSTER="${2:-}"; shift 2 ;;
    --database)  DATABASE="${2:-}"; shift 2 ;;
    --all)       ALL_DATABASES=1; shift ;;
    --container) CONTAINER="${2:-}"; shift 2 ;;
    --exec-cmd)  EXEC_CMD="${2:-}"; shift 2 ;;
    --dry-run)   DRY_RUN=1; shift ;;
    -h|--help)   sed -n '2,20p' "$0" >&2; exit 2 ;;
    *)           die "unknown argument: $1" ;;
  esac
done

[ -n "$DEST" ] || die "--dest is required"
if [ "$ALL_DATABASES" -eq 0 ] && [ -z "$DATABASE" ]; then
  die "--database <name> or --all is required. There is no safe default: this cluster
     hosts several databases (n8n, chores_tracker), so guessing one would silently
     under-protect the rest."
fi
[ "$ALL_DATABASES" -eq 1 ] && [ -n "$DATABASE" ] && die "--all and --database are mutually exclusive"

is_s3_dest() { case "$1" in s3://*) return 0 ;; *) return 1 ;; esac; }

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
# Checked against both the literal argument and its canonical form: the caller may pass
# a relative or symlinked path, and macOS canonicalises /var to /private/var, which would
# otherwise slip past a naive prefix match.
in_cluster_storage() {
  local p="${1#/private}"
  case "$p/" in
    /var/lib/rancher/k3s/*|/var/lib/kubelet/*) return 0 ;;
  esac
  return 1
}

if ! is_s3_dest "$DEST"; then
  if in_cluster_storage "$DEST" || in_cluster_storage "$(canon "$DEST")"; then
    die "§V21 violation: '$DEST' is inside cluster storage. local-path volumes are
     pinned to the node that also hosts this database, so the artifact would be lost in
     exactly the failure it is meant to cover. Use an off-cluster path or s3://."
  fi
fi

# Default: exec into the CNPG primary. Overridable so the round-trip can be proven
# against a disposable container without a cluster.
if [ -z "$EXEC_CMD" ]; then
  command -v kubectl >/dev/null 2>&1 || die "kubectl not found and no --exec-cmd given"
  if [ "$DRY_RUN" -eq 0 ]; then
    PRIMARY="$(kubectl get pods -n "$NAMESPACE" \
      -l "cnpg.io/cluster=$CLUSTER,cnpg.io/instanceRole=primary" \
      -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
    [ -n "$PRIMARY" ] || die "could not resolve the primary pod for cluster '$CLUSTER' in '$NAMESPACE'"
    # -c pins the container so kubectl's "Defaulted container" notice stays off the wire.
    EXEC_CMD="kubectl exec -n $NAMESPACE $PRIMARY -c $CONTAINER --"
  else
    EXEC_CMD="kubectl exec -n $NAMESPACE <primary> -c $CONTAINER --"
  fi
fi

LABEL="${DATABASE:-all}"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "pg-backup: dry run OK — db=$LABEL dest=$DEST exec='$EXEC_CMD'"
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="pg-${CLUSTER}-${LABEL}-${STAMP}.sql.gz"

if is_s3_dest "$DEST"; then
  STAGE_OUT="$(mktemp -d)"
  OUT_DIR="$STAGE_OUT"
else
  mkdir -p "$DEST"
  OUT_DIR="$DEST"
fi
ARTIFACT="$OUT_DIR/$NAME"

# Plain-format SQL, not custom: restoring must not depend on a matching pg_restore
# build, which is the sort of assumption that fails precisely during an upgrade.
#
# --all uses pg_dumpall so the artifact also carries globals (roles, grants). Restoring
# databases without their roles leaves you with data nobody can log in to read.
echo "pg-backup: dumping '$LABEL' ..."
if [ "$ALL_DATABASES" -eq 1 ]; then
  # shellcheck disable=SC2086
  $EXEC_CMD pg_dumpall -U postgres --clean | gzip -c > "$ARTIFACT"
else
  # shellcheck disable=SC2086
  $EXEC_CMD pg_dump -U postgres --clean --if-exists -d "$DATABASE" | gzip -c > "$ARTIFACT"
fi

[ -s "$ARTIFACT" ] || die "dump produced an empty artifact — refusing to record it as a backup"

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
  echo "pg-backup: uploaded ${DEST%/}/$NAME"
else
  echo "pg-backup: wrote $ARTIFACT"
fi

if [ "$ALL_DATABASES" -eq 1 ]; then
  echo "pg-backup: restore with: gunzip -c <artifact> | psql -U postgres"
else
  echo "pg-backup: restore with: gunzip -c <artifact> | psql -U postgres -d $DATABASE"
fi
