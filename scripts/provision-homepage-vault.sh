#!/bin/sh
# homepage — Vault provisioning (one-time, idempotent, safe to re-run)
#
# Creates the scoped Vault secret, policy, and kubernetes-auth role backing the
# ESO manifests in base-apps/homepage/:
#   - k8s-secrets/homepage  (props: plex-token, grafana-token)
#   - policy homepage       (reads only that one path)
#   - role   homepage       (default SA @ homepage namespace)
#
# SAFE TO RE-RUN: values are written only if absent. Nothing is silently
# rotated — rotating a value without restarting the pod leaves it holding a
# stale credential. See base-apps/homepage/runbook.md for rotation.
#
# How to run (inside the vault-0 pod, matching the donetick pattern):
#
#   kubectl -n vault cp scripts/provision-homepage-vault.sh vault-0:/tmp/prov.sh
#   kubectl -n vault exec -it vault-0 -- sh
#   export VAULT_TOKEN=<root token>
#   PLEX_TOKEN=... GRAFANA_TOKEN=glsa_... sh /tmp/prov.sh
set -eu

: "${PLEX_TOKEN:?set PLEX_TOKEN}"
: "${GRAFANA_TOKEN:?set GRAFANA_TOKEN}"
# Fail loudly here rather than 403-ing on the first vault call.
: "${VAULT_TOKEN:?set VAULT_TOKEN — run this inside vault-0 with a token that can write policies and auth roles}"

if vault kv get k8s-secrets/homepage >/dev/null 2>&1; then
  echo "k8s-secrets/homepage already exists — leaving values untouched."
else
  vault kv put k8s-secrets/homepage \
    plex-token="$PLEX_TOKEN" \
    grafana-token="$GRAFANA_TOKEN"
  echo "wrote k8s-secrets/homepage"
fi

vault policy write homepage - <<'EOF'
path "k8s-secrets/data/homepage" {
  capabilities = ["read"]
}
path "k8s-secrets/metadata/homepage" {
  capabilities = ["read", "list"]
}
EOF
echo "wrote policy homepage"

vault write auth/kubernetes/role/homepage \
  bound_service_account_names=default \
  bound_service_account_namespaces=homepage \
  policies=homepage \
  ttl=24h
echo "wrote role homepage"
