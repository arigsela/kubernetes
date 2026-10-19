#!/usr/bin/env bash
# beacon enterprise tenants — Vault provisioning (beacon §T.42, beacon §V.52). Idempotent; safe to
# re-run. Run it ONCE, before the first XTenant lands in base-apps/beacon-tenants/ and before
# the beacon §T.45 PR merges: a SecretStore whose Vault role does not exist fails its app's sync.
#
# Two roles, both provisioned once for every tenant, so no per-tenant Vault step exists:
#
#   policy beacon-tenant-rw    templated on the caller's service-account namespace (like the IDP's
#                              app-namespace-rw): read/write k8s-secrets/tenants/<namespace>/*, the
#                              metadata included: ESO's PushSecret marks each key it writes with
#                              custom metadata (managed-by: external-secrets), a PUT on
#                              metadata/<key> (beacon §B.26)
#   role   beacon-tenant       kubernetes auth: SA eso-tenant in any namespace matching tenant-*
#                              (Vault matches the list as globs), policies default,beacon-tenant-rw
#                              -> each tenant namespace reaches only k8s-secrets/tenants/<itself>/*
#
#   policy beacon-tenants-read list k8s-secrets/metadata/tenants/*, read k8s-secrets/data/tenants/+/api
#   role   beacon-tenants      kubernetes auth: SA eso-beacon-tenants in beacon, ttl 1h
#                              -> beacon-api's per-tenant DSN + signing key (beacon §V.55), nothing
#                                 else: not the tenant DB passwords, no listing outside tenants/
#
# Every tenant key lives under ONE folder, k8s-secrets/tenants/, because ESO's Vault `find` lists
# its path as a folder, recursively, and fails on any 403 (beacon §R.47): discovering tenant-*/api
# at the mount root would need list on all of k8s-secrets/metadata/*.
#
# It writes no secret values. Each tenant's secrets are generated in its namespace by ESO Password
# generators and pushed to its folder (beacon §V.52). The one key it writes is a marker,
# k8s-secrets/tenants/.keep: ESO's find fails on a folder Vault cannot list, and before the first
# tenant tenants/ would not exist (beacon §B.25). beacon-tenant-dsns's name filter skips it unread.
#
#   export VAULT_ADDR=https://vault.arigsela.com   # or a port-forward, http://127.0.0.1:8200
#   vault login -method=oidc                       # Dex; or export VAULT_TOKEN=<token>
#   scripts/provision-beacon-tenants-vault.sh
set -euo pipefail
MOUNT=k8s-secrets
FOLDER=tenants
: "${VAULT_ADDR:?set VAULT_ADDR (https://vault.arigsela.com, or a port-forward on http://127.0.0.1:8200)}"
if ! vault token lookup >/dev/null 2>&1; then
  echo "not logged in to Vault: run 'vault login -method=oidc' or export VAULT_TOKEN" >&2
  exit 2
fi
# The templated policy names the kubernetes auth mount by its accessor.
accessor=$(vault read -field=accessor sys/auth/kubernetes/ 2>/dev/null || true)
if [[ ! "$accessor" =~ ^auth_kubernetes_[0-9a-f]+$ ]]; then
  echo "cannot read the kubernetes auth accessor (sys/auth/kubernetes/): got '${accessor}'" >&2
  exit 2
fi
ns="{{identity.entity.aliases.${accessor}.metadata.service_account_namespace}}"

vault policy write beacon-tenant-rw - >/dev/null <<POLICY
path "$MOUNT/data/$FOLDER/$ns/*" { capabilities = ["create", "read", "update", "patch"] }
path "$MOUNT/metadata/$FOLDER/$ns/*" { capabilities = ["create", "read", "update", "patch", "list", "delete"] }
POLICY
vault policy write beacon-tenants-read - >/dev/null <<POLICY
path "$MOUNT/metadata/$FOLDER/*" { capabilities = ["list"] }
path "$MOUNT/data/$FOLDER/+/api" { capabilities = ["read"] }
POLICY
if ! vault kv get -mount="$MOUNT" "$FOLDER/.keep" >/dev/null 2>&1; then
  vault kv put -mount="$MOUNT" "$FOLDER/.keep" \
    purpose="keeps k8s-secrets/tenants/ listable before the first tenant (beacon §B.25)" >/dev/null
fi
vault write auth/kubernetes/role/beacon-tenant \
  bound_service_account_names=eso-tenant \
  'bound_service_account_namespaces=tenant-*' \
  policies=default,beacon-tenant-rw ttl=1h >/dev/null
vault write auth/kubernetes/role/beacon-tenants \
  bound_service_account_names=eso-beacon-tenants \
  bound_service_account_namespaces=beacon \
  policies=beacon-tenants-read ttl=1h >/dev/null

echo "beacon tenants: policies beacon-tenant-rw, beacon-tenants-read and roles beacon-tenant, beacon-tenants written"
echo "  tenant secrets: $MOUNT/$FOLDER/tenant-<slug>/* (generated in each tenant namespace); marker $MOUNT/$FOLDER/.keep"
