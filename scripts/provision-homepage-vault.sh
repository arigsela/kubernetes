#!/bin/sh
# homepage — Vault provisioning (one-time, idempotent, safe to re-run)
#
# Creates the scoped Vault secret, policy, and kubernetes-auth role backing the
# ESO manifests in base-apps/homepage/:
#   - k8s-secrets/homepage  (props: plex-token, grafana-token)
#   - policy homepage       (reads only that one path)
#   - role   homepage       (default SA @ homepage namespace)
#
# SAFE TO RE-RUN, WITH ONE CAVEAT: the k8s-secrets/homepage write is
# conditional — it's skipped when the secret is confirmed to already exist,
# so a plex-token/grafana-token pair already in place is never silently
# rotated (rotating without restarting the pod would leave it holding a
# stale credential; see base-apps/homepage/runbook.md). The policy and role
# writes below are NOT conditional — `vault policy write` and
# `vault write auth/kubernetes/role/homepage` always run and always
# overwrite. That's intentional: they write exactly the documented policy/
# role, so re-running just re-asserts the intended state.
#
# How to run — two routes:
#
#   1. From your laptop (preferred — lets you use an OIDC/Dex admin token
#      instead of the break-glass root token):
#        kubectl -n vault port-forward svc/vault 8200:8200
#        export VAULT_ADDR=http://127.0.0.1:8200
#        export VAULT_TOKEN=<OIDC/Dex admin token, or root token>
#        PLEX_TOKEN=... GRAFANA_TOKEN=glsa_... sh scripts/provision-homepage-vault.sh
#
#   2. Inside the vault-0 pod (matching the donetick pattern):
#        kubectl -n vault cp scripts/provision-homepage-vault.sh vault-0:/tmp/prov.sh
#        kubectl -n vault exec -it vault-0 -- sh
#        export VAULT_TOKEN=<root token>
#        PLEX_TOKEN=... GRAFANA_TOKEN=glsa_... sh /tmp/prov.sh
set -eu

: "${PLEX_TOKEN:?set PLEX_TOKEN}"
: "${GRAFANA_TOKEN:?set GRAFANA_TOKEN}"
# Fail loudly here rather than 403-ing on the first vault call.
: "${VAULT_TOKEN:?set VAULT_TOKEN to a token that can write policies and auth roles (see header for how to run)}"
VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
export VAULT_ADDR VAULT_TOKEN

# --- 1. Secret: write once, preserve on re-run -------------------------------
# `vault kv get` fails for every reason a path can't be read, not just
# "the secret is absent" — unset/wrong VAULT_ADDR, a sealed Vault, or a token
# with write-but-not-read on this path would all land here too. Treating any
# of those as "absent" would silently overwrite live credentials, exactly
# what the header above promises doesn't happen. So capture the output and
# only treat it as "go ahead and write" when it's actually a missing-secret
# response; anything else aborts loudly instead of guessing.
get_out="$(vault kv get k8s-secrets/homepage 2>&1)" && get_status=0 || get_status=$?

if [ "$get_status" -eq 0 ]; then
  echo "k8s-secrets/homepage already exists — leaving values untouched."
elif printf '%s\n' "$get_out" | grep -qi 'no value found'; then
  vault kv put k8s-secrets/homepage \
    plex-token="$PLEX_TOKEN" \
    grafana-token="$GRAFANA_TOKEN"
  echo "wrote k8s-secrets/homepage"
else
  echo "ERROR: could not determine whether k8s-secrets/homepage exists (not proceeding, to avoid a silent overwrite)." >&2
  echo "  VAULT_ADDR=$VAULT_ADDR" >&2
  echo "$get_out" >&2
  exit 1
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
