---
type: "Kubernetes App Guide"
title: "istio-ingress"
description: "The cluster's north-south ingress: a Gateway API Gateway on the `istio` GatewayClass, directly internet-facing, gated by an IP allow-list AuthorizationPolicy"
app: istio-ingress
catalog_entity: istio-ingress
kind: docs
namespace: istio-ingress
last_reviewed: 2026-09-30
status: current
tags: [ingress, gateway-api, istio]
sources:
  - base-apps/istio-ingress.yaml
  - base-apps/istio-ingress/gateway.yaml
  - base-apps/istio-ingress/gateway-options.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - base-apps/istio-ingress/httproute-redirect.yaml
  - base-apps/istio-ingress/telemetry.yaml
  - base-apps/istio-istiod.yaml
  - base-apps/istio-waf/wasmplugin.yaml
  - base-apps/vault/httproute-internal.yaml
  - base-apps/wan-ip-monitor/cronjob.yaml
  - scripts/validate-waf-scope.py
  - tests/wan_ip/test_policy_annotation.py
---

# istio-ingress

## What it is

The cluster's single north-south entry point, replacing `ingress-nginx` on
2026-07-31. A Gateway API `Gateway` named `main` on the `istio` GatewayClass
(controller `istio.io/gateway-controller`), with one HTTPS listener per public
hostname on `:443` plus HTTP listeners on `:80` — `gateway.yaml` is the list; do
not rely on a count written anywhere else.

`ingress-nginx` was retired because its upstream repository is archived — the
final release `controller-v1.15.1` supports Kubernetes 1.31–1.35 and there will
never be 1.36 support. It was the single component blocking the cluster's
Kubernetes upgrade path.

It is the only `Gateway` in the repo. (The two ambient-mesh waypoint Gateways
that once lived in `chores-tracker`/`chores-tracker-frontend` went with that app
on 2026-08-01.)

## How traffic arrives

```
internet → <home WAN address> → router → k3s-control-01
         → klipper svclb (hostPort :80/:443)
         → Service main-istio (externalTrafficPolicy: Local)
         → Envoy (main-istio pod)
         → HTTPRoute → app Service
```

There is no reverse proxy or tunnel in front. The Gateway is directly
internet-facing and the cluster is scanned continuously — assume anything
exposed is found the same day. The one extra layer runs *inside* this Envoy:
the Coraza WAF filter (see "WAF" below).

The home WAN address is dynamic (residential ISP) — see "When the WAN address
rotates" below. The current value is the `arigsela.com/wan-ip` annotation on
`authorizationpolicy.yaml`.

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
`telemetry.yaml`). Its line format is Istio's default with the query string
stripped (`%REQ_WITHOUT_QUERY%`), set in `meshConfig.accessLogFormat` in
`base-apps/istio-istiod.yaml`: URLs carry search text and OAuth codes, and these
lines live in Loki for 30 days. `tests/istio_logging/` pins it.

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

Every rule except two carries a `from` clause: the home WAN `/32` plus a few
non-rotating remote `/32`s (and, for `atlantis`, GitHub's webhook ranges). A rule
**without** `from` admits any source. Only two exist, both public by design:

| Public | Why | App-layer control |
|---|---|---|
| `grafana.arigsela.com` | read from mobile; carrier IPs cannot be allow-listed | GitHub OAuth (logging docs) |
| `n8n.arigsela.com` `/webhook*`, `/webhook-test*`, `/mcp-server*` | arbitrary external senders | per-workflow auth; the n8n admin UI is a separate, restricted rule |

Everything else is restricted — including `oncall` (restricted 2026-08-11, which
pauses its Slack Events API integration; the rule's comment says how to undo it)
and `chores` (donetick). Read the file for the current list; the comment on each
rule records why.

The `vault.local` / `vault.10.0.1.110` rule is the one LAN exception. Those names
have no public DNS, but Host-header routing needs none, so the `http-vault-local`
/ `http-vault-ip` listeners (plain HTTP by design, kept off the HTTPS redirect;
routed by `base-apps/vault/httproute-internal.yaml` to Vault, which runs without
TLS) were reachable from the internet until 2026-08-11. Their rule now admits
only the home WAN `/32` (hairpin) and `10.0.1.0/24` (direct LAN — **not**
`10.0.0.0/8`, and deliberately not the remote `/32`s).

**What CI checks.** `scripts/validate-waf-scope.py` (validate workflow) fails if a
host is public — appears in any rule with no `from` — without being in the WAF's
scope regex. `tests/wan_ip/` pins the WAN-address contract: the
`arigsela.com/wan-ip` annotation must match the `ipBlocks` (`test_policy_annotation.py`),
and every allow-listed `*.arigsela.com` host must be in wan-ip-monitor's
`MANAGED_HOSTNAMES` (`test_route53.py`). There is no annotation on app manifests
any more; the nginx-era `public-by-design` annotation and `ingress-policy` job are
gone.

## When the WAN address rotates

Every restricted host trusts the home address by `/32`, and every public DNS
record points at it, so an ISP reassignment takes out **every host at once**:
first as timeouts (DNS still points at the dead address), then, once DNS is
fixed, as `403`s (the allow-list still trusts the old one). `wan-ip-monitor`
(CronJob, every 12 hours, armed) detects the new address, moves the Route 53 A
records it owns directly, and opens a PR that **replaces** the old `/32` (never
appends — the old address goes back to the ISP pool) and moves the annotation in
the same commit. Merging that PR restores access. See `base-apps/wan-ip-monitor/`.

## WAF

`base-apps/istio-waf/` attaches the OWASP Coraza WAF to this Gateway as a Wasm
filter (`targetRefs: main`, `phase: STATS`, i.e. after the allow-list). Its scope
regex (rule `9000`) covers the public hosts plus `oncall` as a deliberate
superset; every other host has the engine turned off. It is `FAIL_OPEN`: the
AuthorizationPolicy remains the boundary. Two ingress-side consequences: a `403`
on a public host can be Coraza rather than the allow-list, and the gateway pod's
memory now includes the CRS (see the istio-waf runbook).

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
