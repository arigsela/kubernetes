---
type: "Kubernetes App Guide"
title: "${{ values.name }}"
description: "${{ values.description }}"
app: ${{ values.name }}
catalog_entity: ${{ values.name }}
kind: docs
namespace: ${{ values.namespace }}
last_reviewed: 2026-07-20
status: current
tags: ${{ values.tags | dump }}
sources:
  - base-apps/${{ values.name }}/deployments.yaml
---

# ${{ values.name }}

## What it is
${{ values.description }}

## Architecture & data flow
_Fill in: how requests flow, dependencies, config sources._

## Where config lives
- Manifests: `base-apps/${{ values.name }}/`
