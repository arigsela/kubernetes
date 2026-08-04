#!/bin/sh
# homepage (media widgets) — Vault provisioning (one-time, idempotent)
#
# ADDS five properties to the EXISTING k8s-secrets/homepage secret:
#   qbittorrent-username, qbittorrent-password, sonarr-key, radarr-key, seerr-key
#
# Uses `vault kv patch`, NOT `vault kv put`. put REPLACES the whole secret and
# would silently delete plex-token and grafana-token, breaking the Plex and
# Grafana tiles. patch merges.
#
# No policy or role changes are needed: the `homepage` policy already grants
# read on k8s-secrets/data/homepage, and these are properties of that same
# secret.
#
# SAFE TO RE-RUN: patch overwrites only the five keys named here. Re-running
# with the same values is a no-op; re-running with new values rotates them,
# which requires a pod restart to take effect (env vars are read once at
# startup) — see base-apps/homepage/runbook.md.
#
# How to run — from a laptop, against a port-forward (preferred: uses an
# OIDC/Dex admin token rather than the break-glass root token):
#
#   kubectl port-forward -n vault svc/vault 8200:8200 &
#   export VAULT_ADDR=http://127.0.0.1:8200
#   vault login -method=oidc role=admin
#   QBITTORRENT_USERNAME=... QBITTORRENT_PASSWORD=... \
#   SONARR_KEY=... RADARR_KEY=... SEERR_KEY=... \
#     sh scripts/provision-homepage-media-vault.sh
#
# Or from inside the vault-0 pod:
#
#   kubectl -n vault cp scripts/provision-homepage-media-vault.sh vault-0:/tmp/prov.sh
#   kubectl -n vault exec -it vault-0 -- sh
#   export VAULT_TOKEN=<root-or-admin-token>
#   QBITTORRENT_USERNAME=... ... sh /tmp/prov.sh
#   unset VAULT_TOKEN; rm /tmp/prov.sh; exit
set -eu

VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
export VAULT_ADDR

: "${VAULT_TOKEN:?set VAULT_TOKEN, or run \`vault login\` first — needs write on k8s-secrets/data/homepage}"
: "${QBITTORRENT_USERNAME:?set QBITTORRENT_USERNAME}"
: "${QBITTORRENT_PASSWORD:?set QBITTORRENT_PASSWORD}"
: "${SONARR_KEY:?set SONARR_KEY}"
: "${RADARR_KEY:?set RADARR_KEY}"
: "${SEERR_KEY:?set SEERR_KEY}"

# The secret MUST already exist — this only adds to it. If it is missing, the
# base provisioning never ran; use scripts/provision-homepage-vault.sh first.
if ! vault kv get k8s-secrets/homepage >/dev/null 2>&1; then
  echo "ERROR: k8s-secrets/homepage does not exist (or is unreadable)." >&2
  echo "       Run scripts/provision-homepage-vault.sh first." >&2
  exit 1
fi

vault kv patch k8s-secrets/homepage \
  qbittorrent-username="$QBITTORRENT_USERNAME" \
  qbittorrent-password="$QBITTORRENT_PASSWORD" \
  sonarr-key="$SONARR_KEY" \
  radarr-key="$RADARR_KEY" \
  seerr-key="$SEERR_KEY"

echo "patched k8s-secrets/homepage with the five media properties"
echo "existing properties preserved:"
vault kv get -format=json k8s-secrets/homepage \
  | grep -oE '"(plex-token|grafana-token)"' | sort -u
