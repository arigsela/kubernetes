---
type: "Kubernetes App Runbook"
title: "${{ values.name }} — Runbook"
description: "Operational runbook for ${{ values.name }}: failure modes, checks, and fixes."
app: ${{ values.name }}
catalog_entity: ${{ values.name }}
kind: runbook
namespace: ${{ values.namespace }}
last_reviewed: 2026-07-20
status: current
tags: [{% for t in values.tags %}"${{ t }}"{% if not loop.last %}, {% endif %}{% endfor %}]
sources:
  - base-apps/${{ values.name }}/deployments.yaml
---

# ${{ values.name }} — Runbook

## Failure modes
_Fill in: known ways this app breaks and their observable symptoms._

## Checks
- Manifests: `base-apps/${{ values.name }}/`
- Pods: `kubectl -n ${{ values.namespace }} get pods -l app=${{ values.name }}`

## Fixes
_Fill in: remediation steps once a failure mode above is confirmed._
