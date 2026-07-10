---
app: logging
catalog_entity: logging
kind: runbook
namespace: logging
last_reviewed: 2026-07-10
status: current
tags: [loki, grafana, prometheus, alloy]
sources:
  - base-apps/logging/alloy-daemonset.yaml
  - base-apps/logging/loki-config.yaml
  - base-apps/logging/loki-deployment.yaml
  - base-apps/logging/loki-s3-external-secret.yaml
  - base-apps/logging/secret-store.yaml
  - base-apps/logging/grafana-deployment.yaml
  - base-apps/logging/grafana-ingress.yaml
---

# logging runbook

## Failure modes

### Symptom: Alloy DaemonSet pods CrashLooping / OOMKilled
- **Check:** `kubectl -n logging get pods -l app=alloy` — look for `OOMKilled` (exit code
  137) in `kubectl -n logging describe pod <alloy-pod>`, and restart counts. Compare against
  current limits in `alloy-daemonset.yaml` (`requests.memory: 256Mi`, `limits.memory: 512Mi`
  — these were raised from 128Mi/256Mi after pods sitting at ~253Mi steady-state caused a
  real OOMKilled crashloop and log-shipping gaps).
- **Fix:** if pods are again running close to the current 512Mi limit,
  PR a further increase to `alloy-daemonset.yaml`'s `resources.requests/limits.memory`. Since
  Alloy is a DaemonSet, an OOMKilled pod only breaks log/metric collection on that one node,
  but if it's cluster-wide, check whether a recent change to `alloy-config.yaml` (e.g. a new
  scrape target or log volume spike) is driving memory up rather than assuming the limit
  alone is at fault.

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
