#!/usr/bin/env bash
# provision-n8n-bug-investigator-vault.sh — Vault side of the n8n bug-investigator POC (§T.152,
# Decision 4). One-time, idempotent, safe to re-run.
#
# The POC has two GitHub tokens, each with its own Vault identity (templates/agent-identity,
# invariant 1): a path, a policy that reads only that path, and a Kubernetes-auth role bound to
# a dedicated ESO ServiceAccount in namespace n8n. Neither touches the shared `n8n` key.
#
#   PAT A  k8s-secrets/n8n-bug-investigator  (prop gh-token)      -> Secret n8n-bug-investigator-gh
#          policy + role n8n-bug-investigator  (SA eso-n8n-bug-investigator @ n8n)
#          the n8n workflows' token: arigsela/taskboard Issues read/write; Contents, Pull requests, Metadata read
#   PAT B  k8s-secrets/n8n-github-read-mcp   (prop github-token)  -> Secret n8n-github-read-mcp-token
#          policy + role n8n-github-read-mcp   (SA eso-n8n-github-read-mcp @ n8n)
#          the read-only GitHub MCP server's token: Contents, Pull requests, Metadata read
#
# Policies and roles are always written. A token is written only when its variable is set in the
# environment (never pasted on a command line):
#
#   kubectl port-forward -n vault svc/vault 8200:8200 &
#   export VAULT_ADDR=http://127.0.0.1:8200   # admin token in VAULT_TOKEN (OIDC login or break-glass)
#   scripts/provision-n8n-bug-investigator-vault.sh --dry-run
#   N8N_BUG_INVESTIGATOR_GH_TOKEN=... N8N_GITHUB_READ_MCP_TOKEN=... scripts/provision-n8n-bug-investigator-vault.sh
#
# Rotating: re-run with the new token set; ESO refreshes within 1h, or force it:
#   kubectl -n n8n annotate externalsecret n8n-bug-investigator-gh force-sync=$(date +%s) --overwrite
# PAT A reaches n8n as an env var read at start, so roll the n8n pod after a rotation.

set -euo pipefail

MOUNT="k8s-secrets"
ESO_NS="n8n"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

command -v vault >/dev/null || { echo "vault CLI not found" >&2; exit 1; }
vault token lookup >/dev/null 2>&1 || { echo "not logged in to Vault (VAULT_ADDR=${VAULT_ADDR:-unset})" >&2; exit 1; }

# provision <kv path> <policy and role> <ESO service account> <property> <token value or empty>
provision() {
  local kv_path="$1" role="$2" eso_sa="$3" prop="$4" value="$5"
  local policy_hcl="path \"$MOUNT/data/$kv_path\" { capabilities = [\"read\"] }"
  echo "==> policy $role"
  printf '%s\n' "$policy_hcl" | sed 's/^/    /'
  echo "==> role auth/kubernetes/role/$role (SA $eso_sa @ $ESO_NS, policy $role, ttl 1h)"
  if [ "$DRY_RUN" -eq 0 ]; then
    printf '%s\n' "$policy_hcl" | vault policy write "$role" - >/dev/null
    vault write "auth/kubernetes/role/$role" \
      bound_service_account_names="$eso_sa" \
      bound_service_account_namespaces="$ESO_NS" \
      policies="$role" ttl=1h >/dev/null
    echo "    -> written"
  else
    echo "    -> would run: vault policy write $role - ; vault write auth/kubernetes/role/$role ..."
  fi

  echo "==> $MOUNT/$kv_path (prop $prop)"
  if [ -n "$value" ]; then
    if [ "$DRY_RUN" -eq 0 ]; then
      vault kv put -mount="$MOUNT" "$kv_path" "$prop=$value" >/dev/null
      echo "    -> written (${#value} chars)"
    else
      echo "    -> would run: vault kv put -mount=$MOUNT $kv_path $prop=<${#value} chars>"
    fi
  elif vault kv get -mount="$MOUNT" -field="$prop" "$kv_path" >/dev/null 2>&1; then
    echo "    = present, kept (set the token variable to rotate)"
  else
    echo "    ! NOT SET. Export the token variable and re-run. Until then the ExternalSecret stays SecretSyncedError."
  fi
}

provision "n8n-bug-investigator" "n8n-bug-investigator" "eso-n8n-bug-investigator" "gh-token" "${N8N_BUG_INVESTIGATOR_GH_TOKEN:-}"
provision "n8n-github-read-mcp" "n8n-github-read-mcp" "eso-n8n-github-read-mcp" "github-token" "${N8N_GITHUB_READ_MCP_TOKEN:-}"
