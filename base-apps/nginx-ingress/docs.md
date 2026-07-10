---
app: nginx-ingress
catalog_entity: nginx-ingress
kind: docs
namespace: ingress-nginx
last_reviewed: 2026-07-10
status: current
tags: [ingress, daemonset, cloudflare]
sources:
  - base-apps/nginx-ingress.yaml
  - base-apps/nginx-ingress/nginx-ingress-controller.yaml
---

# nginx-ingress

## What it is
The cluster's `nginx` `IngressClass` implementation (`ingress-nginx`, upstream chart `v4.15.1` from `https://kubernetes.github.io/ingress-nginx`). It is the shared HTTP(S) entry point that most other apps' `Ingress` resources target via `ingressClassName: nginx` (e.g. `base-apps/vault/ingress.yaml`, `base-apps/argo-cd/ingress.yaml`), and it is also what cert-manager's `letsencrypt-prod`/`letsencrypt-staging` `ClusterIssuer`s route HTTP-01 ACME challenges through (`ingress.class: nginx`, see `base-apps/cert-manager/docs.md`).

## How it's deployed
This is **not** deployed as a plain Argo CD-managed Deployment/Helm chart the way most apps in this repo are. `base-apps/nginx-ingress.yaml` is an Argo CD `Application` whose `spec.source.path` is `base-apps/nginx-ingress` and whose `spec.destination.namespace` is `nginx-ingress` — but the single manifest that directory contains, `nginx-ingress-controller.yaml`, is itself a Rancher/k3s `helm.cattle.io/v1` `HelmChart` custom resource with its own `metadata.namespace: kube-system` and `spec.targetNamespace: ingress-nginx` (with `createNamespace: true`). In practice that means **three different namespaces** are involved: Argo CD's declared destination (`nginx-ingress`, effectively inert since the manifest overrides its own namespace), the namespace the `HelmChart` object itself lives in and is reconciled by k3s's built-in helm-controller (`kube-system`), and the namespace the actual `ingress-nginx` controller `DaemonSet`/`Service` end up running in (`ingress-nginx`). Anyone looking for the controller pods should check `ingress-nginx`, not `nginx-ingress`.

## Key configuration
`nginx-ingress-controller.yaml`'s `valuesContent` configures the controller as:
- `kind: DaemonSet` with `hostNetwork: true` — the controller binds directly to each node's ports 80/443 rather than fronting a cloud LoadBalancer; the `Service` is `type: ClusterIP` (ports `http: 80`, `https: 443`).
- Scheduling: `nodeSelector: node.kubernetes.io/workload: infrastructure` plus a toleration for the control-plane taint (`node-role.kubernetes.io/control-plane:NoSchedule`), so it runs on infrastructure/control-plane nodes.
- Timeouts/keepalive tuned explicitly: `proxy-read-timeout`/`proxy-connect-timeout`/`proxy-send-timeout` all `30`, plus `keep-alive-requests`/`upstream-keepalive-*` settings — the values comment notes these replace prior `ServersTransport`-based timeout fixes.
- TLS/HTTP: `ssl-protocols: "TLSv1.2 TLSv1.3"`, `use-http2: "true"`.
- Cloudflare integration: `use-forwarded-headers`/`compute-full-forwarded-for` are enabled, `forwarded-for-header`/`real-ip-header` are set to `X-Forwarded-For`, and `trusted-proxies` is populated with Cloudflare's published IP ranges plus the cluster's private ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) — this cluster sits behind a Cloudflare tunnel, so the controller must trust Cloudflare's edge to read real client IPs.

## How other apps use it
Any app that wants external HTTP(S) routing creates an `Ingress` with `ingressClassName: nginx` (e.g. `vault`, `argo-cd`, `coroot`, `logging` (`grafana-ingress.yaml`), `kagent`, `oncall-agent`, `oncall-crewai`, `vcluster-sandbox-1`). cert-manager's HTTP-01 issuers (`letsencrypt-prod`, `letsencrypt-staging`) also depend on this controller being reachable to complete ACME challenges.
