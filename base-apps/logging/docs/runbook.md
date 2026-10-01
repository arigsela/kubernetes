---
type: "Kubernetes App Runbook"
title: "Logging — Runbook"
description: "Operational runbook for Logging: failure modes, checks, and fixes."
app: logging
catalog_entity: logging
kind: runbook
namespace: logging
last_reviewed: 2026-09-30
status: stable
tags: [loki, grafana, prometheus, alloy]
sources:
  - base-apps/logging/alloy-daemonset.yaml
  - base-apps/logging/loki-config.yaml
  - base-apps/logging/loki-deployment.yaml
  - base-apps/logging/loki-s3-external-secret.yaml
  - base-apps/logging/secret-store.yaml
  - base-apps/logging/grafana-deployment.yaml
  - base-apps/logging/grafana-admin-external-secret.yaml
  - base-apps/logging/grafana-github-oauth-external-secret.yaml
  - base-apps/logging/grafana-alerting.yaml
  - base-apps/logging/httproute.yaml
  - base-apps/logging/certificate.yaml
  - base-apps/logging/prometheus-statefulset.yaml
  - docs/troubleshooting/in-place-resize.md
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
     `spec.nodeName` field selector in `alloy-config.yaml`), and its only metrics are its
     own node's host metrics (`prometheus.exporter.unix "host"` → `remote_write`, about 1.5K
     series per node, `job="node-exporter"`). Removing the node filter, or adding scrapes of
     Kubernetes targets that Prometheus already collects, multiplies memory by the node
     count and makes every pod ship duplicate data — visible
     as Loki `entry too old` drops (`loki_write_dropped_entries_total`) and Prometheus
     `out of order sample` rejections (`prometheus_remote_storage_samples_failed_total`).
  3. **Only if neither holds** and live heap (`inuse_space`) is genuinely growing, raise
     `requests`/`limits` and `GOMEMLIMIT` together, keeping the ~85% ratio.

  Since Alloy is a DaemonSet, an OOMKilled pod only breaks log collection on that one node.

### Symptom: an Alloy pod pinned at its CPU limit after a restart, logging `final error sending batch` / `entry too far behind`
- **Check:** `kubectl -n logging logs <alloy-pod> | grep 'final error'`. HTTP 400s whose
  reason is `entry too far behind` mean Alloy is re-reading old container logs and Loki is
  rejecting lines it already has. Nothing is duplicated or lost; current lines still flow
  (confirm with a recent `{namespace="<ns>"}` query in Grafana).
- **Cause:** Alloy lost its read positions
  (`/var/lib/alloy/data/loki.source.kubernetes.pods/positions.yml`). Since 2026-09-25 that
  directory is a hostPath, so an ordinary restart keeps them. It still happens on a pod's
  first start on a node, or if the node's `/var/lib/alloy/data` was wiped.
- **Fix:** none needed; it stops once the node's logs have been re-read (minutes to tens of
  minutes on the busiest node). If it never stops, check that the hostPath volume is
  mounted at `--storage.path`.

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
- **Check:** `kubectl -n logging get pods -l app=grafana`, then the route and cert:
  `kubectl -n logging get httproute logging` (its `Accepted`/`ResolvedRefs` conditions on the
  `https-grafana` listener) and `kubectl -n logging get certificate grafana-tls` (issued by
  `letsencrypt-route53`). A pod stuck in `CreateContainerConfigError` usually means the
  `grafana-admin` or `grafana-github-oauth` Secret is missing — both are non-optional
  `secretKeyRef`s (`kubectl -n logging get externalsecret`). For missing dashboards/data, check
  the Loki/Prometheus datasources are reachable from inside the Grafana pod
  (`http://loki.logging.svc.cluster.local:3100`, `http://prometheus.logging.svc.cluster.local:9090`
  — both ClusterIP-only) and that the `grafana-dashboard-provider` ConfigMap's folders
  (`Kubernetes`, `Istio`, `Security`) still match the mounted dashboard ConfigMaps.
- **Fix:** PR any datasource URL or dashboard-provider path changes; a stuck cert-manager
  challenge for `grafana-tls` is a cert-manager issue, not this app.

### Symptom: OAuth login broken (GitHub round-trip fails or every login is refused)
Grafana has no other way in: the login form and basic auth are off
(`GF_AUTH_DISABLE_LOGIN_FORM=true`, `GF_AUTH_BASIC_ENABLED=false` in `grafana-deployment.yaml`).
- **Check:** `kubectl -n logging logs deploy/grafana | grep -i oauth`.
  `oauth.role_attribute_strict_violation` means the GitHub login matched no role in
  `GF_AUTH_GITHUB_ROLE_ATTRIBUTE_PATH`; a refused *new* user is the closed sign-up gate
  (`GF_AUTH_GITHUB_ALLOW_SIGN_UP=false`) working as intended. A redirect-URI error from GitHub
  means `GF_SERVER_ROOT_URL` no longer matches `https://grafana.arigsela.com` or the OAuth App's
  callback (`/login/github`). Client errors mean the `grafana-github-oauth` Secret is stale or
  empty (`kubectl -n logging get externalsecret grafana-github-oauth`; Vault
  `k8s-secrets/grafana`, `github-client-id`/`github-client-secret`).
- **Fix:** correct the value in Vault or the env in `grafana-deployment.yaml` and PR it. To add
  a user, follow docs.md, "Login: GitHub OAuth only". **Break-glass** if OAuth cannot be fixed
  quickly: revert `ee5c8c8` ("GitHub OAuth only - disable the local login form and basic
  auth"), which re-enables the form so the `grafana-admin` credentials work again; Argo CD
  re-syncs in about 2 minutes. `kubectl` access is unaffected throughout. Re-apply the commit
  once OAuth is fixed — this host is internet-facing.

## How-to

### Deploy / update
Edit manifests here and PR; Argo CD syncs on merge (`prune`/`selfHeal` enabled). How a
change reaches the pods differs per component, and all are single-replica:
- **Prometheus** is `updateStrategy: OnDelete` (`prometheus-statefulset.yaml`): merging a
  template change does **not** restart it. For resources, resize the running pod in place
  first (`scripts/resize-pod.sh`), then merge the same values; a real restart is a deliberate
  `kubectl -n logging delete pod prometheus-0`. See `docs/troubleshooting/in-place-resize.md`.
- **Grafana** is `strategy: Recreate` — about 20 s of downtime per rollout. An alert-rule change
  also restarts it via the `checksum/alerting` annotation (keep it current; the test fails
  otherwise).
- **Loki** (Deployment) and **Alloy** (DaemonSet) roll normally; expect a brief gap in
  ingestion on the affected node or for Loki as a whole.
