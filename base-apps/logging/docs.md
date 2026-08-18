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
   ClusterRole/ClusterRoleBinding (`alloy-rbac.yaml`) to discover pods. It collects **logs
   only** (`alloy-config.yaml`): `discovery.kubernetes` lists pods on its own node (field
   selector `spec.nodeName=` + `sys.env("HOSTNAME")`, where the DaemonSet injects `HOSTNAME`
   from `spec.nodeName`), `loki.source.kubernetes` tails those containers **through the
   Kubernetes API**, and the pipeline parses JSON fields, drops nginx health-check and
   non-`development` `[DEBUG]` lines, then pushes to
   `http://loki.logging.svc.cluster.local:3100/loki/api/v1/push`.
   Alloy deliberately collects **no metrics** — Prometheus scrapes those itself (step 3).
   Until 2026-08-18 it did both, and neither half was scoped to the local node, so every
   Alloy pod scraped all three nodes plus every annotated pod and tailed every pod in the
   cluster. That duplicated collection was ~160MB of the ~240MB live heap per pod and
   OOM-killed them in a loop; see `runbook.md`.
   Because discovery is node-scoped, a node with no Alloy pod gets no log collection at
   all — which is why the DaemonSet must stay unrestricted by `nodeSelector`.
   Because tailing goes through the API, the `varlog`/`varlibdockercontainers` `hostPath`
   mounts and `privileged: true`/`runAsUser: 0` in `alloy-daemonset.yaml` are vestigial from
   an earlier file-tailing config and are not read by the current pipeline.
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
   Since Alloy stopped forwarding metrics (2026-08-18) this service discovery is the only
   path into the TSDB; `--web.enable-remote-write-receiver` is still enabled but nothing
   writes to it.
4. **Grafana** (`grafana-deployment.yaml`, single-replica Deployment, image
   `grafana/grafana:11.3.1`, 10Gi `local-path` PVC) is provisioned with two datasources
   (`grafana-datasources` ConfigMap): `Loki` at `http://loki.logging.svc.cluster.local:3100`
   and `Prometheus` (default) at `http://prometheus.logging.svc.cluster.local:9090`.
   Dashboards are file-provisioned (`grafana-dashboard-provider` ConfigMap) from two folders:
   `Kubernetes` (`grafana-dashboard-configmap.yaml`, a basic cluster dashboard) and `Istio`
   (`istio-ambient-dashboard.yaml`, the Istio ambient mesh dashboard).

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
