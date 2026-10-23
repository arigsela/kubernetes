#!/usr/bin/env bash
# Model gateway — Vault provisioning (idempotent, safe to re-run). §T.137, spec
# docs/superpowers/specs/2026-10-23-model-gateway-budgets-design.md §3.3.
#   k8s-secrets/model-gateway-keys   one property per budget group (scripts/model_budgets.py
#                                    --groups): the group's virtual key, 32 random bytes as hex.
#                                    Substrate's egress injects it into the group's model calls;
#                                    the gateway maps it to the group's budget. Written only when
#                                    missing; --rotate <group> replaces one (its day's count
#                                    starts again at zero).
#   k8s-secrets/model-gateway-db     password: the Postgres login `model_gateway`
#   policy/role model-gateway        reads kagent-anthropic (the real key), model-gateway-keys and
#                                    model-gateway-db; SA eso-model-gateway in model-gateway
#   policy/role model-gateway-keys   reads model-gateway-keys; SA eso-model-gateway-keys in kagent
#   policy/role model-gateway-db     reads model-gateway-db; SA eso-model-gateway-db in postgresql
#
# No value passes on a command line or reaches the terminal: each is written to a 0600 file in
# a private temp dir and handed to vault as key=@file. The real Anthropic key is not touched:
# the gateway reads the same k8s-secrets/kagent-anthropic entry kagent reads today.
#   kubectl port-forward -n vault svc/vault 8200:8200 &
#   export VAULT_ADDR=http://127.0.0.1:8200
#   vault login -method=oidc            # Dex; or export VAULT_TOKEN=<token>
#   scripts/provision-model-gateway-vault.sh [--dry-run] [--rotate <group>]
# Run it before merging the PR that adds the model-gateway app (its ExternalSecrets read these).
set -euo pipefail
MOUNT=k8s-secrets
KEYS=model-gateway-keys
DB=model-gateway-db
REAL=kagent-anthropic
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
dry=0
rotate=""
while (($#)); do
  case "$1" in
    --dry-run) dry=1; shift ;;
    --rotate) rotate="${2:-}"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
: "${VAULT_ADDR:?set VAULT_ADDR (kubectl port-forward -n vault svc/vault 8200:8200; http://127.0.0.1:8200)}"
if ! vault token lookup >/dev/null 2>&1; then
  echo "not logged in to Vault: run 'vault login -method=oidc' or export VAULT_TOKEN" >&2
  exit 2
fi

groups=$(python3 "$here/model_budgets.py" --groups)
if [[ -n "$rotate" ]] && ! grep -qx -- "$rotate" <<<"$groups"; then
  echo "--rotate $rotate: no such group in base-apps/model-gateway/budgets.yaml" >&2
  exit 2
fi

tmp=$(mktemp -d)
chmod 700 "$tmp"
trap 'rm -rf "$tmp"' EXIT
umask 077

# The property names present at a path ("" when the path does not exist).
present() {
  vault kv get -mount="$MOUNT" -format=json "$1" 2>/dev/null \
    | python3 -c 'import json,sys; print("\n".join(sorted(json.load(sys.stdin)["data"]["data"])))' 2>/dev/null || true
}

# Write the listed properties (name=@file ...) to a path: patch when it exists, put otherwise.
store() {
  local path=$1; shift
  if vault kv get -mount="$MOUNT" "$path" >/dev/null 2>&1; then
    vault kv patch -mount="$MOUNT" "$path" "$@" >/dev/null
  else
    vault kv put -mount="$MOUNT" "$path" "$@" >/dev/null
  fi
}

have_keys=$(present "$KEYS")
args=()
written=()
for g in $groups; do
  if [[ "$g" == "$rotate" ]] || ! grep -qx -- "$g" <<<"$have_keys"; then
    printf '%s' "$(openssl rand -hex 32)" >"$tmp/$g"
    args+=("$g=@$tmp/$g")
    written+=("$g")
  fi
done
db_args=()
if ! grep -qx password <<<"$(present "$DB")"; then
  printf '%s' "$(openssl rand -hex 24)" >"$tmp/db-password"
  db_args+=("password=@$tmp/db-password")
fi

if ((dry)); then
  echo "would write $MOUNT/$KEYS: ${written[*]:-nothing}"
  echo "would write $MOUNT/$DB password: $([[ ${#db_args[@]} -gt 0 ]] && echo yes || echo no)"
  echo "would write policies and roles: model-gateway, model-gateway-keys, model-gateway-db"
  exit 0
fi

((${#args[@]})) && store "$KEYS" "${args[@]}"
((${#db_args[@]})) && store "$DB" "${db_args[@]}"

vault policy write model-gateway - <<POLICY
path "$MOUNT/data/$REAL" { capabilities = ["read"] }
path "$MOUNT/data/$KEYS" { capabilities = ["read"] }
path "$MOUNT/data/$DB" { capabilities = ["read"] }
POLICY
vault policy write model-gateway-keys - <<POLICY
path "$MOUNT/data/$KEYS" { capabilities = ["read"] }
POLICY
vault policy write model-gateway-db - <<POLICY
path "$MOUNT/data/$DB" { capabilities = ["read"] }
POLICY
vault write auth/kubernetes/role/model-gateway \
  bound_service_account_names=eso-model-gateway \
  bound_service_account_namespaces=model-gateway \
  policies=model-gateway ttl=1h >/dev/null
vault write auth/kubernetes/role/model-gateway-keys \
  bound_service_account_names=eso-model-gateway-keys \
  bound_service_account_namespaces=kagent \
  policies=model-gateway-keys ttl=1h >/dev/null
vault write auth/kubernetes/role/model-gateway-db \
  bound_service_account_names=eso-model-gateway-db \
  bound_service_account_namespaces=postgresql \
  policies=model-gateway-db ttl=1h >/dev/null

echo "model-gateway: policies and roles written; keys written this run: ${written[*]:-none};" \
  "db password written: $([[ ${#db_args[@]} -gt 0 ]] && echo yes || echo no)"
echo "groups with a key now: $(present "$KEYS" | tr '\n' ' ')"
