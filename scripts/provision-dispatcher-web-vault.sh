#!/usr/bin/env bash
# Dispatcher approvals page — Vault provisioning for its oauth2-proxy sidecar (§T.119).
# Idempotent; re-run with --rotate to regenerate both secrets, --rotate-cookie for the cookie only.
#   k8s-secrets/dispatcher-web   oauth2-client-secret, oauth2-cookie-secret (both generated HERE,
#                                never printed), allowed-emails (the one address the page admits)
#   k8s-secrets/dex              dispatcher-client-secret (the same client secret; Dex's side)
#   policy dispatcher-web        read k8s-secrets/data/dispatcher-web
#   role   dispatcher-web        kubernetes auth: SA eso-dispatcher-web in dispatcher, ttl 1h
#
#   kubectl port-forward -n vault svc/vault 8200:8200 &
#   export VAULT_ADDR=http://127.0.0.1:8200
#   vault login -method=oidc            # Dex; or export VAULT_TOKEN=<token>
#   scripts/provision-dispatcher-web-vault.sh --email you@example.com
# Run it BEFORE merging base-apps/dispatcher/secret-store.yaml: a SecretStore whose Vault role
# does not exist fails the whole dispatcher app sync.
# After a rotation BOTH pods must restart to read the new values (Dex reads its env, the
# sidecar its files, at start only): `kubectl rollout restart deploy/dex -n dex` and
# `kubectl delete pod -n dispatcher -l app=dispatcher` once ESO has refreshed (≤1h, or annotate
# the ExternalSecrets with force-sync). A dispatcher restart empties the pending list.
set -euo pipefail
MOUNT=k8s-secrets
KEY=dispatcher-web
email=""; rotate=0; rotate_cookie=0
while (($#)); do
  case "$1" in
    --email) email="$2"; shift 2 ;;
    --rotate) rotate=1; shift ;;
    --rotate-cookie) rotate_cookie=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
: "${VAULT_ADDR:?set VAULT_ADDR (kubectl port-forward -n vault svc/vault 8200:8200; http://127.0.0.1:8200)}"
if ! vault token lookup >/dev/null 2>&1; then
  echo "not logged in to Vault: run 'vault login -method=oidc' or export VAULT_TOKEN" >&2
  exit 2
fi
exists=0
if out=$(vault kv get -mount="$MOUNT" "$KEY" 2>&1); then
  exists=1
elif ! grep -qi "no value found\|not found" <<<"$out"; then
  echo "cannot read $MOUNT/$KEY (not a 'not found'): $out" >&2; exit 2   # never rotate on a transient error
fi
if ((exists == 0)) && [[ -z "$email" ]]; then
  echo "--email is required the first time" >&2; exit 2
fi
if [[ -n "$email" ]] && [[ "$email" != *@*.* ]]; then
  echo "--email does not look like an address" >&2; exit 2
fi
args=()
[[ -n "$email" ]] && args+=("allowed-emails=$email")
# oauth2-proxy reads --cookie-secret-file as RAW bytes and needs exactly 16, 24 or 32 of them
# (AES key): 32 hex characters = 32 bytes. base64 of 32 bytes is 44 characters and is refused
# ("cookie_secret from file must be 16, 24, or 32 bytes", seen 2026-10-14).
if ((exists == 0 || rotate == 1 || rotate_cookie == 1)); then
  args+=("oauth2-cookie-secret=$(openssl rand -hex 16)")
fi
client_secret=""
if ((exists == 0 || rotate == 1)); then
  client_secret=$(openssl rand -base64 32 | tr -d '\n')
  args+=("oauth2-client-secret=$client_secret")
fi
if ((exists)); then
  ((${#args[@]})) && vault kv patch -mount="$MOUNT" "$KEY" "${args[@]}" >/dev/null
else
  vault kv put -mount="$MOUNT" "$KEY" "${args[@]}" >/dev/null
fi
# Dex's copy of the client secret, written AFTER ours so a failure never leaves the two apart
# (its ExternalSecret maps dex/dispatcher-client-secret).
[[ -n "$client_secret" ]] && vault kv patch -mount="$MOUNT" dex "dispatcher-client-secret=$client_secret" >/dev/null
vault policy write dispatcher-web - <<POLICY
path "$MOUNT/data/$KEY" { capabilities = ["read"] }
POLICY
vault write auth/kubernetes/role/dispatcher-web \
  bound_service_account_names=eso-dispatcher-web \
  bound_service_account_namespaces=dispatcher \
  policies=dispatcher-web ttl=1h >/dev/null
present=$(vault kv get -mount="$MOUNT" -format=json "$KEY" 2>/dev/null | python3 -c 'import json,sys; print(",".join(sorted(json.load(sys.stdin)["data"]["data"])))' 2>/dev/null || echo none)
echo "dispatcher-web: policy and role written; secrets $( ((exists == 0 || rotate == 1)) && echo generated || { ((rotate_cookie)) && echo "cookie rotated" || echo kept; } ); present now: $present"
