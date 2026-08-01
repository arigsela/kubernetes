# Ingress migration baseline — 2026-07-29

**Captured for SPEC.md §T.50**, before any change under §T.43 (ingress-nginx → Istio
Gateway API). Compare against this after every host cutover (§V.53).

Two vantage points were used, because the allow-lists are the security boundary and a
single vantage cannot test both directions:

- **Allow-listed**: `73.7.190.154` — the first entry in all 15 allow-lists.
- **Non-allow-listed**: mobile LTE, wifi off.

---

## 1. Host status codes — from an allow-listed source

| Host | Code | Note |
|---|---|---|
| argocd.arigsela.com | 200 | |
| rollouts.arigsela.com | 302 | |
| argo-workflows.arigsela.com | 200 | |
| atlantis.arigsela.com | 200 | |
| backstage.arigsela.com | 200 | |
| chores.arigsela.com | 200 | |
| coroot.arigsela.com | 200 | |
| dex.arigsela.com | 404 | app-level; OIDC discovery is under a path |
| kagent.arigsela.com | 200 | |
| kagent-mcp.arigsela.com | 401 | HTTP basic auth challenge — expected |
| langflow.arigsela.com | 200 | |
| grafana.arigsela.com | 302 | redirect to login |
| n8n.arigsela.com | 200 | |
| oncall.arigsela.com | 200 | |
| oncall-crewai.arigsela.com | 200 | |
| vault.arigsela.com | 307 | |
| weather-kitchen.arigsela.com | 200 | |
| **chores-agent.arigsela.com** | **000** | no response — cert broken before migration (§R.33) |
| **sandbox-1.vcluster.arigsela.com** | **000** | no response — cert *is* Ready via `letsencrypt-route53` |

### Split hosts — both paths

| URL | Code |
|---|---|
| chores.arigsela.com/api/v1/health | 200 |
| chores.arigsela.com/ | 200 |
| weather-kitchen.arigsela.com/api/v1/health | 404 |
| weather-kitchen.arigsela.com/ | 200 |

`weather-kitchen`'s `/api/v1/health` 404 is a pre-existing app-level result, not an
ingress fault — but it means that host has **no working `/api` probe** to verify §V.62
routing against. Pick a real backend path before cutting it over.

## 2. Access control — from a NON-allow-listed source

| Host | Result | Verdict |
|---|---|---|
| argocd.arigsela.com | **403** | boundary enforced |
| vault.arigsela.com | **403** | boundary enforced |
| dex.arigsela.com | **403** | boundary enforced |
| **grafana.arigsela.com** | **reachable — login page rendered** | **no allow-list, as §R.31 states** |

Spot-proven on 3 of the 14 allow-listed hosts. The deny mechanism is confirmed working;
per-host proof is required at cutover time by §V.53, not here.

**Grafana is the documented exception.** Its login form is on the public internet. This
is the pre-existing state, not a migration regression — but §T.51 must not "restore" an
allow-list here by accident, and the reverse (adding one) is an open decision.

## 3. Certificates

`kubectl get certificate -A` → **24 total: 23 Ready, 1 not Ready.** Matches §R.33 exactly.

Not Ready: `oncall-crewai/chores-tracker-agent-tls` — `chores-agent.arigsela.com`.
**Broken before this work started.** Do not read it as a regression.

Issuer split — matches §R.27 / §R.28 exactly:

| Issuer | Count | Meaning |
|---|---|---|
| `letsencrypt-prod` | 20 | HTTP-01 through `ingress.class: nginx` — these break when nginx goes (§V.52) |
| `letsencrypt-route53` | 1 | DNS-01, `vcluster-sandbox-1` — the escape hatch, already proven |
| `openshell-ca-issuer` | 2 | internal CA, unaffected |
| `openshell-selfsigned` | 1 | internal CA, unaffected |

## 4. Allow-list contents — the finding

All 15 allow-list files carry `10.0.0.0/8`, which contains both the node network
(`10.0.1.0/24`) and the pod network (`10.42.0.0/16`).

Deliberate for LAN access, but **not portable**: under a SNAT'ing gateway the proxy's own
source address falls inside that range, so a verbatim translation to `AuthorizationPolicy`
`ipBlocks` allows the whole internet while every host still returns 200. See §B.9 and
§V.66. Re-scope at translation; do not copy.

## 5. How to re-run

```bash
# allow-listed leg
for h in argocd rollouts argo-workflows atlantis backstage chores coroot dex kagent \
         kagent-mcp langflow grafana n8n oncall oncall-crewai vault weather-kitchen; do
  printf '%-30s %s\n' "$h" \
    "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$h.arigsela.com/")"
done

# certificates
kubectl get certificate -A
kubectl get certificate -A -o jsonpath='{range .items[*]}{.spec.issuerRef.name}{"\n"}{end}' \
  | sort | uniq -c

# deny leg — MUST run from a non-allow-listed address (mobile LTE, wifi off)
curl -s -o /dev/null -w '%{http_code}\n' --max-time 10 https://argocd.arigsela.com/   # expect 403
```
