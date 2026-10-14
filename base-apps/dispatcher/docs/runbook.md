---
type: "Kubernetes App Runbook"
title: "Dispatcher"
description: "Runbook: no investigation after an alert, a wrong route, the budget, replaying a signal"
app: dispatcher
catalog_entity: dispatcher
kind: runbook
namespace: dispatcher
last_reviewed: 2026-10-12
status: stable
tags: [agents, dispatch, grafana, a2a]
sources:
  - base-apps/dispatcher/dispatch-table.yaml
  - base-apps/dispatcher/deployment.yaml
  - scripts/dispatch.py
---

# Dispatcher runbook

## An alert fired in Slack but no investigation followed
1. Was it routed? `kubectl logs -n dispatcher deploy/dispatcher | grep '"event": "dispatch"'`
   and read `decision`: `none` or `unmatched` means the table said so (add or change a row);
   `skip-budget` / `skip-repeat` means the limits held; `resolved` means the notification was
   the all-clear.
2. Did the investigation finish? `grep '"event": "finding"'`: `error` names an A2A failure
   or a post failure; `session` is the kagent session to open in the UI
   (`https://kagent.arigsela.com/agents/<session>/chat`).
3. Is Grafana delivering at all? Grafana → Alerting → Contact points → `n8n` → the second
   integration's last delivery. A 404 there means the Service or the pod is gone.

## The agent paused instead of answering
The finding says "paused". The dispatcher never answers (§V.88): open the session in the UI
and decide there. If a read-only agent pauses, something bound a gated tool to it;
`tests/dispatch/test_table.py` would have failed, so check what changed.

## Replay a signal by hand (also the acceptance test)
From inside the cluster, post a synthetic payload for a real pod:
```
kubectl run -n dispatcher curl --rm -i --restart=Never --labels=dispatcher/replay=true --image=curlimages/curl:8.11.1 -- \
  sh -c 'sleep 15; curl -s -X POST http://dispatcher.dispatcher.svc.cluster.local:8080/signal/grafana \
  -H 'Content-Type: application/json' \
  -d "{\"alerts\":[{\"status\":\"firing\",\"fingerprint\":\"manual-1\",\"labels\":{\"alertname\":\"Pod CPU starved\",\"namespace\":\"n8n\",\"pod\":\"<a real pod>\"},\"annotations\":{\"summary\":\"manual replay\",\"runbook\":\"none\"}}]}"'
```
Expect `{"decisions":["investigate"]}`, then a Slack message within five minutes and a
session owned by the dispatcher's service account (continue it with `scripts/ask-hk
--as-serviceaccount dispatcher/dispatcher --agent kagent/<agent> --context <session>`). Use a new `fingerprint` each time, or wait
`repeat_after_hours`. The `--labels=dispatcher/replay=true` is what `network-policy.yaml` admits; without it
the POST is reset (only Grafana's pods may reach the webhook). The `sleep 15` matters: k3s's
NetworkPolicy controller adds a new pod's IP to its allow set a few seconds after the pod
starts, and a curl fired at once is reset too (seen 2026-10-14). For the two resource alerts (routed to `implementer`) the finding ends
with a pull request link, or `no pull request: <reason>`; the PR is authored by
`app/arigsela-implementer` and waits for you. `{"decisions":["skip-open-prs"]}` means that
agent already has `open_prs_max` pull requests open: merge or close one first.

## Change the table or the code
Edit `dispatch-table.yaml` (or `scripts/dispatch.py`), then run
`python scripts/gen-dispatcher-configmap.py --repo-root .` and `python -m pytest tests/dispatch/ -q`,
and open a PR. The generator rewrites the code ConfigMap and the two `checksum/*` annotations
on the Deployment's pod template, so Argo CD's sync rolls the pod and it reads the new files
at start. No `kubectl` is needed; a stale checksum fails CI (`tests/dispatch/test_manifests.py`).
