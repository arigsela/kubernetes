#!/bin/sh
# agent-audit-risk — Vault provisioning (one-time, idempotent, safe to re-run)
#
# Creates what base-apps/postgresql/external-secrets-agent-audit-risk.yaml needs:
#   - k8s-secrets/agent-audit-risk  (prop: typesafe-api-key)
#   - policy agent-audit-risk       (reads only that path)
#   - role   agent-audit-risk       (eso-agent-audit-risk @ postgresql)
#
# The key value comes from the TYPESAFE_API_KEY env var so it stays out of shell
# history. Required on the first run; on a re-run the stored value is preserved
# unless the variable is supplied again (that is how you rotate it).
#
# How to run (inside the vault-0 pod, matching provision-donetick-vault.sh):
#
#   kubectl -n vault cp scripts/provision-agent-audit-risk-vault.sh vault-0:/tmp/prov.sh
#   kubectl -n vault exec -it vault-0 -- sh
#   export VAULT_TOKEN=<root-or-admin-token>
#   export TYPESAFE_API_KEY=<key>      # first run, or to rotate
#   sh /tmp/prov.sh
#   unset VAULT_TOKEN TYPESAFE_API_KEY; rm /tmp/prov.sh; exit
set -eu
MOUNT="k8s-secrets"
KEY_PATH="agent-audit-risk"

if [ -z "${VAULT_TOKEN:-}" ]; then
  echo "ERROR: VAULT_TOKEN is not set." >&2; exit 1
fi
VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
export VAULT_ADDR VAULT_TOKEN
command -v vault >/dev/null 2>&1 || { echo "ERROR: vault CLI not found (run inside vault-0)." >&2; exit 1; }
vault token lookup >/dev/null 2>&1 || { echo "ERROR: VAULT_TOKEN cannot authenticate to $VAULT_ADDR." >&2; exit 1; }

# --- 1. the key: write when supplied, otherwise preserve ---------------------
if [ -n "${TYPESAFE_API_KEY:-}" ]; then
  echo "==> writing typesafe-api-key to $MOUNT/$KEY_PATH"
  vault kv put -mount="$MOUNT" "$KEY_PATH" typesafe-api-key="$TYPESAFE_API_KEY" >/dev/null
elif vault kv get -mount="$MOUNT" -field=typesafe-api-key "$KEY_PATH" >/dev/null 2>&1; then
  echo "==> $MOUNT/$KEY_PATH already has typesafe-api-key — preserving it"
else
  echo "ERROR: no stored key and TYPESAFE_API_KEY not set; export it and re-run." >&2; exit 1
fi

# --- 2. policy: read exactly one path ----------------------------------------
echo "==> policy agent-audit-risk"
vault policy write agent-audit-risk - <<EOF2
path "$MOUNT/data/$KEY_PATH" { capabilities = ["read"] }
EOF2

# --- 3. kubernetes-auth role bound to the ESO SA in postgresql ---------------
echo "==> role agent-audit-risk -> eso-agent-audit-risk @ postgresql"
vault write auth/kubernetes/role/agent-audit-risk \
  bound_service_account_names=eso-agent-audit-risk \
  bound_service_account_namespaces=postgresql \
  policies=agent-audit-risk ttl=1h >/dev/null

echo "done. Verify: kubectl -n postgresql get externalsecret agent-audit-risk-credentials"
