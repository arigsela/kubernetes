#!/usr/bin/env bash
# provision-brave-search-vault.sh — Vault side of the Brave Search MCP server (one-time,
# idempotent, safe to re-run).
#
# The MCP server (base-apps/kagent/brave-search-mcp.yaml) reads BRAVE_API_KEY from the Secret
# that External Secrets renders from Vault (brave-search-mcp-external-secret.yaml). Per the
# agent-identity pattern (templates/agent-identity/README.md) it gets its own Vault role and a
# policy that reads exactly one path. This script writes those two; the key value itself is
# written only when BRAVE_API_KEY is set in the environment (never pasted on a command line).
#
#   kubectl port-forward -n vault svc/vault 8200:8200 &
#   export VAULT_ADDR=http://127.0.0.1:8200   # admin token in VAULT_TOKEN (OIDC login or break-glass)
#   scripts/provision-brave-search-vault.sh --dry-run
#   BRAVE_API_KEY=... scripts/provision-brave-search-vault.sh    # role + policy + the key
#   scripts/provision-brave-search-vault.sh                      # role + policy only
#
# Get a key at https://api-dashboard.search.brave.com/ (the Free plan is enough: 2,000
# queries/month). Rotating: re-run with the new BRAVE_API_KEY; ESO refreshes within 1h or
# force it: kubectl -n kagent annotate externalsecret brave-search-mcp-api-key force-sync=$(date +%s) --overwrite
#
# Creates:
#   - k8s-secrets/brave-search-mcp   (prop: api-key)                     -> Secret brave-search-mcp-api-key
#   - policy kagent-brave-search-mcp (read on that one path)
#   - role   kagent-brave-search-mcp (eso-brave-search-mcp @ kagent, ttl 1h)

set -euo pipefail

MOUNT="k8s-secrets"
KV_PATH="brave-search-mcp"
POLICY="kagent-brave-search-mcp"
ROLE="kagent-brave-search-mcp"
ESO_SA="eso-brave-search-mcp"
ESO_NS="kagent"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

command -v vault >/dev/null || { echo "vault CLI not found" >&2; exit 1; }
vault token lookup >/dev/null 2>&1 || { echo "not logged in to Vault (VAULT_ADDR=${VAULT_ADDR:-unset})" >&2; exit 1; }

POLICY_HCL="path \"$MOUNT/data/$KV_PATH\" { capabilities = [\"read\"] }"
echo "==> policy $POLICY"
printf '%s\n' "$POLICY_HCL" | sed 's/^/    /'
echo "==> role auth/kubernetes/role/$ROLE (SA $ESO_SA @ $ESO_NS, policy $POLICY, ttl 1h)"
if [ "$DRY_RUN" -eq 0 ]; then
  printf '%s\n' "$POLICY_HCL" | vault policy write "$POLICY" - >/dev/null
  vault write "auth/kubernetes/role/$ROLE" \
    bound_service_account_names="$ESO_SA" \
    bound_service_account_namespaces="$ESO_NS" \
    policies="$POLICY" ttl=1h >/dev/null
  echo "    -> written"
else
  echo "    -> would run: vault policy write $POLICY - ; vault write auth/kubernetes/role/$ROLE ..."
fi

echo "==> $MOUNT/$KV_PATH (prop api-key)"
if [ -n "${BRAVE_API_KEY:-}" ]; then
  if [ "$DRY_RUN" -eq 0 ]; then
    vault kv put -mount="$MOUNT" "$KV_PATH" api-key="$BRAVE_API_KEY" >/dev/null
    echo "    -> written (${#BRAVE_API_KEY} chars)"
  else
    echo "    -> would run: vault kv put -mount=$MOUNT $KV_PATH api-key=<${#BRAVE_API_KEY} chars>"
  fi
elif vault kv get -mount="$MOUNT" -field=api-key "$KV_PATH" >/dev/null 2>&1; then
  echo "    = present, kept (set BRAVE_API_KEY to rotate)"
else
  echo "    ! NOT SET. Export BRAVE_API_KEY and re-run, or:"
  echo "      vault kv put -mount=$MOUNT $KV_PATH api-key=<key>"
  echo "      Until then ExternalSecret kagent/brave-search-mcp-api-key stays SecretSyncedError."
fi
