#!/bin/sh
# homepage — Vault provisioning (one-time, idempotent, safe to re-run)
#
# Creates the scoped Vault secret, policy, and kubernetes-auth role backing the
# ESO manifests in base-apps/homepage/:
#   - k8s-secrets/homepage  (props: plex-token, grafana-user, grafana-password)
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
#   PLEX_TOKEN=... GRAFANA_USER=homepage GRAFANA_PASSWORD=... sh /tmp/prov.sh
set -eu

: "${PLEX_TOKEN:?set PLEX_TOKEN}"
: "${GRAFANA_USER:?set GRAFANA_USER}"
: "${GRAFANA_PASSWORD:?set GRAFANA_PASSWORD}"

if vault kv get k8s-secrets/homepage >/dev/null 2>&1; then
  echo "k8s-secrets/homepage already exists — leaving values untouched."
else
  vault kv put k8s-secrets/homepage \
    plex-token="$PLEX_TOKEN" \
    grafana-user="$GRAFANA_USER" \
    grafana-password="$GRAFANA_PASSWORD"
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
