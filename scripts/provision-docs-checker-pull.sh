#!/usr/bin/env bash
# docs-checker-pull: the pull-only ECR key Substrate's atelet uses for the docs-checker image (SPEC §T.129,
# §V.100; design and spike: docs/superpowers/specs/2026-10-18-substrate-ecr-pulls.md).
#   IAM user  system/docs-checker-pull       Terraform (terraform/roots/asela-cluster/docs-checker-pull-iam.tf):
#                                             pull of the docs-checker repository only. Its ACCESS KEY is made
#                                             here, never in Terraform, so the secret is not in the state bucket.
#   Vault     k8s-secrets/docs-checker-pull   aws-access-key-id, aws-secret-access-key, aws-region (the property
#                                             names of ecr-auth, which ecr-credentials-sync reads). The source of
#                                             truth. No Vault role reads it: the consumer is a host file, not a
#                                             Secret, so only an operator token does.
#   workers   /opt/ate-image-credential-provider/aws-credentials (root 0600), next to the pinned provider binary,
#             laid down by ansible/playbooks/ate-image-credentials.yml, which then checks the key from each worker.
#
# Needs VAULT_ADDR and a Vault token (vault login -method=oidc), the AWS CLI as an IAM admin (not this user),
# and ansible-playbook with SSH to the workers (ansible/README.md, "Install").
#
#   scripts/provision-docs-checker-pull.sh --check        preview the placement (ansible --check --diff); writes nothing
#   scripts/provision-docs-checker-pull.sh                create the key if Vault has none, then place what Vault holds
#   scripts/provision-docs-checker-pull.sh --limit HOST   place on one worker only (a new or rebuilt one)
#   scripts/provision-docs-checker-pull.sh --rotate       add a second key, store it, place and verify it; the old one stays
#   scripts/provision-docs-checker-pull.sh --retire-old   delete every key of the user except Vault's
#
# Rotation takes two runs on purpose: a key is deleted only after its successor has worked on every worker.
# After --retire-old, `kubectl -n ate-system rollout restart ds/atelet` drops the registry tokens atelet cached
# from the old key (it keeps them for up to ~6 h).
#
# Order on first install: after Atlantis has applied the IAM user, BEFORE merging the substrate.yaml change.
# atelet does not start on a worker without the directory.
#
# Secrets never reach a command line or the output. The AWS CLI's answer goes through a pipe into python, which
# builds the JSON for `vault kv put … -` (read from stdin). Ansible gets the key in its environment. If Vault
# refuses the write, the key just created is deleted again, so no key is left whose secret nobody holds.
set -euo pipefail
MOUNT=k8s-secrets
KEY=docs-checker-pull
IAM_USER=docs-checker-pull
REGION=us-east-2
PLAYBOOK=playbooks/ate-image-credentials.yml
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

mode=ensure; check=0; limit=""
while (($#)); do
  case "$1" in
    --rotate|--retire-old)
      if [[ $mode != ensure ]]; then echo "--rotate and --retire-old are separate runs" >&2; exit 2; fi
      mode=${1#--}; shift ;;
    --check) check=1; shift ;;
    --limit)
      if (($# < 2)); then echo "--limit needs a host" >&2; exit 2; fi
      limit=$2; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
if ((check)) && [[ $mode != ensure ]]; then
  echo "--check previews the placement only; it does not combine with --$mode" >&2; exit 2
fi

: "${VAULT_ADDR:?set VAULT_ADDR (https://vault.arigsela.com, or a port-forward on http://127.0.0.1:8200)}"
if ! vault token lookup >/dev/null 2>&1; then
  echo "not logged in to Vault: run 'vault login -method=oidc' or export VAULT_TOKEN" >&2
  exit 2
fi
if ! aws iam get-user --user-name "$IAM_USER" >/dev/null 2>&1; then
  echo "cannot read IAM user $IAM_USER: apply the Terraform first (atlantis apply on the PR), and run the AWS CLI as an IAM admin" >&2
  exit 2
fi

# The stored key as Vault's JSON; nothing when absent. Any other error stops the run, so a transient failure
# can never look like "absent" and mint a key.
vault_json() {
  local out
  if out=$(vault kv get -mount="$MOUNT" -format=json "$KEY" 2>&1); then
    printf '%s' "$out"
  elif grep -qi "no value found" <<<"$out"; then
    :
  else
    echo "cannot read $MOUNT/$KEY (and it is not a 'not found'): ${out%%$'\n'*}" >&2
    exit 2
  fi
}

prop() {  # prop <name>: that property of the Vault JSON on stdin
  python3 -c 'import json, sys; print(json.load(sys.stdin)["data"]["data"].get(sys.argv[1], ""))' "$1"
}

iam_keys() {  # one "<id> <status>" line per access key of the user
  aws iam list-access-keys --user-name "$IAM_USER" --output json |
    python3 -c 'import json, sys; [print(k["AccessKeyId"], k["Status"]) for k in json.load(sys.stdin)["AccessKeyMetadata"]]'
}

is_active() { grep -qx "$1 Active" <<<"$keys"; }

create_and_store() {
  local created new_id
  created=$(aws iam create-access-key --user-name "$IAM_USER" --output json)
  new_id=$(printf '%s' "$created" | python3 -c 'import json, sys; print(json.load(sys.stdin)["AccessKey"]["AccessKeyId"])')
  if ! printf '%s' "$created" |
      python3 -c 'import json, sys; k = json.load(sys.stdin)["AccessKey"]; print(json.dumps({"aws-access-key-id": k["AccessKeyId"], "aws-secret-access-key": k["SecretAccessKey"], "aws-region": sys.argv[1]}))' "$REGION" |
      vault kv put -mount="$MOUNT" "$KEY" - >/dev/null; then
    echo "Vault refused the write: deleting the new key $new_id, so no key is left whose secret nobody holds" >&2
    aws iam delete-access-key --user-name "$IAM_USER" --access-key-id "$new_id" ||
      echo "could NOT delete $new_id: aws iam delete-access-key --user-name $IAM_USER --access-key-id $new_id" >&2
    exit 1
  fi
  echo "created key $new_id and stored it in $MOUNT/$KEY"
}

place() {  # what Vault holds, onto the workers
  local stored id secret dry="" args=("$PLAYBOOK")
  stored=$(vault_json)
  if [[ -z $stored ]]; then echo "nothing in $MOUNT/$KEY to place" >&2; exit 2; fi
  id=$(printf '%s' "$stored" | prop aws-access-key-id)
  secret=$(printf '%s' "$stored" | prop aws-secret-access-key)
  if ((check)); then args+=(--check --diff); dry=" (dry run)"; fi
  if [[ -n $limit ]]; then args+=(--limit "$limit"); fi
  echo "placing key $id on the workers${limit:+ (only $limit)}$dry"
  if ! (cd "$REPO/ansible" &&
        DOCS_CHECKER_PULL_AWS_ACCESS_KEY_ID="$id" DOCS_CHECKER_PULL_AWS_SECRET_ACCESS_KEY="$secret" \
        ansible-playbook "${args[@]}"); then
    echo "placement failed. Vault holds key $id; fix the cause and re-run without options (it places what Vault holds)." >&2
    exit 1
  fi
}

stored=$(vault_json)
keys=$(iam_keys)
vault_id=""
if [[ -n $stored ]]; then vault_id=$(printf '%s' "$stored" | prop aws-access-key-id); fi

case $mode in
  retire-old)
    if [[ -z $vault_id ]] || ! is_active "$vault_id"; then
      echo "Vault's key ${vault_id:-(none)} is not an active key of $IAM_USER: nothing retired. Run without options first." >&2
      exit 2
    fi
    others=$(awk -v keep="$vault_id" '$1 != "" && $1 != keep { print $1 }' <<<"$keys")
    if [[ -z $others ]]; then echo "only Vault's key $vault_id exists: nothing to retire"; exit 0; fi
    for id in $others; do
      aws iam delete-access-key --user-name "$IAM_USER" --access-key-id "$id"
      echo "deleted key $id"
    done
    echo "next: kubectl -n ate-system rollout restart ds/atelet (drops the tokens atelet cached from the old key)"
    ;;
  rotate)
    if (($(grep -c . <<<"$keys" || true) >= 2)); then
      echo "$IAM_USER already has two keys ($(awk '{ print $1 }' <<<"$keys" | paste -sd, -)): run --retire-old first" >&2
      exit 2
    fi
    old=$(awk '{ print $1 }' <<<"$keys" | paste -sd, -)
    create_and_store
    place
    echo "rotated: Vault and the workers hold the new key${old:+; the old key $old is still active}."
    echo "next: scripts/provision-docs-checker-pull.sh --retire-old, then kubectl -n ate-system rollout restart ds/atelet"
    ;;
  ensure)
    if [[ -n $vault_id ]]; then
      if ! is_active "$vault_id"; then
        echo "Vault's key $vault_id is not an active key of $IAM_USER: make a new one with --rotate" >&2
        exit 2
      fi
    elif ((check)); then
      echo "nothing in $MOUNT/$KEY yet: run without --check first (that creates the key)" >&2
      exit 2
    elif [[ -n $keys ]]; then
      echo "$IAM_USER has keys Vault does not know ($(awk '{ print $1 }' <<<"$keys" | paste -sd, -)); their secrets are lost." >&2
      echo "Delete them (aws iam delete-access-key --user-name $IAM_USER --access-key-id <id>) and re-run." >&2
      exit 2
    else
      create_and_store
    fi
    place
    ;;
esac
