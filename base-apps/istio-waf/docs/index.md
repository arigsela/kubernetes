---
type: "Kubernetes App Guide"
title: "istio-waf"
description: "OWASP Coraza WAF as an Envoy Wasm filter on the main ingress Gateway, protecting the three public-by-design hostnames"
app: istio-waf
catalog_entity: istio-waf
kind: docs
namespace: istio-ingress
last_reviewed: 2026-08-11
status: current
tags: [waf, security, istio, coraza]
sources:
  - base-apps/istio-waf.yaml
  - base-apps/istio-waf/wasmplugin.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - docs/superpowers/specs/2026-08-11-coraza-waf-design.md
---

# istio-waf

## What it is

OWASP Coraza (the successor to end-of-life ModSecurity) compiled to WebAssembly
and loaded as a filter inside the `main` Gateway's Envoy. It runs OWASP Core
Rule Set v4.14.0. There is no new proxy and no extra network hop — the filter
runs in the Envoy that already terminates every request.

## What it protects, and why only three hosts

`base-apps/istio-ingress/authorizationpolicy.yaml` is the security boundary, and
it works at L3: it answers "what IP are you from?" Sixteen of the nineteen
hostnames are restricted to four `/32` addresses and are well covered by it.

Three are public by design and cannot be:

| Host | Why open | App-layer control |
|---|---|---|
| `grafana.arigsela.com` | Read from mobile; carrier IPs unlistable | GitHub OAuth |
| `oncall.arigsela.com` | Slack Events API callbacks | HMAC signing secret |
| `n8n.arigsela.com` `/webhook*` | Arbitrary external senders | Per-workflow auth |

Coraza covers exactly those three. It is **defence in depth on top of** the
AuthorizationPolicy, not a replacement for it.

## Where traffic meets it

```
Envoy (main-istio pod)
  ├── [1] AuthorizationPolicy gateway-allow   ← IP allow-list (L3)
  ├── [2] Coraza Wasm filter (phase: STATS)   ← this app (L7)
  └── [3] HTTPRoute → app Service
```

`phase: STATS` is after Istio's authorization filters, so the allow-list culls
traffic before Coraza spends CPU on it.

## The three things that are not obvious from the YAML

**1. `(:\d+)?` in every host regex is load-bearing, not defensive habit.**
Coraza's authority lookup is an exact string match (`wafmap.go`,
`getWAFOrDefault`). Envoy strips the port when matching routes; Coraza does not
when matching authority. Without the port group, `Host: grafana.arigsela.com:443`
reaches Grafana **completely uninspected**. Do not "simplify" it away. This is
the same asymmetry that makes `authorizationpolicy.yaml` list both `host` and
`host:*` for every rule.

**2. One directive set, not `per_authority_directives`.** The declarative
per-authority map looks tidier, but it is that same exact-match lookup (so it
needs every `host:port` form enumerated) and it compiles a **separate CRS
instance per named set**. Three tiers would mean three copies of CRS resident in
a single gateway pod. The SecLang guard rules do the same job with one.

**3. `failStrategy: FAIL_OPEN` is deliberate, against Istio's default.** Under
`FAIL_CLOSE` a ghcr.io outage or one Coraza panic returns 5xx for all nineteen
hostnames — including the Argo CD UI needed to roll it back. The accepted cost
is that a crashed WAF stops enforcing **silently**, which is why the dashboard's
first panel watches Wasm load errors rather than only rule hits. An empty
"detections" graph cannot distinguish *clean* from *dead*.

## Configuration map

| What | Where |
|---|---|
| Which hosts are inspected | rule `9000` scope regex, `wasmplugin.yaml` |
| Whether a host blocks or logs | rules `9001`–`9003` (present = log only) |
| Body inspection | rules `9010` (n8n, path-scoped), `9011` (oncall) |
| CRS exclusions | end of the `default` directives list |
| Log verbosity | `SecDebugLogLevel` |

## Adding a fourth host

1. Add it to rule `9000`'s regex.
2. Add a `DetectionOnly` line for it in the `9001`–`9003` block.
3. Observe ≥7 days, tune (see runbook).
4. Delete its `DetectionOnly` line.

`scripts/validate-waf-scope.py` fails CI if a host becomes public in the
AuthorizationPolicy without step 1.
