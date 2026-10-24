---
type: "Kubernetes App Runbook"
title: "alert-demo"
description: "Runbook: end a demo run early, clean up after one, the alert did not fire, a demo pull request was merged"
app: alert-demo
catalog_entity: alert-demo
kind: runbook
namespace: alert-demo
last_reviewed: 2026-10-24
status: stable
tags: [agents, demo]
sources:
  - base-apps/alert-demo/deployment.yaml
  - scripts/alert_demo.py
  - base-apps/hk-demo/runbook.md
---

# alert-demo runbook

## End a demo run early

Roll the pod (a change in git, or ask the owner): the new pod starts idle and the memory is gone.
The alert resolves at Grafana's next evaluation.

## After a demo run

- Close the pull request the implementer opened (author `app/arigsela-implementer`, title naming
  `alert-demo`). An open one counts toward the dispatcher's `open_prs_max` (3); at 3 the
  dispatcher skips real alert fixes.
- The run releases its memory by itself after `HOLD_SECONDS` (30 minutes), and the alert resolves.

## The alert did not fire

- **The run is not holding.** Check the hk demo page's status line, or the pod's last log lines
  (`event: alert-demo`, `state: holding`). A pod restart drops the run.
- **The working set is under 90% of the limit.** Grafana: Cluster Overview → Pod pressure → Memory
  used vs limit, for `alert-demo`. If a merged pull request raised the limit, see below.
- **It fired but nothing was investigated.** Check the dispatcher's `dispatch` line for it in Loki:
  - `{namespace="dispatcher"} |= "alert-demo"`;
  - `skip-repeat` means this pod was investigated in the last 6 hours (roll the pod or wait);
  - `skip-open-prs` means the implementer has 3 open pull requests.

## A demo pull request was merged

The limit grew by half, for example 256Mi → 384Mi. The next run then holds the same 236Mi, stays
under 90% and fires nothing. Open a pull request that puts the limit (and its request) back to
256Mi / 64Mi.
