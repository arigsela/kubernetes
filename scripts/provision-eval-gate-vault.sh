#!/usr/bin/env bash
# Evaluation gate — Vault provisioning (idempotent).
#   k8s-secrets/eval-gate   anthropic-api-key (the judge; may reuse kagent-anthropic's value),
#                           typesafe-api-key (Jev, optional), github-token (a fine-grained PAT,
#                           Commit statuses: read and write on arigsela/kubernetes, optional)
#   policy eval-gate        read k8s-secrets/data/eval-gate
#   role   eval-gate        kubernetes auth: SA eso-eval-gate in kagent, ttl 1h
# Values are read from the environment, never from this script's command line:
#   kubectl port-forward -n vault svc/vault 8200:8200 &
#   export VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=<token>
#   EVAL_GATE_ANTHROPIC_API_KEY=... EVAL_GATE_GITHUB_TOKEN=... scripts/provision-eval-gate-vault.sh
# A missing variable leaves that property untouched (so the script can be re-run per key).
set -euo pipefail
MOUNT=k8s-secrets
KEY=eval-gate
: "${VAULT_ADDR:?set VAULT_ADDR}" "${VAULT_TOKEN:?set VAULT_TOKEN}"
args=()
[[ -n "${EVAL_GATE_ANTHROPIC_API_KEY:-}" ]] && args+=("anthropic-api-key=$EVAL_GATE_ANTHROPIC_API_KEY")
[[ -n "${EVAL_GATE_TYPESAFE_API_KEY:-}" ]] && args+=("typesafe-api-key=$EVAL_GATE_TYPESAFE_API_KEY")
[[ -n "${EVAL_GATE_GITHUB_TOKEN:-}" ]] && args+=("github-token=$EVAL_GATE_GITHUB_TOKEN")
if ((${#args[@]})); then
  if vault kv get -mount="$MOUNT" "$KEY" >/dev/null 2>&1; then
    vault kv patch -mount="$MOUNT" "$KEY" "${args[@]}"
  else
    vault kv put -mount="$MOUNT" "$KEY" "${args[@]}"
  fi
fi
vault policy write eval-gate - <<POLICY
path "$MOUNT/data/$KEY" { capabilities = ["read"] }
POLICY
vault write auth/kubernetes/role/eval-gate \
  bound_service_account_names=eso-eval-gate \
  bound_service_account_namespaces=kagent \
  policies=eval-gate ttl=1h
echo "eval-gate: policy and role written; properties set: ${#args[@]}"
