#!/usr/bin/env bash
# beacon — Vault provisioning (beacon §T.40). Idempotent; safe to re-run. App repo:
# arigsela/beacon (private); its manifests are base-apps/beacon/ and base-apps/postgresql/.
#   k8s-secrets/beacon      signing-key  base64 of 32 random bytes: the AES-GCM key that
#                                        seals webhook channel secrets in the DB (beacon §V.4)
#                           smtp-url     optional (--smtp-url); without it email deliveries
#                                        dead-letter
#   k8s-secrets/beacon-db   owner-user, owner-password  beacon_owner: runs the migrations
#                           app-user, app-password      beacon_app: every service at runtime
#                           db-name                     beacon
#   policy beacon / beacon-db    each reads only its one path
#   role   beacon                kubernetes auth: SA eso-beacon    in beacon,            ttl 1h
#   role   beacon-db             kubernetes auth: SA eso-beacon-db in beacon,postgresql, ttl 1h
#
# The two-role split mirrors donetick: the postgresql namespace reads the DB passwords (its
# init Job sets them on the roles) and must not be able to read the signing key.
#
# Generated values are written to Vault and never printed. Passwords are hex, so they are
# safe inside a DATABASE_URL and a SQL literal without escaping.
#
#   export VAULT_ADDR=https://vault.arigsela.com   # or a port-forward, http://127.0.0.1:8200
#   vault login -method=oidc                       # Dex; or export VAULT_TOKEN=<token>
#   scripts/provision-beacon-vault.sh [--smtp-url smtp://user:pass@host:587] [--rotate-db]
#
# Run it BEFORE merging the beacon §T.15 PR: a SecretStore whose Vault role does not exist
# fails the whole app sync, and the postgresql app's beacon init hook would block every
# postgresql sync.
#
# --rotate-db regenerates both DB passwords. The next postgresql sync's init hook sets them on
# the roles; until the beacon pods restart (after ESO refreshes, <= 1h, or force-sync) they
# hold the old ones and fail to connect. There is deliberately no option to rotate
# signing-key: every stored webhook secret is sealed with it, so a new key makes them all
# unreadable. Rotating it is a re-encryption job in beacon, not a Vault write.
set -euo pipefail
MOUNT=k8s-secrets
APP_KEY=beacon
DB_KEY=beacon-db
smtp_url=""; rotate_db=0
while (($#)); do
  case "$1" in
    --smtp-url) smtp_url="$2"; shift 2 ;;
    --rotate-db) rotate_db=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
: "${VAULT_ADDR:?set VAULT_ADDR (https://vault.arigsela.com, or a port-forward on http://127.0.0.1:8200)}"
if ! vault token lookup >/dev/null 2>&1; then
  echo "not logged in to Vault: run 'vault login -method=oidc' or export VAULT_TOKEN" >&2
  exit 2
fi
if [[ -n "$smtp_url" ]] && [[ "$smtp_url" != smtp://* && "$smtp_url" != smtps://* ]]; then
  echo "--smtp-url must start with smtp:// or smtps://" >&2; exit 2
fi

# Prints the key's property NAMES, one per line (nothing when absent). Values never leave
# the pipe into python's stdin: not argv, not a variable. Exits on anything other than "not
# found", so a transient error can never look like "absent" and trigger generation.
current() {
  local out
  if out=$(vault kv get -mount="$MOUNT" -format=json "$1" 2>&1); then
    python3 -c 'import json,sys; print("\n".join(sorted(json.load(sys.stdin)["data"]["data"])))' <<<"$out"
  elif grep -qi "no value found\|not found" <<<"$out"; then
    :
  else
    echo "cannot read $MOUNT/$1 (not a 'not found'): ${out%%$'\n'*}" >&2; exit 2
  fi
}
has() { grep -qx -- "$2" <<<"$1"; }

write() {  # write <key> <existing-names> <k=v>...; patch when the key exists, put otherwise
  local key=$1 existing=$2; shift 2
  (($#)) || return 0
  if [[ -z "$existing" ]]; then
    vault kv put -mount="$MOUNT" "$key" "$@" >/dev/null
  else
    vault kv patch -mount="$MOUNT" "$key" "$@" >/dev/null
  fi
}

app=$(current "$APP_KEY")
args=()
has "$app" signing-key || args+=("signing-key=$(openssl rand -base64 32 | tr -d '\n')")
[[ -n "$smtp_url" ]] && args+=("smtp-url=$smtp_url")
write "$APP_KEY" "$app" "${args[@]+"${args[@]}"}"

db=$(current "$DB_KEY")
args=()
has "$db" owner-user || args+=("owner-user=beacon_owner")
has "$db" app-user || args+=("app-user=beacon_app")
has "$db" db-name || args+=("db-name=beacon")
if ((rotate_db)) || ! has "$db" owner-password; then args+=("owner-password=$(openssl rand -hex 24)"); fi
if ((rotate_db)) || ! has "$db" app-password; then args+=("app-password=$(openssl rand -hex 24)"); fi
write "$DB_KEY" "$db" "${args[@]+"${args[@]}"}"

vault policy write beacon - >/dev/null <<POLICY
path "$MOUNT/data/$APP_KEY" { capabilities = ["read"] }
POLICY
vault policy write beacon-db - >/dev/null <<POLICY
path "$MOUNT/data/$DB_KEY" { capabilities = ["read"] }
POLICY
vault write auth/kubernetes/role/beacon \
  bound_service_account_names=eso-beacon \
  bound_service_account_namespaces=beacon \
  policies=beacon ttl=1h >/dev/null
vault write auth/kubernetes/role/beacon-db \
  bound_service_account_names=eso-beacon-db \
  bound_service_account_namespaces=beacon,postgresql \
  policies=beacon-db ttl=1h >/dev/null

echo "beacon: policies and roles written; DB passwords $( ((rotate_db)) && echo rotated || echo "kept (generated if missing)" )"
echo "  $MOUNT/$APP_KEY: $(current "$APP_KEY" | paste -sd, -)"
echo "  $MOUNT/$DB_KEY: $(current "$DB_KEY" | paste -sd, -)"
