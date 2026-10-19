---
type: "Kubernetes App Guide"
title: "Tracing"
description: "Distributed tracing: Tempo 3.x on S3, queried from Grafana; the platform's trace store, added for beacon's Phase 3"
app: tracing
catalog_entity: tracing
kind: docs
namespace: tracing
last_reviewed: 2026-10-19
status: draft
tags: [tempo, tracing, opentelemetry]
sources:
  - base-apps/tracing.yaml
  - base-apps/tracing/tempo.yaml
  - base-apps/tracing/smoke-job.yaml
  - appsets/managed-apps/tracing-aws-infrastructure.yaml
  - base-apps/tracing-aws-infrastructure/s3-bucket.yaml
  - base-apps/tracing-aws-infrastructure/s3-lifecycle.yaml
  - base-apps/tracing-aws-infrastructure/iam.yaml
  - base-apps/logging/grafana-deployment.yaml
  - tests/beacon/test_tracing.py
---

# Tracing

## What it is
The cluster's trace store. Before it, Coroot was the only place traces went (kagent and
Substrate export there directly, `base-apps/kagent.yaml`). This app was built for beacon's
Phase 3 (`arigsela/beacon` SPEC, beacon §T.18 to beacon §T.23): proving that a service with
no tracing code still shows up in Tempo because platform policy instruments it. Every
comment cites beacon's sections as `beacon §T.n` / `beacon §V.n`, never a bare `§T.n` (that
is this repo's own SPEC.md).

| Workload | Kind | What it does |
|---|---|---|
| `tempo` | Deployment, 1 replica | Tempo 3.1 single binary: receives OTLP gRPC on 4317, keeps recent traces in its live-store, writes blocks to S3, serves queries on 3200. No Kafka in this mode. |
| `tracing-smoke` | PreSync Job | Runs every third-party image of beacon's Phase 3 on a node before any of it rolls out (beacon §V.18): Tempo, the collector, the OpenTelemetry Operator and its Python layer. |

The OpenTelemetry Collector (the only thing that sends to Tempo) arrives in beacon §T.19.

## Storage
- Bucket `asela-tempo-traces-20261019` (us-east-2), its IAM user, policy and access key are
  the managed app `tracing-aws-infrastructure`. The `AccessKey` writes Secret
  `tempo-s3-creds` straight into this namespace (keys `username`, `attribute.secret`), so
  no key is ever made by hand.
- Retention: Tempo deletes blocks after 168h; the bucket expires anything left after 8 days.
- The live-store WAL is an `emptyDir`: a restart loses only traces not yet flushed (minutes).

## Grafana
`base-apps/logging/grafana-deployment.yaml` provisions the `Tempo` datasource (uid `tempo`).
Trace to logs: from a span, "Logs for this span" queries Loki by namespace and pod around
the span's time. Logs to trace: Loki's derived field `trace_id=([0-9a-f]{32})` turns a log
line's trace id into a link to Tempo. beacon's services write that field (beacon §T.43).

## Known limits
- One replica, `Recreate` rollouts: Tempo is briefly down on every config change.
- Metrics-generator is off (no span metrics, no TraceQL metrics) until its cost is measured.
