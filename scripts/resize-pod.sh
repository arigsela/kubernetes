#!/usr/bin/env bash
# resize-pod.sh — change a running container's CPU/memory without restarting it
# (in-place pod resize, GA in Kubernetes 1.35; docs/troubleshooting/in-place-resize.md)
#
#   resize-pod.sh <namespace> <pod> <container> [cpu=REQ/LIM] [memory=REQ/LIM] [--apply]
#
#   cpu=250m/1        request 250m, limit 1          memory=2Gi/3Gi
#   cpu=250m          request only                   memory=/3Gi   limit only
#
# Without --apply it only validates: a local QoS check, then a server-side dry run of the
# resize. With --apply it resizes, waits for the kubelet to finish, verifies the container
# did NOT restart, and prints the git change that makes the manifest match.
#
# Why the checks:
#   * A resize may not change the pod's QoS class (BestEffort/Burstable/Guaranteed). The API
#     rejects it anyway; checking first gives a clear reason instead of an API error.
#   * Git is the source of truth. Merging the new values is safe only for an OnDelete
#     StatefulSet (no restart). For a Deployment or RollingUpdate owner the merge rolls the
#     pod — the resize is then an emergency lever that buys time until a planned rollout.
#   * CloudNativePG pods are refused: CNPG 1.30 does not support in-place resize and a later
#     git change restarts the instance anyway (upstream cloudnative-pg#11116).
set -euo pipefail

usage() { sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "resize-pod: $*" >&2; exit 2; }

[ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ] && { usage; exit 0; }
[ $# -ge 4 ] || { usage >&2; exit 2; }

NS="$1"; POD="$2"; CONTAINER="$3"; shift 3
CPU=""; MEM=""; APPLY=0
for a in "$@"; do
  case "$a" in
    cpu=*)    CPU="${a#cpu=}" ;;
    memory=*) MEM="${a#memory=}" ;;
    --apply)  APPLY=1 ;;
    *) die "unknown argument: $a" ;;
  esac
done
[ -n "$CPU$MEM" ] || die "give at least one of cpu=REQ/LIM or memory=REQ/LIM"

QTY='^[0-9]+(\.[0-9]+)?(m|k|Ki|M|Mi|G|Gi|T|Ti|E|Ei)?$'
for spec in "$CPU" "$MEM"; do
  [ -z "$spec" ] && continue
  req="${spec%%/*}"; lim=""; [[ "$spec" == */* ]] && lim="${spec#*/}"
  [ -n "$req$lim" ] || die "empty resource value: '$spec'"
  for v in $req $lim; do [[ "$v" =~ $QTY ]] || die "not a Kubernetes quantity: '$v'"; done
done

POD_JSON=$(kubectl get pod -n "$NS" "$POD" -o json) || die "cannot read pod $NS/$POD"

# Everything that needs structured logic (patch body, QoS prediction, owner lookup) is done in
# python3 from the pod JSON, so the shell never parses JSON by hand.
PLAN=$(printf '%s' "$POD_JSON" | CPU="$CPU" MEM="$MEM" CONTAINER="$CONTAINER" python3 -c '
import json, os, sys
pod = json.load(sys.stdin)
cname = os.environ["CONTAINER"]
cs = pod["spec"]["containers"]
names = [c["name"] for c in cs]
if cname not in names:
    sys.exit(f"container {cname!r} not in pod (have: {names})")

def split(spec):
    if not spec: return None, None
    req, _, lim = spec.partition("/")
    return (req or None), (lim or None)

new = {"requests": {}, "limits": {}}
for res, env in (("cpu", "CPU"), ("memory", "MEM")):
    req, lim = split(os.environ[env])
    if req: new["requests"][res] = req
    if lim: new["limits"][res] = lim
new = {k: v for k, v in new.items() if v}

UNITS = {"m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "E": 1e18,
         "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Ei": 2**60}

def num(q):
    # Kubernetes quantity -> number, so "1" == "1000m" and "1Gi" == "1024Mi".
    if q is None: return None
    for u in sorted(UNITS, key=len, reverse=True):
        if q.endswith(u): return float(q[:-len(u)]) * UNITS[u]
    return float(q)

def qos(containers):
    # Kubernetes QoS rules, over app containers (init containers also count, below).
    reqs, lims, any_set = [], [], False
    for c in containers:
        r = c.get("resources", {})
        rq, lm = r.get("requests", {}), r.get("limits", {})
        if rq or lm: any_set = True
        for res in ("cpu", "memory"):
            q = rq.get(res, lm.get(res))  # a limit without a request defaults the request to it
            reqs.append((res, q)); lims.append((res, lm.get(res)))
    if not any_set: return "BestEffort"
    if all(l is not None for _, l in lims) and all(num(q) == num(l) for (_, q), (_, l) in zip(reqs, lims)):
        return "Guaranteed"
    return "Burstable"

def merged(c):
    if c["name"] != cname: return c
    c = json.loads(json.dumps(c))
    r = c.setdefault("resources", {})
    for k, v in new.items(): r.setdefault(k, {}).update(v)
    return c

init = pod["spec"].get("initContainers", [])
before = pod["status"].get("qosClass") or qos(cs + init)
after = qos([merged(c) for c in cs] + init)
owner = (pod["metadata"].get("ownerReferences") or [{}])[0]
cur = next(c for c in cs if c["name"] == cname).get("resources", {})
status = next((s for s in pod["status"].get("containerStatuses", []) if s["name"] == cname), {})
labels = pod["metadata"].get("labels", {})
print(json.dumps({
    "patch": {"spec": {"containers": [{"name": cname, "resources": new}]}},
    "qos_before": before, "qos_after": after,
    "owner_kind": owner.get("kind", ""), "owner_name": owner.get("name", ""),
    "cnpg": "cnpg.io/cluster" in labels,
    "current": cur, "new": new,
    "restarts": status.get("restartCount", 0),
}))
') || die "could not plan the resize (see above)"

field() { printf '%s' "$PLAN" | python3 -c "import json,sys; v=json.load(sys.stdin)[sys.argv[1]]; print(json.dumps(v) if isinstance(v,(dict,list)) else v)" "$1"; }

QOS_BEFORE=$(field qos_before); QOS_AFTER=$(field qos_after)
OWNER_KIND=$(field owner_kind); OWNER_NAME=$(field owner_name)
PATCH=$(field patch); RESTARTS_BEFORE=$(field restarts)

echo "pod        $NS/$POD  container=$CONTAINER  owner=${OWNER_KIND:-none}/${OWNER_NAME:-}"
echo "current    $(field current)"
echo "requested  $(field new)"

[ "$(field cnpg)" = "True" ] && die "refusing a CloudNativePG pod: CNPG 1.30 restarts the instance on any resource change (cloudnative-pg#11116)"
[ "$QOS_BEFORE" = "$QOS_AFTER" ] || die "would change QoS class $QOS_BEFORE -> $QOS_AFTER, which in-place resize does not allow. A restart is required to change QoS (e.g. BestEffort -> Burstable)."
echo "qos        $QOS_BEFORE (unchanged)"

kubectl patch pod -n "$NS" "$POD" --subresource resize --dry-run=server -p "$PATCH" >/dev/null \
  || die "the API server rejected the resize (dry run) — see the error above"
echo "dry run    accepted by the API server"

if [ "$APPLY" -eq 0 ]; then
  echo "(dry run only; re-run with --apply to resize)"
  exit 0
fi

kubectl patch pod -n "$NS" "$POD" --subresource resize -p "$PATCH" >/dev/null
echo "resize     requested; waiting for the kubelet..."
for _ in $(seq 1 60); do
  conds=$(kubectl get pod -n "$NS" "$POD" -o jsonpath='{range .status.conditions[*]}{.type}={.status}:{.reason}{"\n"}{end}')
  if ! printf '%s\n' "$conds" | grep -qE '^PodResize(Pending|InProgress)=True'; then break; fi
  if printf '%s\n' "$conds" | grep -qE '^PodResizePending=True:Infeasible'; then
    die "the node cannot fit the new requests (PodResizePending: Infeasible); the resize will not happen"
  fi
  sleep 2
done
printf '%s\n' "$conds" | grep -E '^PodResize' && die "resize still pending or in progress after 120s"

ACTUAL=$(kubectl get pod -n "$NS" "$POD" -o jsonpath="{.status.containerStatuses[?(@.name==\"$CONTAINER\")].resources}")
RESTARTS_AFTER=$(kubectl get pod -n "$NS" "$POD" -o jsonpath="{.status.containerStatuses[?(@.name==\"$CONTAINER\")].restartCount}")
echo "actual     $ACTUAL"
[ "$RESTARTS_AFTER" = "$RESTARTS_BEFORE" ] || die "container restarted ($RESTARTS_BEFORE -> $RESTARTS_AFTER) — check its resizePolicy"
echo "restarts   $RESTARTS_AFTER (unchanged — resized in place)"

echo
echo "Now make git match (otherwise the next restart reverts this):"
echo "  resources for container '$CONTAINER' in the manifest of $OWNER_KIND/$OWNER_NAME:"
printf '%s' "$PLAN" | python3 -c '
import json, sys
p = json.load(sys.stdin); r = dict(p["current"])
for k, v in p["new"].items(): r.setdefault(k, {}).update(v)
for k in ("requests", "limits"):
    if k in r:
        print(f"    {k}:"); [print(f"      {res}: {val}") for res, val in r[k].items()]'
case "$OWNER_KIND" in
  StatefulSet)
    strat=$(kubectl get statefulset -n "$NS" "$OWNER_NAME" -o jsonpath='{.spec.updateStrategy.type}')
    if [ "$strat" = "OnDelete" ]; then
      echo "  StatefulSet is OnDelete: merging these values does NOT restart the pod. Safe."
    else
      echo "  WARNING: StatefulSet updateStrategy is $strat — merging these values ROLLS the pod."
    fi ;;
  ReplicaSet)
    echo "  WARNING: owned by a Deployment — merging these values starts a ROLLOUT (new pod)."
    echo "  Merge at a planned time; until then the pod runs with the resized values." ;;
  *) echo "  Owner $OWNER_KIND: check how its controller treats a template change before merging." ;;
esac
