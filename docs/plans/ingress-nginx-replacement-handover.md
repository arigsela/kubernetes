# Handover: replacing ingress-nginx

**Written** 2026-07-29. **For** an agent picking up SPEC.md §T.43.
**Every fact below was verified against the live cluster on 2026-07-29**, not read from
older docs. Where this contradicts SPEC.md, this document is newer — see
[Corrections](#corrections-to-specmd) at the end.

---

## 1. The job

Get the cluster off `ingress-nginx` so it can run Kubernetes 1.36.

`kubernetes/ingress-nginx` is **archived** (`archived: true`, last push 2026-03-23). Its
final release is `controller-v1.15.1`, which is exactly what runs here, and it supports
Kubernetes 1.31–1.35. There will be no 1.36 support, ever. Every other component that caps
at 1.35 can ship support later; this one cannot, because nobody is maintaining it.

The cluster is on **1.35.6 as of 2026-07-29** — the last version ingress-nginx supports. So
nothing is on fire, but 1.36 is blocked until this is solved, and the controller is now
unmaintained software sitting directly on the public internet.

**Success** = every hostname below keeps working, with equivalent access control, and no
`Ingress` object depends on `ingress-nginx` any more.

**This is not a "swap the ingress class" job.** Read §4 before estimating.

---

## 2. Hard constraints

These come from `CLAUDE.md` and the cluster's shape. Violating them breaks things in ways
that are annoying to undo.

| Constraint | Consequence |
|---|---|
| **GitOps only.** All changes via git → Argo CD. No direct `kubectl apply`. | Your work is PRs against `base-apps/`. Argo has `prune: true` + `selfHeal: true` on everything. |
| **The cluster is directly internet-facing.** No proxy, no tunnel, no WAF. | The `whitelist-source-range` allow-lists **are** the security boundary. Losing them during migration exposes admin UIs (Argo CD, Vault, Grafana, Backstage) to the internet. |
| **ingress-nginx is `hostNetwork: true`** and binds `:80`/`:443` on `k3s-control-01` (10.0.1.50). | Only one thing can hold those ports. The final cutover is close to atomic — you cannot run both on `:443` on the same node. See §5. |
| **`local-path` is the only StorageClass**, PVs are node-pinned. | Irrelevant for ingress itself, but it constrains where a replacement can be scheduled if it needs state. |
| Single control-plane node. | `k3s-control-01` carries `node.kubernetes.io/workload: infrastructure`; that label is what pins the controller there. |

---

## 3. Verified current state

### How traffic arrives

```
internet ──▶ 10.0.1.50 :80/:443 ──▶ ingress-nginx (hostNetwork DaemonSet, 1 replica)
                                       └─▶ ClusterIP Services ─▶ pods
```

There is **no** reverse proxy in front. Confirmed from the controller's access log: real
public client addresses arrive directly (`74.248.24.145`, `164.92.144.52`,
`205.210.31.169`), and the allow-lists return 403 to non-allowlisted sources. The cluster is
actively scanned — PHP-vulnerability probes and Palo Alto Xpanse scans show up in the log
hourly. **Assume anything you expose is found within the day.**

A Cloudflare tunnel used to front this. It does not any more, and all references were
removed on 2026-07-29 (see `9e53db8`). Do not reintroduce `X-Forwarded-For` handling: the
old `trusted-proxies` list contained `10.0.0.0/8`, which contains the pod network
`10.42.0.0/16`, meaning any pod could spoof its client IP past the allow-lists. If you ever
put a real proxy in front, scope `trusted-proxies` to that proxy's addresses only.

### Deployment shape

`ingress-nginx` is **not** a normal Argo-managed Helm release. `base-apps/nginx-ingress/nginx-ingress-controller.yaml`
is a k3s `helm.cattle.io/v1` **HelmChart CR** living in `kube-system`, reconciled by k3s's
built-in helm-controller into `ingress-nginx`. Three namespaces are involved and only
`ingress-nginx` contains the actual pods. A k3s upgrade re-runs the install job — see
SPEC.md §V.22.

### Gateway API — what is really there

| Resource | State |
|---|---|
| Gateway API CRDs | installed — `gateways`, `httproutes`, `grpcroutes`, `referencegrants`, `backendtlspolicies` (standard channel; **no** `tlsroutes`/`tcproutes`) |
| GatewayClasses (7, all Accepted) | `istio`, `istio-waypoint`, `istio-eastwest`, `istio-remote`, `gloo-gateway-v2`, `agentgateway-enterprise`, `agentgateway-enterprise-waypoint` |
| Gateways (2) | `chores-tracker/chores-tracker-waypoint`, `chores-tracker-frontend/chores-tracker-frontend-waypoint` — both `istio-waypoint` |
| **HTTPRoutes** | **zero** |
| Ambient-enrolled namespaces | 2 — `chores-tracker`, `chores-tracker-frontend` |

**Read that carefully.** The two existing Gateways are **ambient mesh waypoints** — they do
east-west policy enforcement *inside* the mesh. They are not north-south ingress and they
serve no external traffic. There is **no working ingress Gateway today and not a single
HTTPRoute**. The GatewayClasses being "Accepted" only means a controller claimed them.

This is the single most important correction in this document. The migration is closer to
greenfield than SPEC.md §R.14 implies.

### TLS

- 24 Certificates. **20 use `letsencrypt-prod`**, which solves **HTTP-01 through
  `ingress.class: nginx`**. Removing nginx breaks renewal for all 20.
- **`letsencrypt-route53` already exists, is Ready, and uses DNS-01 via Route 53.** It is
  currently used by exactly 1 certificate. **This is your escape hatch** and it is already
  proven in this cluster.
- 3 certs use an internal `openshell` CA — unaffected.
- cert-manager's HTTP-01 solver creates its own transient `Ingress` objects (there is a
  live one, `oncall-crewai/cm-acme-http-solver-wf87g`). So cert issuance *itself* is a
  consumer of ingress-nginx, not just app routing.
- One pre-existing failure: `oncall-crewai/chores-tracker-agent-tls` is stuck
  (`Issuing certificate as Secret does not exist`) for `chores-agent.arigsela.com`. **This
  is broken before you start** — do not let it look like your regression.

---

## 4. The migration surface

**25 Ingress objects, 21 hostnames, 30 repo files.** Find them with:

```bash
grep -rl 'ingress-nginx\|ingressClassName: nginx' base-apps/ terraform/
```

### Hostnames

| Host | Namespace(s) | Notable |
|---|---|---|
| argocd.arigsela.com | argo-cd | allow-list, rate limits |
| rollouts.arigsela.com | argo-rollouts | allow-list, rate limits |
| argo-workflows.arigsela.com | argo-workflows | allow-list, rate limits |
| atlantis.arigsela.com | atlantis | allow-list **includes GitHub webhook ranges** (`192.30.252.0/22`…) |
| backstage.arigsela.com | backstage | allow-list |
| chores.arigsela.com | chores-tracker + chores-tracker-frontend | **2 Ingresses, one host**, `priority` 100/50, `rewrite-target: /api/$1` |
| coroot.arigsela.com | coroot | allow-list, 600s timeouts |
| dex.arigsela.com | dex | allow-list; OIDC provider |
| kagent.arigsela.com | kagent | allow-list |
| kagent-mcp.arigsela.com | kagent | **HTTP basic auth**, `proxy-buffering: off` |
| langflow.arigsela.com | langflow-ide | allow-list (narrower — 3 IPs) |
| grafana.arigsela.com | logging | no allow-list |
| n8n.arigsela.com | n8n | **2 Ingresses** — admin (50m body) + webhook (10m body, rate-limited) |
| oncall.arigsela.com | oncall-agent | rate limits, 10m body |
| chores-agent.arigsela.com | oncall-crewai | allow-list; **cert currently broken** |
| oncall-crewai.arigsela.com | oncall-crewai | allow-list |
| vault.arigsela.com | vault | allow-list, 300s timeouts |
| vault.local, vault.10.0.1.110 | vault | **internal, no TLS**, `rewrite-target: /` |
| sandbox-1.vcluster.arigsela.com | vcluster-sandbox-1 | **`backend-protocol: HTTPS`**, 3600s timeouts |
| weather-kitchen.arigsela.com | weather-kitchen + -frontend | **2 Ingresses, one host**, `priority` 100/50, `rewrite-target: /api/$1` |

`atlantis/atlantis` is a second, class-less Ingress with no host — inert, but confirm before
deleting.

### Annotations with no standard Gateway API equivalent

This is where the real work is. Gateway API's core spec deliberately excludes most of this;
each needs an implementation-specific replacement.

| Annotation | Uses | Replacement path |
|---|---|---|
| `whitelist-source-range` | **17** | **The hard one.** No Gateway API equivalent. With Istio: `AuthorizationPolicy` with `ipBlocks` — but that needs the real client IP to survive to the enforcement point, which depends on `externalTrafficPolicy` and proxy protocol config. **Verify with a real request from a blocked IP, not by reading YAML.** |
| `auth-type: basic` + `auth-secret` | 1 (kagent-mcp) | No equivalent. Istio has no built-in basic auth — needs an ext_authz service, or swap to OIDC via the existing `dex`, or accept a different auth model. |
| `rewrite-target` | 3 (2 with regex capture) | Gateway API `URLRewrite` filter supports prefix rewrite, **not regex capture groups**. The paths must be re-expressed as prefix matches, which may change routing semantics. |
| `priority: 100 / 50` | 4 (2 host-pairs) | nginx-specific ordering. Gateway API has deterministic precedence (longest path match wins) — these should be expressible, but the two-app-one-host split must be verified per path. |
| `limit-rps`, `limit-connections` | 7 | No standard equivalent. Istio needs a `RateLimit` / EnvoyFilter or local rate limit config. |
| `proxy-body-size: 50m/10m` | 3 | Istio: no direct per-route knob; needs listener-level config. |
| `backend-protocol: HTTPS` | 1 (vcluster) | `BackendTLSPolicy` — CRD is installed. |
| `proxy-read-timeout: 3600` | 1 (vcluster) | Long-lived connections; Istio route timeout + idle timeout. |
| `force-ssl-redirect` / `ssl-redirect` | 22 | Straightforward: an HTTP listener with a `RequestRedirect` filter to HTTPS. |

---

## 5. Options to evaluate

Not prescribed — evaluate and recommend. Considerations for each:

**A. Istio ingress Gateway (Gateway API).** Istio is already installed and its GatewayClass
is Accepted. *But* Istio here is **1.24.0, deliberately deferred and already out of its
tested range** (SPEC.md §V.49, §T.10 — a 5-step sequential upgrade). Building the entire
ingress on a component you have consciously postponed upgrading is a real coupling risk;
it may argue for doing §T.10 first. Only 2 of 45 namespaces are in the mesh, so this is not
"turn on what's already there".

**B. Gloo Gateway v2** (`gloo-system/gloo-gateway`, ClusterIP, GatewayClass Accepted).
Already present. Investigate why it was installed, whether it is licensed/supported, and
what it actually does today — it serves nothing currently.

**C. Traefik.** k3s ships it and it is *explicitly disabled* (`disable: traefik` in
`/etc/rancher/k3s/config.yaml`). Re-enabling is low-effort and it supports both `Ingress`
and Gateway API, which would allow a mostly annotation-preserving move. Note the repo's
history suggests it was deliberately replaced by nginx.

**D. A maintained Ingress controller** — e.g. HAProxy or Envoy Gateway — keeping `Ingress`
objects and remapping only annotations. Lowest semantic risk, no Gateway API rewrite, but
does not advance the Gateway API direction.

**E. Do nothing and stay on 1.35.** Legitimate to state as a baseline. 1.35 is supported for
a long while. The cost is running archived, unmaintained, internet-facing software. Worth
pricing honestly rather than dismissing.

**Sequencing note:** whichever you choose, `letsencrypt-route53` (DNS-01) lets you
**decouple certificate issuance from ingress first**, as an independent, low-risk,
reversible step. Doing that before touching routing removes cert-manager from the critical
path entirely. That is probably the single highest-value first move regardless of option.

---

## 6. Traps

1. **`:80`/`:443` on one node cannot be shared.** A replacement that also wants
   `hostNetwork` cannot run alongside nginx. Either give it different ports and cut over via
   DNS/firewall, put it on a different node, or accept a hard cutover. **Plan this before
   writing manifests.**
2. **Argo has `prune: true` everywhere.** Deleting an `Ingress` from git deletes it from the
   cluster on next sync. Migrate host-by-host with the old object still present, or you get
   a gap.
3. **cert-manager is a consumer.** Move issuers to `letsencrypt-route53` *before* removing
   nginx, or 20 certificates stop renewing. They will not fail immediately — they fail
   ~30 days later, long after you have declared victory.
4. **The allow-lists are the only thing protecting admin UIs.** Argo CD, Vault, Grafana,
   Backstage, Dex are all internet-reachable. If IP restriction does not carry over on a
   host, that host is exposed the moment it cuts over. **Test from a non-allowlisted
   address.**
5. **`chores-agent.arigsela.com`'s cert is already broken.** Baseline it.
6. **Two hosts serve two apps each** (`chores.`, `weather-kitchen.`) via `priority` +
   regex rewrite. These are the most likely to break subtly — verify `/api/*` and `/*` both
   land correctly.
7. **k3s upgrades re-run the HelmChart install job** (§V.22), and §V.50/§V.51 in SPEC.md
   describe CNI failures that look like ingress failures. If pods will not start after a
   node hop, read those before blaming your work.
8. **`base-apps/index.md` is generated.** Never hand-edit. Change `description:` in the
   app's `docs.md`, then run `scripts/gen-okf.py --repo-root .` and
   `scripts/gen-techdocs.py --repo-root .`. CI gates both.

---

## 7. How to verify

There is no synthetic test suite for ingress. Verification is real requests.

```bash
# every host still answers (from an allow-listed address)
for h in argocd vault grafana backstage coroot dex kagent n8n oncall rollouts; do
  echo -n "$h: "
  curl -s -o /dev/null -w '%{http_code}\n' --max-time 10 "https://$h.arigsela.com/"
done

# the two split hosts, both paths
curl -s -o /dev/null -w 'api  %{http_code}\n' https://chores.arigsela.com/api/v1/health
curl -s -o /dev/null -w 'root %{http_code}\n' https://chores.arigsela.com/

# access control still works — MUST be 403 from a non-allowlisted source
curl -s -o /dev/null -w '%{http_code}\n' https://argocd.arigsela.com/
```

Baseline before you start: `curl` every host and save the status codes. Current spot-check
(2026-07-29, from an allow-listed address): `argocd` 200, `vault` 307, `grafana` 302.

Also: `kubectl get certificate -A` (expect 23 Ready, 1 broken as noted), and
`scripts/hop-verify.sh gate` for overall cluster health — its `§V.22` check specifically
watches ingress-nginx and **will need updating** when nginx goes away.

---

## 8. Key files

| Path | What |
|---|---|
| `base-apps/nginx-ingress/nginx-ingress-controller.yaml` | the HelmChart CR — controller config |
| `base-apps/nginx-ingress/docs.md`, `runbook.md` | architecture + failure modes |
| `base-apps/cert-manager/` | ClusterIssuers, incl. the Route 53 DNS-01 one |
| `base-apps/*/[nginx-]ingress*.yaml` | 27 per-app Ingress manifests |
| `scripts/hop-verify.sh` | cluster gate; `check_ingress()` assumes nginx |
| `SPEC.md` | §R.13, §R.14, §R.18, §V.46, §T.43 |
| `docs/plans/k3s-1.36-upgrade-plan.md` | the k3s runbook this unblocks |

---

## 9. Questions for the human

1. **Is 1.36 actually wanted, or is "stop running archived internet-facing software" the
   real driver?** These lead to different answers — the second permits option D.
2. **Is the Istio 1.24 → 1.29 upgrade (§T.10) in scope, or to be avoided?** This decides
   whether option A is viable.
3. **Why was Gloo Gateway installed, and is it licensed/supported?**
4. **Was Traefik dropped for a specific reason, or incidentally?**
5. **Is the IP allow-list model intended long-term**, or is the real goal SSO via `dex` (in
   which case the migration could reduce annotation surface rather than reproduce it)?
6. **Is there a maintenance window**, or must this be zero-downtime? This heavily affects
   the `:443` cutover strategy in §6.1.

---

## Corrections to SPEC.md

Fix these in `SPEC.md` as part of the work.

- **§R.14 is over-optimistic.** It reads *"migration path ∃, ⊥ greenfield"* on the basis of
  live GatewayClasses and the `chores-tracker` waypoints. But those waypoints are
  **east-west ambient mesh**, not ingress, and there are **zero HTTPRoutes**. No north-south
  Gateway serves traffic today. The path is closer to greenfield than that row implies.
- **§T.43 says "infra ∃ already"** — same over-statement, same correction.
- Neither §R.14 nor §T.43 mentions that **cert-manager HTTP-01 depends on nginx**, which is
  a genuine blocker, nor that **`letsencrypt-route53` already exists** as the way out. Both
  belong in the task.
- Nothing in the spec records that **`whitelist-source-range` on 17 Ingresses is the
  cluster's only external access control** and has no Gateway API equivalent. That is the
  largest single piece of work and should be its own task.
