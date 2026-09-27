#!/usr/bin/env bash
# install-authn-config.sh — install kube-apiserver's structured authentication config on the
# control plane (docs/plans/k8s-136-features-implementation-plan.md, Phase 5, Task 5.2)
#
#   install-authn-config.sh [--dry-run]
#
# Source of truth: node-config/k3s-control-01/{authn-config.yaml,config.yaml.d/10-authn.yaml}.
# Steps, each a gate for the next:
#   1. static guards: the `anonymous: {enabled: false}` block is present (k3s drops its own
#      --anonymous-auth=false when given a config file, and the Kubernetes default is
#      ENABLED); no --oidc-* flag (mutually exclusive with --authentication-config)
#   2. validate in a throwaway k3s of the cluster's version: the API must come up, load the
#      config, and answer an anonymous request with 401. A broken file stops here and never
#      reaches the node.
#   3. node preflight (read-only): no --oidc-* anywhere in the node's k3s config or unit
#   4. back up the node's current files, write the new ones to a temp name, rename into place
#   5. drop-in changed → restart k3s and wait for /readyz, ROLLING BACK automatically if the
#      API does not return. Only authn-config.yaml changed → no restart: the API server
#      hot-reloads it; wait for the reload counter (a bad file is ignored, not fatal).
#   6. post-check: anonymous is still denied (401) on the node
# --dry-run runs 1-3 and prints what 4-6 would do.
#
# Env: NODE_SSH (ssh command for the node), NODE_IP, AUTHN_SRC (source dir, for tests),
#      K3S_IMAGE, READY_TIMEOUT (seconds, default 240).
set -euo pipefail

usage() { sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "install-authn-config: $1" >&2; exit "${2:-2}"; }
# NOTE: never end a pipeline in `grep -q` here. It exits at the first match, the writer gets
# SIGPIPE, and under `set -o pipefail` the pipeline FAILS on a match: a refusal guard would
# pass exactly when it should fire. Count with `grep -c`, or grep a here-string.

DRY=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $a" ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${AUTHN_SRC:-$REPO_ROOT/node-config/k3s-control-01}"
NODE_IP="${NODE_IP:-10.0.1.50}"
NODE_SSH="${NODE_SSH:-ssh -i $HOME/.ssh/ari_sela_key -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=8 asela@$NODE_IP}"
K3S_IMAGE="${K3S_IMAGE:-rancher/k3s:v1.36.4-k3s1}"   # the cluster's version; bump with it
READY_TIMEOUT="${READY_TIMEOUT:-240}"
DEST=/etc/rancher/k3s
AUTHN=authn-config.yaml
DROPIN=config.yaml.d/10-authn.yaml

node() { $NODE_SSH "$@" </dev/null; }                 # read-only node commands
act() {                                               # node commands that change state
  if [ "$DRY" -eq 1 ]; then echo "  DRY: would run on node: $*"; else node "$@"; fi
}
sha() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

# ---- 1. static guards ------------------------------------------------------------------------
[ -f "$SRC/$AUTHN" ] && [ -f "$SRC/$DROPIN" ] || die "missing $SRC/$AUTHN or $SRC/$DROPIN"
python3 - "$SRC/$AUTHN" <<'PY' || die "static check failed (see above)"
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
assert d.get("apiVersion") == "apiserver.config.k8s.io/v1" and d.get("kind") == "AuthenticationConfiguration", \
    "not an apiserver.config.k8s.io/v1 AuthenticationConfiguration"
anon = d.get("anonymous")
if not (isinstance(anon, dict) and anon.get("enabled") is False):
    sys.exit("REFUSED: authn-config.yaml must carry `anonymous: {enabled: false}`. k3s stops "
             "passing --anonymous-auth=false when given this file, and the default is ENABLED.")
assert isinstance(d.get("jwt", []), list), "`jwt` must be a list"
PY
[ "$(grep -vE '^[[:space:]]*#' "$SRC/$DROPIN" | grep -c "oidc-" || true)" -eq 0 ] \
  || die "REFUSED: $DROPIN sets an --oidc-* flag (exclusive with --authentication-config)"
echo "1. static guards: ok"

# ---- 2. validate in a throwaway k3s ----------------------------------------------------------
VAL="install-authn-validate-$$"
# colima/Docker Desktop only share $HOME with the VM: stage the files there.
STAGE="$HOME/.cache/install-authn-config/$$"
cleanup() { docker rm -f "$VAL" >/dev/null 2>&1 || true; rm -rf "$STAGE"; }
trap cleanup EXIT
mkdir -p "$STAGE/config.yaml.d"
cp "$SRC/$AUTHN" "$STAGE/$AUTHN"; cp "$SRC/$DROPIN" "$STAGE/$DROPIN"
docker run -d --name "$VAL" --privileged -p 127.0.0.1::6443 \
  -v "$STAGE/$AUTHN:$DEST/$AUTHN:ro" -v "$STAGE/config.yaml.d:$DEST/config.yaml.d:ro" \
  "$K3S_IMAGE" server --disable traefik --disable metrics-server --disable local-storage \
  --disable servicelb >/dev/null || die "could not start the validation container" 3
up=0
for _ in $(seq 1 60); do
  [ "$(docker exec "$VAL" kubectl get --raw /readyz 2>/dev/null)" = ok ] && { up=1; break; }
  [ "$(docker inspect -f '{{.State.Running}}' "$VAL" 2>/dev/null)" = true ] || break
  sleep 2
done
if [ "$up" -ne 1 ]; then
  echo "--- validation k3s did not come up; last log lines:" >&2
  docker logs --tail 15 "$VAL" 2>&1 | grep -viE "^I[0-9]" | tail -8 >&2
  die "VALIDATION FAILED: the API server does not start with these files. Nothing changed on the node." 3
fi
METRICS="$(docker exec "$VAL" kubectl get --raw /metrics 2>/dev/null || true)"
grep -q '^apiserver_authentication_config_controller_last_config_info' <<< "$METRICS" \
  || die "VALIDATION FAILED: the API server did not load the authentication config. Nothing changed on the node." 3
PORT=$(docker port "$VAL" 6443/tcp | head -1 | sed 's/.*://')
anon=$(curl -sk -o /dev/null -w '%{http_code}' "https://127.0.0.1:$PORT/version" || true)
[ "$anon" = 401 ] || die "VALIDATION FAILED: anonymous /version answered $anon, want 401. Nothing changed on the node." 3
echo "2. validated in $K3S_IMAGE: API up, config loaded, anonymous → 401"

# ---- 3. node preflight (read-only) -----------------------------------------------------------
NODE_CFG="$(node "sudo cat $DEST/config.yaml $DEST/config.yaml.d/*.yaml 2>/dev/null; systemctl cat k3s 2>/dev/null" || true)"
[ -n "$NODE_CFG" ] || die "could not read the node's k3s config over ssh (NODE_SSH=$NODE_SSH)"
if [ "$(grep -vE '^[[:space:]]*#' <<< "$NODE_CFG" | grep -c -- "oidc-" || true)" -gt 0 ]; then
  die "REFUSED: the node's k3s config or unit already sets an --oidc-* flag"
fi
changed_authn=0; changed_dropin=0
[ "$(node "sudo sha256sum $DEST/$AUTHN 2>/dev/null | cut -d' ' -f1")" = "$(sha "$SRC/$AUTHN")" ] || changed_authn=1
[ "$(node "sudo sha256sum $DEST/$DROPIN 2>/dev/null | cut -d' ' -f1")" = "$(sha "$SRC/$DROPIN")" ] || changed_dropin=1
echo "3. node preflight: ok (changed: $AUTHN=$changed_authn $DROPIN=$changed_dropin)"
if [ "$changed_authn" -eq 0 ] && [ "$changed_dropin" -eq 0 ]; then echo "already installed; nothing to do"; exit 0; fi

metric() {  # metric <status> → current automatic_reloads_total{status=...}, summed
  kubectl get --raw /metrics 2>/dev/null \
    | awk -v s="status=\"$1\"" '/^apiserver_authentication_config_controller_automatic_reloads_total/ && index($0, s) {n += $NF} END {print n + 0}'
}
wait_ready() {
  for _ in $(seq 1 $((READY_TIMEOUT / 3))); do
    [ "$(kubectl get --raw /readyz 2>/dev/null)" = ok ] && return 0
    sleep 3
  done
  return 1
}

# Hot-reload path: take the counter baseline BEFORE installing. A file with no JWT issuers
# reloads in well under a second, i.e. before a post-install read: the rollback rehearsal
# (jwt: []) reported "no reload observed" for a reload that had already happened.
if [ "$changed_dropin" -eq 0 ] && [ "$DRY" -eq 0 ]; then
  before_ok=$(metric success); before_fail=$(metric failure)
fi

# ---- 4. back up, then install by rename ------------------------------------------------------
BK="$DEST/authn-backup/$(date -u +%Y%m%dT%H%M%SZ)"
act "sudo mkdir -p $BK/config.yaml.d $DEST/config.yaml.d && \
     { sudo cp -p $DEST/$AUTHN $BK/ 2>/dev/null || sudo touch $BK/.no-$AUTHN; } && \
     { sudo cp -p $DEST/$DROPIN $BK/config.yaml.d/ 2>/dev/null || sudo touch $BK/.no-10-authn.yaml; }"
install_file() {  # install_file <relpath>
  if [ "$DRY" -eq 1 ]; then echo "  DRY: would install $1 → $DEST/$1 (0600, via temp + rename)"; return; fi
  $NODE_SSH "sudo tee $DEST/$1.tmp >/dev/null && sudo chmod 0600 $DEST/$1.tmp && sudo mv $DEST/$1.tmp $DEST/$1" < "$SRC/$1"
}
[ "$changed_authn" -eq 1 ] && install_file "$AUTHN"
[ "$changed_dropin" -eq 1 ] && install_file "$DROPIN"
if [ "$DRY" -eq 1 ]; then echo "4. (dry run) would back up to $BK and install"; else echo "4. backed up to $BK; installed"; fi

# ---- 5. restart (drop-in changed) or hot reload (config only) --------------------------------
if [ "$changed_dropin" -eq 1 ]; then
  if [ "$DRY" -eq 1 ]; then echo "  DRY: would restart k3s and wait up to ${READY_TIMEOUT}s for /readyz (auto-rollback on timeout)"
  else
    echo "5. restarting k3s (control-plane blip; workloads keep running)..."
    t0=$(date +%s)
    node "sudo systemctl restart k3s" || true
    if wait_ready; then
      echo "   API ready after $(( $(date +%s) - t0 ))s"
    else
      echo "   API NOT ready after ${READY_TIMEOUT}s — ROLLING BACK from $BK" >&2
      node "sudo sh -c 'for f in $AUTHN $DROPIN; do if [ -e $BK/.no-\$(basename \$f) ]; then rm -f $DEST/\$f; else cp -p $BK/\$f $DEST/\$f; fi; done' && sudo systemctl restart k3s" || true
      wait_ready && die "rolled back; the API is back on the previous config" 4
      die "ROLLBACK DID NOT RESTORE THE API: fix on the node by hand ($BK)" 5
    fi
  fi
elif [ "$DRY" -eq 1 ]; then echo "  DRY: would wait up to 90s for the API server to hot-reload $AUTHN"
else
  echo "5. waiting for the hot reload (success counter was $before_ok before the install)..."
  for _ in $(seq 1 30); do
    [ "$(metric failure)" -gt "$before_fail" ] && die "the API server REJECTED the new file on reload (it keeps the old config). Check its log." 6
    [ "$(metric success)" -gt "$before_ok" ] && { echo "   reloaded (success counter $(metric success))"; break; }
    sleep 3
  done
  [ "$(metric success)" -gt "$before_ok" ] || die "no reload observed within 90s" 6
fi

# ---- 6. post-check ---------------------------------------------------------------------------
if [ "$DRY" -eq 1 ]; then echo "  DRY: would check anonymous https://$NODE_IP:6443/api → 401"; exit 0; fi
code=$(curl -sk -o /dev/null -w '%{http_code}' "${API_URL:-https://$NODE_IP:6443}/api" || true)
[ "$code" = 401 ] || die "POST-CHECK FAILED: anonymous /api answered $code, want 401" 7
echo "6. anonymous still denied (401). Done."
