---
type: "Kubernetes App Guide"
title: "homepage"
description: "Homelab dashboard listing every cluster app plus the WSL2-hosted Plex server, with live widgets for Plex, Grafana, Argo CD, and cluster resources."
app: homepage
catalog_entity: homepage
kind: docs
namespace: homepage
last_reviewed: 2026-08-04
status: current
tags: [dashboard, gitops, self-hosted]
sources:
  - base-apps/homepage/configmap.yaml
  - base-apps/homepage/deployments.yaml
  - base-apps/homepage/serviceaccount.yaml
  - base-apps/homepage/external-secrets.yaml
  - base-apps/homepage/secret-store.yaml
  - base-apps/homepage/httproute.yaml
  - base-apps/homepage/certificate.yaml
  - base-apps/homepage.yaml
  - terraform/roots/asela-cluster/argocd.tf
  - base-apps/logging/grafana-deployment.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
---

# homepage

## What it is

[gethomepage/homepage](https://github.com/gethomepage/homepage) is a static,
server-rendered dashboard: one page, tiles grouped into rows, each tile a link
plus an optional live-data widget. It is the landing page for the homelab —
`home.arigsela.com` — and the one place that enumerates essentially every app
this repo deploys, in-cluster or not.

It is deployed as a single stateless `Deployment`, one replica, one container,
image `ghcr.io/gethomepage/homepage:v1.13.2`. There is no database and no
persistent volume; all state is either baked into the ConfigMap or discovered
live from the Kubernetes API on each page load.

## Architecture & data flow

Request path: `home.arigsela.com` → the shared `main` Gateway in
`istio-ingress` (listener `https-homepage`) → `HTTPRoute` → `Service/homepage`
→ pod, port 3000. TLS terminates at the Gateway using `homepage-tls`, a
Secret in this namespace reached across namespaces by `reference-grant.yaml`.

Homepage builds its page from two independent sources, and this split is the
single most important thing to understand about this app:

**The tile-sourcing rule, verbatim:** *Tiles with a widget are defined in
`configmap.yaml`. Link-only tiles are defined by annotations on their own
`httproute.yaml`. Never put a credential in an annotation — annotations are
plaintext in Git, and Homepage performs `{{HOMEPAGE_VAR_*}}` substitution only
in config files, never in annotations.*

Concretely: `configmap.yaml`'s `services.yaml` hand-lists exactly three tiles
that need a widget (Argo CD, Grafana, Plex — see below), because only widgets
need credentials or a non-default query. Every other app on the dashboard is
discovered by Homepage's Kubernetes provider (`kubernetes.yaml`: `mode:
cluster`, `gateway: true`) reading `gethomepage.dev/*` annotations off
`HTTPRoute` objects cluster-wide — which is why the ClusterRole below grants
read on `gateway.networking.k8s.io` resources.

### Adding a new app to the dashboard

Add six annotations to the app's own `httproute.yaml` (see
`base-apps/argo-rollouts/httproute.yaml` or
`base-apps/weather-kitchen-frontend/httproute.yaml` for worked examples):

```yaml
metadata:
  annotations:
    gethomepage.dev/enabled: "true"
    gethomepage.dev/name: My App
    gethomepage.dev/group: Home        # must match a key under settings.yaml's `layout:`
    gethomepage.dev/icon: mdi-something  # or an upstream dashboard-icons filename, e.g. grafana.png
    gethomepage.dev/description: One line
    gethomepage.dev/pod-selector: app=my-app   # optional; drives the tile's health dot
```

The tile appears with **no pod restart and no change to this app whatsoever**
— annotations are read from the Kubernetes API per request, not baked into a
config that needs reloading. This is the single most useful asymmetry to
know about this app: `configmap.yaml` changes need a rollout (see the
`checksum/config` note below); `httproute.yaml` annotation changes do not,
because they never go through the ConfigMap/subPath path at all.

If `group` does not match one of the keys under `layout:` in `settings.yaml`
(`GitOps & Delivery`, `Automation`, `Observability`, `Platform`, `AI &
Agents`, `Home`), Homepage silently renders the tile into its own
unconfigured row rather than erroring — see the runbook for the "tile
missing" symptom.

### Why `checksum/config` exists and is mandatory

`configmap.yaml` is mounted into the container as eight individual `subPath`
files (`deployments.yaml`), and **Kubernetes never propagates ConfigMap
updates into subPath mounts** — this is a documented kubelet limitation, not
a bug specific to this app. Editing `configmap.yaml` and letting Argo CD sync
it deploys the new ConfigMap object cleanly and changes **nothing** in the
running pod, because the subPath mount was resolved once at pod creation and
is never re-synced.

The fix is the `checksum/config` pod-template annotation in
`deployments.yaml`: because it's part of the pod template, changing its value
forces a new pod (and therefore a fresh subPath resolution) on every
ConfigMap edit. Recompute it after any `configmap.yaml` change:

```bash
shasum -a 256 base-apps/homepage/configmap.yaml | cut -c1-16
```

The current value baked into `deployments.yaml` is `243d51a75a0b41e9`.
Forgetting this step is indistinguishable from a successful, no-op deploy —
Argo CD reports Synced/Healthy either way.

### Why the Argo CD tile reads Prometheus, not the Argo CD API

The natural design would be an Argo CD `apiKey` account and the built-in
`argocd` widget. That requires editing `argocd-cm`, and in this repo the
Argo CD Terraform module (`terraform/modules/argocd`) writes Helm values
under the deprecated `server.config.*` path, which the chart no longer reads
(it reads `configs.cm.*`) — see `templates/agent-docs/README.md` for the full
account of why that path is dead. So instead the tile uses a `prometheusmetric`
widget against `argocd_app_info`, counting apps by `sync_status` and
`health_status`.

**This has a dependency that lives entirely outside the `homepage`
namespace, and outside Argo CD sync entirely:** `argocd_app_info` only
exists because `controller.metrics.enabled` is set in
`terraform/roots/asela-cluster/argocd.tf`, which creates the
`argocd-application-controller-metrics` Service and its Prometheus scrape
annotations. That Terraform is applied by **Atlantis on a pull request behind
a required-reviewer gate** — not by Argo CD's automated sync, and not by
anything in this app's own manifests. A blank or all-zero Argo CD tile is
therefore very often a Terraform/Atlantis problem, not a homepage problem;
see the runbook.

### Why the Grafana tile uses `customapi` + a Bearer header, not the built-in `grafana` widget

That Grafana (`base-apps/logging/grafana-deployment.yaml`) runs
`GF_AUTH_BASIC_ENABLED=false` and `GF_AUTH_DISABLE_LOGIN_FORM=true`, so
GitHub OAuth is the *only* interactive way to log in — there is no
username/password to authenticate against. Homepage's built-in `grafana`
widget speaks only HTTP basic auth and cannot authenticate against this
instance at all.

The tile instead uses the generic `customapi` widget with an explicit
`Authorization: Bearer {{HOMEPAGE_VAR_GRAFANA_TOKEN}}` header against a
Grafana service-account token. This was verified directly: a `glsa_`-prefixed
service-account token returns `401` when sent as basic auth, and `200` when
sent as a Bearer header. **If someone "fixes" this back to the built-in
`grafana` widget expecting it to be simpler, it will break** — the widget
issue is not the token, it's the auth scheme.

### The external Plex dependency (lives outside Git)

The Plex tile points at `10.0.1.200:32401` — **not** Plex's default port
32400. Plex itself runs on a Windows/WSL2 host, not in the cluster; it is
reachable from cluster pods only because WSL2 mirrored networking is enabled
on that host (`networkingMode=mirrored` in `%UserProfile%\.wslconfig`,
requires WSL 2.0+ and Windows 11 22H2+). Without mirrored networking, WSL2's
default NAT mode puts Plex behind a private address the cluster cannot route
to.

The Windows host needs a static DHCP reservation: `10.0.1.200` is hardcoded
into `configmap.yaml`'s Plex tile (both `href` and the widget `url`), and
there is nothing in this app that would notice or recover from the lease
moving. If the widget goes blank while the link still works, this dependency
— entirely outside this repo, outside the cluster, and outside Kubernetes —
is almost always why. See the runbook for the probe command.

## Where config lives

| What | Where |
|---|---|
| Layout, groups, kubernetes-provider mode | `configmap.yaml` (`settings.yaml`, `kubernetes.yaml`) |
| The three widget-bearing tiles (Argo CD, Grafana, Plex) | `configmap.yaml` (`services.yaml`) |
| Cluster-resources widget (CPU/memory on the tile row) | `configmap.yaml` (`widgets.yaml`) |
| Link-only tiles for every other app | `gethomepage.dev/*` annotations on that app's own `httproute.yaml` |
| Plex token, Grafana service-account token | Vault `k8s-secrets/homepage` → `external-secrets.yaml` |
| Allowed request hostnames | `HOMEPAGE_ALLOWED_HOSTS` env var in `deployments.yaml` |
| TLS certificate | `certificate.yaml` (ClusterIssuer `letsencrypt-route53`) |
| Who can reach it at all | `base-apps/istio-ingress/authorizationpolicy.yaml` |
| RBAC for cluster/gateway discovery | `serviceaccount.yaml` |

## RBAC

The app holds a **cluster-wide READ** `ClusterRole`: `namespaces`, `pods`,
`nodes` (core API), `httproutes` and `gateways`
(`gateway.networking.k8s.io`), and `nodes`/`pods` under `metrics.k8s.io`.
This is the one new privilege this app introduces to the cluster — every
other app in this repo is namespace-scoped.

It is deliberately **narrower** than upstream's example RBAC manifest:
upstream also grants `ingresses` (`networking.k8s.io`/`extensions`) and
`traefik.io` `ingressroutes`. Both are dropped here because this cluster is
**Gateway-API only** — there is no nginx Ingress controller and no Traefik
since the Istio cutover, so those rules would be dead grants that only widen
the blast radius of a compromised pod for no functional benefit.

## No authentication of its own

Upstream is explicit that Homepage has **no built-in authentication and none
is planned**. This app is reachable, unauthenticated, by anyone whose source
IP is on the allow-list — and the page it serves enumerates essentially every
service in the homelab (name, description, group, and for three apps, live
operational data). The Istio `AuthorizationPolicy` IP allow-list in
`base-apps/istio-ingress/authorizationpolicy.yaml` is therefore not *a*
control, it is the **only** control standing in front of a full service
inventory of this cluster.

This is also why the Grafana credential baked into this app is a
**Viewer-scoped service-account token**, not the admin credentials used
elsewhere in this cluster (see
`base-apps/logging/grafana-admin-external-secret.yaml`) — a leak of this
app's environment should not hand out Grafana admin. There is no equivalent
scoped credential for Argo CD (the tile reads Prometheus, which has no
authentication of its own inside the cluster network) or Plex (the token is
Plex's own least-privileged "server" access token, not an account password).

## Known follow-ups (deliberately out of scope)

- **Tautulli** would give real now-playing detail on the Plex tile; the core
  Plex API this widget uses only reports library counts and a stream count.
- **Coroot, Backstage, Vault, n8n, Atlantis, and Kagent have no Homepage
  widget** and are permanently link-only tiles unless someone hand-builds a
  `customapi` widget for each, the same way the Grafana tile was built.
- **The WSL2 `portproxy` fallback**, if the Windows host ever needs it instead
  of mirrored networking, requires a Scheduled Task to survive reboots —
  `netsh interface portproxy` rules do not persist on their own.
