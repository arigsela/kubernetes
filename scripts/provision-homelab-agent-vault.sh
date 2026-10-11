#!/bin/sh
# homelab-agent — Vault provisioning (one-time, idempotent, safe to re-run)
#
# ==============================================================================
# RETIRED 2026-10-11. DO NOT RUN. Kept as a pattern only.
#
# homelab-agent (the LangGraph BYO kagent agent) was retired at the kagent 1.0
# migration: kagent-langgraph does not implement 1.0's BYO contract (gRPC A2A,
# TaskStore), and homelab-knowledge stays the single knowledge agent (decided
# 2026-09-26). Its manifests are gone from base-apps/kagent/ and
# base-apps/postgresql/ (Task 5.2 of
# docs/plans/kagent-1-0-on-k8s-1-37-implementation-plan.md). Other provisioning
# scripts (e.g. provision-donetick-vault.sh) cite this one as the reference for
# the per-consumer split: generate-once DB password, one read-one-path policy and
# one kubernetes-auth role per consumer, a DB role bound in two namespaces.
#
# One-time manual cleanup, run by the owner AFTER the retirement PR is merged
# and Argo CD has pruned the ExternalSecrets/SecretStores (otherwise ESO keeps
# retrying against the deleted paths/roles and logs 403s until the prune):
#
#   # 1. Vault (inside vault-0, as in "How to run" below, with an admin token)
#   vault kv metadata delete -mount=k8s-secrets homelab-agent      # all versions
#   vault kv metadata delete -mount=k8s-secrets homelab-agent-db   # all versions
#   vault delete auth/kubernetes/role/homelab-agent
#   vault delete auth/kubernetes/role/homelab-agent-db
#   vault policy delete homelab-agent
#   vault policy delete homelab-agent-db
#
#   # 2. Postgres: the pgvector `postgresql` Deployment (namespace postgresql),
#   #    NOT the CNPG cluster. First take an off-cluster dump with a checksum
#   #    (Task 5.1 of the plan; skip if that dump already exists and
#   #    `pg_restore --list` reads it). Run from a laptop, so the file lands
#   #    off-cluster:
#   kubectl -n postgresql exec deploy/postgresql -- sh -c \
#     'pg_dump -U "$POSTGRES_USER" -d homelab_agent -Fc' > homelab_agent.dump
#   pg_restore --list homelab_agent.dump >/dev/null && shasum -a 256 homelab_agent.dump
#   #    then drop it as the instance's superuser (POSTGRES_USER, the
#   #    `n8n-user` key of postgresql-credentials). Plain DROP DATABASE
#   #    refuses while anything is still connected, which is the check that
#   #    nothing uses it any more:
#   kubectl -n postgresql exec -it deploy/postgresql -- sh -c \
#     'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
#   DROP DATABASE homelab_agent;
#   DROP ROLE homelab_agent;
#   #    (If DROP ROLE reports dependent objects, run
#   #    `REASSIGN OWNED BY homelab_agent TO CURRENT_USER; DROP OWNED BY homelab_agent;`
#   #    in each database it names, then DROP ROLE again.)
#
#   # 3. The agent's dedicated Anthropic API key (it lived only in
#   #    k8s-secrets/homelab-agent): revoke it in the Anthropic console.
#   #    The backstage-token in the same path was a copy of Backstage's MCP
#   #    token, which kagent's backstage-catalog MCP server still uses.
#
# The ECR repository (homelab-agent) is Terraform-managed and is deleted
# through Atlantis (terraform/roots/asela-cluster/ecr.tf), not by hand.
# ==============================================================================
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
# Design + runbook: the "homelab-agent (BYO)" section of base-apps/kagent/docs.md
# and "Provision homelab-agent's Vault side" in base-apps/kagent/runbook.md, both
# removed at retirement (read them in git history before 2026-10-11).

set -eu

# Retired (see the header): never recreate the homelab-agent paths, policies
# and roles. Drop this block only in a copy made from this pattern.
echo "ERROR: homelab-agent was retired on 2026-10-11; this script is a pattern only." >&2
exit 1

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
echo "    Verify per base-apps/kagent/runbook.md (\"Provision homelab-agent's Vault side\")."
