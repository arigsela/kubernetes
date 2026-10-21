#!/usr/bin/env bash
# Warm up the Agents dashboards before a demo (§T.135): three read-only questions to three agents,
# each a real run with model and tool calls, then the landing page's address.
#
#   scripts/agent-demo.sh
#
# Runs scripts/ask-hk as you (your Dex login), so run it from the home network: off it the Gateway's
# allow-list answers 403. Each question starts a new session. Nothing here changes the cluster, sends
# an alert or scores an agent. The Agent Platform dashboard opens on the newest run; its numbers
# appear within a minute (the collector flushes, Prometheus scrapes).
#
# ASK_HK and GRAFANA_URL override the client and the address (tests use a stub).
set -euo pipefail

ASK_HK=${ASK_HK:-"$(cd "$(dirname "$0")" && pwd)/ask-hk"}
GRAFANA_URL=${GRAFANA_URL:-https://grafana.arigsela.com}

QUESTIONS=(
  "homelab-knowledge|Which Argo CD application deploys the MCP adapters in kagent, and which tools does agent-docs-mcp allow?"
  "k8s-reader|How many pods run in the kagent namespace right now, and is any of them not Ready?"
  "istio-agent|Which namespaces run in Istio ambient mode, and which of them have a waypoint?"
)

for entry in "${QUESTIONS[@]}"; do
  agent=${entry%%|*}
  question=${entry#*|}
  echo "== kagent/$agent: $question"
  status=0
  "$ASK_HK" --agent "kagent/$agent" "$question" || status=$?
  if [ "$status" -eq 2 ]; then
    echo "kagent/$agent is waiting for an approval or an answer; these questions should not need one. Stopping." >&2
    exit 2
  elif [ "$status" -ne 0 ]; then
    echo "kagent/$agent failed (exit $status). Stopping." >&2
    exit "$status"
  fi
  echo
done

echo "Done: three agent runs. Open ${GRAFANA_URL}/d/agent-platform (it opens on the newest run)."
