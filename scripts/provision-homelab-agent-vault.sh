#!/bin/sh
# homelab-agent — Vault provisioning (one-time, idempotent, safe to re-run)
#
# Creates the scoped Vault secrets, policies, and kubernetes-auth roles that
# back the ESO manifests in base-apps/kagent/ and base-apps/postgresql/:
#   - k8s-secrets/homelab-agent-db   (prop: password)   -> DSN + init Job
#   - k8s-secrets/homelab-agent      (props: anthropic-api-key, backstage-token)
#   - policies homelab-agent / homelab-agent-db (each reads only its one path)
#   - roles    homelab-agent (eso-homelab-agent @ kagent)
#              homelab-agent-db (eso-homelab-agent-db @ kagent,postgresql)
#
# SAFE TO RE-RUN: the DB password is generated ONCE and preserved on every
# later run (never silently rotated — rotating it would break the live agent
# until the init Job + pod restart). App tokens are only written when supplied;
# unset ones are preserved.
#
# App-token values are read from the environment (never CLI args, so they don't
# leak into shell history or the process list):
#   ANTHROPIC_API_KEY   — the agent's own scoped Anthropic key (NOT the shared one)
#   BACKSTAGE_MCP_TOKEN  — the Backstage MCP bearer token value
# Both are REQUIRED the first time k8s-secrets/homelab-agent is created; on a
# re-run, provide only the one(s) you want to change.
#
# How to run (inside the vault-0 pod, matching the phase0 bootstrap pattern):
#
#   kubectl -n vault cp scripts/provision-homelab-agent-vault.sh vault-0:/tmp/prov.sh
#   kubectl -n vault exec -it vault-0 -- sh
#   export VAULT_TOKEN=<root-or-admin-token>
#   export ANTHROPIC_API_KEY=<key>          # first run only (or to rotate)
#   export BACKSTAGE_MCP_TOKEN=<token>       # first run only (or to rotate)
#   sh /tmp/prov.sh
#   unset ANTHROPIC_API_KEY BACKSTAGE_MCP_TOKEN VAULT_TOKEN
#   rm /tmp/prov.sh
#   exit
#
# Spec: docs/superpowers/specs/2026-07-17-homelab-agent-deployment-design.md
# Plan: docs/superpowers/plans/2026-07-17-homelab-agent-deployment.md
# Runbook: docs/superpowers/homelab-agent-vault-provisioning.md

set -eu

MOUNT="k8s-secrets"
APP_PATH="homelab-agent"
DB_PATH="homelab-agent-db"

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
# 64 hex chars (URL-safe, so the MEMORY_DB_URL DSN needs no escaping).
gen_password() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 64
    echo
  fi
}

# --- 1. DB password: generate once, preserve on re-run ----------------------
if vault kv get -mount="$MOUNT" -field=password "$DB_PATH" >/dev/null 2>&1; then
  echo "==> $MOUNT/$DB_PATH already has a password — preserving it (not rotating)"
else
  echo "==> generating homelab-agent DB password at $MOUNT/$DB_PATH"
  vault kv put -mount="$MOUNT" "$DB_PATH" password="$(gen_password)" >/dev/null
fi

# --- 2. App tokens: anthropic-api-key + backstage-token ---------------------
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
BACKSTAGE_MCP_TOKEN="${BACKSTAGE_MCP_TOKEN:-}"

if vault kv get -mount="$MOUNT" "$APP_PATH" >/dev/null 2>&1; then
  # Path exists — patch only the value(s) supplied this run; preserve the rest.
  if [ -n "$ANTHROPIC_API_KEY" ]; then
    vault kv patch -mount="$MOUNT" "$APP_PATH" anthropic-api-key="$ANTHROPIC_API_KEY" >/dev/null
    echo "==> updated anthropic-api-key"
  fi
  if [ -n "$BACKSTAGE_MCP_TOKEN" ]; then
    vault kv patch -mount="$MOUNT" "$APP_PATH" backstage-token="$BACKSTAGE_MCP_TOKEN" >/dev/null
    echo "==> updated backstage-token"
  fi
  echo "==> $MOUNT/$APP_PATH present (unsupplied values preserved)"
else
  # First run — both values required.
  if [ -z "$ANTHROPIC_API_KEY" ] || [ -z "$BACKSTAGE_MCP_TOKEN" ]; then
    echo "ERROR: $MOUNT/$APP_PATH does not exist yet — both ANTHROPIC_API_KEY and" >&2
    echo "       BACKSTAGE_MCP_TOKEN must be exported for the first run." >&2
    exit 1
  fi
  vault kv put -mount="$MOUNT" "$APP_PATH" \
    anthropic-api-key="$ANTHROPIC_API_KEY" \
    backstage-token="$BACKSTAGE_MCP_TOKEN" >/dev/null
  echo "==> wrote $MOUNT/$APP_PATH (anthropic-api-key, backstage-token)"
fi

# --- 3. Policies (least privilege: one path each) ---------------------------
echo "==> writing policy homelab-agent"
vault policy write homelab-agent - <<EOF >/dev/null
path "$MOUNT/data/$APP_PATH" { capabilities = ["read"] }
EOF

echo "==> writing policy homelab-agent-db"
vault policy write homelab-agent-db - <<EOF >/dev/null
path "$MOUNT/data/$DB_PATH" { capabilities = ["read"] }
EOF

# --- 4. Kubernetes-auth roles -----------------------------------------------
echo "==> writing role homelab-agent (eso-homelab-agent @ kagent)"
vault write auth/kubernetes/role/homelab-agent \
  bound_service_account_names=eso-homelab-agent \
  bound_service_account_namespaces=kagent \
  policies=homelab-agent ttl=1h >/dev/null

echo "==> writing role homelab-agent-db (eso-homelab-agent-db @ kagent,postgresql)"
vault write auth/kubernetes/role/homelab-agent-db \
  bound_service_account_names=eso-homelab-agent-db \
  bound_service_account_namespaces=kagent,postgresql \
  policies=homelab-agent-db ttl=1h >/dev/null

echo
echo "==> Done. The ExternalSecrets will resolve on the next ESO refresh"
echo "    (or delete the target Secret to force an immediate resync)."
echo "    Verify per docs/superpowers/homelab-agent-vault-provisioning.md section 4."
