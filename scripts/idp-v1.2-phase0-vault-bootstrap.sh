#!/bin/sh
# IDP v1.2 — Phase 0 Vault bootstrap (one-time, idempotent)
#
# Reads the K8s auth method accessor, verifies that identity-alias metadata
# is populated correctly, and writes the templated `app-namespace-rw` policy
# that grants per-namespace read/write on k8s-secrets/<ns>/*.
#
# How to run inside the vault-0 pod:
#
#   # 1. Copy the script in:
#   kubectl -n vault cp scripts/idp-v1.2-phase0-vault-bootstrap.sh \
#                       vault-0:/tmp/phase0.sh
#
#   # 2. Exec into the pod and run with VAULT_TOKEN set:
#   kubectl -n vault exec -it vault-0 -- sh
#   export VAULT_TOKEN=<your-root-or-admin-token>
#   sh /tmp/phase0.sh
#
#   # 3. Clean up after:
#   rm /tmp/phase0.sh   # (still inside the pod)
#   exit
#
# Spec: docs/superpowers/specs/2026-04-30-idp-v1.2-vault-credentials-design.md
# Plan: docs/superpowers/plans/2026-04-30-idp-v1.2-vault-credentials.md (Phase 0)

set -e

# Pre-check: require an authenticated token
if [ -z "${VAULT_TOKEN:-}" ]; then
  echo "ERROR: VAULT_TOKEN is not set in the environment."
  echo "       Set it (export VAULT_TOKEN=<root-or-admin-token>) and re-run."
  exit 1
fi

# VAULT_ADDR is set by the pod's env; override if needed.
VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
export VAULT_ADDR
export VAULT_TOKEN

echo "=== Pre-check: vault status ==="
vault status | grep -E '^(Sealed|Initialized|Version)' || true
echo

echo "=== Task 0.1 Step 1: Get K8s auth method accessor ==="
ACCESSOR=$(vault read -field=accessor sys/auth/kubernetes/ 2>/dev/null || true)
if [ -z "$ACCESSOR" ]; then
  echo "ERROR: empty accessor. Either:"
  echo "  - The K8s auth method is not enabled at sys/auth/kubernetes/"
  echo "  - VAULT_TOKEN does not have permission to read sys/auth/"
  exit 1
fi
echo "Accessor: $ACCESSOR"
echo

echo "=== Task 0.1 Step 2: Verify identity-alias metadata (best-effort) ==="
# vault list returns one ID per line; head -1 picks the first.
# If no entities exist, output is empty and we proceed with a warning.
ENTITY_ID=$(vault list -format=table identity/entity/id 2>/dev/null \
            | tail -n +3 \
            | head -n 1 \
            | tr -d '[:space:]' || true)

if [ -z "$ENTITY_ID" ]; then
  echo "WARN: no existing identity entities found."
  echo "      Modern Vault (1.10+) populates service_account_namespace metadata"
  echo "      automatically on first K8s-auth login. Trusting the docs and proceeding."
  echo "      Smoke validation (Phase 3) will catch any deviation in practice."
else
  echo "Inspecting first entity: $ENTITY_ID"
  ALIAS_OUTPUT=$(vault read identity/entity/id/$ENTITY_ID 2>/dev/null || true)
  echo "$ALIAS_OUTPUT" | grep -E 'aliases|metadata|service_account' | head -20

  # Look for service_account_namespace anywhere in the entity output.
  # Vault's tabular `read` output formats aliases as nested maps;
  # presence of the substring is sufficient signal that metadata is populated.
  if echo "$ALIAS_OUTPUT" | grep -q 'service_account_namespace'; then
    echo
    echo "  ✓ service_account_namespace is present on at least one alias"
  else
    echo
    echo "  ✗ STOP: service_account_namespace not found in any alias metadata."
    echo "    See spec §5.4 — debug Vault version + auth-method config before proceeding."
    echo "    Common causes: very old Vault (pre-1.2), customized auth/kubernetes/config."
    exit 1
  fi
fi
echo

echo "=== Task 0.2 Step 1: Substitute accessor + write templated policy ==="
# Heredoc with $ACCESSOR expanded by sh (EOF unquoted).
# vault policy write reads from stdin when given "-".
vault policy write app-namespace-rw - <<EOF
# IDP v1.2 — templated policy granting per-namespace read/write on
# k8s-secrets/<ns>/*. Expands at request time using K8s auth alias metadata.

path "k8s-secrets/data/{{identity.entity.aliases.${ACCESSOR}.metadata.service_account_namespace}}/*" {
  capabilities = ["create", "read", "update", "patch"]
}
path "k8s-secrets/metadata/{{identity.entity.aliases.${ACCESSOR}.metadata.service_account_namespace}}/*" {
  capabilities = ["list", "read", "delete"]
}
EOF
echo

echo "=== Task 0.2 Step 3: Verify policy round-trip ==="
vault policy read app-namespace-rw
echo

echo "=== Phase 0 SUMMARY ==="
echo "  Accessor recorded:  $ACCESSOR"
echo "  Policy written:     app-namespace-rw"
echo "  Re-runnable:        yes (vault policy write is upsert)"
echo "  Phase 0 status:     GREEN"
echo
echo "Next step: Per-namespace role binding happens at smoke-app onboard time"
echo "(plan Task 3.2). No more Phase 0 work needed; ready for Phase 1."
