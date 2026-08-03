#!/bin/sh
# donetick — Vault provisioning (one-time, idempotent, safe to re-run)
#
# Creates the scoped Vault secrets, policies, and kubernetes-auth roles that
# back the ESO manifests in base-apps/donetick/ and base-apps/postgresql/:
#   - k8s-secrets/donetick-db  (prop: db-password)  -> the app AND the CNPG role
#   - k8s-secrets/donetick     (prop: jwt-secret)   -> session signing key
#   - policies donetick / donetick-db (each reads only its one path)
#   - roles    donetick    (default          @ donetick)
#              donetick-db (eso-donetick-db  @ donetick,postgresql)
#
# The two-role split is the point: the postgresql namespace must read the DB
# password to provision the CNPG role, and must NOT be able to read the JWT
# signing key while doing so. This mirrors provision-homelab-agent-vault.sh.
#
# SAFE TO RE-RUN: both values are generated ONCE and preserved on every later
# run. Neither is silently rotated — rotating the DB password without also
# restarting the app leaves the pod holding a stale credential, and rotating the
# JWT secret logs every user out. Both are deliberate acts; see
# base-apps/donetick/runbook.md for the rotation procedures.
#
# How to run (inside the vault-0 pod, matching the homelab-agent pattern):
#
#   kubectl -n vault cp scripts/provision-donetick-vault.sh vault-0:/tmp/prov.sh
#   kubectl -n vault exec -it vault-0 -- sh
#   export VAULT_TOKEN=<root-or-admin-token>
#   sh /tmp/prov.sh
#   unset VAULT_TOKEN
#   rm /tmp/prov.sh
#   exit
#
# Docs: base-apps/donetick/docs.md, base-apps/donetick/runbook.md

set -eu

MOUNT="k8s-secrets"
APP_PATH="donetick"
DB_PATH="donetick-db"

# --- pre-checks -------------------------------------------------------------
if [ -z "${VAULT_TOKEN:-}" ]; then
  echo "ERROR: VAULT_TOKEN is not set. export VAULT_TOKEN=<root-or-admin-token> and re-run." >&2
  exit 1
fi
VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
export VAULT_ADDR VAULT_TOKEN

if ! command -v vault >/dev/null 2>&1; then
  echo "ERROR: vault CLI not found (run this inside the vault-0 pod)." >&2
  exit 1
fi
vault token lookup >/dev/null 2>&1 || {
  echo "ERROR: VAULT_TOKEN is not valid / cannot authenticate to $VAULT_ADDR." >&2
  exit 1
}

# --- helpers ----------------------------------------------------------------
# 64 hex chars. Hex, not base64: the DB password is interpolated into a
# libpq DSN by donetick (`password=%s`), where a `'` or a space would need
# quoting the app does not do. It also clears the 32-character floor that
# config.validateJWTSecret() enforces, by a wide margin.
gen_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 64
    echo
  fi
}

# --- 1. DB password: generate once, preserve on re-run ----------------------
if vault kv get -mount="$MOUNT" -field=db-password "$DB_PATH" >/dev/null 2>&1; then
  echo "==> $MOUNT/$DB_PATH already has db-password — preserving it (not rotating)"
else
  echo "==> generating donetick DB password at $MOUNT/$DB_PATH"
  vault kv put -mount="$MOUNT" "$DB_PATH" db-password="$(gen_secret)" >/dev/null
fi

# --- 2. JWT signing key: generate once, preserve on re-run ------------------
if vault kv get -mount="$MOUNT" -field=jwt-secret "$APP_PATH" >/dev/null 2>&1; then
  echo "==> $MOUNT/$APP_PATH already has jwt-secret — preserving it (not rotating)"
else
  echo "==> generating donetick JWT secret at $MOUNT/$APP_PATH"
  vault kv put -mount="$MOUNT" "$APP_PATH" jwt-secret="$(gen_secret)" >/dev/null
fi

# --- 3. Policies (least privilege: one path each) ---------------------------
echo "==> writing policy donetick"
vault policy write donetick - <<EOF >/dev/null
path "$MOUNT/data/$APP_PATH" { capabilities = ["read"] }
EOF

echo "==> writing policy donetick-db"
vault policy write donetick-db - <<EOF >/dev/null
path "$MOUNT/data/$DB_PATH" { capabilities = ["read"] }
EOF

# --- 4. Kubernetes-auth roles -----------------------------------------------
# The app role binds the `default` SA, matching every other per-app SecretStore
# in base-apps/. The db role binds a dedicated SA in BOTH namespaces, because
# two different consumers need the same one credential.
echo "==> writing role donetick (default @ donetick)"
vault write auth/kubernetes/role/donetick \
  bound_service_account_names=default \
  bound_service_account_namespaces=donetick \
  policies=donetick ttl=1h >/dev/null

echo "==> writing role donetick-db (eso-donetick-db @ donetick,postgresql)"
vault write auth/kubernetes/role/donetick-db \
  bound_service_account_names=eso-donetick-db \
  bound_service_account_namespaces=donetick,postgresql \
  policies=donetick-db ttl=1h >/dev/null

echo
echo "==> Done. The ExternalSecrets will resolve on the next ESO refresh"
echo "    (or delete the target Secret to force an immediate resync)."
echo "    Expect, in order: donetick-db-credentials syncs in both namespaces,"
echo "    CNPG creates the donetick role, Database/donetick reports ready,"
echo "    then the Deployment stops crash-looping."
