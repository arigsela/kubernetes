---
type: "Kubernetes App Runbook"
title: "Logging — Runbook"
description: "Operational runbook for Logging: failure modes, checks, and fixes."
app: logging
catalog_entity: logging
kind: runbook
namespace: logging
last_reviewed: 2026-08-18
status: current
tags: [loki, grafana, prometheus, alloy]
sources:
  - base-apps/logging/alloy-daemonset.yaml
  - base-apps/logging/loki-config.yaml
  - base-apps/logging/loki-deployment.yaml
  - base-apps/logging/loki-s3-external-secret.yaml
  - base-apps/logging/secret-store.yaml
  - base-apps/logging/grafana-deployment.yaml
  - base-apps/logging/httproute.yaml
---

# logging runbook

## Failure modes

### Symptom: Alloy DaemonSet pods CrashLooping / OOMKilled
- **Check:** `kubectl -n logging get pods -l app=alloy` — look for `OOMKilled` (exit code
  137) in `kubectl -n logging describe pod <alloy-pod>`, and restart counts. Then find out
  *where* the memory is before touching limits. Port-forward the pod
  (`kubectl -n logging port-forward <alloy-pod> 12345:12345`) and read its own metrics:

  ```bash
  curl -s localhost:12345/metrics | grep -E \
    'process_resident_memory_bytes|go_memstats_(heap_inuse|next_gc)_bytes|scrape_targets_gauge'
  curl -s localhost:12345/debug/pprof/heap > heap.pb.gz   # go tool pprof -top -inuse_space
  ```

  `next_gc_bytes` at or above `limits.memory` means Go is being killed before it collects,
  not that it needs more memory. `scrape_targets_gauge` above the local node's share means
  discovery has lost its node filter.
- **Fix:** raising `resources.limits.memory` is usually the wrong first move — it was raised
  twice already (128Mi/256Mi → 256Mi/512Mi) and the crashloop came back both times. Work
  through these in order:
  1. **Is `GOMEMLIMIT` still ~85% of `limits.memory`?** (Both in `alloy-daemonset.yaml`.) If
     they drift apart, Go sizes its heap target from host memory rather than the cgroup and
     the kernel kills the process before a GC runs. That was the 2026-08-18 crashloop:
     `next_gc` 527MB against a 512Mi limit, 86-97 restarts per pod.
  2. **Has Alloy's workload widened?** It should tail only pods on its own node (the
     `spec.nodeName` field selector in `alloy-config.yaml`) and collect no metrics at all.
     Removing the node filter, or re-adding `prometheus.scrape`/`remote_write` components,
     multiplies memory by the node count and makes every pod ship duplicate data — visible
     as Loki `entry too old` drops (`loki_write_dropped_entries_total`) and Prometheus
     `out of order sample` rejections (`prometheus_remote_storage_samples_failed_total`).
  3. **Only if neither holds** and live heap (`inuse_space`) is genuinely growing, raise
     `requests`/`limits` and `GOMEMLIMIT` together, keeping the ~85% ratio.

  Since Alloy is a DaemonSet, an OOMKilled pod only breaks log collection on that one node.

### Symptom: no logs in Loki from a whole node, or from a namespace that only runs there
- **Check:** `kubectl -n logging get pods -l app=alloy -o wide` and confirm there is one
  Alloy pod **per node** (`kubectl get nodes`). Then confirm the missing namespace's pods
  actually run on a node that has one:
  `kubectl get pods -A -o wide --field-selector status.phase=Running | grep <namespace>`.
  To see what a given Alloy is tailing:
  `kubectl -n logging port-forward <alloy-pod> 12345:12345` then
  `curl -s localhost:12345/api/v0/web/components/discovery.relabel.pods` — every target
  should carry the local node's `__meta_kubernetes_pod_node_name`.
- **Fix:** log discovery is scoped to the local node (`spec.nodeName` field selector in
  `alloy-config.yaml`), so **a node without an Alloy pod has no log collection at all** —
  the two settings are coupled. Don't add a `nodeSelector` to `alloy-daemonset.yaml` while
  that filter is in place. This bit us on 2026-08-18: the DaemonSet was pinned to
  `node.kubernetes.io/workload: application`, and when discovery became node-scoped every
  pod on `k3s-control-01` (argo-cd, kyverno, kube-system, cert-manager, external-secrets,
  dex, falco, atlantis) silently stopped shipping logs. If a new node is tainted such that
  the existing `NoSchedule`/`Exists` toleration doesn't cover it, widen the toleration
  rather than narrowing where Alloy runs.

### Symptom: Loki can't write logs / storage errors ("AccessDenied", "NoSuchBucket")
- **Check:** `kubectl -n logging get pods -l app=loki` and `kubectl -n logging logs
  deploy/loki` for S3 errors. Then confirm the credentials chain: `kubectl -n logging get
  externalsecret loki-s3-credentials -o yaml` (status/conditions should show `SecretSynced`),
  `kubectl -n logging get secret loki-s3-credentials` (should have `username`/`password`
  keys per `loki-s3-external-secret.yaml`'s template), and that Vault (`vault-backend`
  SecretStore, `secret-store.yaml`) is reachable and unsealed — Loki's S3 target is bucket
  `asela-chores-loki-logs-20251017` in `us-east-1` (`loki-config.yaml`).
- **Fix:** if the `ExternalSecret` isn't syncing, check Vault health first (a sealed/down
  Vault stops this and every other namespace's secret sync at once). If Vault is healthy but
  this secret specifically fails, verify the `logging` Vault role/policy grants read on the
  `loki-s3` KV v2 entry. If the bucket/region itself changed, PR the update to
  `loki-config.yaml`'s `common.storage.s3` and `storage_config.aws.s3` (both must match).

### Symptom: Grafana unreachable or dashboards missing
- **Check:** `kubectl -n logging get pods -l app=grafana` and
  `kubectl -n logging get ingress grafana-nginx` — confirm the `grafana-tls` cert is issued
  (`kubectl -n logging get certificate grafana-tls`) and the ingress host
  `grafana.arigsela.com` resolves. For missing dashboards/data, check the Loki/Prometheus
  datasources are reachable from inside the Grafana pod (`http://loki.logging.svc.cluster
  .local:3100`, `http://prometheus.logging.svc.cluster.local:9090` — both ClusterIP-only, no
  ingress) and that the `grafana-dashboard-provider` ConfigMap's folders (`Kubernetes`,
  `Istio`) still match the mounted dashboard ConfigMaps.
- **Fix:** PR any datasource URL or dashboard-provider path changes; a stuck cert-manager
  challenge for `grafana-tls` is a cert-manager issue, not this app.

## How-to

### Deploy / update
Edit manifests here and PR; Argo CD syncs on merge (`prune`/`selfHeal` enabled). All four
components are single-replica (Deployment or StatefulSet) — expect a brief gap in
collection/storage/visualization during a rolling update of any one of them.
