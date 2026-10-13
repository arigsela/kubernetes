#!/usr/bin/env bash
# Evaluation gate — Vault provisioning (idempotent, safe to re-run).
#   k8s-secrets/eval-gate   anthropic-api-key (the judge), typesafe-api-key (Jev, optional),
#                           github-token (a fine-grained PAT, Commit statuses: read and write
#                           on arigsela/kubernetes, optional)
#   policy eval-gate        read k8s-secrets/data/eval-gate
#   role   eval-gate        kubernetes auth: SA eso-eval-gate in kagent, ttl 1h
#
# Values never pass on a command line. Either copy them INSIDE Vault from the keys that
# already hold them, or read them from the environment:
#   kubectl port-forward -n vault svc/vault 8200:8200 &
#   export VAULT_ADDR=http://127.0.0.1:8200
#   vault login -method=oidc            # Dex; or export VAULT_TOKEN=<token>
#   scripts/provision-eval-gate-vault.sh --copy-anthropic --copy-typesafe
#   EVAL_GATE_GITHUB_TOKEN=<pat> scripts/provision-eval-gate-vault.sh      # later, per key
# --copy-anthropic reads k8s-secrets/kagent-anthropic (anthropic-api-key); --copy-typesafe
# reads k8s-secrets/agent-audit-risk (typesafe-api-key). An unset value leaves that property
# untouched, so the script can be re-run for one key at a time.
set -euo pipefail
MOUNT=k8s-secrets
KEY=eval-gate
copy_anthropic=0
copy_typesafe=0
for arg in "$@"; do
  case "$arg" in
    --copy-anthropic) copy_anthropic=1 ;;
    --copy-typesafe) copy_typesafe=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
: "${VAULT_ADDR:?set VAULT_ADDR (kubectl port-forward -n vault svc/vault 8200:8200; http://127.0.0.1:8200)}"
if ! vault token lookup >/dev/null 2>&1; then
  echo "not logged in to Vault: run 'vault login -method=oidc' or export VAULT_TOKEN" >&2
  exit 2
fi
anthropic="${EVAL_GATE_ANTHROPIC_API_KEY:-}"
typesafe="${EVAL_GATE_TYPESAFE_API_KEY:-}"
github="${EVAL_GATE_GITHUB_TOKEN:-}"
if ((copy_anthropic)); then
  anthropic=$(vault kv get -mount="$MOUNT" -field=anthropic-api-key kagent-anthropic)
fi
if ((copy_typesafe)); then
  typesafe=$(vault kv get -mount="$MOUNT" -field=typesafe-api-key agent-audit-risk)
fi
args=()
[[ -n "$anthropic" ]] && args+=("anthropic-api-key=$anthropic")
[[ -n "$typesafe" ]] && args+=("typesafe-api-key=$typesafe")
[[ -n "$github" ]] && args+=("github-token=$github")
if ((${#args[@]})); then
  if vault kv get -mount="$MOUNT" "$KEY" >/dev/null 2>&1; then
    vault kv patch -mount="$MOUNT" "$KEY" "${args[@]}" >/dev/null
  else
    vault kv put -mount="$MOUNT" "$KEY" "${args[@]}" >/dev/null
  fi
fi
vault policy write eval-gate - <<POLICY
path "$MOUNT/data/$KEY" { capabilities = ["read"] }
POLICY
vault write auth/kubernetes/role/eval-gate \
  bound_service_account_names=eso-eval-gate \
  bound_service_account_namespaces=kagent \
  policies=eval-gate ttl=1h >/dev/null
present=$(vault kv get -mount="$MOUNT" -format=json "$KEY" 2>/dev/null | python3 -c 'import json,sys; print(",".join(sorted(json.load(sys.stdin)["data"]["data"])))' 2>/dev/null || echo none)
echo "eval-gate: policy and role written; properties set this run: ${#args[@]}; present now: $present"
