#!/usr/bin/env bash
# kagent 1.0 + Agent Substrate — Vault provisioning (idempotent, safe to re-run)
#
# Creates the Vault side of base-apps/substrate/, base-apps/postgresql/init-{kagent-v1,substrate}-db.yaml
# and base-apps/kagent/kagent-postgres-{owner,readwrite}-external-secret.yaml:
#
#   k8s-secrets/kagent-db     EXISTING key (kagent 0.10's). Adds v1-db-name, v1-user, v1-password,
#                             v1-readwrite-user, v1-readwrite-password (kagent >= alpha14, kagent#3160).
#                             The 0.10 properties are never touched. No new role: the existing
#                             `kagent-db` role/policy already reads this key from kagent + postgresql.
#   k8s-secrets/substrate-db  db-name, owner-user, owner-password, readwrite-user, readwrite-password
#   k8s-secrets/substrate-cas the seven CA/JWT pools `kagent install` would create
#                             (go/core/cli/internal/commands/substrate_init.go @ kagent v1.0.0-alpha11):
#                               actor-id-jwt-pool                          ES256 JWT pool (JSON)
#                               actor-id-ca-pool{,-crt,-key}               ED25519 CA
#                               egress-mitm-ca-pool{,-crt,-key}            ECDSA P-256 CA
#                               service-dns-ca-pool{,-crt,-key}            ED25519 CA
#                               pod-identity-ca-pool{,-crt,-key}           ED25519 CA
#                               postgres-ca-pool{,-crt,-key}               ED25519 CA
#                             <pool> = the `pool` JSON, -crt = PEM CERTIFICATE, -key = PEM PKCS#8
#                             "PRIVATE KEY". actor-id-ca-certs' ca.crt is actor-id-ca-pool-crt.
#   policy substrate          read k8s-secrets/data/substrate-db and k8s-secrets/data/substrate-cas
#   role   substrate          kubernetes auth: SA eso-substrate in ate-system,
#                             podcertificate-controller-system, postgresql; ttl 1h
#
# SAFE TO RE-RUN: every value is generated ONCE and preserved afterwards; only missing properties
# are written. Passwords are 64 hex characters (URL-safe: the ExternalSecrets template them into
# connection strings unescaped).
#
# Formats (checked against the Go source, not guessed): each CA is a fresh key and a self-signed
# certificate, 365 days, basicConstraints critical CA:TRUE, keyUsage critical
# digitalSignature+keyCertSign, as substrate_init.go's newCAPoolSecretData. Its `pool` is Go's
# json.Marshal of {"CAs":[{"ID":"1","SigningKeyPKCS8":<b64>,"RootCertificateDER":<b64>}],
# "ActiveForSigning":"1"}; the JWT pool is {"Authorities":[{"ID":"1","Algorithm":"ES256",
# "SigningKeyPKCS8":<b64>}],"ActiveForSigning":"1"} (Substrate internal/localca and
# internal/localjwtauthority read these). The CLI cannot print them instead of creating Secrets
# (`kagent install` and `kubectl ate admin make-ca-pool` both write straight to the cluster), so
# this script generates the same material with openssl. Only the certificate CN differs: the CLI
# uses a random token, this uses "<pool>-<random>" so `openssl x509 -subject` says which CA it is.
#
# Usage (from a workstation: needs openssl 3, python3 and the vault CLI; the vault-0 pod has no
# openssl or python):
#
#   scripts/provision-kagent-1-0-vault.sh --dry-run               # offline: no Vault calls, prints
#                                                                 # the plan with secrets redacted
#   kubectl port-forward -n vault svc/vault 8200:8200 &
#   export VAULT_ADDR=http://127.0.0.1:8200
#   export VAULT_TOKEN=<token allowed to write k8s-secrets/*, sys/policies, auth/kubernetes/role/*>
#   scripts/provision-kagent-1-0-vault.sh
#   unset VAULT_TOKEN
#
#   scripts/provision-kagent-1-0-vault.sh --rotate-cas            # 30-day expiry alert fired:
#                                                                 # replace the five CAs, then follow
#                                                                 # base-apps/substrate/runbook.md
#
# Private keys are generated in a mode-0700 temporary directory that is deleted on exit, and are
# handed to vault as @file arguments, never on a command line. --keep DIR (with --dry-run) keeps
# the generated material for inspection instead; delete it afterwards.
set -euo pipefail

MOUNT="k8s-secrets"
KAGENT_PATH="kagent-db"
SUBSTRATE_DB_PATH="substrate-db"
SUBSTRATE_CAS_PATH="substrate-cas"
POLICY="substrate"
ROLE="substrate"
ESO_SA="eso-substrate"
ESO_NAMESPACES="ate-system,podcertificate-controller-system,postgresql"
CA_DAYS=365
# name:keytype — the order and key types of substrate_init.go's ensureSubstratePrerequisites.
CA_POOLS="actor-id-ca-pool:ed25519 egress-mitm-ca-pool:p256 service-dns-ca-pool:ed25519 pod-identity-ca-pool:ed25519 postgres-ca-pool:ed25519"
JWT_POOL="actor-id-jwt-pool"

DRY_RUN=0
ROTATE=0
KEEP=""

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --rotate-cas) ROTATE=1 ;;
    --keep) KEEP="${2:?--keep needs a directory}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done
if [ -n "$KEEP" ] && [ "$DRY_RUN" -ne 1 ]; then
  echo "ERROR: --keep only goes with --dry-run (real key material is never left on disk)." >&2
  exit 2
fi

# --- pre-checks ----------------------------------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: $1 not found. $2" >&2; exit 1; }; }
need openssl "Install OpenSSL 3 (macOS: brew install openssl@3, and put it first on PATH)."
need python3 "Python 3 (standard library only) builds the pool JSON."
case "$(openssl version)" in
  OpenSSL\ 3.*) ;;
  *) echo "ERROR: $(openssl version): need OpenSSL 3.x for Ed25519 (LibreSSL will not do)." >&2; exit 1 ;;
esac

if [ "$DRY_RUN" -eq 0 ]; then
  need vault "Install the Vault CLI."
  if [ -z "${VAULT_TOKEN:-}" ]; then
    echo "ERROR: VAULT_TOKEN is not set (see the usage header)." >&2
    exit 1
  fi
  export VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
  vault token lookup >/dev/null 2>&1 || {
    echo "ERROR: cannot authenticate to $VAULT_ADDR with VAULT_TOKEN (port-forward running?)." >&2
    exit 1
  }
fi

umask 077
WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

# --- generators ----------------------------------------------------------------------------
gen_password() { openssl rand -hex 32 | tr -d '\n' >"$1"; }

gen_key() { # gen_key <ed25519|p256> <out.pem>
  case "$1" in
    ed25519) openssl genpkey -algorithm ED25519 -out "$2" 2>/dev/null ;;
    p256) openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 \
            -pkeyopt ec_param_enc:named_curve -out "$2" 2>/dev/null ;;
    *) echo "BUG: key type $1" >&2; exit 1 ;;
  esac
}

pool_json() { # pool_json ca <key.der> <crt.der> <out> | pool_json jwt <key.der> <out>
  python3 -I - "$@" <<'PY'
import base64, json, sys
kind = sys.argv[1]
b64 = lambda p: base64.b64encode(open(p, "rb").read()).decode()
if kind == "ca":
    key, crt, out = sys.argv[2:5]
    pool = {"CAs": [{"ID": "1", "SigningKeyPKCS8": b64(key), "RootCertificateDER": b64(crt)}],
            "ActiveForSigning": "1"}
else:
    key, out = sys.argv[2:4]
    pool = {"Authorities": [{"ID": "1", "Algorithm": "ES256", "SigningKeyPKCS8": b64(key)}],
            "ActiveForSigning": "1"}
# Go's json.Marshal: struct field order, no whitespace, no trailing newline.
open(out, "w").write(json.dumps(pool, separators=(",", ":")))
PY
}

gen_ca() { # gen_ca <name> <ed25519|p256>  ->  $WORK/<name>{.json,-crt,-key}
  local name="$1" type="$2" d="$WORK/$1"
  mkdir -p "$d"
  gen_key "$type" "$d/key.pem"
  cat >"$d/req.cnf" <<EOF
[req]
distinguished_name = dn
prompt = no
x509_extensions = ca
[dn]
CN = ${name}-$(openssl rand -hex 8)
[ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,digitalSignature,keyCertSign
subjectKeyIdentifier = hash
EOF
  openssl req -x509 -new -key "$d/key.pem" -config "$d/req.cnf" -days "$CA_DAYS" -out "$d/crt.pem" 2>/dev/null
  openssl pkey -in "$d/key.pem" -outform DER -out "$d/key.der"
  openssl x509 -in "$d/crt.pem" -outform DER -out "$d/crt.der"
  pool_json ca "$d/key.der" "$d/crt.der" "$WORK/$name.json"
  cp "$d/crt.pem" "$WORK/$name-crt"
  cp "$d/key.pem" "$WORK/$name-key"
  self_check_ca "$name" "$type"
}

gen_jwt_pool() { # -> $WORK/actor-id-jwt-pool.json
  local d="$WORK/$JWT_POOL"
  mkdir -p "$d"
  gen_key p256 "$d/key.pem"
  openssl pkey -in "$d/key.pem" -outform DER -out "$d/key.der"
  pool_json jwt "$d/key.der" "$WORK/$JWT_POOL.json"
}

# Fail before anything reaches Vault if the material is not what Substrate parses.
self_check_ca() {
  local name="$1" type="$2" d="$WORK/$1" want
  case "$type" in ed25519) want="ED25519" ;; p256) want="id-ecPublicKey" ;; esac
  openssl x509 -in "$d/crt.pem" -noout -text | grep -q "Public Key Algorithm: $want" \
    || { echo "SELF-CHECK FAILED: $name is not a $want certificate" >&2; exit 1; }
  openssl x509 -in "$d/crt.pem" -noout -text | grep -q "CA:TRUE" \
    || { echo "SELF-CHECK FAILED: $name is not a CA" >&2; exit 1; }
  [ "$(openssl pkey -in "$d/key.pem" -pubout)" = "$(openssl x509 -in "$d/crt.pem" -noout -pubkey)" ] \
    || { echo "SELF-CHECK FAILED: $name key does not match its certificate" >&2; exit 1; }
  grep -q -- "-----BEGIN PRIVATE KEY-----" "$d/key.pem" \
    || { echo "SELF-CHECK FAILED: $name key is not PKCS#8" >&2; exit 1; }
}

describe_ca() { # one redacted line about a generated CA
  local d="$WORK/$1"
  printf '%s, %s, notAfter %s, sha256 %s' \
    "$(openssl x509 -in "$d/crt.pem" -noout -subject | sed 's/^subject=//')" \
    "$(openssl x509 -in "$d/crt.pem" -noout -text | sed -n 's/^ *Public Key Algorithm: //p' | head -1)" \
    "$(openssl x509 -in "$d/crt.pem" -noout -enddate | cut -d= -f2)" \
    "$(openssl x509 -in "$d/crt.pem" -noout -fingerprint -sha256 | cut -d= -f2 | cut -c1-23)…"
}

# --- Vault helpers (never called in --dry-run) ---------------------------------------------
kv_exists() { vault kv get -mount="$MOUNT" "$1" >/dev/null 2>&1; }
kv_has() { vault kv get -mount="$MOUNT" -field="$2" "$1" >/dev/null 2>&1; }

# Pending writes for one path: "prop=@file" arguments, all to the same key.
PENDING=()
pend() { PENDING+=("$1=@$2"); echo "    + $1  ($3)"; }
flush() { # flush <path>
  local path="$1"
  if [ "${#PENDING[@]}" -eq 0 ]; then
    echo "    (nothing to write: every property already present)"
    return
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "    -> would run: vault kv $([ "$2" = new ] && echo put || echo patch) -mount=$MOUNT $path <${#PENDING[@]} properties from files>"
  elif [ "$2" = new ]; then
    vault kv put -mount="$MOUNT" "$path" "${PENDING[@]}" >/dev/null
    echo "    -> wrote $MOUNT/$path (${#PENDING[@]} properties)"
  else
    vault kv patch -mount="$MOUNT" "$path" "${PENDING[@]}" >/dev/null
    echo "    -> patched $MOUNT/$path (${#PENDING[@]} properties)"
  fi
  PENDING=()
}
# have <path> <prop>: in --dry-run nothing is assumed to exist (first-run plan).
have() { [ "$DRY_RUN" -eq 0 ] && kv_has "$1" "$2"; }
literal() { printf '%s' "$2" >"$WORK/lit-$1"; echo "$WORK/lit-$1"; }

[ "$DRY_RUN" -eq 1 ] && echo "DRY RUN — no Vault calls; the plan assumes nothing exists yet. Secrets are redacted." && echo

# --- 1. kagent 1.0 database identity on the EXISTING kagent-db key -------------------------
echo "==> $MOUNT/$KAGENT_PATH (kagent 1.0 database kagent_v1; 0.10 properties untouched)"
if [ "$DRY_RUN" -eq 0 ] && ! kv_exists "$KAGENT_PATH"; then
  echo "ERROR: $MOUNT/$KAGENT_PATH does not exist. It is kagent 0.10's key and must already be there;" >&2
  echo "       refusing to create it (the ExternalSecrets would then resolve against a new, empty key)." >&2
  exit 1
fi
have "$KAGENT_PATH" v1-db-name || pend v1-db-name "$(literal kv1db kagent_v1)" "kagent_v1"
have "$KAGENT_PATH" v1-user || pend v1-user "$(literal kv1user kagent_v1_user)" "kagent_v1_user"
if ! have "$KAGENT_PATH" v1-password; then
  gen_password "$WORK/kagent-v1-password"
  pend v1-password "$WORK/kagent-v1-password" "<64 hex, generated, redacted>"
fi
# kagent ≥ 1.0.0-alpha14 (kagent#3160): a second, read/write login beside the owner login.
have "$KAGENT_PATH" v1-readwrite-user || pend v1-readwrite-user "$(literal kv1rw kagent_v1_readwrite_user)" "kagent_v1_readwrite_user"
if ! have "$KAGENT_PATH" v1-readwrite-password; then
  gen_password "$WORK/kagent-v1-readwrite-password"
  pend v1-readwrite-password "$WORK/kagent-v1-readwrite-password" "<64 hex, generated, redacted>"
fi
flush "$KAGENT_PATH" existing

# --- 2. Substrate database identity -------------------------------------------------------
echo "==> $MOUNT/$SUBSTRATE_DB_PATH (database substrate, password auth)"
SDB_STATE=new
[ "$DRY_RUN" -eq 0 ] && kv_exists "$SUBSTRATE_DB_PATH" && SDB_STATE=existing
have "$SUBSTRATE_DB_PATH" db-name || pend db-name "$(literal sdb substrate)" "substrate"
have "$SUBSTRATE_DB_PATH" owner-user || pend owner-user "$(literal sowner substrate_owner_user)" "substrate_owner_user"
have "$SUBSTRATE_DB_PATH" readwrite-user || pend readwrite-user "$(literal srw substrate_readwrite_user)" "substrate_readwrite_user"
for p in owner-password readwrite-password; do
  if ! have "$SUBSTRATE_DB_PATH" "$p"; then
    gen_password "$WORK/substrate-$p"
    pend "$p" "$WORK/substrate-$p" "<64 hex, generated, redacted>"
  fi
done
flush "$SUBSTRATE_DB_PATH" "$SDB_STATE"

# --- 3. Substrate CA and JWT pools ---------------------------------------------------------
echo "==> $MOUNT/$SUBSTRATE_CAS_PATH (Substrate CA/JWT pools; CAs valid $CA_DAYS days)"
CAS_STATE=new
[ "$DRY_RUN" -eq 0 ] && kv_exists "$SUBSTRATE_CAS_PATH" && CAS_STATE=existing
if [ "$ROTATE" -eq 1 ] && [ "$CAS_STATE" = new ] && [ "$DRY_RUN" -eq 0 ]; then
  echo "ERROR: --rotate-cas but $MOUNT/$SUBSTRATE_CAS_PATH does not exist yet; run without it first." >&2
  exit 1
fi
for entry in $CA_POOLS; do
  name="${entry%%:*}" type="${entry##*:}"
  if [ "$ROTATE" -eq 1 ] || ! { have "$SUBSTRATE_CAS_PATH" "$name" && have "$SUBSTRATE_CAS_PATH" "$name-crt" \
                                && have "$SUBSTRATE_CAS_PATH" "$name-key"; }; then
    gen_ca "$name" "$type"
    pend "$name" "$WORK/$name.json" "pool JSON: 1 CA, ID 1, ActiveForSigning 1; key + cert redacted"
    pend "$name-crt" "$WORK/$name-crt" "$(describe_ca "$name")"
    pend "$name-key" "$WORK/$name-key" "PEM PKCS#8 private key, redacted"
  else
    echo "    = $name (present, kept)"
  fi
done
if ! have "$SUBSTRATE_CAS_PATH" "$JWT_POOL"; then
  # Not rotated by --rotate-cas: a bare signing key with no expiry; replacing it would only
  # invalidate every actor identity token in flight.
  gen_jwt_pool
  pend "$JWT_POOL" "$WORK/$JWT_POOL.json" "pool JSON: 1 authority, ID 1, ES256 P-256 key redacted"
else
  echo "    = $JWT_POOL (present, kept)"
fi
flush "$SUBSTRATE_CAS_PATH" "$CAS_STATE"

# --- 4. Policy and kubernetes-auth role -----------------------------------------------------
POLICY_HCL="path \"$MOUNT/data/$SUBSTRATE_DB_PATH\" { capabilities = [\"read\"] }
path \"$MOUNT/data/$SUBSTRATE_CAS_PATH\" { capabilities = [\"read\"] }"
echo "==> policy $POLICY"
printf '%s\n' "$POLICY_HCL" | sed 's/^/    /'
echo "==> role auth/kubernetes/role/$ROLE (SA $ESO_SA @ $ESO_NAMESPACES, policy $POLICY, ttl 1h)"
if [ "$DRY_RUN" -eq 0 ]; then
  printf '%s\n' "$POLICY_HCL" | vault policy write "$POLICY" - >/dev/null
  vault write "auth/kubernetes/role/$ROLE" \
    bound_service_account_names="$ESO_SA" \
    bound_service_account_namespaces="$ESO_NAMESPACES" \
    policies="$POLICY" ttl=1h >/dev/null
  echo "    -> written"
else
  echo "    -> would run: vault policy write $POLICY - ; vault write auth/kubernetes/role/$ROLE ..."
fi

if [ -n "$KEEP" ]; then
  mkdir -p "$KEEP"
  cp -R "$WORK"/. "$KEEP"/
  echo
  echo "Kept the generated (throwaway, never stored) material in $KEEP — delete it when done."
fi

echo
if [ "$DRY_RUN" -eq 1 ]; then
  echo "Dry run complete. Nothing was written."
elif [ "$ROTATE" -eq 1 ]; then
  echo "CAs replaced in Vault. ESO picks them up within its 1h refresh; then restart in the order"
  echo "given in base-apps/substrate/runbook.md (\"Rotate the Substrate CAs\")."
else
  echo "Done. ExternalSecrets resolve on their next refresh (1h), or force one:"
  echo "  kubectl annotate externalsecret -n <ns> <name> force-sync=\$(date +%s) --overwrite"
fi
