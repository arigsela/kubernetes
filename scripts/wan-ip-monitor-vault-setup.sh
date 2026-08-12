#!/bin/sh
# wan-ip-monitor Vault setup — run INSIDE the Vault pod.
#
#   kubectl -n vault cp wan-ip-monitor-vault-setup.sh vault-0:/tmp/setup.sh
#   kubectl -n vault exec -it vault-0 -- sh /tmp/setup.sh
#   kubectl -n vault exec vault-0 -- rm -f /tmp/setup.sh      # afterwards
#
# Creates the three things base-apps/wan-ip-monitor needs:
#   1. ACL policy      wan-ip-monitor      (read one KV path, nothing else)
#   2. K8s auth role   wan-ip-monitor      (default SA in the wan-ip-monitor ns)
#   3. KV secret       k8s-secrets/wan-ip-monitor
#
# Idempotent: re-running overwrites the policy and role with identical values,
# and re-prompts for the secret. Safe to run twice.
#
# Secrets are read with `read` and never appear in argv, so they stay out of
# the process list. They DO land in Vault's audit log if one is enabled - that
# is expected and is where they belong.

set -eu

POLICY_NAME="wan-ip-monitor"
ROLE_NAME="wan-ip-monitor"
K8S_NAMESPACE="wan-ip-monitor"
K8S_SA="default"
KV_MOUNT="k8s-secrets"
KV_PATH="wan-ip-monitor"
AUTH_MOUNT="kubernetes"
TOKEN_TTL="24h"

: "${VAULT_ADDR:=http://127.0.0.1:8200}"
export VAULT_ADDR

say()  { printf '\n=== %s ===\n' "$1"; }
fail() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------- preflight --
say "Preflight"
command -v vault >/dev/null 2>&1 || fail "vault CLI not found - are you inside the Vault pod?"
printf 'VAULT_ADDR = %s\n' "$VAULT_ADDR"

vault status >/dev/null 2>&1 || fail "cannot reach Vault, or it is sealed. Run: vault status"

if ! vault token lookup >/dev/null 2>&1; then
  printf 'Not authenticated. Paste a token with permission to write policies\n'
  printf 'and auth roles (root, or an admin token), then press Enter.\n'
  printf 'Token (hidden): '
  stty -echo 2>/dev/null || true
  read -r VAULT_TOKEN
  stty echo 2>/dev/null || true
  printf '\n'
  export VAULT_TOKEN
  vault token lookup >/dev/null 2>&1 || fail "that token is not valid"
fi
printf 'Authenticated OK\n'

# Confirm the mounts this script assumes actually exist, rather than creating
# a policy that silently points at nothing.
#
# Listing mounts needs sys/mounts + sys/auth read, which a scoped identity may
# not have even when it CAN write policies and roles. So a denial here is a
# warning, not a failure - the real writes below will error clearly if a mount
# is genuinely wrong, and refusing to run over a missing read permission would
# be a false blocker.
if SECRETS_JSON=$(vault secrets list -format=json 2>/dev/null); then
  printf '%s' "$SECRETS_JSON" | grep -q "\"${KV_MOUNT}/\"" \
    || fail "KV mount '${KV_MOUNT}/' does not exist. Check: vault secrets list"
  printf "KV mount %s/ present\n" "$KV_MOUNT"
else
  printf "WARN: cannot list secrets mounts (no permission) - skipping that check\n"
fi

if AUTH_JSON=$(vault auth list -format=json 2>/dev/null); then
  printf '%s' "$AUTH_JSON" | grep -q "\"${AUTH_MOUNT}/\"" \
    || fail "auth mount '${AUTH_MOUNT}/' does not exist. Check: vault auth list"
  printf "auth mount %s/ present\n" "$AUTH_MOUNT"
else
  printf "WARN: cannot list auth mounts (no permission) - skipping that check\n"
fi

# ------------------------------------------------------------------ policy ---
# The /data/ segment is required: k8s-secrets is KV v2, where the API path for
# a secret at <mount>/<path> is <mount>/data/<path>. Omitting it is the single
# most common cause of a 403 that looks like a role problem.
say "1/3  ACL policy '${POLICY_NAME}'"
vault policy write "$POLICY_NAME" - <<EOF
path "${KV_MOUNT}/data/${KV_PATH}" {
  capabilities = ["read"]
}
EOF
printf 'Written. Effective policy:\n'
vault policy read "$POLICY_NAME"

# -------------------------------------------------------------------- role ---
say "2/3  Kubernetes auth role '${ROLE_NAME}'"
printf "Binding: serviceaccount '%s' in namespace '%s'\n" "$K8S_SA" "$K8S_NAMESPACE"
printf "(the namespace need not exist yet - Argo CD creates it on sync)\n"
vault write "auth/${AUTH_MOUNT}/role/${ROLE_NAME}" \
  bound_service_account_names="$K8S_SA" \
  bound_service_account_namespaces="$K8S_NAMESPACE" \
  policies="$POLICY_NAME" \
  ttl="$TOKEN_TTL" >/dev/null
printf 'Written. Effective role:\n'
vault read "auth/${AUTH_MOUNT}/role/${ROLE_NAME}"

# ------------------------------------------------------------------ secret ---
say "3/3  Secret '${KV_MOUNT}/${KV_PATH}'"

if vault kv get "${KV_MOUNT}/${KV_PATH}" >/dev/null 2>&1; then
  printf 'A secret already exists at this path.\n'
  printf 'Overwrite it? [y/N]: '
  read -r REPLY
  case "$REPLY" in
    y|Y) : ;;
    *) printf 'Left unchanged. Skipping to verification.\n'; SKIP_SECRET=1 ;;
  esac
fi

if [ "${SKIP_SECRET:-0}" != "1" ]; then
  printf '\nAWS access key id (Route 53 scoped): '
  read -r AWS_ID
  [ -n "$AWS_ID" ] || fail "AWS access key id cannot be empty"

  printf 'AWS secret access key (hidden): '
  stty -echo 2>/dev/null || true
  read -r AWS_SECRET
  stty echo 2>/dev/null || true
  printf '\n'
  [ -n "$AWS_SECRET" ] || fail "AWS secret access key cannot be empty"

  printf 'GitHub token, Contents+PRs read/write on arigsela/kubernetes (hidden): '
  stty -echo 2>/dev/null || true
  read -r GH_TOKEN
  stty echo 2>/dev/null || true
  printf '\n'
  [ -n "$GH_TOKEN" ] || fail "GitHub token cannot be empty"

  # Key names must match base-apps/wan-ip-monitor/external-secret.yaml exactly.
  vault kv put "${KV_MOUNT}/${KV_PATH}" \
    aws-access-key-id="$AWS_ID" \
    aws-secret-access-key="$AWS_SECRET" \
    github-token="$GH_TOKEN" >/dev/null

  unset AWS_ID AWS_SECRET GH_TOKEN
  printf 'Written.\n'
fi

# ------------------------------------------------------------- verification --
say "Verification"

printf 'Keys present (values not shown):\n'
vault kv get -format=json "${KV_MOUNT}/${KV_PATH}" \
  | tr ',' '\n' | grep -oE '"(aws-access-key-id|aws-secret-access-key|github-token)"' \
  | sort -u | sed 's/^/  /'

MISSING=0
for K in aws-access-key-id aws-secret-access-key github-token; do
  vault kv get -format=json "${KV_MOUNT}/${KV_PATH}" | grep -q "\"$K\"" || {
    printf 'MISSING KEY: %s\n' "$K"; MISSING=1; }
done
[ "$MISSING" -eq 0 ] || fail "one or more required keys are missing - re-run and overwrite"

printf '\nAll three objects exist:\n'
printf '  policy  %s\n' "$POLICY_NAME"
printf '  role    auth/%s/role/%s\n' "$AUTH_MOUNT" "$ROLE_NAME"
printf '  secret  %s/%s (3 keys)\n' "$KV_MOUNT" "$KV_PATH"

cat <<'NEXT'

Next, from your laptop (not this pod):

  # after PR #549 merges and Argo CD syncs
  kubectl -n wan-ip-monitor get externalsecret wan-ip-monitor
  # want: SecretSynced / True

  # dry run - DRY_RUN is still "true", so this writes nothing
  kubectl -n wan-ip-monitor create job --from=cronjob/wan-ip-monitor manual-check
  kubectl -n wan-ip-monitor logs job/manual-check

Expect five lines. Line 2 (route53: ... already on <ip>) proves the AWS key
works; line 3 (allow-list: declared=...) proves the GitHub token does. Only
after seeing both should you flip DRY_RUN to "false" in its own commit.

Then delete this script from the pod:
  kubectl -n vault exec vault-0 -- rm -f /tmp/setup.sh
NEXT
