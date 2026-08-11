# Coraza WAF at the Ingress Gateway — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OWASP Coraza as a WebAssembly filter inside the existing `main` Gateway's Envoy, giving OWASP CRS v4 inspection to the three hostnames that are public by design, ending in enforcement on all three.

**Architecture:** A standalone Argo CD Application (`istio-waf`) carrying one `WasmPlugin` that attaches to the Gateway API `Gateway` named `main` via `targetRefs`. Host scoping, enforcement mode, and body inspection are all expressed as SecLang guard rules inside a single directive set — so one compiled CRS instance serves all hosts, and the per-host enforcement flip is deleting one line. Rollout is detection-only first, then per-host flips ordered by silent-failure risk.

**Tech Stack:** Istio 1.30.3 (ambient profile), Gateway API, `coraza-proxy-wasm` 0.6.0 (OCI, CRS v4.14.0 embedded), Argo CD, Python 3.12 + pytest for the CI validator, Grafana/Loki/Alloy for observability.

**Design doc:** [`docs/superpowers/specs/2026-08-11-coraza-waf-design.md`](../specs/2026-08-11-coraza-waf-design.md)

## Global Constraints

- **GitOps only.** Every change is a git commit that Argo CD syncs. No `kubectl apply`, ever (CLAUDE.md, SPEC §C). `kubectl`/`istioctl` are for **reading and verifying** only.
- **Own commit, own sync, own rollback point per component.** Do not batch unrelated changes (SPEC §V.8).
- **Image pinned:** `oci://ghcr.io/corazawaf/coraza-proxy-wasm:0.6.0`. Never `latest` — an untagged URL flips `imagePullPolicy` to `Always` and lets gateway behaviour change with no commit.
- **`failStrategy: FAIL_OPEN`** on the WasmPlugin. Istio's default is `FAIL_CLOSE`, which would 5xx all 19 hostnames on a fetch failure or plugin panic.
- **`phase: STATS`** — places Coraza after Istio's authorization filters so the IP allow-list culls first.
- **`SecDebugLogLevel 3`.** Never 9 (upstream's example value) — it logs every internal decision for every request into Loki, which backs to S3.
- **Guards only ever downgrade** the engine (`On` → `DetectionOnly` → `Off`). A guard that fails to apply must result in *more* enforcement, never a silent bypass.
- **Protected hosts:** `grafana.arigsela.com`, `oncall.arigsela.com`, `n8n.arigsela.com`. All regexes matching them must carry `(:\d+)?` — Envoy strips the port when matching routes, Coraza does not when matching authority.
- **Argo CD app must set** `spec.source.directory.exclude: '{catalog-info.yaml,mkdocs.yml}'` or sync fails (agent-docs contract).
- **Loki labels available:** `namespace`, `pod`, `container`, `app`. Gateway logs are `{namespace="istio-ingress", container="istio-proxy"}`. Loki datasource uid is `loki`.

---

### Task 1: Phase 0 — Measure the real `:authority` forms

**Why first:** The entire scope guard rests on the claim that authority arrives as either `host` or `host:port`, handled by `(:\d+)?`. That claim is currently a reading of Envoy's behaviour, not a measurement. If it is wrong, the regex in Task 2 is wrong and the WAF has a bypass on day one. This task costs one query and can invalidate a premise before it becomes load-bearing.

**Files:**
- Modify: `docs/superpowers/specs/2026-08-11-coraza-waf-design.md` (§12.3 — record the finding)

**Interfaces:**
- Produces: a confirmed or corrected host regex for Task 2's rules `9000`–`9011`.

- [ ] **Step 1: Confirm the gateway pod name and log flow**

```bash
kubectl -n istio-ingress get pods -l gateway.networking.k8s.io/gateway-name=main
```

Expected: one `Running` pod named `main-istio-<hash>`. Note the name.

- [ ] **Step 2: Query Loki for distinct authority values on the three protected hosts**

Open Grafana → Explore → Loki datasource, and run:

```logql
{namespace="istio-ingress", container="istio-proxy"}
  |= "arigsela.com"
  | pattern `<_> "<method> <uri> <_>" <status> <_> <_> <_> <_> <_> "<_>" "<_>" <_> "<authority>" <_>`
  | authority =~ `(grafana|oncall|n8n)\..*`
```

If the `pattern` parser does not match your access log format, fall back to reading raw lines:

```logql
{namespace="istio-ingress", container="istio-proxy"} |= "grafana.arigsela.com"
```

and inspect the authority field position directly in a few lines.

- [ ] **Step 3: Record which forms appear**

Write the observed set into §12.3 of the design doc, replacing "Phase 0 measures rather than assumes". Expected findings and their consequences:

| Observed | Consequence |
|---|---|
| Only `grafana.arigsela.com` | `(:\d+)?` is harmless belt-and-braces. Proceed unchanged. |
| Both bare and `:443` | `(:\d+)?` is load-bearing exactly as designed. Proceed unchanged. |
| Something else (e.g. trailing dot, uppercase) | **Stop.** Adjust the regex in Task 2 and note it here before proceeding. |

- [ ] **Step 4: Commit the finding**

```bash
git add docs/superpowers/specs/2026-08-11-coraza-waf-design.md
git commit -m "docs: record observed :authority forms at the gateway (Coraza §12.3)"
```

---

### Task 2: Deploy the WAF in detection-only

**Files:**
- Create: `base-apps/istio-waf.yaml`
- Create: `base-apps/istio-waf/wasmplugin.yaml`

**Interfaces:**
- Consumes: the confirmed host regex from Task 1.
- Produces: a `WasmPlugin` named `coraza` in namespace `istio-ingress`; SecLang rule IDs `9000`–`9011` reserved; the scope regex `^(grafana|oncall|n8n)\.arigsela\.com(:\d+)?$` that Task 5's validator parses.

- [ ] **Step 1: Check rule IDs 9000–9011 do not collide with CRS v4.14.0**

```bash
docker run --rm --entrypoint sh ghcr.io/corazawaf/coraza-proxy-wasm:0.6.0-busybox \
  -c 'grep -rhoE "id:9[0-9]{3}" / 2>/dev/null | sort -u | head -40'
```

Expected: no IDs in the range 9000–9011. CRS reserves 9xxxxx (six-digit) for exclusion packages; four-digit 9000-range is conventionally free for local rules. If any collide, shift this plan's guards to 9100–9111 and update every reference in Tasks 2, 5, 8, 9, 10.

If the image has no shell, skip to the empirical check: rule collisions surface in Step 6 as a plugin load error.

- [ ] **Step 2: Create the Argo CD Application**

Create `base-apps/istio-waf.yaml`:

```yaml
# OWASP Coraza WAF as an Envoy Wasm filter on the main ingress Gateway.
#
# SEPARATE APP, NOT part of base-apps/istio-ingress/, deliberately (V8): this is
# the component most likely to need an emergency rollback, and the tuning window
# produces many commits. Folding it in would make every tuning iteration re-sync
# the app that owns the Gateway, its 19 listeners and the AuthorizationPolicy.
#
# sync-wave 3: istio-ingress is wave 2. The Gateway must exist before a
# WasmPlugin can targetRef it.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  finalizers:
    - resources-finalizer.argocd.argoproj.io
  name: istio-waf
  namespace: argo-cd
  annotations:
    argocd.argoproj.io/sync-wave: "3"
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/istio-waf
    directory:
      # catalog-info.yaml is a Backstage entity and mkdocs.yml is TechDocs
      # config; neither is a Kubernetes manifest. Without this exclude Argo CD
      # tries to apply them and the app fails sync.
      exclude: '{catalog-info.yaml,mkdocs.yml}'
  destination:
    server: https://kubernetes.default.svc
    namespace: istio-ingress
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

Note: no `CreateNamespace` effect here in practice — `istio-ingress` already exists from the Gateway app. The option is harmless and consistent with the other apps.

- [ ] **Step 3: Create the WasmPlugin**

Create `base-apps/istio-waf/wasmplugin.yaml`. Every SecLang directive is **one item in the `default` list** — `directives_map` takes an array of strings, not a config blob:

```yaml
# Coraza WAF filter for the main Gateway.
#
# WHAT THIS PROTECTS: the three hostnames that are public by design and so are
# NOT covered by the ipBlocks in base-apps/istio-ingress/authorizationpolicy.yaml:
#   grafana.arigsela.com   - read from mobile, carrier IPs unlistable
#   oncall.arigsela.com    - Slack Events API callbacks
#   n8n.arigsela.com       - /webhook* from arbitrary external services
# The other 16 hostnames are IP-restricted and get SecRuleEngine Off (rule 9000).
apiVersion: extensions.istio.io/v1alpha1
kind: WasmPlugin
metadata:
  name: coraza
  namespace: istio-ingress
spec:
  # targetRefs, NOT selector. Coraza's upstream example uses
  #   selector: matchLabels: {app: istio-ingressgateway, istio: ingressgateway}
  # which are IstioOperator-era labels. This Gateway is Gateway API-generated and
  # its pods DO NOT carry them, so the upstream example attaches to NOTHING - and
  # a plugin attached to nothing looks exactly like a plugin finding no attacks.
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: main
  # Pinned. An untagged oci:// URL means :latest, which flips imagePullPolicy to
  # Always and lets the gateway's behaviour change with no commit.
  url: oci://ghcr.io/corazawaf/coraza-proxy-wasm:0.6.0
  imagePullPolicy: IfNotPresent
  # STATS = after Istio's authorization filters, so the ipBlocks allow-list culls
  # traffic before Coraza spends CPU on it.
  phase: STATS
  # FAIL_OPEN, against Istio's FAIL_CLOSE default. This filter is in the chain for
  # ALL 19 hostnames; under FAIL_CLOSE a ghcr.io outage or one Coraza panic returns
  # 5xx for everything - including the Argo CD UI needed to roll it back. The
  # security boundary is the AuthorizationPolicy; this is defence in depth on top,
  # and fails back to the posture that exists without it.
  # COST: a crashed WAF stops enforcing SILENTLY. That is what the "Wasm load
  # errors" dashboard panel exists to catch (see base-apps/logging).
  failStrategy: FAIL_OPEN
  pluginConfig:
    default_directives: default
    directives_map:
      default:
        - SecRuleEngine On

        # ── SCOPE ──────────────────────────────────────────────────────────
        # Anything outside the protected set gets the engine turned off.
        # The (:\d+)? is LOAD-BEARING: Envoy strips the port when matching
        # routes but Coraza's authority lookup is an exact string match
        # (wafmap.go getWAFOrDefault). Without it, "Host: grafana.arigsela.com:443"
        # reaches Grafana completely uninspected.
        #
        # EXPANDING THE WAF TO A NEW HOST = ADDING IT TO THIS REGEX.
        - SecRule REQUEST_HEADERS:Host "!@rx ^(grafana|oncall|n8n)\.arigsela\.com(:\d+)?$" "id:9000,phase:1,pass,nolog,ctl:ruleEngine=Off"

        # ── ENFORCEMENT MODE ───────────────────────────────────────────────
        # A host listed here LOGS BUT DOES NOT BLOCK.
        # Flipping a host to enforcement = DELETING ITS LINE.
        - SecRule REQUEST_HEADERS:Host "@rx ^grafana\.arigsela\.com(:\d+)?$" "id:9001,phase:1,pass,nolog,ctl:ruleEngine=DetectionOnly"
        - SecRule REQUEST_HEADERS:Host "@rx ^oncall\.arigsela\.com(:\d+)?$" "id:9002,phase:1,pass,nolog,ctl:ruleEngine=DetectionOnly"
        - SecRule REQUEST_HEADERS:Host "@rx ^n8n\.arigsela\.com(:\d+)?$" "id:9003,phase:1,pass,nolog,ctl:ruleEngine=DetectionOnly"

        # ── BODY INSPECTION ────────────────────────────────────────────────
        # Off by default; on only where an external party controls the payload.
        # n8n's admin UI shares the hostname with its webhooks, so the path guard
        # is required - running CRS body inspection over the workflow editor's
        # JSON is a near-certain false positive.
        - SecRequestBodyAccess Off
        - SecRequestBodyLimit 1048576
        - SecRequestBodyLimitAction ProcessPartial
        - |-
          SecRule REQUEST_HEADERS:Host "@rx ^n8n\.arigsela\.com(:\d+)?$" "id:9010,phase:1,pass,nolog,chain,ctl:requestBodyAccess=On"
            SecRule REQUEST_URI "@rx ^/(webhook|webhook-test|mcp-server)" "t:lowercase"
        - SecRule REQUEST_HEADERS:Host "@rx ^oncall\.arigsela\.com(:\d+)?$" "id:9011,phase:1,pass,nolog,ctl:requestBodyAccess=On"

        # ── LOGGING ────────────────────────────────────────────────────────
        # 3 logs rule matches. Upstream's example uses 9 = every internal
        # decision for every request; on a continuously-scanned internet-facing
        # gateway that floods Loki, which backs to S3.
        - SecDebugLogLevel 3

        # ── RULES ──────────────────────────────────────────────────────────
        - Include @crs-setup-conf
        - Include @owasp_crs/*.conf

        # ── EXCLUSIONS ─────────────────────────────────────────────────────
        # Populated during the tuning window. MUST stay after the CRS include:
        # SecRuleRemoveById / SecRuleUpdateTargetById only act on loaded rules.
        # Every exclusion carries a comment naming the request that triggered it
        # and why it is legitimate.
```

- [ ] **Step 4: Lint locally before pushing**

```bash
yamllint base-apps/istio-waf.yaml base-apps/istio-waf/wasmplugin.yaml
```

Expected: clean. CI runs the same lint.

- [ ] **Step 5: Commit and let Argo CD sync**

```bash
git add base-apps/istio-waf.yaml base-apps/istio-waf/wasmplugin.yaml
git commit -m "istio-waf: Coraza WAF in detection-only on the three public hosts"
git push
```

- [ ] **Step 6: Verify the app syncs and the plugin loads**

```bash
kubectl -n argo-cd get application istio-waf \
  -o jsonpath='{.status.sync.status} {.status.health.status}{"\n"}'
```

Expected: `Synced Healthy`.

```bash
kubectl -n istio-ingress logs -l gateway.networking.k8s.io/gateway-name=main \
  -c istio-proxy --tail=200 | grep -iE "wasm|coraza"
```

Expected: evidence the module fetched and initialised. **A `failed to load` or SecLang parse error here means the plugin is not running** — and because `failStrategy` is `FAIL_OPEN`, traffic flows normally, so nothing else will tell you. Do not proceed until this is clean.

---

### Task 3: Verify attachment, detection, and memory headroom (Gates A, B, C)

**Why this is its own task:** Task 2's deliverable is "a plugin that is loaded." This task's deliverable is "evidence the plugin is actually in the filter chain, actually inspects, actually scopes correctly, and does not threaten the gateway's memory." A reviewer could reasonably accept Task 2 and reject this. These gates are the difference between a WAF and the appearance of one.

**Files:** none created. Produces recorded evidence.

**Interfaces:**
- Consumes: the deployed `WasmPlugin` from Task 2.
- Produces: a go/no-go for the observation window; possibly a resources block requirement for `base-apps/istio-ingress/gateway-options.yaml`.

- [ ] **Step 1: Gate A — prove the filter is in the listener chain**

```bash
istioctl -n istio-ingress proxy-config listener \
  deploy/main-istio -o json | grep -c "coraza"
```

Expected: a count **greater than 0**.

If this is `0`, the plugin attached to nothing. Check `targetRefs.name` matches the Gateway (`main`) and that the `WasmPlugin` is in namespace `istio-ingress`. **Do not interpret an absence of detections as success until this passes** — that is precisely the failure this gate exists to catch.

- [ ] **Step 2: Gate B1 — prove detection fires on a protected host**

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  'https://grafana.arigsela.com/?arg=<script>alert(0)</script>'
```

Expected: **200 or 302** (not 403). `DetectionOnly` means log, do not block.

```bash
kubectl -n istio-ingress logs -l gateway.networking.k8s.io/gateway-name=main \
  -c istio-proxy --tail=100 | grep -iE "941|XSS|coraza"
```

Expected: a CRS rule from the `REQUEST-941-APPLICATION-ATTACK-XSS` family logged.

This single check proves three things at once: the filter runs, CRS is loaded, and `ctl:ruleEngine=DetectionOnly` is honoured (verification item §12.1).

- [ ] **Step 3: Gate B2 — prove the scope guard excludes internal hosts (BLOCKING pre-flight, not a one-time check)**

This is not an ordinary gate to tick off once. It is a **blocking pre-flight
check**: run it within minutes of the WAF's first sync, before treating the
deployment as safe, and have rollback rung 2 (`SecRuleEngine Off`, see
`runbook.md`'s Emergency section) staged and ready to apply the moment it
fails.

**Why this one is different from B1/B3/B4:** `ctl:ruleEngine=Off` on rule
`9000` is the ONLY thing keeping untuned CRS v4 off `argocd.arigsela.com`,
`vault.arigsela.com`, `backstage.arigsela.com`, and every other host on the
Gateway. If that guard is inert for any reason — a Coraza Wasm build quirk, a
`ctl:` action semantics mismatch (design §12.1), a directive ordering bug —
CRS starts **blocking** requests on every one of those hosts immediately on
first sync. `FAIL_OPEN` (design §7.2/§9.2) does **not** catch this failure
mode: it only fires when the plugin itself is unhealthy (crashed, unfetched).
Here the plugin is perfectly healthy and doing exactly what a working WAF
does — it is only the scope guard that failed to apply. A green dashboard and
a healthy gateway pod are fully consistent with Argo CD, Vault, and Backstage
being silently 403'd for everyone. That is why this probe cannot wait to be
"noticed" — it has to run, and block, before the rollout is trusted at all.

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  'https://argocd.arigsela.com/?arg=<script>alert(0)</script>'
```

Expected: normal response, and **no new Coraza rule match** in the log for this request. Rule 9000 turned the engine off for this host.

If this does **not** hold — any block, any rule match — apply rollback rung 2
(`SecRuleEngine Off`) immediately and diagnose before proceeding. Do not move
on to Steps 4–7 with this gate unresolved.

- [ ] **Step 4: Gate B3 — prove the port form does not bypass**

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H 'Host: grafana.arigsela.com:443' \
  'https://grafana.arigsela.com/?arg=<script>alert(0)</script>'
```

Expected: the rule fires, same as Step 2. This is the direct test of the `(:\d+)?` guard — the exact bypass described in design §6.1. If it does **not** fire, that bypass is live; stop and fix the regex before continuing.

If curl normalises the `Host` header away, reproduce it at the HTTP/2 layer instead:

```bash
curl -sS --http2-prior-knowledge -o /dev/null -w '%{http_code}\n' \
  -H 'Host: grafana.arigsela.com:443' \
  'https://grafana.arigsela.com/?arg=<script>alert(0)</script>'
```

- [ ] **Step 5: Gate B4 — prove the chained body-inspection rule scopes by path**

This verifies design §12.4 (chained-rule action semantics for `id:9010`). It is checked **now**, not at the end of the rollout: if the chain misbehaves, body inspection is active on n8n's *entire* host including the admin UI for the whole observation window, which would fill the tuning data with workflow-editor false positives and corrupt the very evidence Tasks 8–10 depend on.

Send a body that CRS would flag, to a **non**-webhook path on n8n:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST 'https://n8n.arigsela.com/rest/workflows' \
  -H 'Content-Type: application/json' \
  --data "{\"q\":\"' OR 1=1 --\"}"
```

Expected: **no CRS body rule (942xxx family) in the log for this request.** The chain's path guard turned `requestBodyAccess` off.

Now the same body to a webhook path:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST 'https://n8n.arigsela.com/webhook/waf-probe' \
  -H 'Content-Type: application/json' \
  --data "{\"q\":\"' OR 1=1 --\"}"
```

Expected: a **942xxx SQL-injection rule fires**. (A 404 from n8n for a non-existent webhook is fine — the WAF runs before routing, so the rule still fires.)

```bash
kubectl -n istio-ingress logs -l gateway.networking.k8s.io/gateway-name=main \
  -c istio-proxy --tail=100 | grep -E "942|SQL"
```

If **both** requests fire the rule, the chain is not scoping and body inspection is on for the whole host — fix rule `9010` before proceeding. If **neither** fires, `ctl:requestBodyAccess=On` is not taking effect and n8n's webhooks have no body coverage at all.

- [ ] **Step 6: Gate C — record memory limit and headroom**

```bash
kubectl -n istio-ingress get pod -l gateway.networking.k8s.io/gateway-name=main \
  -o jsonpath='{.items[0].spec.containers[0].resources}{"\n"}'
kubectl -n istio-ingress top pod -l gateway.networking.k8s.io/gateway-name=main
```

Record both. The Gateway is a **single pod** pinned to `k3s-control-01` fronting all 19 hostnames, and `gateway-options.yaml` currently sets no resources — so its memory failure mode is "all ingress stops," not "the WAF degrades."

Decision rule:
- Observed usage **< 50%** of limit → proceed to the observation window.
- Observed usage **≥ 50%** of limit, or no limit is set → add an explicit `resources` block to `base-apps/istio-ingress/gateway-options.yaml` under `deployment:` as its own commit **before** Task 8's first flip (body inspection is already active for n8n/oncall from Task 2).

- [ ] **Step 7: Record the gate results in the design doc**

Update §12 of `docs/superpowers/specs/2026-08-11-coraza-waf-design.md`, marking 12.1 (Gate B1), 12.4 (Gate B4), 12.5 (module loaded from the plain tag in Task 2 Step 6) and 12.6 (Gate C) resolved with what was observed. 12.2 was resolved in Task 2 Step 1 and 12.3 in Task 1.

```bash
git add docs/superpowers/specs/2026-08-11-coraza-waf-design.md
git commit -m "docs: Coraza gates A/B/C results, resolve verification items"
git push
```

---

### Task 4: Agent-docs contract for `istio-waf`

**Files:**
- Create: `base-apps/istio-waf/catalog-info.yaml`
- Create: `base-apps/istio-waf/docs.md`
- Create: `base-apps/istio-waf/runbook.md`
- Create: `base-apps/istio-waf/mkdocs.yml`
- Modify: `scripts/agent-docs-scope.txt`
- Generated: `base-apps/istio-waf/docs/`, `base-apps/index.md`

**Interfaces:**
- Consumes: the deployed app from Task 2, the gate evidence from Task 3.
- Produces: `catalog_entity: istio-waf`, which `scripts/validate-catalog-refs.py` will resolve.

- [ ] **Step 1: Create `catalog-info.yaml`**

```yaml
apiVersion: backstage.io/v1alpha1
kind: Resource
metadata:
  name: istio-waf
  namespace: istio-ingress
  annotations:
    agent-docs/path: docs.md
    backstage.io/techdocs-ref: dir:.
    # The WAF has no workload of its own - it is a filter inside the Gateway's
    # Envoy. The selector points at the pod that actually runs it.
    backstage.io/kubernetes-label-selector: 'gateway.networking.k8s.io/gateway-name=main'
    backstage.io/kubernetes-namespace: istio-ingress
  tags: [waf, security, istio, coraza]
spec:
  type: infrastructure
  lifecycle: production
  owner: group:default/platform
  system: default/platform-networking
  dependsOn:
    - resource:istio-ingress/istio-ingress
```

- [ ] **Step 2: Create `mkdocs.yml`**

```yaml
site_name: istio-waf
docs_dir: docs
nav:
  - Overview: index.md
  - Runbook: runbook.md
plugins:
  - techdocs-core
```

- [ ] **Step 3: Create `docs.md`**

```markdown
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
```

- [ ] **Step 4: Create `runbook.md`**

```markdown
---
type: "Kubernetes App Runbook"
title: "istio-waf — Runbook"
description: "Operational runbook for istio-waf: tuning, emergency disable, and the per-host enforcement flip."
app: istio-waf
catalog_entity: istio-waf
kind: runbook
namespace: istio-ingress
last_reviewed: 2026-08-11
status: current
tags: [waf, security, istio, coraza]
sources:
  - base-apps/istio-waf/wasmplugin.yaml
  - base-apps/logging/grafana-dashboard-coraza.yaml
---

# istio-waf — Runbook

## Emergency: the WAF is blocking legitimate traffic

Fastest first. All three are commits — Argo CD syncs them; never `kubectl edit`.

**1. One host is affected** — restore its `DetectionOnly` line in
`wasmplugin.yaml`:

```yaml
- SecRule REQUEST_HEADERS:Host "@rx ^grafana\.arigsela\.com(:\d+)?$" "id:9001,phase:1,pass,nolog,ctl:ruleEngine=DetectionOnly"
```

**2. Broader problem** — change the first directive to `SecRuleEngine Off`. The
plugin stays loaded and observable, so you can keep diagnosing.

**3. Structural problem** — `git revert` the commit. Argo CD prunes the
WasmPlugin.

To force the sync rather than wait for the poll:

```bash
kubectl -n argo-cd patch application istio-waf --type merge \
  -p '{"operation":{"sync":{"revision":"main"}}}'
```

## Failure modes

### Symptom: legitimate requests return 403 after a flip
- **Check:** dashboard panel "Coraza-attributed 403s", then find the rule:
  ```logql
  {namespace="istio-ingress", container="istio-proxy"} |= "Coraza" |= "denied"
  ```
- **Fix:** emergency step 1 above, then add a commented exclusion (see Tuning).

### Symptom: no detections at all, ever
This is the dangerous one — it looks identical to "no attacks."
- **Check:** is the filter actually attached?
  ```bash
  istioctl -n istio-ingress proxy-config listener deploy/main-istio -o json | grep -c coraza
  ```
  Expect > 0. If 0, the plugin is attached to nothing — check `targetRefs`.
- **Check:** did the module load?
  ```logql
  {namespace="istio-ingress", container="istio-proxy"} |~ "(?i)(wasm.*(fail|error|unable)|(fail|error|unable).*wasm)"
  ```
- **Fix:** if the fetch failed, confirm ghcr.io reachability from the node and
  that the tag `0.6.0` still exists.

### Symptom: gateway pod OOMKilled — ALL 19 hostnames down
- **Check:**
  ```bash
  kubectl -n istio-ingress describe pod -l gateway.networking.k8s.io/gateway-name=main | grep -A3 "Last State"
  ```
- **Fix:** emergency step 3 (revert the WAF) to restore ingress immediately.
  Then raise the gateway's memory limit in
  `base-apps/istio-ingress/gateway-options.yaml` and reduce
  `SecRequestBodyLimit` before redeploying.

### Symptom: Loki ingest volume spiked
- **Check:** `SecDebugLogLevel` in `wasmplugin.yaml`.
- **Fix:** it must be `3`. Level 9 logs every internal decision for every
  request and floods Loki, which backs to S3.

## Tuning: the loop

1. **Find what fired**, ranked:
   ```logql
   topk(20, sum by (rule_id) (count_over_time(
     {namespace="istio-ingress", container="istio-proxy"}
       |= "Coraza" | regexp `id \"(?P<rule_id>\d+)\"` [24h])))
   ```
2. **Find the requests behind one rule:**
   ```logql
   {namespace="istio-ingress", container="istio-proxy"} |= "Coraza" |= "941100"
   ```
3. **Classify** — genuine attack, or your own app?
4. **If a false positive**, add an exclusion at the END of the directives list in
   `wasmplugin.yaml`, after the CRS include (`SecRuleRemoveById` only acts on
   loaded rules), always with a comment:
   ```yaml
   # 942100 fires on Grafana's /api/ds/query — the SQL-ish JSON body of a
   # legitimate dashboard query, not an injection. Confirmed 2026-08-xx.
   - SecRuleUpdateTargetById 942100 "!REQUEST_BODY"
   ```
5. **Commit**, wait for sync, confirm it stopped firing.

An exclusion without a comment is indistinguishable from something someone gave
up on. Always say which request triggered it and why it is legitimate.

## Flipping a host to enforcement

Only when all three hold for that host:
1. ≥7 days of detection data covering real usage.
2. Zero unexplained triggers in the last 7 days.
3. Its primary paths were actually exercised — an empty log means *untested*,
   not *clean*.

Then delete that host's `DetectionOnly` line, commit, and soak 48 hours before
the next host. Order is `grafana` → `oncall` → `n8n`, ascending by
*silent*-failure risk.

## Verification probes

Re-run after every flip.

```bash
# Detects on a protected host (403 once enforcing, 200/302 while DetectionOnly)
curl -sS -o /dev/null -w '%{http_code}\n' \
  'https://grafana.arigsela.com/?arg=<script>alert(0)</script>'

# Does NOT inspect an internal host - expect no new Coraza log line
curl -sS -o /dev/null -w '%{http_code}\n' \
  'https://argocd.arigsela.com/?arg=<script>alert(0)</script>'

# The port form must NOT bypass
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H 'Host: grafana.arigsela.com:443' \
  'https://grafana.arigsela.com/?arg=<script>alert(0)</script>'
```
```

- [ ] **Step 5: Add to the agent-docs scope**

Append `istio-waf` to `scripts/agent-docs-scope.txt`.

- [ ] **Step 6: Run the generators**

```bash
python3 scripts/gen-okf.py --repo-root .
python3 scripts/gen-techdocs.py --repo-root .
```

These create `base-apps/istio-waf/docs/` and regenerate `base-apps/index.md`. Never hand-edit `base-apps/index.md`.

- [ ] **Step 7: Run the validators locally**

```bash
python3 scripts/validate-agent-docs.py --repo-root .
python3 scripts/validate-catalog-refs.py --repo-root .
python3 scripts/gen-techdocs.py --repo-root . --check
```

Expected: all pass. The agent-docs validator specifically checks that `base-apps/istio-waf.yaml` carries the `directory.exclude` added in Task 2.

- [ ] **Step 8: Commit**

```bash
git add base-apps/istio-waf/ scripts/agent-docs-scope.txt base-apps/index.md
git commit -m "istio-waf: agent-docs contract"
git push
```

---

### Task 5: Scope validator — public hosts must be covered by the WAF

**Why:** The design rests on a premise spanning two files with nothing connecting them: *the WAF covers the hosts that are public by design*. If someone later removes a `from:` block in `authorizationpolicy.yaml` and does not touch the WAF, that host becomes internet-facing with no allow-list **and** no WAF, and every existing test still passes. This turns the assumption into a build failure.

**Files:**
- Create: `scripts/validate-waf-scope.py`
- Create: `tests/waf/test_validate_waf_scope.py`
- Modify: `.github/workflows/validate.yaml`

**Interfaces:**
- Consumes: rule `9000`'s scope regex from `base-apps/istio-waf/wasmplugin.yaml`; the `rules` list in `base-apps/istio-ingress/authorizationpolicy.yaml`.
- Produces: `scripts/validate-waf-scope.py --repo-root .`, exit 0 on pass / 1 on failure. Module-level functions `public_hosts(policy_doc)` and `waf_scope_hosts(wasmplugin_doc)` used by the tests.

- [ ] **Step 1: Write the failing tests**

Create `tests/waf/test_validate_waf_scope.py`:

```python
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate-waf-scope.py"
_spec = importlib.util.spec_from_file_location("validate_waf_scope", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

import yaml


def _policy(rules):
    return {"spec": {"rules": rules}}


def _plugin(regex):
    return {
        "spec": {
            "pluginConfig": {
                "directives_map": {
                    "default": [
                        "SecRuleEngine On",
                        f'SecRule REQUEST_HEADERS:Host "!@rx {regex}" '
                        '"id:9000,phase:1,pass,nolog,ctl:ruleEngine=Off"',
                    ]
                }
            }
        }
    }


def test_rule_without_from_is_public():
    doc = _policy([{"to": [{"operation": {"hosts": ["grafana.arigsela.com"]}}]}])
    assert mod.public_hosts(doc) == {"grafana.arigsela.com"}


def test_rule_with_from_is_not_public():
    doc = _policy([
        {
            "to": [{"operation": {"hosts": ["argocd.arigsela.com"]}}],
            "from": [{"source": {"ipBlocks": ["73.7.190.154/32"]}}],
        }
    ])
    assert mod.public_hosts(doc) == set()


def test_host_public_if_any_rule_lacks_from():
    """n8n appears twice: path-scoped webhooks (public) + admin UI (restricted).
    It is public."""
    doc = _policy([
        {
            "to": [{"operation": {"hosts": ["n8n.arigsela.com"],
                                  "paths": ["/webhook/*"]}}]
        },
        {
            "to": [{"operation": {"hosts": ["n8n.arigsela.com"]}}],
            "from": [{"source": {"ipBlocks": ["73.7.190.154/32"]}}],
        },
    ])
    assert "n8n.arigsela.com" in mod.public_hosts(doc)


def test_port_suffix_hosts_are_normalised():
    """The policy lists both "host" and "host:*"; they are one host."""
    doc = _policy([
        {"to": [{"operation": {"hosts": ["grafana.arigsela.com",
                                         "grafana.arigsela.com:*"]}}]}
    ])
    assert mod.public_hosts(doc) == {"grafana.arigsela.com"}


def test_host_with_no_from_is_public_even_if_it_looks_internal():
    """No exemption list: a host with no `from:` clause is reported as public
    regardless of how internal-looking its name is. vault.local previously had
    no `from:` clause and was wrongly assumed unreachable from the internet
    (Host-header routing needs no DNS) — this is the property that assumption
    violated, and the property this validator must never again let slide."""
    doc = _policy([
        {"to": [{"operation": {"hosts": ["vault.local", "vault.10.0.1.110"]}}]}
    ])
    assert mod.public_hosts(doc) == {"vault.local", "vault.10.0.1.110"}


def test_waf_scope_extracts_hosts_from_rule_9000():
    doc = _plugin(r"^(grafana|oncall|n8n)\.arigsela\.com(:\d+)?$")
    assert mod.waf_scope_hosts(doc) == {
        "grafana.arigsela.com",
        "oncall.arigsela.com",
        "n8n.arigsela.com",
    }


def test_check_passes_when_covered():
    policy = _policy([{"to": [{"operation": {"hosts": ["grafana.arigsela.com"]}}]}])
    plugin = _plugin(r"^(grafana)\.arigsela\.com(:\d+)?$")
    assert mod.check(policy, plugin) == []


def test_check_reports_uncovered_public_host():
    policy = _policy([
        {"to": [{"operation": {"hosts": ["grafana.arigsela.com"]}}]},
        {"to": [{"operation": {"hosts": ["newapp.arigsela.com"]}}]},
    ])
    plugin = _plugin(r"^(grafana)\.arigsela\.com(:\d+)?$")
    problems = mod.check(policy, plugin)
    assert len(problems) == 1
    assert "newapp.arigsela.com" in problems[0]


def test_scope_regex_must_carry_port_group():
    """Dropping (:\\d+)? reintroduces the Host-header bypass."""
    doc = _plugin(r"^(grafana)\.arigsela\.com$")
    policy = _policy([{"to": [{"operation": {"hosts": ["grafana.arigsela.com"]}}]}])
    problems = mod.check(policy, doc)
    assert any("(:\\d+)?" in p or "port" in p.lower() for p in problems)


def test_real_repo_is_consistent():
    root = Path(__file__).resolve().parents[2]
    policy = yaml.safe_load(
        (root / "base-apps/istio-ingress/authorizationpolicy.yaml").read_text())
    plugin = yaml.safe_load(
        (root / "base-apps/istio-waf/wasmplugin.yaml").read_text())
    assert mod.check(policy, plugin) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest tests/waf/ -q
```

Expected: FAIL — `scripts/validate-waf-scope.py` does not exist yet (collection error on the `importlib` load).

- [ ] **Step 3: Write the validator**

Create `scripts/validate-waf-scope.py`:

```python
#!/usr/bin/env python3
"""Assert that every internet-reachable public hostname on the ingress Gateway is
covered by the Coraza WAF's scope regex.

The design premise is: the WAF covers the hosts that are public by design. That
premise spans two files with nothing linking them —
base-apps/istio-ingress/authorizationpolicy.yaml decides who is public, and
base-apps/istio-waf/wasmplugin.yaml decides who is inspected. Without this check,
removing a `from:` block to make a host public leaves it with no IP allow-list AND
no WAF, and every other test still passes.

Exits non-zero if a public host is not in the WAF scope, or if the scope regex has
lost its port group.
"""
import argparse
import re
import sys
from pathlib import Path

import yaml

# An exemption list ("_LAN_ONLY") used to live here for vault.local /
# vault.10.0.1.110, on the premise that LAN-only hostnames with no public DNS
# are not internet-reachable. That premise was wrong — Host-header routing
# needs no DNS, and both hosts were confirmed reachable from the public
# internet (see authorizationpolicy.yaml's 2026-08-11 correction). The
# exemption masked a real exposure instead of flagging it.
#
# Correct fix: restrict the hosts (give them a `from:` clause) rather than
# exempt them from this check. Do not reintroduce a "not really public"
# exemption list here — if a host is genuinely unreachable from the internet,
# that needs to be demonstrated the way this one's absence was: empirically,
# not asserted in a comment.

_RULE_9000 = re.compile(r'id:9000\b')
_SCOPE_RX = re.compile(r'!@rx\s+(\S+?)"')
_ALTERNATION = re.compile(r'\^\((?P<alts>[^)]+)\)(?P<suffix>[^"$]*)\$?')


def _strip_port(host):
    """"grafana.arigsela.com:*" and ":443" both name one host."""
    return host.split(":", 1)[0]


def public_hosts(policy_doc):
    """Hosts appearing in ANY rule that has no `from:` clause.

    "Any", not "all": n8n appears twice — once path-scoped with no `from:` (the
    public webhooks) and once IP-restricted (the admin UI). It is public.
    """
    found = set()
    for rule in policy_doc.get("spec", {}).get("rules", []) or []:
        if "from" in rule:
            continue
        for to in rule.get("to", []) or []:
            for host in to.get("operation", {}).get("hosts", []) or []:
                found.add(_strip_port(host))
    return found


def _default_directives(plugin_doc):
    return (
        plugin_doc.get("spec", {})
        .get("pluginConfig", {})
        .get("directives_map", {})
        .get("default", [])
        or []
    )


def _scope_regex(plugin_doc):
    """The negated-rx pattern from scope rule 9000, or None."""
    for directive in _default_directives(plugin_doc):
        if not _RULE_9000.search(directive):
            continue
        m = _SCOPE_RX.search(directive)
        if m:
            return m.group(1)
    return None


def waf_scope_hosts(plugin_doc):
    """Hostnames named by rule 9000's alternation, e.g. ^(a|b)\\.example\\.com$."""
    rx = _scope_regex(plugin_doc)
    if not rx:
        return set()
    m = _ALTERNATION.search(rx)
    if not m:
        return set()
    domain = m.group("suffix")
    # Drop the optional-port group and unescape the literal dots.
    domain = domain.replace(r"(:\d+)?", "").replace("\\.", ".")
    return {f"{alt}{domain}" for alt in m.group("alts").split("|")}


def check(policy_doc, plugin_doc):
    """Return a list of human-readable problems; empty means consistent."""
    problems = []

    rx = _scope_regex(plugin_doc)
    if rx is None:
        problems.append(
            "no scope rule id:9000 with a !@rx pattern found in the WAF "
            "directives - the WAF has no host scoping"
        )
        return problems

    if r"(:\d+)?" not in rx:
        problems.append(
            f"scope regex {rx!r} has no (:\\d+)? port group. Envoy strips the "
            "port when matching routes but Coraza's authority lookup is an exact "
            "string match, so 'Host: <host>:443' would bypass inspection "
            "entirely. See the design doc section 6.1."
        )

    covered = waf_scope_hosts(plugin_doc)
    for host in sorted(public_hosts(policy_doc) - covered):
        problems.append(
            f"{host} is public in authorizationpolicy.yaml (no `from:` clause) "
            f"but is not in the WAF scope regex {rx!r}. It is internet-facing "
            "with neither an IP allow-list nor L7 inspection. Add it to scope "
            "rule id:9000, or restrict it with a `from:` clause if it should "
            "not be public at all."
        )
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".", type=Path)
    args = ap.parse_args()

    policy_path = args.repo_root / "base-apps/istio-ingress/authorizationpolicy.yaml"
    plugin_path = args.repo_root / "base-apps/istio-waf/wasmplugin.yaml"

    for p in (policy_path, plugin_path):
        if not p.exists():
            print(f"ERROR: {p} not found", file=sys.stderr)
            return 1

    problems = check(
        yaml.safe_load(policy_path.read_text()),
        yaml.safe_load(plugin_path.read_text()),
    )
    if problems:
        print("WAF scope validation FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    print("WAF scope OK: every public host is covered by the Coraza scope regex.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest tests/waf/ -q
```

Expected: all PASS, including `test_real_repo_is_consistent`.

- [ ] **Step 5: Run the validator against the real repo**

```bash
python3 scripts/validate-waf-scope.py --repo-root .
```

Expected: `WAF scope OK: every public host is covered by the Coraza scope regex.`

- [ ] **Step 6: Prove the check actually catches the thing it exists for**

Temporarily delete `grafana` from rule 9000's regex in `wasmplugin.yaml`, then:

```bash
python3 scripts/validate-waf-scope.py --repo-root .; echo "exit=$?"
```

Expected: `exit=1` and a message naming `grafana.arigsela.com`. **Revert the edit.** A validator that has never been seen to fail is not known to work.

- [ ] **Step 7: Add the CI job**

Add to `.github/workflows/validate.yaml`, following the shape of `catalog-refs-validate`:

```yaml
  waf-scope-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: pip install pyyaml==6.0.2 pytest==8.3.3
      - name: Run WAF scope validator tests
        run: python -m pytest tests/waf/ -q
      - name: Validate every public host is covered by the WAF
        run: python scripts/validate-waf-scope.py --repo-root .
```

- [ ] **Step 8: Commit**

```bash
git add scripts/validate-waf-scope.py tests/waf/ .github/workflows/validate.yaml
git commit -m "istio-waf: CI check that public hosts stay covered by the WAF scope"
git push
```

---

### Task 6: Grafana dashboard

**Files:**
- Create: `base-apps/logging/grafana-dashboard-coraza.yaml`
- Modify: `base-apps/logging/grafana-deployment.yaml` (volume + volumeMount)
- Modify: `base-apps/logging/grafana-dashboard-configmap.yaml` (provider entry)

**Interfaces:**
- Consumes: Loki datasource uid `loki`; gateway logs at `{namespace="istio-ingress", container="istio-proxy"}`.
- Produces: the evidence base for Task 7's tuning and Tasks 8–10's flip decisions.

- [ ] **Step 1: Create the dashboard ConfigMap**

Create `base-apps/logging/grafana-dashboard-coraza.yaml`:

```yaml
# Coraza WAF dashboard. Drives the tuning window and the per-host enforcement
# flips (see base-apps/istio-waf/runbook.md).
#
# Panel 1 is not decoration. failStrategy is FAIL_OPEN, so a crashed or unfetched
# Wasm module stops enforcing SILENTLY - and a "detections" graph cannot tell
# CLEAN from DEAD, since both are an empty graph. Panel 1 is what separates them.
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dashboard-coraza
  namespace: logging
  labels:
    grafana_dashboard: "1"
data:
  coraza-waf.json: |
    {
      "annotations": {"list": []},
      "editable": true,
      "graphTooltip": 0,
      "id": null,
      "links": [],
      "title": "Coraza WAF",
      "uid": "coraza-waf",
      "version": 1,
      "schemaVersion": 39,
      "refresh": "1m",
      "time": {"from": "now-24h", "to": "now"},
      "tags": ["waf", "security", "coraza"],
      "panels": [
        {
          "type": "timeseries",
          "title": "Wasm load / fetch errors — IS THE PLUGIN ALIVE?",
          "description": "FAIL_OPEN means a dead plugin stops enforcing silently. Non-zero here means requests are flowing UNINSPECTED. This must stay flat at zero.",
          "id": 1,
          "gridPos": {"h": 7, "w": 24, "x": 0, "y": 0},
          "datasource": {"type": "loki", "uid": "loki"},
          "targets": [
            {
              "refId": "A",
              "datasource": {"type": "loki", "uid": "loki"},
              "expr": "sum(count_over_time({namespace=\"istio-ingress\", container=\"istio-proxy\"} |~ `(?i)(wasm.*(fail|error|unable)|(fail|error|unable).*wasm)` [5m]))",
              "legendFormat": "wasm errors"
            }
          ],
          "fieldConfig": {
            "defaults": {
              "custom": {"drawStyle": "line", "fillOpacity": 20},
              "thresholds": {
                "mode": "absolute",
                "steps": [
                  {"color": "green", "value": null},
                  {"color": "red", "value": 1}
                ]
              }
            },
            "overrides": []
          }
        },
        {
          "type": "timeseries",
          "title": "Would-be blocks over time, by host",
          "description": "The flip signal. A host is ready when this is explained — every spike is either a real attack or a documented exclusion.",
          "id": 2,
          "gridPos": {"h": 8, "w": 24, "x": 0, "y": 7},
          "datasource": {"type": "loki", "uid": "loki"},
          "targets": [
            {
              "refId": "A",
              "datasource": {"type": "loki", "uid": "loki"},
              "expr": "sum by (authority) (count_over_time({namespace=\"istio-ingress\", container=\"istio-proxy\"} |= `Coraza` | regexp `authority[\"=: ]+(?P<authority>[a-z0-9.-]+)` [5m]))",
              "legendFormat": "{{authority}}"
            }
          ],
          "fieldConfig": {
            "defaults": {"custom": {"drawStyle": "bars", "fillOpacity": 60}},
            "overrides": []
          }
        },
        {
          "type": "table",
          "title": "Top triggered rule IDs — THE TUNING WORKLIST",
          "description": "Work top-down. For each: real attack, or your own app? If your app, add a commented exclusion.",
          "id": 3,
          "gridPos": {"h": 10, "w": 12, "x": 0, "y": 15},
          "datasource": {"type": "loki", "uid": "loki"},
          "targets": [
            {
              "refId": "A",
              "datasource": {"type": "loki", "uid": "loki"},
              "expr": "topk(20, sum by (rule_id) (count_over_time({namespace=\"istio-ingress\", container=\"istio-proxy\"} |= `Coraza` | regexp `id[\"=: ]+(?P<rule_id>\\d{6})` [$__range])))",
              "instant": true,
              "format": "table"
            }
          ]
        },
        {
          "type": "table",
          "title": "Triggers by URI path — attack or my own app?",
          "description": "A path that is obviously part of your own UI (e.g. /api/ds/query) points to a false positive.",
          "id": 4,
          "gridPos": {"h": 10, "w": 12, "x": 12, "y": 15},
          "datasource": {"type": "loki", "uid": "loki"},
          "targets": [
            {
              "refId": "A",
              "datasource": {"type": "loki", "uid": "loki"},
              "expr": "topk(20, sum by (uri) (count_over_time({namespace=\"istio-ingress\", container=\"istio-proxy\"} |= `Coraza` | regexp `uri[\"=: ]+(?P<uri>[^ \"]+)` [$__range])))",
              "instant": true,
              "format": "table"
            }
          ]
        },
        {
          "type": "timeseries",
          "title": "Coraza-attributed 403s — DID THE FLIP BREAK SOMETHING?",
          "description": "Watch this for 48h after every per-host flip. A rise here on legitimate paths means roll back that host to DetectionOnly.",
          "id": 5,
          "gridPos": {"h": 8, "w": 24, "x": 0, "y": 25},
          "datasource": {"type": "loki", "uid": "loki"},
          "targets": [
            {
              "refId": "A",
              "datasource": {"type": "loki", "uid": "loki"},
              "expr": "sum(count_over_time({namespace=\"istio-ingress\", container=\"istio-proxy\"} |= `Coraza` |~ `(?i)(denied|interrupt)` [5m]))",
              "legendFormat": "blocked"
            }
          ],
          "fieldConfig": {
            "defaults": {"custom": {"drawStyle": "bars", "fillOpacity": 60}},
            "overrides": []
          }
        }
      ]
    }
```

- [ ] **Step 2: Add the volume to the Grafana Deployment**

In `base-apps/logging/grafana-deployment.yaml`, in the `volumes:` list, after the `dashboards-istio` entry:

```yaml
      - name: dashboards-coraza
        configMap:
          name: grafana-dashboard-coraza
```

- [ ] **Step 3: Add the volumeMount**

In the same file, in `volumeMounts:`, after the `dashboards-istio` mount:

```yaml
        - name: dashboards-coraza
          mountPath: /var/lib/grafana/dashboards/coraza
```

- [ ] **Step 4: Add the dashboard provider entry**

In `base-apps/logging/grafana-dashboard-configmap.yaml`, append to `providers:`:

```yaml
      - name: 'coraza'
        orgId: 1
        folder: 'Security'
        type: file
        disableDeletion: false
        editable: true
        options:
          path: /var/lib/grafana/dashboards/coraza
```

- [ ] **Step 5: Validate the JSON parses before committing**

A malformed dashboard will not stop Grafana from starting (unlike the alerting
provisioning file, which will), but it silently fails to appear:

```bash
python3 -c "
import yaml, json
d = yaml.safe_load(open('base-apps/logging/grafana-dashboard-coraza.yaml'))
json.loads(d['data']['coraza-waf.json'])
print('dashboard JSON OK')
"
```

Expected: `dashboard JSON OK`.

- [ ] **Step 6: Commit**

```bash
git add base-apps/logging/grafana-dashboard-coraza.yaml \
        base-apps/logging/grafana-deployment.yaml \
        base-apps/logging/grafana-dashboard-configmap.yaml
git commit -m "logging: Coraza WAF dashboard"
git push
```

- [ ] **Step 7: Verify it renders with real data**

Grafana restarts (the Deployment changed). Then open
`https://grafana.arigsela.com` → Dashboards → Security → Coraza WAF.

Expected: panel 1 flat at zero; panels 2–4 showing data if any scanner traffic
has arrived. If panels 2–4 are empty but Task 3's Gate B fired, the log-parsing
regexes need adjusting to the actual Coraza log line format — capture one real
line and correct the `regexp` stages. Do not skip this: silently-empty panels
would make Task 7's observation window meaningless.

---

### Task 7: Observation window and tuning

**Goal:** produce a tuned ruleset and the evidence that each host is safe to enforce. This is the task that makes the flips safe rather than hopeful.

**Files:**
- Modify: `base-apps/istio-waf/wasmplugin.yaml` (exclusions block, once per false positive found)

**Interfaces:**
- Consumes: the dashboard from Task 6.
- Produces: a per-host verdict against the flip criteria, gating Tasks 8–10.

- [ ] **Step 1: Let it run for a minimum of 7 days**

Seven, not "a few," because the automation has weekly rhythms — a Sunday-night n8n workflow that fires once a week would otherwise be flipped to enforcement having never been observed once.

Record the start date.

- [ ] **Step 2: Work the tuning loop, top-down from panel 3**

For each rule ID in the "Top triggered rule IDs" table:

1. Find the requests behind it in Explore:
   ```logql
   {namespace="istio-ingress", container="istio-proxy"} |= "Coraza" |= "<RULE_ID>"
   ```
2. Classify it: genuine attack, or your own application?
3. If genuine attack → nothing to do; it is working.
4. If false positive → add a **commented** exclusion at the end of the `default`
   directives list in `wasmplugin.yaml`, after the CRS include:

   ```yaml
   # 942100 fires on Grafana's /api/ds/query — the SQL-ish JSON body of a
   # legitimate dashboard query, not an injection. Confirmed 2026-08-xx.
   - SecRuleUpdateTargetById 942100 "!REQUEST_BODY"
   ```

   Prefer the narrowest fix: `SecRuleUpdateTargetById` (drop one target) over
   `SecRuleRemoveById` (drop the rule entirely). Never remove a rule globally to
   fix one path.
5. Commit each exclusion separately so it can be reverted alone:
   ```bash
   git add base-apps/istio-waf/wasmplugin.yaml
   git commit -m "istio-waf: exclude 942100 on Grafana's /api/ds/query (FP)"
   git push
   ```
6. Confirm on the dashboard that it stopped firing.

- [ ] **Step 3: Confirm real usage was actually exercised**

For each of the three hosts, verify its primary paths appear in the access logs
during the window:

```logql
sum by (uri) (count_over_time({namespace="istio-ingress", container="istio-proxy"}
  |= "n8n.arigsela.com" | regexp `uri["=: ]+(?P<uri>[^ "]+)` [7d]))
```

An empty log for a host means **untested**, not **clean**. If a host saw no real
traffic, exercise it deliberately (trigger an n8n webhook, post a Slack event,
load Grafana dashboards) and extend its window.

- [ ] **Step 4: Record the per-host verdict**

For each host, all three must hold:

| Criterion | grafana | oncall | n8n |
|---|---|---|---|
| ≥7 days of data | | | |
| Zero unexplained triggers in last 7d | | | |
| Primary paths exercised | | | |

Write the filled table into `base-apps/istio-waf/docs.md` under a new
`## Tuning history` heading, with dates.

- [ ] **Step 5: Commit the record**

```bash
git add base-apps/istio-waf/docs.md
git commit -m "istio-waf: record observation window results per host"
git push
```

---

### Task 8: Flip `grafana` to enforcement

**Why first:** you are the primary user, so a false positive is immediately visible and affects only you. Lowest silent-failure risk of the three.

**Files:**
- Modify: `base-apps/istio-waf/wasmplugin.yaml`

- [ ] **Step 1: Confirm the flip criteria hold for grafana**

Re-read the table from Task 7 Step 4. All three must be met for `grafana`. If any is not, stop.

- [ ] **Step 2: Delete grafana's DetectionOnly line**

Remove exactly this line from the enforcement-mode block in `wasmplugin.yaml`:

```yaml
        - SecRule REQUEST_HEADERS:Host "@rx ^grafana\.arigsela\.com(:\d+)?$" "id:9001,phase:1,pass,nolog,ctl:ruleEngine=DetectionOnly"
```

Leave `9002` (oncall) and `9003` (n8n) untouched.

- [ ] **Step 3: Commit and sync**

```bash
git add base-apps/istio-waf/wasmplugin.yaml
git commit -m "istio-waf: enforce on grafana.arigsela.com"
git push
```

- [ ] **Step 4: Verify enforcement is live**

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  'https://grafana.arigsela.com/?arg=<script>alert(0)</script>'
```

Expected: **403** (was 200/302 while in DetectionOnly).

- [ ] **Step 5: Verify normal use still works**

Load `https://grafana.arigsela.com`, open a dashboard with real queries (the
Istio Ambient Mesh one), and change a time range. Expected: no 403s, panels
render.

- [ ] **Step 6: Soak 48 hours**

Watch dashboard panel 5 ("Coraza-attributed 403s"). Any rise on legitimate paths
→ restore the `9001` line, commit, and return to Task 7 for that rule.

Do not start Task 9 before the soak completes.

---

### Task 9: Flip `oncall` to enforcement

**Why second:** Slack retries and surfaces failures reasonably fast, and the app already validates HMAC, so the WAF is genuinely secondary there. Body inspection is active on this host (rule `9011`), so it carries more false-positive surface than grafana.

**Files:**
- Modify: `base-apps/istio-waf/wasmplugin.yaml`

- [ ] **Step 1: Confirm grafana's 48h soak was clean and oncall's criteria hold**

- [ ] **Step 2: Delete oncall's DetectionOnly line**

Remove exactly:

```yaml
        - SecRule REQUEST_HEADERS:Host "@rx ^oncall\.arigsela\.com(:\d+)?$" "id:9002,phase:1,pass,nolog,ctl:ruleEngine=DetectionOnly"
```

- [ ] **Step 3: Commit and sync**

```bash
git add base-apps/istio-waf/wasmplugin.yaml
git commit -m "istio-waf: enforce on oncall.arigsela.com"
git push
```

- [ ] **Step 4: Verify a real Slack event still round-trips**

Trigger a genuine Slack Events API callback (post in the connected channel, or
use Slack's "Retry" on a recent event). Confirm the oncall agent receives it:

```bash
kubectl -n oncall-agent logs -l app=oncall-agent --tail=50
```

Expected: the event is processed. **A 403 here would be invisible from Slack's
side beyond a retry**, which is exactly why this is checked explicitly rather
than assumed.

- [ ] **Step 5: Soak 48 hours**

Watch panel 5. Any rise → restore the `9002` line and return to Task 7.

---

### Task 10: Flip `n8n` to enforcement

**Why last:** body inspection is active on its webhook paths, giving it the largest false-positive surface, and a blocked webhook fails silently inside someone else's system — you would find out late.

**Files:**
- Modify: `base-apps/istio-waf/wasmplugin.yaml`

- [ ] **Step 1: Confirm oncall's 48h soak was clean and n8n's criteria hold**

Pay particular attention to Task 7 Step 3 for this host: confirm that **each**
active webhook workflow fired at least once during the window. A workflow that
never ran was never tested.

- [ ] **Step 2: Delete n8n's DetectionOnly line**

Remove exactly:

```yaml
        - SecRule REQUEST_HEADERS:Host "@rx ^n8n\.arigsela\.com(:\d+)?$" "id:9003,phase:1,pass,nolog,ctl:ruleEngine=DetectionOnly"
```

- [ ] **Step 3: Commit and sync**

```bash
git add base-apps/istio-waf/wasmplugin.yaml
git commit -m "istio-waf: enforce on n8n.arigsela.com"
git push
```

- [ ] **Step 4: Verify a real webhook still round-trips**

Trigger each active webhook workflow with its genuine payload. Confirm in n8n's
execution list that each ran.

- [ ] **Step 5: Verify the n8n admin UI is unaffected**

Open `https://n8n.arigsela.com`, edit and save a workflow. Expected: no 403.
This exercises rule `9010`'s path guard — the admin UI must **not** be getting
body inspection.

- [ ] **Step 6: Soak 48 hours, then close out**

- [ ] **Step 7: Update the docs to reflect the enforcing end state**

In `base-apps/istio-waf/docs.md`, update the tuning history table with the flip
dates and note that all three hosts now enforce. Update `last_reviewed` in both
`docs.md` and `runbook.md` frontmatter.

Re-run the generators and validators:

```bash
python3 scripts/gen-okf.py --repo-root .
python3 scripts/gen-techdocs.py --repo-root .
python3 scripts/validate-agent-docs.py --repo-root .
python3 scripts/validate-waf-scope.py --repo-root .
python -m pytest tests/waf/ -q
```

- [ ] **Step 8: Commit**

```bash
git add base-apps/istio-waf/ base-apps/index.md
git commit -m "istio-waf: all three public hosts enforcing; close out rollout"
git push
```

---

## Follow-ons (not in this plan)

Recorded in design §11, deliberately out of scope: mirroring the Wasm image to ECR (would make `FAIL_CLOSE` defensible), Prometheus metrics scrape, Grafana alert rules on 403 spikes, response-body inspection, east-west WAF via waypoints, and migrating `WasmPlugin` → `TrafficExtension`.

One unrelated cleanup noticed during design: `base-apps/istio-ingress/docs.md` references waypoints in `chores-tracker` / `chores-tracker-frontend`, namespaces that no longer exist since `donetick` replaced that app.
