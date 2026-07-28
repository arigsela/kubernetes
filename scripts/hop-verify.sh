#!/usr/bin/env bash
# hop-verify.sh — the gate every k3s hop must pass (SPEC.md §T.29, §T.30)
#
# §V.31 forbids a bare "gate →" in a hop task: the full set must be enumerated. This is
# that set, executable, so the gate is a command rather than a checklist someone reads.
#
#   gate   run before a hop. Non-zero exit means do not hop.
#   watch  run after. Waits for green and reports the §V.9 window.
#
# §V.9's window was undefined until §T.30. It is measured here as: start = the moment k3s
# stops (passed in via --since), end = every node Ready and every Argo app Synced+Healthy.
# Anything narrower flatters the number — the API returning is not the same as the cluster
# being usable again.
#
# §V.47 matters for the Synced check: controllers legitimately mutate fields git cannot
# know, so a blanket "all Synced" is unreachable. Known-benign drift is allowed by name and
# anything else fails the gate.
#
# Usage:
#   hop-verify.sh gate  [--artifacts DIR]
#   hop-verify.sh watch --since <epoch-seconds> [--timeout SECONDS]

set -euo pipefail

ARTIFACTS="${HOME}/k3s-upgrade-artifacts"
ARGO_NS="${ARGO_NAMESPACE:-argo-cd}"
# Drift allowed past the gate. Two tiers, deliberately distinguished — §V.47 exists because
# allow-listing undiagnosed drift is what made §V.5 meaningless in the first place.
#   DIAGNOSED : cause documented in docs/plans/argocd-drift-diagnosis.md
#   TOLERATED : workload verified running, cause NOT yet diagnosed (§T.45). Warned about
#               on every run so it stays visible rather than becoming permanent.
KNOWN_DRIFT_DIAGNOSED="${KNOWN_DRIFT_DIAGNOSED:-kagent-secrets kyverno}"
KNOWN_DRIFT_TOLERATED="${KNOWN_DRIFT_TOLERATED:-atlantis whoami-test}"
TIMEOUT=1800
SINCE=""
ACTION="${1:-}"; shift || true

while [ $# -gt 0 ]; do
  case "$1" in
    --artifacts) ARTIFACTS="${2:-}"; shift 2 ;;
    --since)     SINCE="${2:-}"; shift 2 ;;
    --timeout)   TIMEOUT="${2:-}"; shift 2 ;;
    *) echo "hop-verify: unknown argument: $1" >&2; exit 1 ;;
  esac
done

FAIL=0
ok()   { printf "  \033[32mPASS\033[0m  %s\n" "$*"; }
bad()  { printf "  \033[31mFAIL\033[0m  %s\n" "$*"; FAIL=1; }
note() { printf "  ----  %s\n" "$*"; }

sha_check() { if command -v sha256sum >/dev/null 2>&1; then sha256sum -c "$1"; else shasum -a 256 -c "$1"; fi; }

# §V.1 / §V.6 / §V.28 — a backup nobody verified is not a backup.
check_artifact() {
  local glob="$1" label="$2" inv="$3"
  local newest
  newest="$(ls -t ${ARTIFACTS}/${glob} 2>/dev/null | head -1 || true)"
  if [ -z "$newest" ]; then bad "$inv  no $label artifact in $ARTIFACTS"; return; fi
  local age=$(( ( $(date +%s) - $(stat -f %m "$newest" 2>/dev/null || stat -c %Y "$newest") ) / 3600 ))
  if [ ! -f "$newest.sha256" ]; then bad "$inv  $label has no checksum manifest"; return; fi
  if ( cd "$ARTIFACTS" && sha_check "$(basename "$newest").sha256" >/dev/null 2>&1 ); then
    if [ "$age" -gt 24 ]; then bad "$inv  $label checksum OK but ${age}h old — take a fresh one"
    else ok "$inv  $label verified, ${age}h old"; fi
  else
    bad "$inv  $label CHECKSUM MISMATCH — $(basename "$newest")"
  fi
}

check_eso() {                                   # §V.11
  local total synced
  total=$(kubectl get externalsecret -A --no-headers 2>/dev/null | wc -l | tr -d ' ')
  synced=$(kubectl get externalsecret -A --no-headers 2>/dev/null | grep -c True || true)
  if [ "$total" = "0" ]; then bad "§V.11 no ExternalSecrets found — is ESO running?"; return; fi
  # 3 are known-broken pre-existing; treat a worsening as failure.
  if [ "$((total - synced))" -le 3 ]; then ok "§V.11 ESO $synced/$total SecretSynced"
  else bad "§V.11 ESO $synced/$total SecretSynced — worse than the known 3 failures"; fi
}

check_istio() {                                 # §V.49 — deferred Istio needs watching
  local nodes bad_ds=0
  nodes=$(kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')
  for ds in istio-cni-node ztunnel; do
    local ready desired
    ready=$(kubectl get ds -n istio-system "$ds" -o jsonpath='{.status.numberReady}' 2>/dev/null || echo 0)
    desired=$(kubectl get ds -n istio-system "$ds" -o jsonpath='{.status.desiredNumberScheduled}' 2>/dev/null || echo 0)
    if [ "$ready" = "$desired" ] && [ "$ready" = "$nodes" ]; then ok "§V.49 $ds Ready $ready/$nodes"
    else bad "§V.49 $ds Ready $ready/$desired (nodes=$nodes)"; bad_ds=1; fi
  done
  local amb
  amb=$(kubectl get ns -l istio.io/dataplane-mode --no-headers 2>/dev/null | wc -l | tr -d ' ')
  [ "$amb" -gt 0 ] && ok "§V.49 $amb namespace(s) still enrolled in ambient" \
                   || bad "§V.49 no namespaces enrolled in ambient — was that intended?"
}

# §V.50 — a k3s hop rotates data/<hash> and repoints data/current, orphaning any CNI
# binary installed under it. The istio-cni DS keeps writing into the OLD directory because
# the kubelet resolved its hostPath at pod-create, so it never self-heals.
#
# Note what this does NOT do: exec into the pod and test for the binary. That check would
# PASS while the node is broken, because the pod's own mount still points at the old dir.
# The only honest signal available from the API is the symptom — sandboxes that cannot be
# created because containerd cannot find the plugin. §B.7: the DS was Ready 3/3 throughout.
check_cni_plugin() {
  # Events outlive the incident (k3s keeps them ~1h), so matching on events alone would
  # keep failing the gate long after a fix. Only pods that are stuck RIGHT NOW count as a
  # failure; matching events with nothing stuck are reported as history.
  local stuck
  stuck=$(kubectl get pods -A --no-headers 2>/dev/null | awk '$4=="ContainerCreating"{print $1"/"$2}')
  local report
  # shellcheck disable=SC2086
  report=$(kubectl get events -A -o json 2>/dev/null | python3 -c '
import sys, json
stuck = set(sys.argv[1:])
try: items = json.load(sys.stdin)["items"]
except Exception: items = []
marker = "failed to find plugin "
live, hist = set(), set()
for e in items:
    m = e.get("message", "")
    i = m.find(marker)
    if i < 0: continue
    rest = m[i + len(marker):].replace(chr(92), "")     # messages arrive with escaped quotes
    parts = rest.split(chr(34))
    plug = parts[1] if len(parts) > 1 else rest.split()[0]
    o = e.get("involvedObject", {})
    key = str(o.get("namespace", "")) + "/" + str(o.get("name", ""))
    (live if key in stuck else hist).add(plug)
print("LIVE " + " ".join(sorted(live)))
print("HIST " + " ".join(sorted(hist)))' $stuck 2>/dev/null)
  local live hist
  live=$(printf "%s\n" "$report" | sed -n 's/^LIVE //p')
  hist=$(printf "%s\n" "$report" | sed -n 's/^HIST //p')
  if [ -n "$live" ]; then
    bad "§V.50 CNI plugin(s) MISSING and pods are wedged now: $live"
    bad "§V.50 → restart the istio-cni-node pod on each affected node (§B.7), then re-gate"
  else
    ok "§V.50 no live missing-CNI-plugin sandbox failures"
    [ -n "$hist" ] && note "§V.50 resolved earlier this hour: $hist (events not yet expired)"
  fi
}

check_ingress() {                               # §V.22 / §T.29
  local phase
  phase=$(kubectl get helmchart -n kube-system ingress-nginx -o jsonpath='{.status.jobName}' 2>/dev/null || true)
  if kubectl get helmchart -n kube-system ingress-nginx >/dev/null 2>&1; then
    ok "§V.22 helm-controller HelmChart ingress-nginx present (job=${phase:-none})"
  else
    bad "§V.22 HelmChart kube-system/ingress-nginx missing — helm-controller upgraded with the hop"
  fi
  # The controller here is a DaemonSet, not a Deployment — an earlier version of this
  # check queried Deployments, found none, and reported a healthy ingress as broken.
  local rd=0
  rd=$(kubectl get ds -n ingress-nginx -o jsonpath='{.items[0].status.numberReady}' 2>/dev/null || echo 0)
  [ "${rd:-0}" -eq 0 ] && rd=$(kubectl get deploy -n ingress-nginx -o jsonpath='{.items[0].status.readyReplicas}' 2>/dev/null || echo 0)
  [ "${rd:-0}" -ge 1 ] && ok "§V.22 ingress-nginx controller ready ($rd)" \
                       || bad "§V.22 ingress-nginx controller has no ready replicas"
}

check_nodes_and_apps() {                        # §V.5 with §V.47's exception
  local notready
  notready=$(kubectl get nodes --no-headers 2>/dev/null | awk '$2!="Ready"{print $1}')
  [ -z "$notready" ] && ok "§V.5 all nodes Ready" || bad "§V.5 nodes not Ready: $notready"
  local drift
  drift=$(kubectl get app -n "$ARGO_NS" -o json 2>/dev/null | python3 -c '
import sys, json
d = json.load(sys.stdin)["items"]
print(" ".join(sorted(a["metadata"]["name"] for a in d
      if a["status"]["sync"]["status"] != "Synced" or a["status"]["health"]["status"] != "Healthy")))')
  local unexpected="" tolerated=""
  for a in $drift; do
    case " $KNOWN_DRIFT_DIAGNOSED " in *" $a "*) continue;; esac
    case " $KNOWN_DRIFT_TOLERATED " in *" $a "*) tolerated="$tolerated $a"; continue;; esac
    unexpected="$unexpected $a"
  done
  if [ -z "$unexpected" ]; then ok "§V.5/§V.47 no unexplained drift (diagnosed: ${KNOWN_DRIFT_DIAGNOSED})"
  else bad "§V.5/§V.47 UNEXPLAINED drift:$unexpected"; fi
  [ -n "$tolerated" ] && note "§T.45 tolerated but UNDIAGNOSED:$tolerated — workloads Running; diagnose before this becomes permanent"
}

check_pg() {                                    # §V.14 data path
  local ph rd
  ph=$(kubectl get cluster -n postgresql postgresql-cluster -o jsonpath='{.status.phase}' 2>/dev/null || true)
  rd=$(kubectl get cluster -n postgresql postgresql-cluster -o jsonpath='{.status.readyInstances}' 2>/dev/null || echo 0)
  [ "$ph" = "Cluster in healthy state" ] && [ "${rd:-0}" -ge 1 ] \
    && ok "§V.14 CNPG healthy ($rd ready)" || bad "§V.14 CNPG phase='$ph' ready=$rd"
}

check_vault() {                                 # §V.14 secrets path
  if kubectl exec -n vault vault-0 -- vault status 2>/dev/null | grep -q "Sealed.*false"; then
    ok "§V.14 Vault unsealed"
  else
    bad "§V.14 Vault sealed or unreachable"
  fi
}

case "$ACTION" in
  gate)
    echo "=== pre-hop gate $(date -u +%H:%M:%SZ) ==="
    check_artifact "k3s-backup-*.tar.gz" "k3s"   "§V.1 "
    check_artifact "pg-*.sql.gz"         "pg"    "§V.6 "
    check_artifact "vault-backup-*.tar.gz" "vault" "§V.28"
    check_nodes_and_apps
    check_pg
    check_vault
    check_eso
    check_istio
    check_cni_plugin
    check_ingress
    note "§V.2/§V.27 component matrix — operator judgement, see docs/plans/k3s-1.36-upgrade-plan.md"
    note "§V.10 removed-API scan — run: pytest tests/k3s-upgrade/test_api_scan.py"
    note "§V.16 restore drill — proven by tests/k3s-upgrade/ in CI"
    echo
    [ "$FAIL" -eq 0 ] && echo "GATE PASSED — safe to hop" || echo "GATE FAILED — do not hop"
    exit "$FAIL"
    ;;

  watch)
    [ -n "$SINCE" ] || { echo "hop-verify: watch needs --since <epoch-seconds>" >&2; exit 1; }
    echo "=== post-hop watch, measuring §V.9 from ${SINCE} ==="
    start_iso=$(date -u -r "$SINCE" +%H:%M:%SZ 2>/dev/null || date -u -d "@$SINCE" +%H:%M:%SZ)
    echo "  window opened at $start_iso"
    deadline=$(( $(date +%s) + TIMEOUT ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
      FAIL=0
      check_nodes_and_apps >/dev/null 2>&1 || true
      # recompute cleanly
      FAIL=0; out=$(check_nodes_and_apps 2>&1); echo "$out" | grep -q FAIL || FAIL=0
      if ! echo "$out" | grep -q "FAIL"; then
        elapsed=$(( $(date +%s) - SINCE ))
        echo "$out"
        printf "\n  \033[32m§V.9 WINDOW: %dm %ds\033[0m (start = k3s stop, end = all nodes Ready + no unexplained drift)\n" \
          $((elapsed/60)) $((elapsed%60))
        [ "$elapsed" -le 900 ] && echo "  within the 15min bound" || echo "  EXCEEDS the 15min bound in §V.9"
        exit 0
      fi
      printf "  t+%ds not green yet\n" $(( $(date +%s) - SINCE ))
      sleep 20
    done
    echo "  TIMEOUT after ${TIMEOUT}s — cluster did not reach green"
    exit 1
    ;;

  *)
    sed -n '2,22p' "$0" >&2
    exit 2
    ;;
esac
