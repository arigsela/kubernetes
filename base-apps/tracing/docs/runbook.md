---
type: "Kubernetes App Runbook"
title: "Tracing — Runbook"
description: "Operational runbook for Tracing: Tempo startup and S3 access, the image smoke Job, Grafana datasource."
app: tracing
catalog_entity: tracing
kind: runbook
namespace: tracing
last_reviewed: 2026-10-19
status: draft
tags: [tempo, tracing, runbook]
sources:
  - base-apps/tracing/tempo.yaml
  - base-apps/tracing/smoke-job.yaml
  - base-apps/tracing-aws-infrastructure/iam.yaml
  - base-apps/logging/grafana-deployment.yaml
---

# Tracing — Runbook

Everything here is a Git change. Never `kubectl apply/edit/patch/delete`; the read-only
commands below only inspect.

## Failure modes

### Symptom: the tracing sync is stuck or failed at PreSync (`tracing-smoke`)
- **Check:** `kubectl -n tracing logs job/tracing-smoke --all-containers --prefix`.
- **A container exited 132 (SIGILL):** that image was built for AVX2 (x86-64-v3), which these
  Xeon E5-2670 nodes lack. Do not roll it out; pin an older digest or a v2 build.
- Nothing else in the cluster is affected: the Job gates only this app.

### Symptom: `tempo` CrashLoopBackOff or not Ready
- **Check:** `kubectl -n tracing logs deploy/tempo --tail=100`.
- `secret "tempo-s3-creds" not found`: Crossplane has not written the key yet. Check
  `kubectl get accesskey.iam.aws.upbound.io tempo-s3-key` (READY/SYNCED) and the
  `tracing-aws-infrastructure` app.
- `AccessDenied` from S3: the policy or its attachment is not ready, or the bucket name in
  `tempo.yaml` and `iam.yaml` disagree. `kubectl get policy.iam.aws.upbound.io tempo-s3-access`.
- A config error: Tempo names the bad key and exits. Fix `tempo-config` and update
  `checksum/config` (`tests/beacon/test_tracing.py` checks it).

### Symptom: Grafana's Tempo datasource fails ("Data source connection error")
- **Check:** `kubectl -n tracing get endpoints tempo` and Tempo's readiness. Grafana reads
  datasource provisioning only at startup: a datasource change must bump
  `checksum/datasources` on the Grafana Deployment (tested in `tests/beacon/test_tracing.py`).

### Symptom: no traces for a service
- Until beacon §T.19 nothing sends to Tempo. After it, check the collector first (its runbook
  section lands with it).
