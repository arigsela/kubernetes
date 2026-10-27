---
type: "Kubernetes App Runbook"
title: "Taskboard"
description: "Runbook: open the page through a port-forward, deploy a Taskboard commit, roll back a deploy, tail the logs, the pod is not ready"
app: taskboard
catalog_entity: taskboard
kind: runbook
namespace: taskboard
last_reviewed: 2026-10-27
status: draft
tags: [demo, bug-investigator]
sources:
  - base-apps/taskboard/deployment.yaml
  - base-apps/taskboard/network-policy.yaml
  - scripts/gen-taskboard-configmap.py
  - id: taskboard-repo
    resource: https://github.com/arigsela/taskboard
    title: arigsela/taskboard (private app repo)
---

# Taskboard runbook

## Open the page

There is no public URL. Port-forward the Service and open <http://localhost:8080/>:

```bash
kubectl -n taskboard port-forward svc/taskboard 8080:8080
```

The port-forward enters the pod's own network namespace, so `network-policy.yaml` does not block
it. There is no login: the app names the user by the `X-User` header, and the configured users and
roles are in the app repo's `docs/configuration.md`[^taskboard-repo].

## Deploy a Taskboard commit

A deploy pins an `arigsela/taskboard` commit in this repo:

```bash
git -C ../taskboard checkout main && git -C ../taskboard pull
python3 scripts/gen-taskboard-configmap.py --source ../taskboard \
  --commit "$(git -C ../taskboard rev-parse HEAD)"
python3 -m pytest tests/taskboard/ -q
```

The generator refuses a checkout that is not at `--commit` or has uncommitted changes to the
deployed files, a subdirectory in `taskboard/`, a file over 200 KB, and a file it cannot write as
a clean YAML block (trailing spaces, tabs, CR line endings: fix those in the app repo). Commit
`code-configmap.yaml` and `deployment.yaml` together on a branch and open a PR titled
"taskboard: deploy <short sha>". When it merges, Argo CD syncs and both pods roll (the
`checksum/code` annotation changed); the app pod also rolls when only the commit changed.
`--check` with the same arguments says whether the committed files already match that commit.

Check it went live:

```bash
kubectl -n taskboard get pods -l app=taskboard \
  -o jsonpath='{.items[*].metadata.annotations.taskboard\.arigsela\.com/source-commit}'
```

## Roll back a deploy

Revert the pin PR (`git revert <merge commit>`, open the PR, merge). That restores the previous
ConfigMap, checksum and source commit together, and the pods roll back. Don't edit the ConfigMap or
the annotations by hand: CI fails if they disagree. A rollback is itself a deploy, so it shows on
the timeline like any other.

## Tail the logs

In Grafana Explore (Loki):

```logql
{namespace="taskboard", app="taskboard"} | json
{namespace="taskboard", app="taskboard-traffic"}
```

From a terminal:

```bash
kubectl -n taskboard logs deploy/taskboard -f
kubectl -n taskboard logs deploy/taskboard-traffic -f
```

## The pod is not ready

- **Check:** `kubectl -n taskboard get pods` and the last log lines of `deploy/taskboard`. A bad
  setting fails at start with a `ConfigError` naming the variable.
- **Fix:** a code fault is fixed in the app repo and deployed (above), or rolled back. The data
  resets on every restart (SQLite on an `emptyDir`, seeded on start): that is expected.

## The traffic generator stopped

- **Check:** `kubectl -n taskboard logs deploy/taskboard-traffic --tail=20`. It calls
  `TASKBOARD_URL` every `INTERVAL_SECONDS` (`deployment.yaml`) and logs each result.
- **Fix:** it recovers by itself once the app is ready again; a crash loop is a code fault in
  `traffic/simulate.py`, fixed and deployed like the app.

[^taskboard-repo]: arigsela/taskboard (private app repo)
