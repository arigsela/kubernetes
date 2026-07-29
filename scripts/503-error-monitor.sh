#!/usr/bin/env bash
# 503-error-monitor.sh
#
# Tails the request path end to end while reproducing a 503, so you can see which hop
# actually broke rather than guessing from the browser.
#
# The path is: client -> ingress-nginx (hostNetwork, binds :80/:443 on the infrastructure
# node) -> the app's Service -> pods. There is no proxy in front of the cluster, so a 503
# is produced by ingress-nginx itself and the reason is almost always visible in the
# controller log alongside the upstream it tried.

set -euo pipefail

echo "Starting log monitoring for 503 errors..."
echo "Press Ctrl+C to stop monitoring"

LOG_DIR="/tmp/503-troubleshooting-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"

echo "Logs will be written to: $LOG_DIR"

PIDS=()

# The ingress controller is the component that emits the 503, so this is the log that
# matters most: it names the upstream and the reason.
kubectl logs -f -n ingress-nginx -l app.kubernetes.io/component=controller --prefix=true \
  > "$LOG_DIR/ingress-nginx.log" 2>&1 &
PIDS+=($!)

kubectl logs -f -n chores-tracker -l app=chores-tracker --prefix=true \
  > "$LOG_DIR/backend.log" 2>&1 &
PIDS+=($!)

kubectl logs -f -n chores-tracker-frontend -l app=chores-tracker-frontend --prefix=true \
  > "$LOG_DIR/frontend.log" 2>&1 &
PIDS+=($!)

echo "Monitoring for 503 errors... (Ctrl+C to stop)"

cleanup() {
  echo "Stopping log monitoring..."
  kill "${PIDS[@]}" 2>/dev/null || true
  wait || true
  echo "Logs saved in: $LOG_DIR"
}

trap cleanup INT TERM

# Tail the logs for 503s and common error patterns in the background
grep -iE "(^|\W)(503|timeout|timed out|connection reset|unavailable|upstream)\b" -n --line-buffered "$LOG_DIR"/*.log &

# Wait for background log streams
wait
