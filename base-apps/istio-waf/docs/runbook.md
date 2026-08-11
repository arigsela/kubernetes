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
  POD=$(kubectl -n istio-ingress get pods \
    -l gateway.networking.k8s.io/gateway-name=main -o jsonpath='{.items[0].metadata.name}')
  kubectl -n istio-ingress exec $POD -c istio-proxy -- \
    pilot-agent request GET config_dump | grep -c -i coraza
  ```
  Expect > 0 (was 20 when verified 2026-08-11). If 0, the plugin is attached to
  nothing — check `targetRefs`.

  Use `pilot-agent`, NOT `istioctl` (not installed) and NOT `curl` inside the
  container (not present in the istio-proxy image). A failed exec produces empty
  output and `grep -c` then returns 0, which reads exactly like "filter not
  attached" — that false negative happened during the real Gate A run.
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
       |= "Coraza" | regexp `id \"(?P<rule_id>\d{6})\"` [24h])))
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

The second probe below (the internal-host negative check) is **not merely a
re-run-after-flip item** — it is a **BLOCKING pre-flight gate**. Run it within
minutes of the WAF's first sync, before trusting the deployment at all, and
have rollback rung 2 (`SecRuleEngine Off`, see Emergency above) staged and
ready to apply the instant it fails. Rationale: if the `ctl:ruleEngine=Off`
scope guard (rule `9000`) is inert for any reason, untuned CRS v4 starts
**blocking** requests on `argocd`, `vault`, `backstage`, and every other host
on the Gateway — and `FAIL_OPEN` cannot catch that, because the plugin is
perfectly healthy; only the guard is inert. A quiet dashboard and a healthy
pod are consistent with the whole Gateway silently blocking legitimate
traffic on every internal host. This is why the check has to run immediately
and block, not wait to be noticed.

Re-run all three probes after every subsequent flip.

```bash
# Detects on a protected host (403 once enforcing, 200/302 while DetectionOnly)
curl -sS -o /dev/null -w '%{http_code}\n' \
  'https://grafana.arigsela.com/?arg=<script>alert(0)</script>'

# BLOCKING PRE-FLIGHT GATE — does NOT inspect an internal host - expect no new
# Coraza log line. Run within minutes of first sync; if this fails, apply
# rollback rung 2 (SecRuleEngine Off) immediately.
curl -sS -o /dev/null -w '%{http_code}\n' \
  'https://argocd.arigsela.com/?arg=<script>alert(0)</script>'

# The port form must NOT bypass
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H 'Host: grafana.arigsela.com:443' \
  'https://grafana.arigsela.com/?arg=<script>alert(0)</script>'
```
