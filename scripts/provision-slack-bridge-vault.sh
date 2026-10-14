#!/usr/bin/env bash
# Slack bridge — Vault provisioning (§T.120). Idempotent; re-run to rotate either token.
#   k8s-secrets/slack-bridge   bot-token (xoxb-…, a COPY of n8n's: the same Slack app, its own
#                              path), app-token (xapp-…, Socket Mode, scope connections:write)
#   policy slack-bridge        read k8s-secrets/data/slack-bridge
#   role   slack-bridge        kubernetes auth: SA eso-slack-bridge in slack-bridge, ttl 1h
#
#   kubectl port-forward -n vault svc/vault 8200:8200 &
#   export VAULT_ADDR=http://127.0.0.1:8200
#   vault login -method=oidc            # Dex; or export VAULT_TOKEN=<token>
#   scripts/provision-slack-bridge-vault.sh            # prompts for both tokens (not echoed)
#   scripts/provision-slack-bridge-vault.sh --app-token-only   # rotate just the app token
# Run it BEFORE merging base-apps/slack-bridge/secret-store.yaml: a SecretStore whose Vault
# role does not exist fails the whole app sync. After a rotation the pod must restart to read
# the new values (env at start only): `kubectl delete pod -n slack-bridge -l app=slack-bridge`
# once ESO has refreshed (≤1h, or annotate the ExternalSecret with force-sync).
set -euo pipefail
MOUNT=k8s-secrets
KEY=slack-bridge
mode=both
while (($#)); do
  case "$1" in
    --app-token-only) mode=app; shift ;;
    --bot-token-only) mode=bot; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
: "${VAULT_ADDR:?set VAULT_ADDR (kubectl port-forward -n vault svc/vault 8200:8200; http://127.0.0.1:8200)}"
if ! vault token lookup >/dev/null 2>&1; then
  echo "not logged in to Vault: run 'vault login -method=oidc' or export VAULT_TOKEN" >&2
  exit 2
fi
read_secret() {   # read_secret PROMPT PREFIX -> value on stdout, never echoed
  local value
  read -r -s -p "$1: " value </dev/tty; echo >&2
  if [[ "$value" != "$2"* ]]; then echo "expected a token starting with $2" >&2; exit 2; fi
  printf '%s' "$value"
}
args=()
if [[ "$mode" != app ]]; then args+=("bot-token=$(read_secret 'Slack bot token (xoxb-…)' xoxb-)"); fi
if [[ "$mode" != bot ]]; then args+=("app-token=$(read_secret 'Slack app-level token (xapp-…)' xapp-)"); fi
# kv patch keeps the other field on a partial rotation; put creates the key the first time.
if vault kv get -mount="$MOUNT" "$KEY" >/dev/null 2>&1; then
  vault kv patch -mount="$MOUNT" "$KEY" "${args[@]}" >/dev/null
else
  if [[ "$mode" != both ]]; then echo "$MOUNT/$KEY does not exist yet: run without --*-only first" >&2; exit 2; fi
  vault kv put -mount="$MOUNT" "$KEY" "${args[@]}" >/dev/null
fi
echo "wrote $MOUNT/$KEY (${#args[@]} field(s))"
vault policy write slack-bridge - <<POLICY
path "$MOUNT/data/$KEY" {
  capabilities = ["read"]
}
POLICY
vault write auth/kubernetes/role/slack-bridge \
  bound_service_account_names=eso-slack-bridge \
  bound_service_account_namespaces=slack-bridge \
  policies=slack-bridge \
  ttl=1h >/dev/null
echo "policy slack-bridge and kubernetes role slack-bridge (SA eso-slack-bridge in slack-bridge) in place"
