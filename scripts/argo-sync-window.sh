#!/usr/bin/env bash
# argo-sync-window.sh — pause/resume Argo auto-sync across an upgrade hop (SPEC.md §T.25)
#
# §V.18 requires auto-sync paused for every app carrying a PVC during a hop window, because
# `local-path` volumes are node-bound and a mid-hop reconcile can act on a node whose pods
# cannot reschedule.
#
# Two things this gets right that are easy to get wrong:
#
#   §B.4 — suspending a child app is not enough. `master-app` re-applies every Application
#   from git, restoring `syncPolicy.automated` mid-operation. It gets suspended too, and
#   FIRST, so it cannot undo the children.
#
#   Scope — apps are discovered two ways and unioned. Querying only PVCs with an Argo
#   tracking-id misses StatefulSets using `volumeClaimTemplates`, whose claims the
#   StatefulSet controller creates rather than Argo. That omission would have skipped
#   `vault`, the one that matters most.
#
# §V.30: the pause must be liftable to reach §V.5 green. A paused app cannot self-heal, so
# resume is not gated on Synced — the order is pause → hop → manual sync → verify → resume.
#
# Usage:
#   argo-sync-window.sh scope                 # list what would be paused, change nothing
#   argo-sync-window.sh pause  [--state FILE] # suspend; record prior policy for exact restore
#   argo-sync-window.sh resume [--state FILE] # restore exactly what was recorded

set -euo pipefail

ARGO_NS="${ARGO_NAMESPACE:-argo-cd}"
STATE="${HOME}/.k3s-hop-argo-state.json"
ACTION="${1:-}"; shift || true

while [ $# -gt 0 ]; do
  case "$1" in
    --state) STATE="${2:-}"; shift 2 ;;
    --argo-namespace) ARGO_NS="${2:-}"; shift 2 ;;
    *) echo "argo-sync-window: unknown argument: $1" >&2; exit 1 ;;
  esac
done

die() { echo "argo-sync-window: $*" >&2; exit 1; }

# Union of: apps tracking a PVC, and apps owning a StatefulSet with volumeClaimTemplates.
discover_apps() {
  {
    kubectl get pvc -A -o json 2>/dev/null | python3 -c '
import sys, json
for i in json.load(sys.stdin)["items"]:
    tid = (i["metadata"].get("annotations") or {}).get("argocd.argoproj.io/tracking-id")
    if tid: print(tid.split(":")[0])
'
    kubectl get sts -A -o json 2>/dev/null | python3 -c '
import sys, json
for s in json.load(sys.stdin)["items"]:
    if s["spec"].get("volumeClaimTemplates"):
        tid = (s["metadata"].get("annotations") or {}).get("argocd.argoproj.io/tracking-id")
        if tid: print(tid.split(":")[0])
'
  } | sort -u | while read -r a; do
    [ -n "$a" ] && kubectl get app -n "$ARGO_NS" "$a" >/dev/null 2>&1 && echo "$a"
  done
}

case "$ACTION" in
  scope)
    echo "master-app   (§B.4 — parent re-applies children, must be suspended first)"
    discover_apps | sed 's/^/PVC app: /'
    ;;

  pause)
    apps="$(discover_apps)"
    [ -n "$apps" ] || die "discovered no PVC-bearing apps — refusing to record an empty window"
    # master-app first: while it is still syncing it would undo the children (§B.4).
    # Apps go in argv, not stdin -- stdin is the heredoc carrying this program, and
    # combining `<<'PY'` with `<<<"$list"` silently feeds the list in as the script.
    # shellcheck disable=SC2086
    python3 - "$ARGO_NS" "$STATE" master-app $apps <<'PY'
import json, subprocess, sys
ns, state = sys.argv[1], sys.argv[2]
apps = sys.argv[3:]
saved = {}
for a in apps:
    out = subprocess.run(["kubectl","get","app","-n",ns,a,"-o","jsonpath={.spec.syncPolicy.automated}"],
                         capture_output=True, text=True)
    saved[a] = out.stdout.strip()          # "" means already suspended
    subprocess.run(["kubectl","patch","app","-n",ns,a,"--type","merge",
                    "-p",'{"spec":{"syncPolicy":{"automated":null}}}'],
                   capture_output=True, text=True, check=True)
    print(f"  paused {a}" + ("  (was already suspended)" if not saved[a] else ""))
json.dump(saved, open(state,"w"), indent=2)
print(f"  prior policy recorded -> {state}")
PY
    ;;

  resume)
    [ -f "$STATE" ] || die "no state file at $STATE — refusing to guess which apps to resume"
    python3 - "$ARGO_NS" "$STATE" <<'PY'
import json, subprocess, sys
ns, state = sys.argv[1], sys.argv[2]
saved = json.load(open(state))
# children first, master-app last: restoring the parent first lets it fight the children.
order = [a for a in saved if a != "master-app"] + (["master-app"] if "master-app" in saved else [])
for a in order:
    prior = saved[a]
    if not prior:
        print(f"  skipped {a} (was suspended before the window; leaving as-is)")
        continue
    subprocess.run(["kubectl","patch","app","-n",ns,a,"--type","merge",
                    "-p",json.dumps({"spec":{"syncPolicy":{"automated":json.loads(prior)}}})],
                   capture_output=True, text=True, check=True)
    print(f"  resumed {a} -> {prior}")
PY
    echo "  §V.45: confirm each app observes the intended revision before trusting its sync status"
    ;;

  *)
    sed -n '2,26p' "$0" >&2
    exit 2
    ;;
esac
