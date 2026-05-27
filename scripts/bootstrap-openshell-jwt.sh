#!/usr/bin/env bash
#
# bootstrap-openshell-jwt.sh — one-time provisioning of the openshell-jwt-keys
# secret material into Vault. Idempotent by default; pass --rotate to regenerate.
#
# Spec: docs/superpowers/specs/2026-05-27-openshell-jwt-keys-fix-design.md

set -euo pipefail

VAULT_PATH="k8s-secrets/openshell/jwt"
VAULT_POLICY="openshell-read"
VAULT_ROLE="openshell"
K8S_NAMESPACE="openshell"
K8S_SA="default"

ROTATE=false
DRY_RUN=false
ALLOW_ROOT=false

usage() {
  cat <<EOF
Usage: $0 [--rotate] [--dry-run] [--allow-root]

  --rotate       Regenerate keypair even if Vault already has one.
                 Existing minted tokens fail validation on TTL expiry.
  --dry-run      Print every vault/kubectl command without executing.
                 Note: precondition checks and idempotency probe still
                 require an active VAULT_ADDR and valid token.
  --allow-root   Permit running with a Vault root token (off by default).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rotate)      ROTATE=true; shift ;;
    --dry-run)     DRY_RUN=true; shift ;;
    --allow-root)  ALLOW_ROOT=true; shift ;;
    -h|--help)     usage; exit 0 ;;
    *) echo "unknown flag: $1" >&2; usage; exit 2 ;;
  esac
done

log() { echo "[bootstrap-openshell-jwt] $*"; }
die() { echo "[bootstrap-openshell-jwt] ERROR: $*" >&2; exit 1; }

run() {
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "DRY-RUN: $*"
  else
    "$@"
  fi
}

preconditions() {
  for bin in vault openssl jq kubectl; do
    command -v "$bin" >/dev/null 2>&1 || die "$bin not found on PATH"
  done
  [[ -n "${VAULT_ADDR:-}" ]] || die "VAULT_ADDR not set"
  local token_info
  token_info=$(vault token lookup -format=json 2>/dev/null) \
    || die "vault token lookup failed (no active token?)"
  if [[ "$ALLOW_ROOT" != "true" ]]; then
    if jq -e '.data.policies | index("root")' <<<"$token_info" >/dev/null 2>&1; then
      die "refusing to run with a root token; pass --allow-root to override"
    fi
  fi
  log "preconditions OK"
}

bootstrap_vault_auth() {
  if ! vault policy read "$VAULT_POLICY" >/dev/null 2>&1; then
    log "creating Vault policy $VAULT_POLICY"
    local policy_hcl
    policy_hcl=$(cat <<EOF
# Policy uses the raw KV v2 API path (k8s-secrets/data/...). VAULT_PATH
# above uses the KV-aware CLI path. If VAULT_PATH changes, update here too.
path "k8s-secrets/data/openshell/*" {
  capabilities = ["read"]
}
EOF
)
    if [[ "$DRY_RUN" == "true" ]]; then
      echo "DRY-RUN: vault policy write $VAULT_POLICY <<EOF"
      echo "$policy_hcl"
      echo "EOF"
    else
      echo "$policy_hcl" | vault policy write "$VAULT_POLICY" -
    fi
  else
    log "Vault policy $VAULT_POLICY already exists"
  fi

  if ! vault read "auth/kubernetes/role/$VAULT_ROLE" >/dev/null 2>&1; then
    log "creating Vault Kubernetes auth role $VAULT_ROLE"
    run vault write "auth/kubernetes/role/$VAULT_ROLE" \
      "bound_service_account_names=$K8S_SA" \
      "bound_service_account_namespaces=$K8S_NAMESPACE" \
      "policies=$VAULT_POLICY" \
      "ttl=24h"
  else
    log "Vault role $VAULT_ROLE already exists"
  fi
}

secret_exists() {
  vault kv get "$VAULT_PATH" >/dev/null 2>&1
}

generate_keypair() {
  local outdir="$1"
  openssl genpkey -algorithm ED25519 -out "$outdir/signing.pem" \
    || die "openssl genpkey -algorithm ED25519 failed — is Ed25519 supported? (OpenSSL >= 1.1.1 required)"
  openssl pkey -in "$outdir/signing.pem" -pubout -out "$outdir/public.pem" 2>/dev/null
  # kid = first 16 chars of base64url(sha256(SPKI DER))
  openssl pkey -pubin -in "$outdir/public.pem" -outform der \
    | openssl dgst -sha256 -binary \
    | base64 \
    | tr '+/' '-_' \
    | tr -d '=' \
    | cut -c1-16 > "$outdir/kid"
}

write_vault_secret() {
  local outdir="$1"
  local kid
  kid=$(cat "$outdir/kid")
  run vault kv put "$VAULT_PATH" \
    "signing_pem=@$outdir/signing.pem" \
    "public_pem=@$outdir/public.pem" \
    "kid=$kid"
  [[ "$DRY_RUN" == "true" ]] || log "wrote Vault secret at $VAULT_PATH (kid=$kid)"
}

main() {
  preconditions
  bootstrap_vault_auth

  if secret_exists && [[ "$ROTATE" != "true" ]]; then
    log "already bootstrapped at $VAULT_PATH — pass --rotate to regenerate"
    exit 0
  fi

  local tmp
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT

  generate_keypair "$tmp"
  write_vault_secret "$tmp"

  if [[ "$ROTATE" == "true" ]]; then
    log "ROTATED. Tokens minted under the previous kid will fail validation on TTL expiry."
    log "To pick up the new kid immediately: kubectl delete pod -n openshell openshell-0"
  fi

  if [[ "$DRY_RUN" == "true" ]]; then
    echo
    echo "Dry-run complete (no changes made)."
  else
    echo
    echo "Bootstrap complete."
  fi
  cat <<EOF

  kid: $(cat "$tmp/kid")

To force ESO sync immediately rather than waiting on refreshInterval:
  kubectl annotate externalsecret/openshell-jwt-keys -n openshell \\
    force-sync=\$(date +%s) --overwrite

Public key (safe to share):
$(cat "$tmp/public.pem")
EOF
}

main "$@"
