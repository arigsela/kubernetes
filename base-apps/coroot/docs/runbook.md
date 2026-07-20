---
type: "Kubernetes App Runbook"
title: "Coroot — Runbook"
description: "Operational runbook for Coroot: failure modes, checks, and fixes."
app: coroot
catalog_entity: coroot
kind: runbook
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

# coroot runbook

## Failure modes

### Symptom: `Coroot` CR exists but no coroot-server/ClickHouse pods come up
This app has an operator/instance split: `coroot-operator.yaml` is a nested Argo CD `Application`
(named `coroot-operator`) that installs the operator chart; only once that operator is
Synced/Healthy does it reconcile the `Coroot` CR (`coroot-instance.yaml`) into real workloads.
- **Check:** `kubectl -n argo-cd get application coroot-operator` for sync/health status, then
  `kubectl -n coroot get pods -l app.kubernetes.io/name=coroot-operator` and its logs
  (`kubectl -n coroot logs -l app.kubernetes.io/name=coroot-operator`) for reconcile errors against
  the `Coroot` object (`kubectl -n coroot get coroot coroot -o yaml`).
- **Fix:** if the `coroot-operator` Application is out of sync (e.g. Helm chart version `0.6.0`
  pinned in `coroot-operator.yaml` no longer resolves), PR a version bump or values fix there. If
  the operator is healthy but the CR won't reconcile, PR a fix to `coroot-instance.yaml` (e.g. a
  malformed field) rather than editing the CR live.

### Symptom: eBPF node agent CrashLoopBackOff on some/all nodes
`nodeAgent` runs with `ebpfTracer.enabled: true` / `ebpfProfiler.enabled: true` and
`tolerations: [{operator: Exists}]` so it schedules on every node; it needs the privileged access
granted by `namespace-config.yaml`'s `pod-security.kubernetes.io/enforce: privileged` label.
- **Check:** `kubectl -n coroot get pods -l app.kubernetes.io/component=node-agent -o wide` and
  `kubectl -n coroot describe pod <pod>` for PodSecurity admission denials or eBPF/kernel errors;
  confirm the label is still present with `kubectl get ns coroot -o yaml`.
- **Fix:** PR restoring the `pod-security.kubernetes.io/enforce: privileged` label in
  `namespace-config.yaml` if it was changed, or adjust `nodeAgent.resources` in
  `coroot-instance.yaml` if pods are being OOMKilled (current limit is `memory: 1Gi`).

### Symptom: dashboard loads but shows no/incomplete metrics
Coroot has no bundled Prometheus — metrics come from `externalPrometheus.url:
http://prometheus.logging.svc.cluster.local:9090` (the `logging` app's Prometheus). Traces/logs/
profiles are unaffected since those go to Coroot's own bundled ClickHouse.
- **Check:** `kubectl -n coroot exec deploy/coroot -- wget -qO- http://prometheus.logging.svc.cluster.local:9090/-/healthy`
  (or check from any pod in-cluster), and `kubectl -n logging get pods -l app=prometheus` for the
  Prometheus StatefulSet's health.
- **Fix:** this is a `logging`-namespace problem, not a coroot-config problem — resolve Prometheus
  there. Only PR `coroot-instance.yaml` if the `externalPrometheus.url` itself needs to change
  (e.g. Prometheus moved namespace/service name).

## How-to

### Access the dashboard
`https://coroot.arigsela.com` — restricted by `nginx.ingress.kubernetes.io/whitelist-source-range`
in `ingress.yaml` to a fixed list of home/LAN CIDRs. A `403`/connection refusal from an otherwise
allowed network usually means that IP list is stale — PR an update to `ingress.yaml`.

### Deploy / update
Edit manifests under `base-apps/coroot/` and PR; Argo CD syncs `coroot` (and, via
`coroot-operator.yaml`, the separate `coroot-operator` Application) on merge.
