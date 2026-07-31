---
type: "Kubernetes App Guide"
title: "istio-ingress"
description: "The cluster's north-south ingress: a Gateway API Gateway on the `istio` GatewayClass, directly internet-facing"
app: istio-ingress
catalog_entity: istio-ingress
kind: docs
namespace: istio-ingress
last_reviewed: 2026-07-31
status: current
tags: [ingress, gateway-api, istio]
sources:
  - base-apps/istio-ingress.yaml
  - base-apps/istio-ingress/gateway.yaml
  - base-apps/istio-ingress/gateway-options.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - base-apps/istio-ingress/telemetry.yaml
---

# istio-ingress

## What it is

The cluster's single north-south entry point, replacing `ingress-nginx` on
2026-07-31. A Gateway API `Gateway` named `main` on the `istio` GatewayClass
(controller `istio.io/gateway-controller`), serving 18 hostnames on `:80`/`:443`.

`ingress-nginx` was retired because its upstream repository is archived — the
final release `controller-v1.15.1` supports Kubernetes 1.31–1.35 and there will
never be 1.36 support. It was the single component blocking the cluster's
Kubernetes upgrade path.

Do not confuse this Gateway with the two `istio-waypoint` Gateways in
`chores-tracker` and `chores-tracker-frontend`. Those are **ambient mesh
waypoints** doing east-west policy enforcement inside the mesh; they serve no
external traffic. This is the only Gateway that does.

## How traffic arrives

```
internet → 73.7.190.154 → router → k3s-control-01
         → klipper svclb (hostPort :80/:443)
         → Service main-istio (externalTrafficPolicy: Local)
         → Envoy (main-istio pod)
         → HTTPRoute → app Service
```

There is no reverse proxy, tunnel, or WAF in front. The Gateway is directly
internet-facing and the cluster is scanned continuously — assume anything
exposed is found the same day.

## Why it does NOT use hostNetwork

`ingress-nginx` ran `hostNetwork: true` and got the real client IP for free. The
obvious move was to copy that. It does not work, and the reason is worth knowing
before anyone tries again:

Envoy runs as uid 1337. Istio permits it to bind privileged ports using the
`net.ipv4.ip_unprivileged_port_start` sysctl — and **Kubernetes forbids that
sysctl when `hostNetwork` is true**. nginx got away with it because *its image*
carries `cap_net_bind_service` as a file capability, so a non-root process can
raise it. Istio's Envoy image has no such file capability, so `NET_BIND_SERVICE`
in the container spec is present and inert:

```
cannot bind '0.0.0.0:443': Permission denied
```

Adding `allowPrivilegeEscalation: true` does not help either. hostNetwork and an
Istio gateway are mutually exclusive on privileged ports, whatever securityContext
is applied.

Instead: no hostNetwork. Envoy binds inside the pod network namespace where
Istio's own sysctl applies, klipper performs the privileged bind on the host, and
`externalTrafficPolicy: Local` carries the client address through. That last part
is not assumed — the access log records the real client address (see
`telemetry.yaml`).

## Access control

`authorizationpolicy.yaml` is **the security boundary**. Istio ALLOW policies are
deny-by-default for the workloads they select, so that one object is both the
default-deny and the allow-list: a host added to the Gateway is refused until it
is named there deliberately.

`10.0.0.0/8` from the old nginx allow-lists is **deliberately not carried over**.
It contains the pod network `10.42.0.0/16` and the node network, so under any
SNAT it would make arbitrary internet traffic look allow-listed — failing open,
silently. It also costs nothing to drop: the router hairpin-NATs LAN traffic to
the public address, so LAN clients already match the public `/32`s.

`ipBlocks` is correct here rather than `remoteIpBlocks`, because
`externalTrafficPolicy: Local` means the packet source *is* the client.
`remoteIpBlocks` would require trusting `X-Forwarded-For`, which is meaningless
with no proxy in front and dangerous if mis-scoped.

Hosts that are public by design carry no `from` clause and are annotated
`arigsela.com/public-by-design` on their manifests, which the `ingress-policy` CI
check honors: `n8n`'s webhook paths, `oncall-agent` (Slack Events API, HMAC-signed),
`grafana` (GitHub OAuth), `chores` (family app, app-level JWT).

## Certificates

All certificates use `letsencrypt-route53` (DNS-01). Nothing here depends on the
ingress for issuance — deliberately, since HTTP-01 solves *through* the ingress
and would have made ingress replacement break certificate renewal silently, about
30 days later.

Certificates stay in their app namespaces and are reached by `ReferenceGrant`.
Every app with a listener has one; without it the listener comes up **silently
certless** rather than erroring.

## Routes

`HTTPRoute`s live in the **app** namespaces, not here. That keeps each
`backendRef` same-namespace (so no second ReferenceGrant is needed) and keeps the
route beside what it routes to. Cross-namespace attachment is permitted by the
Gateway's `allowedRoutes.namespaces.from: All`.
