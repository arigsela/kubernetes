#!/usr/bin/env bash
# Implementer agent — Vault provisioning for its GitHub App (idempotent, safe to re-run).
#   k8s-secrets/implementer-github-app   GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID,
#                                        GITHUB_APP_PRIVATE_KEY (PEM) — the env var names the
#                                        GitHub MCP server reads (docs/github-app-auth.md)
#   policy implementer-mcp               read k8s-secrets/data/implementer-github-app
#   role   implementer-mcp               kubernetes auth: SA eso-implementer-mcp in kagent, ttl 1h
#
# The private key never passes on a command line: it is read from a file you downloaded from
# the App's settings page and should delete afterwards. Ids are not secret and are flags.
#   kubectl port-forward -n vault svc/vault 8200:8200 &
#   export VAULT_ADDR=http://127.0.0.1:8200
#   vault login -method=oidc            # Dex; or export VAULT_TOKEN=<token>
#   scripts/provision-implementer-vault.sh --app-id 123456 --installation-id 7891011 --key-file ~/Downloads/arigsela-implementer.pem
# Re-running with only --key-file rotates the key and leaves the ids untouched.
set -euo pipefail
MOUNT=k8s-secrets
KEY=implementer-github-app
app_id=""; installation_id=""; key_file=""
while (($#)); do
  case "$1" in
    --app-id) app_id="$2"; shift 2 ;;
    --installation-id) installation_id="$2"; shift 2 ;;
    --key-file) key_file="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
: "${VAULT_ADDR:?set VAULT_ADDR (kubectl port-forward -n vault svc/vault 8200:8200; http://127.0.0.1:8200)}"
if ! vault token lookup >/dev/null 2>&1; then
  echo "not logged in to Vault: run 'vault login -method=oidc' or export VAULT_TOKEN" >&2
  exit 2
fi
args=()
[[ -n "$app_id" ]] && args+=("GITHUB_APP_ID=$app_id")
[[ -n "$installation_id" ]] && args+=("GITHUB_APP_INSTALLATION_ID=$installation_id")
if [[ -n "$key_file" ]]; then
  [[ -r "$key_file" ]] || { echo "cannot read $key_file" >&2; exit 2; }
  grep -q "BEGIN RSA PRIVATE KEY\|BEGIN PRIVATE KEY" "$key_file" || { echo "$key_file is not a PEM private key" >&2; exit 2; }
  args+=("GITHUB_APP_PRIVATE_KEY=$(cat "$key_file")")
fi
if ((${#args[@]})); then
  if vault kv get -mount="$MOUNT" "$KEY" >/dev/null 2>&1; then
    vault kv patch -mount="$MOUNT" "$KEY" "${args[@]}" >/dev/null
  else
    vault kv put -mount="$MOUNT" "$KEY" "${args[@]}" >/dev/null
  fi
fi
vault policy write implementer-mcp - <<POLICY
path "$MOUNT/data/$KEY" { capabilities = ["read"] }
POLICY
vault write auth/kubernetes/role/implementer-mcp \
  bound_service_account_names=eso-implementer-mcp \
  bound_service_account_namespaces=kagent \
  policies=implementer-mcp ttl=1h >/dev/null
present=$(vault kv get -mount="$MOUNT" -format=json "$KEY" 2>/dev/null | python3 -c 'import json,sys; print(",".join(sorted(json.load(sys.stdin)["data"]["data"])))' 2>/dev/null || echo none)
echo "implementer-mcp: policy and role written; properties set this run: ${#args[@]}; present now: $present"
