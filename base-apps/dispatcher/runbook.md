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
The finding says "paused" and names the command. The dispatcher never answers (§V.88), and
the kagent UI cannot see a dispatcher-owned session: continue it as the dispatcher's
identity, `scripts/ask-hk --agent kagent/<agent> --as-serviceaccount dispatcher/dispatcher
--context <id>` (an answer), or `… --task <task> --approve | --reject REASON` (an approval
the laptop never saw). If a read-only agent pauses, something bound a gated tool to it;
`tests/dispatch/test_table.py` would have failed, so check what changed. The `restarter` is
meant to pause: see the next section.

## A restart is waiting for approval
A memory alert whose verdict was `leak` produces a second Slack message, "Restart awaiting
approval", listing the pending `k8s_delete_resource` call with its exact arguments (resource
type `pod`, name, namespace), the implementer session that holds the evidence, and two
commands. The dispatcher prints the approve command only when the pending call is exactly
one delete of the alert's pod; anything else is headed "MISMATCH" and carries a reject
command only (one `--approve` would approve every pending call). Read the arguments, then:
```
scripts/ask-hk --agent kagent/restarter --as-serviceaccount dispatcher/dispatcher --context <id> --task <task> --approve
scripts/ask-hk --agent kagent/restarter --as-serviceaccount dispatcher/dispatcher --context <id> --task <task> --reject "<reason>"
```
`--task` matters: this laptop never saw the pause (the dispatcher opened the session), so
ask-hk fetches the pending request by task id first (A2A `GetTask`). The kagent UI cannot
decide it: the session is owned by the dispatcher's service account, not the UI's identity.
The pod's owner recreates it (for a StatefulSet with `OnDelete`, this is also how it picks up
a new template). The dispatcher never answers (§V.88). There is no second restart for the
same namespace/workload within 24 h ("Recurring leak" in the finding; a Deployment's
replacement pod has a new name, so the key is the workload, not the alert) and at most
`restarts_per_day` (2) a day ("Restart cap reached"): a leak that recurs needs an upstream
fix, not another restart. "No restart requested: namespace … is never restarted" means the
dispatcher refused the target before any session opened (its deny list is the restarter's).
If the message says `no restart: <reason>`, the restarter itself refused (no owner, a
deny-listed namespace, a target it was not given); nothing was deleted. `verdict: null` in
the dispatcher log (`event: remediate`) means the implementer's answer had no verdict line:
a prompt regression, see `tests/implementer`.

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
