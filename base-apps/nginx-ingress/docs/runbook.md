---
type: "Kubernetes App Runbook"
title: "nginx-ingress — Runbook"
description: "Operational runbook for nginx-ingress: failure modes, checks, and fixes."
app: nginx-ingress
catalog_entity: nginx-ingress
kind: runbook
namespace: ingress-nginx
last_reviewed: 2026-07-10
status: current
tags: [ingress, daemonset]
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
- **Check:** the controller does **no** `X-Forwarded-For` processing by design. It is `hostNetwork`, so the client address is the socket source address — `kubectl -n ingress-nginx logs -l app.kubernetes.io/component=controller | tail` should show real public IPs in the first field. If apps see `10.42.x.x` or a single repeated address instead, something is proxying that should not be, or the header keys were reintroduced.
- **Fix:** do not "fix" this by enabling `use-forwarded-headers`/`real-ip-header`. Those were removed because the old `trusted-proxies` list included `10.0.0.0/8`, which contains the pod network, letting any pod spoof its source IP past the `whitelist-source-range` allow-lists. If a real reverse proxy is introduced, add the keys back with `trusted-proxies` scoped to that proxy's addresses only, via a PR to `nginx-ingress-controller.yaml` — never edit the live ConfigMap, since Argo CD/helm-controller reverts it.

## How-to

### Route a new app through this ingress
Set `ingressClassName: nginx` on the app's `Ingress` (see `base-apps/vault/ingress.yaml` or `base-apps/argo-cd/ingress.yaml` for the pattern). For TLS, pair it with cert-manager's `letsencrypt-prod`/`letsencrypt-staging` `ClusterIssuer` (HTTP-01, routes through this same controller) as documented in `base-apps/cert-manager/docs.md`.

### Change controller config (timeouts, TLS, scheduling)
Edit `valuesContent` in `base-apps/nginx-ingress/nginx-ingress-controller.yaml` and open a PR; Argo CD syncs the `HelmChart` object, and k3s's helm-controller re-runs the Helm upgrade job in `kube-system` against the release in `ingress-nginx`.
