# Coraza WAF at the Ingress Gateway — Design

**Date:** 2026-08-11
**Author:** Ari Sela (with Claude)
**Status:** Approved for plan
**Scope:** L7 request inspection on the three internet-open hostnames
**Prerequisite:** `istio-ingress` Gateway live on `:80`/`:443` (SPEC §T.55/§T.56, cutover 2026-07-31)

## 1. Goal

Add OWASP Coraza as a WebAssembly filter inside the existing `main` Gateway's Envoy, giving L7 request inspection (OWASP Core Rule Set v4) to the three hostnames that are public by design and therefore unprotected by the IP allow-list.

The end state is **enforcement, not detection** — each of the three hosts blocks malicious requests once its ruleset has been tuned against real traffic. Detection-only is the starting position, not the destination.

Adding a fourth host later is a one-line regex change plus a documented tuning cycle.

## 2. Current state

| Layer | What exists | What it inspects |
|---|---|---|
| `AuthorizationPolicy gateway-allow` | IP allow-lists per hostname, deny-by-default | Source IP only (L3) |
| Kyverno | Admission policies | Kubernetes objects, not traffic |
| Falco | Runtime syscall monitoring | Container behaviour, post-compromise |
| Telemetry `gateway-access-logs` | Envoy access logs → Alloy → Loki | Records requests, does not judge them |
| **Nothing** | — | **HTTP request content** |

`base-apps/istio-ingress/docs.md` states the gap directly:

> There is no reverse proxy, tunnel, or WAF in front. The Gateway is directly internet-facing and the cluster is scanned continuously — assume anything exposed is found the same day.

### 2.1 The three unprotected hostnames

Sixteen of the nineteen hostnames on the Gateway are IP-restricted, but not uniformly: thirteen to the same four `/32` addresses, `atlantis` to those plus six GitHub webhook CIDR ranges, and `vault.local` / `vault.10.0.1.110` to the LAN plus the hairpin address. Those last two had **no `from:` clause at all** until they were restricted as part of this work on 2026-08-11 — see `authorizationpolicy.yaml`'s comment on that rule for what was exposed. Three hostnames remain public, each for a reason that cannot be engineered away:

| Host | Why open | Current control |
|---|---|---|
| `grafana.arigsela.com` | Read from mobile; carrier IPs are not allow-listable | GitHub OAuth |
| `oncall.arigsela.com` | Slack Events API callbacks from an unstable address set | `SLACK_SIGNING_SECRET` HMAC + API keys |
| `n8n.arigsela.com` (`/webhook*`, `/webhook-test*`, `/mcp-server*`) | Webhooks arrive from arbitrary external services | Per-workflow authentication |

For these three, requests reach the application uninspected. That is the gap this design closes.

### 2.2 Platform facts this design depends on

- Istio **1.30.3**, `ambient` profile (istiod + `ztunnel` DS + `istio-cni` DS).
- Gateway `main` in namespace `istio-ingress`, `gatewayClassName: istio`, Gateway API — **not** `IstioOperator`.
- Gateway runs as a **single pod** pinned to `k3s-control-01` via `nodeSelector`, no `hostNetwork`, `externalTrafficPolicy: Local`.
- `gateway-options.yaml` sets no resource block, so the pod runs on Istio's defaults: **2 CPU / 1Gi memory limits**, 100m/128Mi requests (measured 2026-08-11).
- Observability: Alloy → Loki → Grafana, plus Prometheus. Grafana dashboards mount as **explicit volumes**, not via a sidecar.

## 3. Why Coraza

Coraza is the OWASP successor to ModSecurity, which is end-of-life. It is a Go library implementing the `SecLang` rule dialect and is compatible with OWASP CRS v4. The `coraza-proxy-wasm` build compiles it to WebAssembly, loadable directly into Envoy — meaning no new proxy, no additional network hop, and no change to the traffic path.

**Project health, verified 2026-08-11:**

| Repo | Latest release | Latest commit | Read |
|---|---|---|---|
| `corazawaf/coraza` | v3.7.0 (2026-04-06) | 2026-08-05 | Healthy, actively maintained |
| `corazawaf/coraza-proxy-wasm` | 0.6.0 (2025-07-06) | 2026-07-17 | Maintained but pre-1.0; releases are infrequent |

The pre-1.0 status of the Wasm build is a real risk and is reflected in the fail-open decision (§7.2) and the memory-headroom gate (§9).

### 3.1 Why the ambient mesh does not obstruct this

In ambient mode `ztunnel` is L4-only — it terminates mTLS and has no HTTP parser, so it cannot host a WAF. This is irrelevant here: the `main` Gateway is a standard Envoy deployment, and Wasm filters attach to it normally. Only *east-west* L7 filtering would require waypoint proxies, which is out of scope (§11).

> Note: `base-apps/istio-ingress/docs.md` references waypoints in `chores-tracker` / `chores-tracker-frontend`. Those namespaces no longer exist — `donetick` replaced that app. The reference is stale and should be corrected, but it is not a blocker for this work.

## 4. Target state — the wire

```
internet → 73.7.190.154 → router → k3s-control-01
         → klipper svclb (hostPort :80/:443)
         → Service main-istio (externalTrafficPolicy: Local)
         → Envoy (main-istio pod)
              ├── [1] AuthorizationPolicy gateway-allow   ← IP allow-list (L3)
              ├── [2] Coraza Wasm filter (phase: STATS)   ← NEW: CRS v4 (L7)
              └── [3] HTTPRoute → app Service
```

`phase: STATS` places the filter **after** Istio's authorization filters. Two consequences, both desirable:

1. The IP allow-list culls traffic before Coraza sees it, so the WAF spends no CPU on requests that are about to be rejected.
2. The sixteen restricted hosts are excluded twice — by the allow-list, and by the scope guard in §6. Defence in depth at zero cost.

## 5. Architecture and file layout

A standalone Argo CD Application, synced at wave 3 (after `istio-ingress` at wave 2) so the Gateway exists before anything attaches to it.

```
base-apps/istio-waf.yaml               # Argo CD Application, sync-wave "3"
base-apps/istio-waf/
  ├── wasmplugin.yaml                  # the entire enforcement surface
  ├── catalog-info.yaml                # Backstage entity
  ├── docs.md                          # architecture + tuning tribal knowledge
  ├── runbook.md                       # symptom → check → fix, emergency disable, LogQL
  └── mkdocs.yml
base-apps/logging/
  ├── grafana-dashboard-coraza.yaml    # NEW ConfigMap
  ├── grafana-deployment.yaml          # + volume + volumeMount
  └── grafana-dashboard-configmap.yaml # + provider entry
scripts/
  ├── agent-docs-scope.txt             # + istio-waf
  └── validate-waf-scope.py            # NEW — see §10.2
tests/waf/                             # NEW — pytest for the validator
.github/workflows/validate.yaml        # + waf-scope-validate job
```

### 5.1 Why a standalone Application

SPEC §V.8: *"∀ component upgrade → own commit, own sync, own rollback point. ⊥ batch."*

The WAF is the component most likely to need an emergency rollback, and the tuning window will produce many iterations. Folding it into `istio-ingress` would make every tuning commit re-sync the app that owns the Gateway, its 20 listeners serving 19 hostnames, and the AuthorizationPolicy. Separate apps mean the WAF can be reverted, suspended, or deleted without touching ingress itself.

### 5.2 The WasmPlugin

```yaml
apiVersion: extensions.istio.io/v1alpha1
kind: WasmPlugin
metadata:
  name: coraza
  namespace: istio-ingress          # must match the Gateway's namespace
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: main
  url: oci://ghcr.io/corazawaf/coraza-proxy-wasm:0.6.0
  imagePullPolicy: IfNotPresent
  phase: STATS
  failStrategy: FAIL_OPEN
  pluginConfig:
    default_directives: default
    directives_map:
      default:                      # §6.2, one YAML list item per directive
        - SecRuleEngine On
        - ...
```

**Note on encoding.** §6.2 presents the ruleset as a `SecLang` file for readability, but `directives_map` takes an **array of directive strings**, not a config blob. Each directive becomes one list item. Multi-line `SecRule ... \` continuations in §6.2 must be collapsed into a single string per rule, and chained rules (`id:9010`) become a single string containing both the starter and its chained `SecRule`. This translation is mechanical but is the most likely place to introduce a silent syntax error — a malformed directive fails plugin load, which under `FAIL_OPEN` means no protection and no traffic impact, i.e. silently. Gate B catches it.

Three deliberate choices:

**`targetRefs`, not `selector`.** Coraza's upstream example uses `selector: matchLabels: {app: istio-ingressgateway, istio: ingressgateway}`. Those are `IstioOperator`-era labels that Gateway API-generated pods **do not carry**. Copying the example verbatim yields a plugin attached to nothing — and "attached to nothing" is indistinguishable from "working, no attacks seen." This is the single most likely way to end up with a false sense of protection, which is why §7.1 verifies attachment positively rather than inferring it from a quiet log.

**Image pinned to `0.6.0`.** An untagged `oci://` URL means `latest`, which flips `imagePullPolicy` to `Always` and lets gateway behaviour change with no commit. Both `0.6.0` and `0.6.0-busybox` tags exist; the plain tag is the default choice.

**`failStrategy: FAIL_OPEN`** — see §7.2.

### 5.3 Argo CD `directory.exclude`

Required by the agent-docs contract, identical to `istio-ingress`:

```yaml
spec:
  source:
    directory:
      exclude: '{catalog-info.yaml,mkdocs.yml}'
```

Without it Argo CD attempts to apply the Backstage entity as a Kubernetes manifest and the app fails sync. The validator enforces this.

## 6. Ruleset configuration

### 6.1 Why one directive set rather than `per_authority_directives`

Coraza's `per_authority_directives` maps an authority to a named ruleset. It was rejected as the primary mechanism for two reasons found by reading the source:

**It is an exact string match.** `wafmap.go`:

```go
func (m *wafMap) getWAFOrDefault(key string) (coraza.WAF, bool, error) {
	if w, ok := m.kv[key]; ok {
		return w, false, nil
	}
	// ... falls back to defaultWAF
}
```

The key is the raw `:authority` header. No port stripping, no wildcards, no normalisation. Envoy strips the port when matching routes but Coraza does not when matching authority — so with a bare-hostname map and an "off" default, `Host: grafana.arigsela.com:443` would **skip inspection entirely while still routing to Grafana**. A one-header bypass. (This same asymmetry is why `authorizationpolicy.yaml` already lists both `host` and `host:*` for every rule.)

**Each named set compiles its own WAF instance.** Three tiers means three independently compiled copies of CRS v4 resident in one gateway pod, on a pre-1.0 Wasm build with known memory-pressure history. Given §9's OOM risk, that cost is not worth paying.

A negated regex guard in a single ruleset solves both: `(:\d+)?` closes the port bypass without enumeration, and one ruleset means one compiled CRS.

### 6.2 The directive set

```seclang
SecRuleEngine On

# ── SCOPE ────────────────────────────────────────────────────────────────
# Anything outside the protected set gets the engine turned off entirely.
# The (:\d+)? is load-bearing: Envoy strips the port when matching routes,
# Coraza does not when matching authority. Without it,
# "Host: grafana.arigsela.com:443" reaches Grafana uninspected.
#
# EXPANDING THE WAF TO A NEW HOST = ADDING IT TO THIS REGEX.
SecRule REQUEST_HEADERS:Host "!@rx ^(grafana|oncall|n8n)\.arigsela\.com(:\d+)?$" \
    "id:9000,phase:1,pass,nolog,ctl:ruleEngine=Off"

# ── ENFORCEMENT MODE ─────────────────────────────────────────────────────
# A host listed here logs but does not block.
# THE PROGRESSIVE FLIP (§7.4) IS DELETING ITS LINE.
SecRule REQUEST_HEADERS:Host "@rx ^grafana\.arigsela\.com(:\d+)?$" \
    "id:9001,phase:1,pass,nolog,ctl:ruleEngine=DetectionOnly"
SecRule REQUEST_HEADERS:Host "@rx ^oncall\.arigsela\.com(:\d+)?$" \
    "id:9002,phase:1,pass,nolog,ctl:ruleEngine=DetectionOnly"
SecRule REQUEST_HEADERS:Host "@rx ^n8n\.arigsela\.com(:\d+)?$" \
    "id:9003,phase:1,pass,nolog,ctl:ruleEngine=DetectionOnly"

# ── BODY INSPECTION ──────────────────────────────────────────────────────
# Off by default. On only where an external party controls the payload.
# n8n's admin UI shares the hostname with its webhooks, so the path guard is
# required: running CRS body inspection over the workflow editor's JSON is a
# near-certain false positive.
SecRequestBodyAccess Off
SecRequestBodyLimit 1048576
SecRequestBodyLimitAction ProcessPartial

SecRule REQUEST_HEADERS:Host "@rx ^n8n\.arigsela\.com(:\d+)?$" \
    "id:9010,phase:1,pass,nolog,chain,ctl:requestBodyAccess=On"
    SecRule REQUEST_URI "@rx ^/(webhook|webhook-test|mcp-server)" "t:lowercase"

SecRule REQUEST_HEADERS:Host "@rx ^oncall\.arigsela\.com(:\d+)?$" \
    "id:9011,phase:1,pass,nolog,ctl:requestBodyAccess=On"

# ── LOGGING ──────────────────────────────────────────────────────────────
# Level 3 logs rule matches. Upstream's example uses 9 (every internal
# decision, every request) — on a continuously-scanned internet-facing
# gateway that floods Loki, which backs to S3.
SecDebugLogLevel 3

# ── RULES ────────────────────────────────────────────────────────────────
Include @crs-setup-conf
Include @owasp_crs/*.conf

# ── EXCLUSIONS ───────────────────────────────────────────────────────────
# Populated during the tuning window (§7.3). MUST come after the CRS include:
# SecRuleRemoveById / SecRuleUpdateTargetById only act on loaded rules.
# Every exclusion carries a comment naming the request that triggered it and
# why it is legitimate — an uncommented exclusion list is indistinguishable
# from a list of things someone gave up on.
```

### 6.3 Starting parameters

- CRS **v4.14.0**, embedded in the `0.6.0` image.
- Paranoia level **1**, inbound anomaly threshold **5** — both CRS defaults. PL1 is the low-false-positive tier and is the correct starting point when the objective is to *reach* enforcement rather than to maximise detection.
- Body limit **1 MiB**, `ProcessPartial` — bounds per-request memory (§9).

### 6.4 Design property: guards only ever downgrade

Every `ctl:` guard moves the engine from a stricter state to a laxer one (`On` → `DetectionOnly` → `Off`). This is deliberate. If Coraza's Wasm build implements `ctl:ruleEngine` differently than assumed, a guard that fails to apply results in **more** enforcement, never a silent bypass. The failure direction is safe by construction rather than by verification — though §12.1 verifies it anyway before the first flip.

## 7. Rollout

Each phase is its own commit and rollback point (§V.8).

### 7.1 Phase 0 — Reconnaissance (nothing deployed)

Query the existing `gateway-access-logs` in Loki for the actual `:authority` values arriving on the three hosts. This validates the `(:\d+)?` premise against real traffic before it becomes load-bearing. Zero risk, and it is the cheapest possible way to discover a wrong assumption.

### 7.2 Phase 1 — Deploy in detection-only

All three hosts `DetectionOnly`, all others `Off`. Three gates, all of which must pass:

**Gate A — attachment is real.** `istioctl proxy-config listener` on the gateway pod shows the Coraza filter in the chain. Checked positively; never inferred from an absence of findings (§5.2).

**Gate B — detection actually fires.**
- `?arg=<script>alert(0)</script>` at a protected host → rule trips in the log, request still succeeds (proves `DetectionOnly` is honoured).
- Same request at an internal host → nothing fires (proves the scope guard works).

**Gate C — memory headroom is known.** Record the gateway pod's memory limit and observed usage before and after the plugin loads. See §9.

### 7.3 Phase 2 — Observation window

Minimum **7 days per host**. Seven because the automation has weekly rhythms — a Sunday-night n8n workflow that fires once a week would otherwise be flipped to blocking having never been observed.

Tuning loop: dashboard spike → find the requests in Loki → classify attack or false positive → write a commented exclusion (§6.2) → commit.

### 7.4 Phase 3 — Per-host flip

Order: **`grafana` → `oncall` → `n8n`**, ascending by *silent*-failure risk.

| Host | Failure visibility | Rationale for position |
|---|---|---|
| `grafana` | Immediate, self-inflicted | You are the only user; a 403 is obvious instantly |
| `oncall` | Moderate | Slack retries and surfaces failures; HMAC already validates, so the WAF is genuinely secondary |
| `n8n` | Low — fails silently in someone else's system | Body inspection enabled → most FP surface; a blocked webhook is discovered late |

Each flip is one commit (delete that host's `DetectionOnly` line), then a **48-hour soak** before the next.

**Flip criteria — all three must hold for that host:**
1. ≥7 days of detection data covering real usage.
2. Zero *unexplained* triggers in the last 7 days — every trigger classified as a genuine attack or resolved with a documented exclusion.
3. Primary paths were actually exercised during the window. An empty log means *untested*, not *clean*.

### 7.5 Phase 4 — Document and close

`runbook.md` gets the tuning procedure, the emergency-disable ladder, and the LogQL queries. `docs.md` gets the architecture and the reasoning that is not evident from the YAML — in particular §6.1's exact-match finding, which is the kind of thing that gets "simplified" back into a bug by a future reader.

## 8. Observability

Coraza writes to the `istio-proxy` container's stdout in the gateway pod. Alloy already collects cluster-wide, so events flow to Loki with no pipeline change.

### 8.1 Dashboard — folder `Security`

Four file changes, because dashboards mount as explicit volumes rather than through a sidecar:

1. `grafana-dashboard-coraza.yaml` — new ConfigMap, label `grafana_dashboard: "1"`
2. `grafana-deployment.yaml` — volume `dashboards-coraza`
3. `grafana-deployment.yaml` — volumeMount at `/var/lib/grafana/dashboards/coraza`
4. `grafana-dashboard-configmap.yaml` — provider entry for that path

| Panel | Question it answers |
|---|---|
| Wasm load / fetch errors | *Is the plugin alive?* |
| Would-be blocks over time, by host | *Is this host ready to flip?* |
| Top triggered rule IDs | *What do I tune next?* |
| Triggers by URI path | *Real attack, or my own app?* |
| Coraza-attributed 403s | *Did the flip break something?* |

### 8.2 Why the liveness panel is not optional

`FAIL_OPEN` means a crashed or unfetched Wasm module stops enforcing **silently**. This is the same class of hazard as SPEC §B.9's fail-open-and-silently, which the repo already treats as first-order. A dashboard showing only "rules firing" cannot distinguish *clean* from *dead* — both render as an empty graph. Watching the gateway pod's Wasm load errors is what separates them.

### 8.3 Runbook queries

The LogQL behind each panel goes in `runbook.md` as copy-pasteable queries. The tuning loop — spike, find requests, classify, exclude — runs from the CLI more often than from a dashboard.

## 9. Failure modes and rollback

| Failure | Consequence | Cover |
|---|---|---|
| Plugin attaches to nothing | Silent zero protection | Gate A, §7.2 |
| Wasm fetch fails on pod start | Silent zero protection (fail-open) | Dashboard panel 1, §8.2 |
| False positive after flip | 403 on legitimate traffic | Per-host flip, 48h soak, rollback rung 1 |
| **Gateway pod OOM** | **All 19 hostnames down** | Gate C, §7.2 |
| Coraza panic on malformed request | Fail-open, unprotected | `FAIL_OPEN` + panel 1 |
| WasmPlugin syncs before Gateway | Argo sync error | sync-wave 3 |
| Rule ID collision with CRS v4.14.0 | Undefined rule behaviour | §12.2 |
| Log volume | Loki / S3 cost | `SecDebugLogLevel 3` |

### 9.1 The OOM risk deserves emphasis

The Gateway is a **single pod** pinned to `k3s-control-01`. `gateway-options.yaml` sets no resource block, so Istio's defaults apply: 2 CPU / 1Gi limits. Measured with the WAF loaded (2026-08-11): 43m CPU, 293Mi memory — 29% of the memory limit, under the 50% gate, so no explicit resources block is needed. Adding a compiled CRS plus body-inspection buffers means the WAF's memory failure mode is not "the WAF degrades," it is "all ingress stops."

Gate C establishes the actual limit and observed headroom **before** Phase 2 enables body inspection, and sets an explicit limit on the gateway if the default proves tight.

### 9.2 Why `FAIL_OPEN`

Istio's default is `FAIL_CLOSE`: *"a fatal error in the binary fetching or during the plugin execution causes all subsequent requests to fail with 5xx."* The plugin sits in the filter chain for all nineteen hostnames, so under the default, a ghcr.io outage or a single Coraza panic returns 5xx for Argo CD, Vault, Backstage, and everything else.

That includes the Argo CD UI needed to roll the change back — a deadlock.

`FAIL_OPEN` is correct here because it matches what this component *is*. The security boundary is the AuthorizationPolicy; `authorizationpolicy.yaml` says so in capitals and it is true. Coraza is defence-in-depth layered on top. A defence-in-depth layer that can take down all ingress when it fails is a net loss. `FAIL_OPEN` degrades to *the posture that exists today*, which is a safe floor.

The cost — silent non-enforcement — is real and is covered by §8.2 rather than left implicit.

### 9.3 Rollback ladder, fastest first

1. False positive on one host → restore that host's `DetectionOnly` line. One word, one commit.
2. Broader problem → `SecRuleEngine Off`. Plugin stays loaded and observable.
3. Structural problem → `git revert` the app. Argo prunes the WasmPlugin.

All three are commits: auditable, and none requires `kubectl`. Consistent with the GitOps-only constraint.

## 10. Testing

### 10.1 Existing CI, no new infrastructure

`yaml-lint` and `kubernetes-validate` (kubeconform) pick up the new manifests from the changed-files job automatically. `agent-docs-validate` enforces the docs contract and the `directory.exclude`.

### 10.2 New validator — `scripts/validate-waf-scope.py` + `tests/waf/`

The design rests on a premise: **the WAF covers the hosts that are public by design.** That premise spans two files with nothing connecting them.

If someone later removes a `from:` block in `authorizationpolicy.yaml` to make a host public and does not touch the WAF config, that host becomes internet-facing with no allow-list **and** no WAF — and every existing test still passes.

The validator parses both files and asserts that every internet-reachable public host in the AuthorizationPolicy appears in the WAF's scope regex (§6.2, rule 9000). It converts an assumption into a build failure, which is the only form in which an assumption survives six months.

**Precise rule**, because the naive reading is wrong in two ways:

*A host is "public" if it appears in **any** rule that has no `from:` clause.* Not "all its rules." `n8n.arigsela.com` appears twice — once path-scoped with no `from:` (the webhooks) and once with an IP allow-list (the admin UI). It is public.

*Originally, two hosts (`vault.local`, `vault.10.0.1.110`) were exempted from this check* on the premise that they were LAN-only names with no public DNS and therefore not internet-reachable, so an IP rule on them would be "theatre rather than control." **That premise was wrong** — Host-header routing needs no DNS, and both hosts were confirmed reachable from the public internet (see `authorizationpolicy.yaml`'s 2026-08-11 correction). The exemption list masked a real exposure instead of catching it, which is the exact failure mode this validator exists to prevent.

The fix removed the exemption rather than keeping it: both hosts now carry a `from:` clause, so they are simply no longer "public" by the validator's own definition and need no special case at all. An exemption-free validator is strictly better than one with a list of hosts it trusts not to check — every exemption is a place a wrong assumption can hide, as this one did.

Verified against the live policy on 2026-08-11: the public set is exactly `grafana.arigsela.com`, `oncall.arigsela.com`, `n8n.arigsela.com` (path-scoped) — `vault.local` / `vault.10.0.1.110` are restricted, not exempt.

Follows the established repo pattern: `scripts/validate-*.py` + `tests/*/` pytest + a job in `validate.yaml`.

### 10.3 Post-deploy probes (documented in `runbook.md`)

- Attack request on a protected host → detected, and (pre-flip) passes through.
- Same request on an internal host → nothing fires.
- Benign traffic on all three → untouched.

Re-run after each flip.

## 11. Out of scope

Recorded as follow-ons, deliberately not built:

| Item | Why deferred |
|---|---|
| Mirror the Wasm image to ECR | Removes the ghcr.io runtime dependency and would make `FAIL_CLOSE` defensible. Right hardening step *after* the thing has run clean; adds an `imagePullSecret` to the critical path now. Does not address the runtime-panic trigger. |
| Prometheus metrics scrape | Useful for WAF CPU/memory trends; not needed to drive the flip decision. |
| Grafana alert rules | Thresholds set before seeing a single real detection are guesses. Well-posed once a baseline exists. |
| Response-body inspection | Different problem (data exfiltration), materially more cost. |
| East-west WAF via waypoints | Requires deploying waypoint proxies; `ztunnel` is L4-only. Separate project. |
| Migrate to `TrafficExtension` | Istio 1.30 introduced it as the successor to `WasmPlugin`. `WasmPlugin` is what Coraza documents and what has field mileage. Revisit when Coraza documents `TrafficExtension`. |
| Fix stale waypoint reference in `istio-ingress/docs.md` | Real but unrelated (§3.1). |

## 12. Verification items

Items assumed but not yet confirmed. Each is resolved in the plan before it becomes load-bearing.

**12.1 `ctl:ruleEngine` downgrade semantics in Coraza's Wasm build.** Standard SecLang, but unverified against this build. Mitigated by construction — §6.4 only ever downgrades, so a non-applying guard yields more enforcement, never a bypass. Verified explicitly in Gate B before the first flip.

**12.2 Rule ID range 9000–9011.** Sits in the range CRS reserves for local rules; confirm no collision with CRS v4.14.0's own IDs.

**12.3 Actual `:authority` forms — RESOLVED 2026-08-11.** Measured from 20k gateway access-log lines: 1,524 authority values across 14 hostnames, **all bare hostnames, none carrying a port suffix**.

The `(:\d+)?` group stays. Organic traffic lacking ports is the expected result and does **not** make the guard redundant — the bypass it defeats is a deliberately crafted `Host` header, which by definition would not appear in legitimate traffic. What this measurement establishes is that the guard costs nothing on real traffic, not that it is unnecessary. Gate B3 tests the adversarial case directly.

Incidental finding for §7.3: request volume on the protected hosts over the sampled window was `oncall` 157, `grafana` 23, `n8n` **5**. n8n's webhook traffic is thin enough that the observation window must exercise each workflow deliberately rather than wait for organic traffic.

**12.4 Chained-rule action semantics for `id:9010`.** Confirm the `ctl:` action on the chain starter fires only when the full chain matches.

**12.5 `0.6.0` vs `0.6.0-busybox`.** Confirm the plain tag is the correct artifact for Istio's OCI Wasm pull.

**12.6 Gateway memory limit and headroom.** Gate C. Determines whether `gateway-options.yaml` needs an explicit resources block before body inspection is enabled.

## 13. References

- [OWASP Coraza](https://owasp.org/www-project-coraza-web-application-firewall/) · [coraza.io](https://www.coraza.io/)
- [`corazawaf/coraza-proxy-wasm`](https://github.com/corazawaf/coraza-proxy-wasm) · [Istio example](https://github.com/corazawaf/coraza-proxy-wasm/blob/main/example/istio/README.md)
- [Istio WasmPlugin reference](https://istio.io/latest/docs/reference/config/proxy_extensions/wasm-plugin/) — `failStrategy`, `phase`, `targetRefs`
- [Istio TrafficExtension API (1.30)](https://istio.io/latest/blog/2026/traffic-extension-api/)
- [OWASP CRS false positives and tuning](https://coreruleset.org/docs/concepts/false_positives_tuning/)
- [Tetrate: Envoy WAF performance benchmarks](https://tetrate.io/blog/envoy-waf-performance-benchmarks)
- Repo: `base-apps/istio-ingress/{gateway,authorizationpolicy,gateway-options,telemetry}.yaml`, `SPEC.md` §V.8/§B.9, `templates/agent-docs/README.md`
