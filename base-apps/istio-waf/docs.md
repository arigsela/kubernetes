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
it works at L3: it answers "what IP are you from?" Of the 19 hostnames, 14 are
restricted to IPs exclusively — including `atlantis`, which allows the same
four `/32`s plus six GitHub webhook CIDR ranges on top. `n8n` is mixed — its
webhook paths are public while its admin UI is IP-restricted (see below).
`vault.local` / `vault.10.0.1.110` were unrestricted (no `from:` clause at
all, hence reachable from the public internet) until they were given an IP
allow-list as part of this work (2026-08-11) — see
`authorizationpolicy.yaml`'s comment on that rule for what that exposure was.

Two hosts are public by design and cannot be IP-restricted:

| Host | Why open | App-layer control |
|---|---|---|
| `grafana.arigsela.com` | Read from mobile; carrier IPs unlistable | GitHub OAuth |
| `n8n.arigsela.com` `/webhook*` | Arbitrary external senders | Per-workflow auth |

`oncall.arigsela.com` was a third until **2026-08-11**, when it was given an IP
allow-list. Slack does not publish stable egress addresses, so there is no
allow-list that keeps the Events API working — the choice was public or paused,
and paused was chosen. Deleting the `from` block on that rule in
`authorizationpolicy.yaml` restores it, and nothing else needs to change.

Coraza's scope regex still covers all **three**, which is a deliberate superset
of the public set. `oncall` is the most-probed hostname on this Gateway, the
allow-list and the WAF are independent controls, and keeping it in scope means
protection is already in place if that `from` block is ever removed.

`scripts/validate-waf-scope.py` enforces the direction that matters — every
public host must be covered — and does not object to the WAF covering more than
that.

Coraza is **defence in depth on top of** the AuthorizationPolicy, not a
replacement for it.

## Current mode: ENFORCING (since 2026-08-11)

All three hosts block. A request scoring >= 5 on the CRS anomaly scale gets a
`403` from Coraza and never reaches the app. Rolling one host back means
re-adding its `DetectionOnly` line in `wasmplugin.yaml`; IDs `9001`-`9003` stay
reserved for that.

**Known, unfixed false positive.** An n8n webhook whose JSON body carries a
filesystem path is blocked:

```
{"file":"../reports/x.csv","dir":"/var/log/app"}
  930110 Path Traversal   matched ../      in ARGS_POST:json.file
  930120 OS File Access   matched var/log  in ARGS_POST:json.dir
  949110 anomaly score 15 vs threshold 5   -> 403
```

Measured before enforcing and accepted deliberately. A blocked webhook fails
**silently** from the sender's side, so an automation that stops working with no
error anywhere is the signature. The fix is a scoped exclusion after the CRS
include (`SecRuleUpdateTargetById 930110 "!ARGS_POST:json.file"`), never raising
the anomaly threshold — that would weaken every rule at once.

Enforcement was enabled **without** the 7-day observation window the plan calls
for. The window's job is to surface false positives from real traffic rather
than invented payloads, and it has not run — so treat unexplained breakage on
these three hosts as WAF-related until shown otherwise.

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
| CRS tuning (allow-lists) | rule `9100`, between the two `Include` lines |
| CRS exclusions | end of the `default` directives list |
| Log verbosity | `SecDebugLogLevel` |

### Rule 9000 does not stop logging

`ctl:ruleEngine=Off` suppresses *blocking* on unprotected hosts, but CRS rules
still evaluate and still write to the error log. Observed 2026-08-11..12:
`POST /stats` on `coroot.arigsela.com` — outside the protected set — returned
`200` while emitting `920420` and `949111` at Envoy `critical`.

Two consequences worth remembering. Unprotected hosts are a live source of
`critical` log lines, which Coroot's log alerting reports as "fatal in the
logs" even though nothing is being enforced. And the anomaly scores those hosts
accumulate are real: a host sitting at or above 5 is already at the blocking
threshold, so adding it to rule `9000`'s regex starts returning 403 on its very
first request. Check what a host currently scores before promoting it.

Rule `9100` exists because of exactly that trap — Coroot's UI would have been
blocked by its own telemetry the moment the host was promoted.

## Adding a fourth host

0. Check what it already scores. Unprotected hosts still evaluate CRS (see
   above), so grep the gateway log for its traffic first — a host already
   hitting `949111` is at the blocking threshold and will 403 immediately.
1. Add it to rule `9000`'s regex.
2. Add a `DetectionOnly` line for it in the `9001`–`9003` block.
3. Observe ≥7 days, tune (see runbook).
4. Delete its `DetectionOnly` line.

`scripts/validate-waf-scope.py` fails CI if a host becomes public in the
AuthorizationPolicy without step 1.
