---
type: "Kubernetes App Runbook"
title: "whoami — Runbook"
description: "Operational runbook for whoami: failure modes, checks, and fixes."
app: whoami
catalog_entity: whoami
kind: runbook
namespace: whoami
last_reviewed: 2026-07-20
status: current
tags: ["whoami","test"]
sources:
  - base-apps/whoami/deployments.yaml
---

# whoami — Runbook

## Failure modes
_Fill in: known ways this app breaks and their observable symptoms._

## Checks
- Manifests: `base-apps/whoami/`
- Pods: `kubectl -n whoami get pods -l app=whoami`

## Fixes
_Fill in: remediation steps once a failure mode above is confirmed._
