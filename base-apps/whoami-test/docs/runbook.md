---
type: "Kubernetes App Runbook"
title: "whoami-test — Runbook"
description: "Operational runbook for whoami-test: failure modes, checks, and fixes."
app: whoami-test
catalog_entity: whoami-test
kind: runbook
namespace: whoami-test
last_reviewed: 2026-07-20
status: current
tags: ["test", "whoami"]
sources:
  - base-apps/whoami-test/deployments.yaml
---

# whoami-test — Runbook

## Failure modes
_Fill in: known ways this app breaks and their observable symptoms._

## Checks
- Manifests: `base-apps/whoami-test/`
- Pods: `kubectl -n whoami-test get pods -l app=whoami-test`

## Fixes
_Fill in: remediation steps once a failure mode above is confirmed._
