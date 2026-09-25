---
type: "Kubernetes App Guide"
title: "Logging"
description: "Observability stack (Alloy collector, Loki logs on S3, Prometheus metrics, Grafana)"
app: logging
catalog_entity: logging
kind: docs
namespace: logging
last_reviewed: 2026-08-18
status: current
tags: [loki, grafana, prometheus, alloy]
sources:
  - base-apps/logging/alloy-config.yaml
  - base-apps/logging/alloy-daemonset.yaml
  - base-apps/logging/alloy-rbac.yaml
  - base-apps/logging/loki-config.yaml
  - base-apps/logging/loki-deployment.yaml
  - base-apps/logging/loki-s3-external-secret.yaml
  - base-apps/logging/secret-store.yaml
  - base-apps/logging/grafana-deployment.yaml
  - base-apps/logging/httproute.yaml
  - base-apps/logging/grafana-dashboard-configmap.yaml
  - base-apps/logging/istio-ambient-dashboard.yaml
  - base-apps/logging/grafana-dashboard-cluster-overview.yaml
  - base-apps/logging/prometheus-config.yaml
  - base-apps/logging/prometheus-statefulset.yaml
---

# logging

## What it is
The cluster's observability stack: four components deployed together in the `logging`
namespace — Grafana Alloy (collector), Loki (log store), Prometheus (metrics store), and
Grafana (visualization). There is no Helm chart; everything is plain Kubernetes manifests
under `base-apps/logging/`.

## Pipeline
1. **Alloy** (`alloy-daemonset.yaml`, image `grafana/alloy:v1.4.3`) runs as a DaemonSet on
   **every** node including `k3s-control-01` (no `nodeSelector`; the `NoSchedule` toleration
   covers the control-plane taint), using a cluster-wide RBAC
   ClusterRole/ClusterRoleBinding (`alloy-rbac.yaml`) to discover pods. It collects **logs,
   plus host metrics** (`alloy-config.yaml`). Logs: `discovery.kubernetes` lists pods on its own node (field
   selector `spec.nodeName=` + `sys.env("HOSTNAME")`, where the DaemonSet injects `HOSTNAME`
   from `spec.nodeName`), `loki.source.kubernetes` tails those containers **through the
   Kubernetes API**, and the pipeline parses JSON fields, drops nginx health-check and
   non-`development` `[DEBUG]` lines, then pushes to
   `http://loki.logging.svc.cluster.local:3100/loki/api/v1/push`.
   Alloy collects **no Kubernetes metrics** — Prometheus scrapes those itself (step 3).
   The one exception (2026-09-24) is **host metrics**, which nothing else collects:
   `prometheus.exporter.unix` (node-exporter built into Alloy) reads the node's own
   `/proc`, `/sys` and `/`, mounted **read-only** at `/host/*`, with only the collectors
   the Cluster Overview dashboard uses (cpu, diskstats, filesystem, loadavg, meminfo,
   pressure, stat, uname). It labels every series `node=<node name>` and remote-writes to
   Prometheus as `job="node-exporter"`. netdev/netstat are left out because without
   `hostNetwork` they would report the pod's network namespace, not the node's.
   Until 2026-08-18 it did both, and neither half was scoped to the local node, so every
   Alloy pod scraped all three nodes plus every annotated pod and tailed every pod in the
   cluster. That duplicated collection was ~160MB of the ~240MB live heap per pod and
   OOM-killed them in a loop; see `runbook.md`.
   Because discovery is node-scoped, a node with no Alloy pod gets no log collection at
   all — which is why the DaemonSet must stay unrestricted by `nodeSelector`.
   Because tailing goes through the API, the `varlog`/`varlibdockercontainers` `hostPath`
   mounts in `alloy-daemonset.yaml` are vestigial from an earlier file-tailing config and are
   not read by the current pipeline. `runAsUser: 0` is now used: the host-metrics exporter
   reads root-owned host files.
2. **Loki** (`loki-deployment.yaml`, single-replica Deployment, image `grafana/loki:3.2.1`,
   `-target=all` monolithic mode) receives log pushes and stores chunks/index in **S3**
   (`loki-config.yaml`: `common.storage.s3` and `storage_config.aws.s3` both point at bucket
   `asela-chores-loki-logs-20251017` in `us-east-1`, created by Crossplane). Retention is 30
   days (`limits_config.retention_period: 720h`) with the compactor handling delete requests
   against S3. Loki has no local index/chunk PVC — S3 is the only durable store (the pod's
   `/loki` mount is an `emptyDir`).
3. **Prometheus** (`prometheus-statefulset.yaml`, single-replica StatefulSet, image
   `prom/prometheus:v3.0.1`) stores metrics on a 50Gi `local-path` PVC with 15-day retention
   (`--storage.tsdb.retention.time=15d`) bounded by a 40GB size cap
   (`--storage.tsdb.retention.size=40GB`, base-2, so ~43 GB on disk). The size cap matters
   because `local-path` is a hostPath directory with no quota enforcement — without it the
   TSDB grew to 59 GB, past its own 50Gi request, consuming worker-01's root filesystem.
   Whichever limit trips first wins, so sustained ingest growth shortens the retention
   window rather than filling the node. `prometheus-config.yaml` has it scrape the Kubernetes
   API server, nodes, cAdvisor, and any pod/service annotated `prometheus.io/scrape: "true"`.
   Since Alloy stopped forwarding Kubernetes metrics (2026-08-18) this service discovery is
   their only path into the TSDB. `--web.enable-remote-write-receiver` carries exactly one
   stream: Alloy's host metrics (`job="node-exporter"`, step 1).
4. **Grafana** (`grafana-deployment.yaml`, single-replica Deployment, image
   `grafana/grafana:11.3.9`, 10Gi `local-path` PVC) is provisioned with two datasources
   (`grafana-datasources` ConfigMap): `Loki` at `http://loki.logging.svc.cluster.local:3100`
   and `Prometheus` (default) at `http://prometheus.logging.svc.cluster.local:9090`.
   Dashboards are file-provisioned (`grafana-dashboard-provider` ConfigMap) into folders:
   `Kubernetes` — **Cluster Overview** (`grafana-dashboard-cluster-overview.yaml`, uid
   `cluster-overview`: k3s version per node and API server, version skew, node
   Ready/cordoned, host CPU/memory/root-disk/load/PSI, namespace CPU and memory, PVC fill,
   API server errors, Argo sync/health, and a **Pod pressure** section: per-pod PSI CPU,
   memory and IO stall, CPU throttling, and memory used vs limit). The JSON is
   **generated** by `scripts/gen-cluster-overview-dashboard.py` (CI runs it with
   `--check`; never hand-edit it). Its ConfigMap keeps the historical name
   `grafana-dashboard-k8s-basic`, which until 2026-09-24 existed only in the cluster with an
   empty placeholder. Also `Istio` (`istio-ambient-dashboard.yaml`) and `Security`
   (`grafana-dashboard-coraza.yaml`, Coraza WAF).
   Alert rules (`grafana-alerting.yaml`, delivered to n8n → Slack) cover agent guardrails
   and Falco from Loki, and cluster health from Prometheus: **Pod CPU starved** (PSI CPU
   stall > 20% for 30m) and **Container near memory limit** (working set > 90% of the
   limit for 15m). Prometheus rules reference the datasource by its pinned uid
   `PBFA97CFB590B2093`. Grafana reads alerting provisioning only at startup, so its pod
   template carries a `checksum/alerting` annotation of that ConfigMap
   (`tests/dashboards/test_grafana_alerting.py` fails when it is stale) — a merged rule
   change restarts Grafana. The Deployment uses `Recreate`, because a rolling update would
   run two Grafana processes on the same SQLite file.

## External access
Grafana is exposed via `grafana-ingress.yaml`: nginx `Ingress` at host `grafana.arigsela.com`,
TLS via `cert-manager.io/cluster-issuer: letsencrypt-prod` into secret `grafana-tls`. Loki and
Prometheus are ClusterIP-only (no ingress) — accessed from inside the cluster (Alloy, Grafana)
or via port-forward.

## How it wires to other apps
Loki's S3 credentials come from Vault: `loki-s3-external-secret.yaml` is an `ExternalSecret`
resolving `loki-s3` (`aws_access_key_id`/`aws_secret_access_key`) through the `vault-backend`
`SecretStore` (`secret-store.yaml`, Vault KV v2 at `k8s-secrets`, Kubernetes auth role
`logging`) into the `loki-s3-credentials` Secret that `loki-deployment.yaml` mounts as
`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`. Grafana's admin credentials
(`GF_SECURITY_ADMIN_USER`/`GF_SECURITY_ADMIN_PASSWORD`) are plain env vars in
`grafana-deployment.yaml`, not Vault-sourced.
