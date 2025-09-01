#!/usr/bin/env bash
# 503-error-monitor.sh

set -euo pipefail

echo "Starting comprehensive log monitoring for 503 errors..."
echo "Press Ctrl+C to stop monitoring"

LOG_DIR="/tmp/503-troubleshooting-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"

echo "Logs will be written to: $LOG_DIR"

# Start log streams in background, prefixing pod names when possible
kubectl logs -f -n cloudflare -l app=cloudflared --prefix=true > "$LOG_DIR/cloudflare.log" 2>&1 &
CLOUDFLARE_PID=$!

kubectl logs -f -n kube-system deployment/traefik --prefix=true > "$LOG_DIR/traefik.log" 2>&1 &
TRAEFIK_PID=$!

kubectl logs -f -n chores-tracker -l app=chores-tracker --prefix=true > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

kubectl logs -f -n chores-tracker-frontend -l app=chores-tracker-frontend --prefix=true > "$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

echo "Monitoring for 503 errors... (Ctrl+C to stop)"

cleanup() {
  echo "Stopping log monitoring..."
  kill "$CLOUDFLARE_PID" "$TRAEFIK_PID" "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  wait || true
  echo "Logs saved in: $LOG_DIR"
}

trap cleanup INT TERM

# Tail the logs for 503s and common error patterns in the background
grep -iE "(^|\W)(503|timeout|timed out|connection reset|unavailable|upstream)\b" -n --line-buffered "$LOG_DIR"/*.log &

# Wait for background log streams
wait

