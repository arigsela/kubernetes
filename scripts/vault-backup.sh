#!/usr/bin/env bash
# vault-backup.sh — backup of Vault's `file` storage backend (SPEC.md §T.35)
#
# `vault-0` stores at /vault/data on a 1Gi local-path volume pinned to k3s-control-01,
# with replicas=1. It holds the credentials every one of the 45 namespaces resolves
# through ESO. Until this script existed there was no backup of it at all (§V.28).
#
# The constraint that shapes the design: Vault's file backend has NO consistent-snapshot
# API. There is no `vault operator raft snapshot` equivalent — the documented method is to
# stop Vault and copy the directory. Copying it live can catch a partial write, which is
# exactly §B.1: an artifact that checksums cleanly and cannot restore. So:
#
#   cold (default)  scale the StatefulSet to 0, copy at rest, scale back, verify unseal.
#   online          requires --allow-inconsistent AND brands the artifact filename
#                   INCONSISTENT, so a torn copy can never be mistaken for a good one.
#
# ⚠ KMS: `vault-0` seals with awskms (alias/vault-auto-unseal). The bytes on disk are
# ciphertext. This artifact is undecryptable without that KMS key — the Shamir recovery
# keys govern recovery operations, not storage decryption. Back up the key policy too.
#
# Usage:
#   vault-backup.sh --dest <dir|s3://…> [--mode cold|online] [--allow-inconsistent]
#                   [--data-dir PATH] [--namespace NS] [--statefulset NAME] [--dry-run]
#
#   --data-dir  copy from an already-at-rest local directory instead of driving kubectl.
#               Used by the drill and by tests. The caller owns quiescence.

set -euo pipefail

NAMESPACE="${VAULT_NAMESPACE:-vault}"
STATEFULSET="${VAULT_STATEFULSET:-vault}"
POD="${VAULT_POD:-vault-0}"
DATA_PATH="${VAULT_DATA_PATH:-/vault/data}"
MODE="cold"
DEST=""
DATA_DIR=""
ARGO_APP="${VAULT_ARGO_APP:-}"
ARGO_NS="${ARGO_NAMESPACE:-argo-cd}"
ALLOW_INCONSISTENT=0
DRY_RUN=0

die() { echo "vault-backup: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dest)               DEST="${2:-}"; shift 2 ;;
    --mode)               MODE="${2:-}"; shift 2 ;;
    --data-dir)           DATA_DIR="${2:-}"; shift 2 ;;
    --namespace)          NAMESPACE="${2:-}"; shift 2 ;;
    --statefulset)        STATEFULSET="${2:-}"; shift 2 ;;
    --argo-app)           ARGO_APP="${2:-}"; shift 2 ;;
    --argo-namespace)     ARGO_NS="${2:-}"; shift 2 ;;
    --allow-inconsistent) ALLOW_INCONSISTENT=1; shift ;;
    --dry-run)            DRY_RUN=1; shift ;;
    -h|--help)            sed -n '2,28p' "$0" >&2; exit 2 ;;
    *)                    die "unknown argument: $1" ;;
  esac
done

[ -n "$DEST" ] || die "--dest is required"
case "$MODE" in cold|online) ;; *) die "--mode must be 'cold' or 'online', got '$MODE'" ;; esac

canon() {
  local p="$1" dir suffix=""
  dir="$p"
  while [ ! -d "$dir" ] && [ "$dir" != "/" ] && [ -n "$dir" ]; do
    suffix="/$(basename "$dir")$suffix"
    dir="$(dirname "$dir")"
  done
  ( cd "$dir" 2>/dev/null && printf '%s%s\n' "$(pwd -P)" "$suffix" )
}

is_s3_dest() { case "$1" in s3://*) return 0 ;; *) return 1 ;; esac; }

# --- §V.21: refuse a destination inside the cluster ---------------------------------
in_cluster_storage() {
  local p="${1#/private}"
  case "$p/" in /var/lib/rancher/k3s/*|/var/lib/kubelet/*) return 0 ;; esac
  return 1
}

if ! is_s3_dest "$DEST"; then
  if in_cluster_storage "$DEST" || in_cluster_storage "$(canon "$DEST")"; then
    die "§V21 violation: '$DEST' is inside cluster storage. local-path volumes are pinned
     to the node hosting Vault itself, so this artifact would be lost in the exact failure
     it exists to cover. Use an off-cluster path or s3://."
  fi
fi

# --- §B.1 lesson: an inconsistent copy must be opt-in and self-identifying -----------
if [ "$MODE" = "online" ] && [ "$ALLOW_INCONSISTENT" -eq 0 ]; then
  die "refusing --mode online without --allow-inconsistent.
     Vault's file backend has no consistent-snapshot API, so copying /vault/data while the
     server runs can capture a partially-written file. The result checksums cleanly and
     may not restore (see §B.1). Use --mode cold, or pass --allow-inconsistent if you
     accept a possibly-torn artifact."
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "vault-backup: dry run OK — mode=$MODE dest=$DEST src=${DATA_DIR:-$NAMESPACE/$POD}"
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
if [ "$MODE" = "online" ]; then
  NAME="vault-backup-${STAMP}-INCONSISTENT.tar.gz"
else
  NAME="vault-backup-${STAMP}.tar.gz"
fi

if is_s3_dest "$DEST"; then
  STAGE_OUT="$(mktemp -d)"; OUT_DIR="$STAGE_OUT"
else
  mkdir -p "$DEST"; OUT_DIR="$DEST"
fi
ARTIFACT="$OUT_DIR/$NAME"

if [ -n "$DATA_DIR" ]; then
  # Local path already at rest. The caller owns quiescence — the drill stops the
  # container first, and the kubectl path below scales the StatefulSet down.
  [ -d "$DATA_DIR" ] || die "--data-dir '$DATA_DIR' is not a directory"
  tar -czf "$ARTIFACT" -C "$DATA_DIR" .
else
  command -v kubectl >/dev/null 2>&1 || die "kubectl not found and no --data-dir given"
  if [ "$MODE" = "cold" ]; then
    # §V.33 / §B.2 — this StatefulSet is GitOps-managed with `replicas: 1` in git and
    # selfHeal: true. Scaling to 0 without suspending the Argo app first lets Argo
    # rescale Vault mid-copy, producing a torn artifact that this script would then
    # brand as consistent. Refuse rather than race.
    if [ -z "$ARGO_APP" ]; then
      SELFHEAL="$(kubectl get app -n "$ARGO_NS" -o jsonpath="{range .items[*]}{.metadata.name}{' '}{.spec.syncPolicy.automated.selfHeal}{'\n'}{end}" 2>/dev/null \
        | awk '$2=="true"{print $1}' | grep -x "$STATEFULSET" || true)"
      [ -z "$SELFHEAL" ] || die "§V33: Argo app '$SELFHEAL' manages this StatefulSet with
     selfHeal: true, so a scale-to-0 will be reverted mid-copy and the artifact silently
     torn (§B.2). Re-run with --argo-app $SELFHEAL so the app is suspended first."
    else
      echo "vault-backup: suspending Argo app $ARGO_APP (§V.33)..."
      kubectl patch app -n "$ARGO_NS" "$ARGO_APP" --type merge \
        -p '{"spec":{"syncPolicy":{"automated":null}}}'
      # On ANY exit, bring Vault back explicitly and then restore auto-sync. Relying on
      # Argo's self-heal alone would work but leaves Vault down for a reconcile interval;
      # scaling directly shortens the window and does not depend on Argo being healthy.
      # shellcheck disable=SC2064
      trap "echo 'vault-backup: restoring Vault + Argo auto-sync'; kubectl scale sts -n $NAMESPACE $STATEFULSET --replicas=1 >/dev/null 2>&1 || true; kubectl patch app -n $ARGO_NS $ARGO_APP --type merge -p '{\"spec\":{\"syncPolicy\":{\"automated\":{\"prune\":true,\"selfHeal\":true}}}}' >/dev/null 2>&1 || true" EXIT
    fi

    echo "vault-backup: scaling $NAMESPACE/$STATEFULSET to 0 for a consistent copy..."
    kubectl scale sts -n "$NAMESPACE" "$STATEFULSET" --replicas=0
    kubectl wait --for=delete "pod/$POD" -n "$NAMESPACE" --timeout=180s || true
    for _ in $(seq 1 60); do
      kubectl get pod -n "$NAMESPACE" "$POD" >/dev/null 2>&1 || break
      sleep 2
    done
    kubectl get pod -n "$NAMESPACE" "$POD" >/dev/null 2>&1 \
      && die "pod $POD still present after scale-down; refusing to copy a live data dir"

    # Helper pod mounts the same PVC. It must land on the node the local-path volume is
    # pinned to, which scheduling handles via the PVC's own node affinity.
    PVC="$(kubectl get pvc -n "$NAMESPACE" -o jsonpath='{.items[0].metadata.name}')"
    echo "vault-backup: copying $DATA_PATH from PVC $PVC via helper pod..."
    # §V.34 / §B.3 — the PV is pinned to k3s-control-01, which carries
    # node-role.kubernetes.io/control-plane:NoSchedule, and the two workers do not match
    # the PV's node affinity. Without the toleration this pod is Pending forever and the
    # backup aborts. Resource limits satisfy kyverno's require-resource-limits (Audit).
    kubectl run vault-backup-helper -n "$NAMESPACE" --restart=Never --quiet \
      --image=alpine:3.20 \
      --overrides="{\"spec\":{\"tolerations\":[{\"key\":\"node-role.kubernetes.io/control-plane\",\"operator\":\"Exists\",\"effect\":\"NoSchedule\"}],\"containers\":[{\"name\":\"helper\",\"image\":\"alpine:3.20\",\"command\":[\"sleep\",\"600\"],\"resources\":{\"requests\":{\"cpu\":\"50m\",\"memory\":\"64Mi\"},\"limits\":{\"cpu\":\"500m\",\"memory\":\"256Mi\"}},\"volumeMounts\":[{\"name\":\"d\",\"mountPath\":\"$DATA_PATH\"}]}],\"volumes\":[{\"name\":\"d\",\"persistentVolumeClaim\":{\"claimName\":\"$PVC\"}}]}}" \
      >/dev/null
    if ! kubectl wait --for=condition=Ready pod/vault-backup-helper -n "$NAMESPACE" --timeout=180s; then
      kubectl describe pod -n "$NAMESPACE" vault-backup-helper 2>&1 | sed -n '/Events:/,$p' >&2
      kubectl delete pod -n "$NAMESPACE" vault-backup-helper --wait=false >/dev/null 2>&1 || true
      die "helper pod never became Ready — see events above (§V.34)"
    fi
    kubectl exec -n "$NAMESPACE" vault-backup-helper -- tar -czf - -C "$DATA_PATH" . > "$ARTIFACT"
    kubectl delete pod -n "$NAMESPACE" vault-backup-helper --wait=false >/dev/null

    echo "vault-backup: scaling $NAMESPACE/$STATEFULSET back to 1..."
    kubectl scale sts -n "$NAMESPACE" "$STATEFULSET" --replicas=1
    kubectl wait --for=condition=Ready "pod/$POD" -n "$NAMESPACE" --timeout=300s
    # KMS auto-unseal should complete within seconds; prove it rather than assume.
    for _ in $(seq 1 30); do
      kubectl exec -n "$NAMESPACE" "$POD" -- vault status 2>/dev/null | grep -q "Sealed .*false" && break
      sleep 2
    done
    kubectl exec -n "$NAMESPACE" "$POD" -- vault status 2>/dev/null | grep -q "Sealed .*false" \
      || die "Vault did not auto-unseal after restart — check the vault-kms-credentials
     Secret and KMS reachability (base-apps/vault/runbook.md)"
  else
    echo "vault-backup: ⚠ online copy — artifact may be torn, branded INCONSISTENT"
    kubectl exec -n "$NAMESPACE" "$POD" -- tar -czf - -C "$DATA_PATH" . > "$ARTIFACT"
  fi
fi

[ -s "$ARTIFACT" ] || die "produced an empty artifact — refusing to record it as a backup"

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
  echo "vault-backup: uploaded ${DEST%/}/$NAME"
else
  echo "vault-backup: wrote $ARTIFACT"
fi

echo "vault-backup: ⚠ ciphertext — undecryptable without KMS key alias/vault-auto-unseal"
echo "vault-backup: restore with scripts/vault-restore.sh (§V.28 requires a proven drill)"
