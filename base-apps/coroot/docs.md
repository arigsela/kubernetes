---
type: "Kubernetes App Guide"
title: "Coroot"
description: "eBPF-based observability/APM (Coroot operator + instance, node/cluster agents, ClickHouse)"
app: coroot
catalog_entity: coroot
kind: docs
namespace: coroot
last_reviewed: 2026-07-10
status: current
tags: [observability, ebpf, apm, clickhouse]
sources:
  - base-apps/coroot/coroot-operator.yaml
  - base-apps/coroot/coroot-instance.yaml
  - base-apps/coroot/ingress.yaml
  - base-apps/coroot/namespace-config.yaml
---

# coroot

## What it is
[Coroot](https://coroot.com) is an eBPF-based observability/APM platform: it auto-instruments
services at the node level (no code changes) to collect metrics, distributed traces, logs, and
continuous CPU/memory profiles, and correlates them into a service map and root-cause views.

## Architecture & deploy model: operator + instance split
This app is deployed in two layers, both sourced from `base-apps/coroot/`:

1. **Operator** (`coroot-operator.yaml`) — a *nested* Argo CD `Application` named `coroot-operator`
   that installs the `coroot-operator` Helm chart (`coroot.github.io/helm-charts`, version `0.6.0`)
   into the `coroot` namespace. Unlike every other app in this repo, this Application manifest is
   not a top-level `base-apps/*.yaml` file — it lives inside `base-apps/coroot/` and is applied as
   part of the `coroot` app's own sync, which is what bootstraps the second, independently-synced
   `coroot-operator` Application (visible in Argo CD as its own app).
2. **Instance** (`coroot-instance.yaml`) — a `Coroot` custom resource (`coroot.com/v1`) that the
   operator (once installed) reconciles into the actual Coroot workloads: the Coroot server,
   a bundled ClickHouse, a per-node eBPF agent, and a cluster agent.

## What it observes and how
The `Coroot` CR (`coroot-instance.yaml`) configures:
- **Node Agent** (`nodeAgent`): eBPF-based tracer and profiler (`ebpfTracer.enabled: true`,
  `ebpfProfiler.enabled: true`) plus log collection (`logCollector`), tolerating all taints
  (`tolerations: [{operator: Exists}]`) so it runs on every node — hence
  `namespace-config.yaml` labels the `coroot` namespace `pod-security.kubernetes.io/enforce: privileged`,
  required for the agent's privileged access.
- **Cluster Agent** (`clusterAgent`): collects Kubernetes object metadata.
- **Metrics refresh**: `metricsRefreshInterval: 15s`, `cacheTTL: 30d`.

## Storage: external Prometheus, bundled ClickHouse
- **Metrics**: Coroot does **not** bundle its own Prometheus — it points at the cluster's existing
  one via `externalPrometheus.url: http://prometheus.logging.svc.cluster.local:9090` (the
  `logging` app's Prometheus, `base-apps/logging/prometheus-statefulset.yaml`).
- **Traces / logs / profiles**: Coroot bundles its own single-shard, single-replica **ClickHouse**
  (`clickhouse: {shards: 1, replicas: 1}`), backed by a 20Gi `local-path` PVC. Retention is
  `tracesTTL`/`logsTTL`/`profilesTTL`: `7d` each.
- **Coroot server data**: a separate 10Gi `local-path` PVC (`storage.size: 10Gi`).
- Single replica for both the Coroot server and ClickHouse (`replicas: 1` at each level) — no HA.

## Ingress
`ingress.yaml` exposes the `coroot-coroot` Service (port `8080`) at `coroot.arigsela.com` via the
shared `nginx` IngressClass, TLS from `letsencrypt-prod` (`cert-manager.io/cluster-issuer`), with a
`nginx.ingress.kubernetes.io/whitelist-source-range` restricting access to a short list of home/LAN
IPs, and long (`600s`) read/send timeouts for dashboard loading.
