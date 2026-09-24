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
#
# 2026-09-24: both lists are empty. kagent-secrets and atlantis came clean with §T.42, and
# kyverno's last reason (master-app diffing Argo's own pre-delete finalizers) went with
# §T.85. Every app must now be Synced+Healthy; add a name here only with its cause.
KNOWN_DRIFT_DIAGNOSED="${KNOWN_DRIFT_DIAGNOSED:-}"
KNOWN_DRIFT_TOLERATED="${KNOWN_DRIFT_TOLERATED:-}"
# Namespaces labelled istio.io/dataplane-mode. 0 since chores-tracker was removed; raise it
# when a namespace is enrolled again so the hop notices a lost enrolment.
AMBIENT_MIN="${AMBIENT_MIN:-0}"
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

# Every check must end on a statement that succeeds. Under `set -e` a function whose last
# command is a false test (`[ -n "$x" ] && note ...` with $x empty) returns 1, and the gate
# dies there without a word - which is exactly what it did on a clean cluster until
# 2026-09-24. Use `if ...; then ...; fi` for optional notes.
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
  # The expected count is explicit: "more than zero" stopped being true on purpose when
  # chores-tracker (the only ambient workload) was removed in 7996557.
  if [ "$amb" -ge "$AMBIENT_MIN" ]; then ok "§V.49 $amb namespace(s) enrolled in ambient (expected >= $AMBIENT_MIN)"
  else bad "§V.49 only $amb namespace(s) enrolled in ambient, expected >= $AMBIENT_MIN — enrolment lost?"; fi
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
    if [ -n "$hist" ]; then note "§V.50 resolved earlier this hour: $hist (events not yet expired)"; fi
  fi

  # §V.51 — the delayed half of §B.7. Every sandbox attempt that failed at the istio-cni
  # step had ALREADY been given an IP by flannel, and that reservation was never released.
  # Eighteen minutes of retries leaked 219 of control-01's 254 addresses; the node then
  # could not create ANY pod for fourteen hours, and nothing here noticed, because a stuck
  # CronJob pod is invisible to an Argo-app-level check.
  # Same live-vs-history discipline as above: the events outlive the fix by an hour, and a
  # gate that keeps failing on a resolved incident is one people learn to override.
  local xreport
  # shellcheck disable=SC2086
  xreport=$(kubectl get events -A -o json 2>/dev/null | python3 -c '
import sys, json
stuck = set(sys.argv[1:])
try: items = json.load(sys.stdin)["items"]
except Exception: items = []
live, hist = set(), set()
for e in items:
    if "no IP addresses available" not in e.get("message", ""): continue
    host = str(e.get("source", {}).get("host") or e.get("reportingInstance") or "?")
    o = e.get("involvedObject", {})
    key = str(o.get("namespace", "")) + "/" + str(o.get("name", ""))
    (live if key in stuck else hist).add(host)
print("LIVE " + " ".join(sorted(live)))
print("HIST " + " ".join(sorted(hist)))' $stuck 2>/dev/null)
  local xlive xhist
  xlive=$(printf "%s\n" "$xreport" | sed -n 's/^LIVE //p')
  xhist=$(printf "%s\n" "$xreport" | sed -n 's/^HIST //p')
  if [ -n "$xlive" ]; then
    bad "§V.51 pod CIDR EXHAUSTED on: $xlive — leaked host-local reservations (§B.8)"
    bad "§V.51 → compare /var/lib/cni/networks/cbr0 against live sandboxes and prune the orphans"
  else
    ok "§V.51 no live pod-CIDR exhaustion"
    if [ -n "$xhist" ]; then note "§V.51 resolved earlier this hour on: $xhist (events not yet expired)"; fi
  fi
}

check_ingress() {                               # §V.22 / §T.29 / §T.53
  # ingress-nginx was retired 2026-07-31 (§T.53). This previously asserted the
  # helm-controller HelmChart CR and the nginx DaemonSet; both are gone, and a
  # gate still looking for them would report every future hop as failed. §V.58
  # requires this to move in the same change that removes the controller.
  local prog
  prog=$(kubectl -n istio-ingress get gateway main \
           -o jsonpath='{.status.conditions[?(@.type=="Programmed")].status}' 2>/dev/null || true)
  [ "$prog" = "True" ] && ok "§V.22 ingress Gateway istio-ingress/main Programmed" \
                       || bad "§V.22 ingress Gateway istio-ingress/main not Programmed (got: ${prog:-missing})"

  # Ready pods, not merely a Programmed Gateway: §B.7 is the standing reminder
  # that a controller can report healthy while serving nothing.
  local rd
  rd=$(kubectl -n istio-ingress get deploy main-istio -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
  [ "${rd:-0}" -ge 1 ] && ok "§V.22 gateway data plane ready ($rd)" \
                       || bad "§V.22 gateway deployment main-istio has no ready replicas"

  # Every listener must resolve its certificateRef. A listener whose ReferenceGrant
  # is missing comes up SILENTLY certless rather than erroring (§V.63).
  local unres
  unres=$(kubectl -n istio-ingress get gateway main -o json 2>/dev/null \
          | python3 -c 'import sys,json
try: st=json.load(sys.stdin)["status"]
except Exception: print("READ-FAILED"); raise SystemExit
bad=[l["name"] for l in st.get("listeners",[])
     if not all(c["status"]=="True" for c in l.get("conditions",[]) if c["type"] in ("Programmed","ResolvedRefs"))]
print(" ".join(bad))' 2>/dev/null || echo "READ-FAILED")
  [ -z "$unres" ] && ok "§V.63 all gateway listeners resolved" \
                  || bad "§V.63 listeners not resolved: $unres"

  # The allow-list IS the security boundary (§R.31). An ingress that serves but
  # enforces nothing is a worse outcome than one that is down.
  kubectl -n istio-ingress get authorizationpolicy gateway-allow >/dev/null 2>&1 \
    && ok "§V.61 gateway AuthorizationPolicy present" \
    || bad "§V.61 gateway-allow AuthorizationPolicy MISSING — gateway is unprotected"
}

check_nodes_and_apps() {                        # §V.5 with §V.47's exception
  # An empty answer from the API must FAIL, not read as "nothing is wrong": no nodes
  # listed is not the same as no nodes NotReady, and mid-hop the API does go away.
  local nodes notready
  nodes=$(kubectl get nodes --no-headers 2>/dev/null || true)
  notready=$(printf "%s\n" "$nodes" | awk 'NF && $2!="Ready"{print $1}')
  if [ -z "$nodes" ]; then bad "§V.5 no nodes returned — API unreachable?"
  elif [ -z "$notready" ]; then ok "§V.5 all nodes Ready"
  else bad "§V.5 nodes not Ready: $notready"; fi
  local drift
  drift=$(kubectl get app -n "$ARGO_NS" -o json 2>/dev/null | python3 -c '
import sys, json
try: d = json.load(sys.stdin)["items"]
except Exception: print("__UNREADABLE__"); sys.exit(0)
print(" ".join(sorted(a["metadata"]["name"] for a in d
      if a.get("status", {}).get("sync", {}).get("status") != "Synced"
      or a.get("status", {}).get("health", {}).get("status") != "Healthy")))' || true)
  if [ "$drift" = "__UNREADABLE__" ]; then
    bad "§V.5/§V.47 could not read Argo applications — API unreachable?"
    return 0
  fi
  local unexpected="" tolerated="" cascade=""
  for a in $drift; do
    case " $KNOWN_DRIFT_DIAGNOSED " in *" $a "*) continue;; esac
    case " $KNOWN_DRIFT_TOLERATED " in *" $a "*) tolerated="$tolerated $a"; continue;; esac
    # master-app is an app-of-apps: Argo propagates each child Application's sync status
    # into the parent's resource tree, so the parent reads OutOfSync whenever ANY child
    # does. That is a cascade of an already-diagnosed cause, not independent drift.
    # Tolerate it ONLY when every resource it flags is itself allow-listed — otherwise the
    # parent would become a blanket excuse and hide real drift underneath itself.
    if [ "$a" = "master-app" ]; then
      local children
      children=$(kubectl get app -n "$ARGO_NS" master-app -o json 2>/dev/null | python3 -c '
import sys, json
try: rs = json.load(sys.stdin)["status"].get("resources", [])
except Exception: rs = []
print(" ".join(sorted(r.get("name","") for r in rs if r.get("status") != "Synced")))')
      local leftover=""
      for c in $children; do
        case " $KNOWN_DRIFT_DIAGNOSED $KNOWN_DRIFT_TOLERATED " in *" $c "*) continue;; esac
        leftover="$leftover $c"
      done
      if [ -z "$leftover" ] && [ -n "$children" ]; then
        cascade="$children"; continue
      fi
      unexpected="$unexpected master-app($( [ -n "$leftover" ] && echo "$leftover" || echo "no children flagged" ))"
      continue
    fi
    unexpected="$unexpected $a"
  done
  if [ -n "$cascade" ]; then note "§V.47 master-app OutOfSync is a CASCADE of allow-listed children:$cascade — not independent drift"; fi
  if [ -z "$unexpected" ]; then ok "§V.5/§V.47 no unexplained drift (diagnosed: ${KNOWN_DRIFT_DIAGNOSED})"
  else bad "§V.5/§V.47 UNEXPLAINED drift:$unexpected"; fi
  if [ -n "$tolerated" ]; then note "§T.45 tolerated but UNDIAGNOSED:$tolerated — workloads Running; diagnose before this becomes permanent"; fi
}

check_kyverno() {                               # §T.72 — kyverno v1.19 is untested on 1.36
  # Accepted risk (2026-09-24): kyverno states k8s 1.33-1.35 only. Every hop re-proves the
  # admission path instead of assuming it: controllers available, policies Ready, and a
  # server-side dry-run pod create, which runs the webhooks without persisting anything.
  # Emergency lever if admission breaks: delete kyverno's webhook configurations
  #   kubectl delete validatingwebhookconfigurations,mutatingwebhookconfigurations \
  #     -l webhook.kyverno.io/managed-by=kyverno
  local notavail
  notavail=$(kubectl get deploy -n kyverno -o json 2>/dev/null | python3 -c '
import sys, json
try: items = json.load(sys.stdin)["items"]
except Exception: items = []
bad = [d["metadata"]["name"] for d in items
       if d["status"].get("availableReplicas", 0) < d["spec"].get("replicas", 1)]
print(" ".join(bad) if items else "NO-DEPLOYMENTS")' || true)
  if [ -z "$notavail" ]; then ok "§T.72 kyverno controllers available"
  else bad "§T.72 kyverno controllers not available: $notavail"; fi
  local notready
  notready=$(kubectl get clusterpolicy -o json 2>/dev/null | python3 -c '
import sys, json
try: items = json.load(sys.stdin)["items"]
except Exception: items = []
print(" ".join(p["metadata"]["name"] for p in items
      if not any(c.get("type") == "Ready" and c.get("status") == "True"
                 for c in p.get("status", {}).get("conditions", []))))' || true)
  if [ -z "$notready" ]; then ok "§T.72 kyverno ClusterPolicies Ready"
  else bad "§T.72 kyverno ClusterPolicies not Ready: $notready"; fi
  if kubectl run hop-verify-admission --image=busybox:1.36 --restart=Never -n default \
       --dry-run=server -o name >/dev/null 2>&1; then
    ok "§T.72 admission path: server-side dry-run pod create accepted"
  else
    bad "§T.72 admission path: dry-run pod create REJECTED — see the emergency lever in check_kyverno"
  fi
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
    check_kyverno
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
      FAIL=0; out=$(check_nodes_and_apps 2>&1) || true; echo "$out" | grep -q FAIL || FAIL=0
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
