---
type: "Kubernetes App Guide"
title: "Taskboard"
description: "Taskboard: a deliberately simple demo task tracker (stdlib Python, SQLite reset on restart) with a traffic generator, the target of the bug investigator demo; code and product docs in arigsela/taskboard, deployed by pinning a commit"
app: taskboard
catalog_entity: taskboard
kind: docs
namespace: taskboard
last_reviewed: 2026-10-27
status: draft
tags: [demo, bug-investigator]
sources:
  - base-apps/taskboard.yaml
  - base-apps/taskboard/deployment.yaml
  - base-apps/taskboard/network-policy.yaml
  - base-apps/taskboard/code-configmap.yaml
  - scripts/gen-taskboard-configmap.py
  - base-apps/logging/alloy-config.yaml
  - base-apps/logging/prometheus-config.yaml
  - id: taskboard-repo
    resource: https://github.com/arigsela/taskboard
    title: arigsela/taskboard (private app repo)
---

# Taskboard

Taskboard is a deliberately simple demo app: a small task tracker (projects, tasks, statuses,
roles) written in standard-library Python. It exists to be the target of the **bug investigator**
proof of concept: planted bugs ship in ordinary-looking deploys, a user files a symptom-only
issue, and the investigator finds the cause from the code, the deploy history, the logs and the
metrics. Nothing else depends on it, and it holds no real data.

## Where things live

| What | Where |
|---|---|
| Code, tests, traffic generator | `arigsela/taskboard` (private): `taskboard/` (server and modules, the page) and `traffic/simulate.py`[^taskboard-repo] |
| Product docs: configuration, statuses, permissions, schema, API (each rule has an ID such as `P3`) | that repo's `docs/`[^taskboard-repo] |
| What runs here, and which commit | `code-configmap.yaml` (generated) and `deployment.yaml` |
| Settings (env) | `deployment.yaml`; names and defaults in the app repo's `docs/configuration.md` |

This page covers how it runs in the cluster. For how the app behaves, read the app repo's docs:
they are the reference the investigator checks behaviour against.

## How it runs

Two Deployments run the same ConfigMap (`taskboard-code`) on the pinned `python:3.12-slim` image,
with no image build (alert-demo's pattern):

- **`taskboard`**: `server.py` on port 8080 (Service `taskboard`). One replica, `Recreate`. SQLite
  lives at `/data/taskboard.db` on an `emptyDir`, and the app seeds demo data on start, so **a
  restart resets the data**. That is fine: the evidence lives in Loki and Prometheus. Users are
  named by the `X-User` header and their roles come from configuration; there is no login.
- **`taskboard-traffic`**: `simulate.py`, a few personas (editors, a viewer, an admin) doing
  ordinary work against the Service every `INTERVAL_SECONDS` (3), so the logs and metrics have a
  normal baseline and a planted bug produces symptoms by itself.

Only the traffic pod and Prometheus can reach the app (`network-policy.yaml`). It has no
HTTPRoute: people open the page through `kubectl port-forward` (runbook).

## How deploys work

A deploy is a commit to this repo that pins an `arigsela/taskboard` commit:

1. `scripts/gen-taskboard-configmap.py --source <taskboard checkout> --commit <sha>` copies the
   app's files into `code-configmap.yaml` and writes, in `deployment.yaml`, both pods'
   `checksum/code` and the app pod's `taskboard.arigsela.com/source-commit` annotation. The
   ConfigMap carries the same commit (and `taskboard.arigsela.com/source-repo`).
2. The PR ("taskboard: deploy <sha>") is merged by a person; Argo CD syncs and the pods roll.
3. The app reads the annotation as `TASKBOARD_COMMIT` (downward API) and reports it.

So **the history of `taskboard.arigsela.com/source-commit` in this repo is Taskboard's deploy
timeline**: each change names the commit that went live, and its merge time is when (give or
take a minute for the sync and the pod start). `git log -p -- base-apps/taskboard/deployment.yaml`
lists them.

## Logs

Loki, stream selector `{namespace="taskboard"}`; add `app="taskboard"` for the server or
`app="taskboard-traffic"` for the generator. Alloy adds the labels `namespace`, `pod`,
`container`, `app` and `level` (from the JSON `level` field). Every line is one JSON object with
`time`, `level`, `logger` and `msg`, plus `request_id` while a request is handled; `| json`
extracts the rest. Domain events are lines whose `msg` names the event (for example
`status_changed`, `permission_denied` with the rule ID it applied, `archive_run`), with their own
fields; errors carry `error` (`Class: message`). The field list is the app repo's to define.

```logql
{namespace="taskboard", app="taskboard"} | json | msg="permission_denied"
```

## Metrics

Prometheus scrapes the app pod (`prometheus.io/scrape`, port 8080, `/metrics`) through the
`kubernetes-pods` job, which adds `namespace="taskboard"`, `pod`, `container` and `app`. The app's
own series are named `taskboard_*` (for example `taskboard_http_requests_total`,
`taskboard_http_request_duration_seconds`, `taskboard_tasks`, `taskboard_build_info`); the app
repo's docs list them. Container CPU and memory come from cAdvisor as for any pod.

```promql
sum by (route, status) (rate(taskboard_http_requests_total{namespace="taskboard"}[5m]))
```

[^taskboard-repo]: arigsela/taskboard (private app repo)
