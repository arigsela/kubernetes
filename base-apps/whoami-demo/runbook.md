---
type: "Kubernetes App Runbook"
title: "whoami-demo — Runbook"
description: "Operational runbook for whoami-demo: failure modes, checks, and fixes."
app: whoami-demo
catalog_entity: whoami-demo
kind: runbook
namespace: whoami-demo
last_reviewed: 2026-07-20
status: current
tags: []
sources:
  - base-apps/whoami-demo/deployments.yaml
---

# whoami-demo — Runbook

## Failure modes
_Fill in: known ways this app breaks and their observable symptoms._

## Checks
- Manifests: `base-apps/whoami-demo/`
- Pods: `kubectl -n whoami-demo get pods -l app=whoami-demo`

## Fixes
_Fill in: remediation steps once a failure mode above is confirmed._
