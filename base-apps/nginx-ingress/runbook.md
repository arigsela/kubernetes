---
app: nginx-ingress
catalog_entity: nginx-ingress
kind: runbook
namespace: ingress-nginx
last_reviewed: 2026-07-10
status: current
tags: [ingress, daemonset, cloudflare]
sources:
  - base-apps/nginx-ingress.yaml
  - base-apps/nginx-ingress/nginx-ingress-controller.yaml
---

# nginx-ingress — Runbook

## Failure modes

### Symptom: cluster-wide "nginx Ingress not routing" (many apps' Ingresses stop working at once)
- **Check:** the controller is a `HelmChart` CR (`nginx-ingress-controller.yaml`, `metadata.namespace: kube-system`), reconciled by k3s's built-in helm-controller into a one-shot Job. Check `kubectl -n kube-system get helmchart ingress-nginx -o yaml` for its job status, and `kubectl -n kube-system get jobs | grep helm-install-ingress-nginx` / `kubectl -n kube-system logs job/helm-install-ingress-nginx` for install/upgrade failures. Then check the actual controller in its real namespace: `kubectl -n ingress-nginx get pods,daemonset`.
- **Fix:** recommend a PR to `base-apps/nginx-ingress/nginx-ingress-controller.yaml` (e.g. correcting `valuesContent`, bumping/pinning `spec.version`, or fixing a bad Helm value) and let Argo CD/helm-controller re-reconcile; do not patch the `HelmChart` object or the rendered resources live.

### Symptom: controller pods missing or stuck Pending in `ingress-nginx`
- **Check:** `kubectl -n ingress-nginx get pods -o wide` and `kubectl -n ingress-nginx describe daemonset ingress-nginx-controller`. Because the controller runs as a `hostNetwork: true` `DaemonSet` restricted by `nodeSelector: node.kubernetes.io/workload: infrastructure` plus a toleration for the control-plane taint, it only schedules onto nodes carrying that label — check `kubectl get nodes -L node.kubernetes.io/workload` if no pods are scheduled anywhere.
- **Fix:** if no nodes carry the `infrastructure` workload label, that's a cluster/node-labeling issue outside this app's manifests, not something to fix by editing `nginx-ingress-controller.yaml`'s scheduling constraints without confirming intent first — raise a PR only if the `nodeSelector`/toleration itself needs to change.

### Symptom: apps behind the ingress see wrong client IPs, or IP-based rate limiting/allow-lists misbehave
- **Check:** `kubectl -n ingress-nginx get configmap ingress-nginx-controller -o yaml` and confirm the `trusted-proxies` entries still match Cloudflare's current published IP ranges (the values in `nginx-ingress-controller.yaml` are a point-in-time list of Cloudflare CIDRs plus the cluster's private ranges). Traffic arrives via a Cloudflare tunnel, so `use-forwarded-headers`/`real-ip-header: X-Forwarded-For` only produce correct client IPs when `trusted-proxies` covers Cloudflare's current edge ranges.
- **Fix:** recommend a PR updating the `trusted-proxies` value in `base-apps/nginx-ingress/nginx-ingress-controller.yaml` to Cloudflare's current IP list (https://www.cloudflare.com/ips/); do not edit the live ConfigMap, since Argo CD/helm-controller will revert it.

## How-to

### Route a new app through this ingress
Set `ingressClassName: nginx` on the app's `Ingress` (see `base-apps/vault/ingress.yaml` or `base-apps/argo-cd/ingress.yaml` for the pattern). For TLS, pair it with cert-manager's `letsencrypt-prod`/`letsencrypt-staging` `ClusterIssuer` (HTTP-01, routes through this same controller) as documented in `base-apps/cert-manager/docs.md`.

### Change controller config (timeouts, TLS, Cloudflare ranges, scheduling)
Edit `valuesContent` in `base-apps/nginx-ingress/nginx-ingress-controller.yaml` and open a PR; Argo CD syncs the `HelmChart` object, and k3s's helm-controller re-runs the Helm upgrade job in `kube-system` against the release in `ingress-nginx`.
