---
app: logging
catalog_entity: logging
kind: docs
namespace: logging
last_reviewed: 2026-07-10
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
  - base-apps/logging/grafana-ingress.yaml
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
   every `node.kubernetes.io/workload: application` node, using a cluster-wide RBAC
   ClusterRole/ClusterRoleBinding (`alloy-rbac.yaml`) to discover pods/nodes. Its pipeline
   config (`alloy-config.yaml`) does two things in parallel:
   - **Logs**: discovers running pods, tails `/var/log/pods/...` (mounted `hostPath`
     `varlog`/`varlibdockercontainers`), parses JSON fields, drops nginx health-check and
     non-`development` `[DEBUG]` lines, and pushes to
     `http://loki.logging.svc.cluster.local:3100/loki/api/v1/push`.
   - **Metrics**: scrapes pods annotated `prometheus.io/scrape: "true"`, plus node and
     cAdvisor metrics (via the node's kubelet on port 10250), and remote-writes to
     `http://prometheus.logging.svc.cluster.local:9090/api/v1/write`.
2. **Loki** (`loki-deployment.yaml`, single-replica Deployment, image `grafana/loki:3.2.1`,
   `-target=all` monolithic mode) receives log pushes and stores chunks/index in **S3**
   (`loki-config.yaml`: `common.storage.s3` and `storage_config.aws.s3` both point at bucket
   `asela-chores-loki-logs-20251017` in `us-east-1`, created by Crossplane). Retention is 30
   days (`limits_config.retention_period: 720h`) with the compactor handling delete requests
   against S3. Loki has no local index/chunk PVC — S3 is the only durable store (the pod's
   `/loki` mount is an `emptyDir`).
3. **Prometheus** (`prometheus-statefulset.yaml`, single-replica StatefulSet, image
   `prom/prometheus:v3.0.1`) stores metrics on a 50Gi `local-path` PVC with 15-day retention
   (`--storage.tsdb.retention.time=15d`) and has `--web.enable-remote-write-receiver` on so it
   can accept Alloy's remote-write pushes. `prometheus-config.yaml` also has it self-scrape
   the Kubernetes API server, nodes, cAdvisor, and any pod/service annotated for scraping —
   so it collects both from its own service discovery and from Alloy's forwarded metrics.
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
