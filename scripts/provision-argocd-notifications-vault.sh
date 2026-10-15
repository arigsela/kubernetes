#!/usr/bin/env bash
# Argo CD notifications — Vault provisioning. Idempotent; re-run to rotate the token.
#   k8s-secrets/argo-cd   slack-bot-token (xoxb-…, a COPY of the bot token n8n and the Slack
#                         bridge use: the same Slack app, its own path per consumer)
#   policy argo-cd        read k8s-secrets/data/argo-cd
#   role   argo-cd        kubernetes auth: SA eso-argo-cd in argo-cd, ttl 1h
#
#   kubectl port-forward -n vault svc/vault 8200:8200 &
#   export VAULT_ADDR=http://127.0.0.1:8200
#   vault login -method=oidc            # Dex; or export VAULT_TOKEN=<token>
#   scripts/provision-argocd-notifications-vault.sh     # prompts for the token (not echoed)
# Run it BEFORE merging base-apps/argo-cd/secret-store.yaml. After a rotation ESO re-syncs
# within 1h (or annotate the ExternalSecret with force-sync); the notifications controller
# reads the Secret on each send, no restart needed.
set -euo pipefail
MOUNT=k8s-secrets
KEY=argo-cd
: "${VAULT_ADDR:?set VAULT_ADDR (kubectl port-forward -n vault svc/vault 8200:8200; http://127.0.0.1:8200)}"
if ! vault token lookup >/dev/null 2>&1; then
  echo "not logged in to Vault: run 'vault login -method=oidc' or export VAULT_TOKEN" >&2
  exit 2
fi
read -r -s -p "Slack bot token (xoxb-…): " token </dev/tty; echo >&2
if [[ "$token" != xoxb-* ]]; then echo "expected a token starting with xoxb-" >&2; exit 2; fi
vault kv put -mount="$MOUNT" "$KEY" "slack-bot-token=$token" >/dev/null
echo "wrote $MOUNT/$KEY (slack-bot-token)"
vault policy write argo-cd - <<POLICY
path "$MOUNT/data/$KEY" {
  capabilities = ["read"]
}
POLICY
vault write auth/kubernetes/role/argo-cd \
  bound_service_account_names=eso-argo-cd \
  bound_service_account_namespaces=argo-cd \
  policies=argo-cd \
  ttl=1h >/dev/null
echo "policy argo-cd and kubernetes role argo-cd (SA eso-argo-cd in argo-cd) in place"
