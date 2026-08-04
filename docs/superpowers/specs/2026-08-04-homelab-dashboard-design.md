# Design: Homelab dashboard (gethomepage/homepage)

**Date:** 2026-08-04
**Status:** design — pending user review
**Topic:** A single dashboard at `home.arigsela.com` listing every app deployed in
the cluster plus the Plex server running on the Windows/WSL2 box, with live
widgets for Plex, Argo CD, Grafana, and cluster resources.

## Goal

There is no single place that answers "what do I run, and is it up?". Argo CD
answers it for cluster workloads only, in GitOps terms rather than user terms, and
it says nothing about Plex.

Deploy [gethomepage/homepage](https://github.com/gethomepage/homepage) as
`base-apps/homepage/`, sourcing most tiles automatically from the `HTTPRoute`
resources that already define every public hostname, so that **adding an app to
`base-apps/` adds its dashboard tile in the same commit**.

## Decisions (settled during brainstorming)

| Decision | Choice | Why |
|---|---|---|
| Product | Homepage, not Heimdall | Heimdall stores tiles in a SQLite DB edited through its web UI — unreviewable, not in Git, invisible to Argo CD. Homepage is YAML + Kubernetes-native discovery. |
| Tile sourcing | Hybrid: annotations for link-only, ConfigMap for widget-bearing | Annotations keep tile metadata beside the route that defines the URL, so tiles follow renames. |
| Depth | Launcher + 4 widgets | Every widget costs a credential or a metrics pipeline. Links for all, live data for the few actually watched. |
| Widgets in v1 | Plex, Argo CD, Grafana, Kubernetes resources | Chosen by the user. |
| Exposure | `home.arigsela.com`, restricted to the existing 4-IP allow-list | Homepage ships **no authentication at all** and upstream states none is planned. Public exposure would require adding oauth2-proxy to guard a page that lists every service in the homelab. |
| Argo CD widget auth | Enable controller metrics → `prometheusmetric` widget | Avoids the `argocd-cm` landmine (below) and makes Argo CD metrics available in Grafana generally. |
| Grafana widget auth | A **Viewer service-account token**, sent as a Bearer header by a `customapi` tile | Basic auth is disabled on that Grafana by design, so the built-in `grafana` widget cannot authenticate at all. Admin creds must not sit in an unauthenticated pod's env either. |

### Rejected alternatives

- **Heimdall.** Actively maintained (v2.8.1, 2026-07-09; last commit 2026-08-03) and
  it does have a Plex enhanced app — but that app only reports item counts for two
  configured library sections, and all tile state lives in a SQLite DB mutated
  through the UI. That inverts this repo's model.
- **All-static ConfigMap.** Simpler and needs no cluster-wide ClusterRole, but every
  new app becomes a two-place change with nothing enforcing the second place.
  `chores.arigsela.com` changing hands from chores-tracker to donetick is the
  concrete failure this avoids: a static tile would still read "Chores Tracker".
- **Generating tiles from `catalog-info.yaml`.** Rejected on inspection —
  `catalog-info.yaml` carries no hostname, so a generator would still have to read
  `HTTPRoute`s for URLs. That is a build-time reimplementation of runtime discovery.
- **Argo CD apiKey account via Terraform `configs.cm`.** Rejected: the argocd module
  writes to the deprecated `server.config.*` path the chart ignores, and
  `templates/agent-docs/README.md` documents that migrating to `configs.cm` clobbers
  the chart's default `resource.exclusions` — which is what lets `catalog-info.yaml`
  sync safely today.

## Non-goals

- Authentication in front of Homepage. The IP allow-list is the control.
- Widgets for Backstage, Coroot, Vault, n8n, Atlantis, or Kagent — Homepage has no
  widget for any of them. They are link-only tiles, permanently.
- Replacing Backstage. Backstage is the service catalog; this is a launcher.
- Making the dashboard reachable from mobile data. Same limitation every other app
  behind the allow-list already has.

## Architecture

One Argo CD Application, `base-apps/homepage/`, following the `base-apps/donetick/`
template. Image `ghcr.io/gethomepage/homepage:v1.13.2` pinned, single replica,
`Recreate`, non-root, `nodeSelector: node.kubernetes.io/workload=application`.

### File inventory

New, in `base-apps/homepage/`:

| File | Purpose |
|---|---|
| `configmap.yaml` | All 8 Homepage config files. Unused ones are empty strings but **must exist**. |
| `deployments.yaml` | Deployment, `checksum/config` annotation, `MY_POD_IP` + `HOMEPAGE_ALLOWED_HOSTS` env, widget secrets as env |
| `services.yaml` | ClusterIP :3000 |
| `serviceaccount.yaml` | ServiceAccount + ClusterRole + ClusterRoleBinding |
| `secret-store.yaml` | Vault SecretStore, role `homepage` |
| `external-secrets.yaml` | Plex token, Grafana service-account token |
| `httproute.yaml` | `home.arigsela.com` → :3000 |
| `certificate.yaml` | `homepage-tls` via `letsencrypt-route53` |
| `reference-grant.yaml` | Gateway in `istio-ingress` may read `homepage-tls` |
| `catalog-info.yaml`, `docs.md`, `runbook.md`, `mkdocs.yml` | Agent-docs contract |

Also new: `base-apps/homepage.yaml` (Argo CD Application, with
`directory.exclude: '{catalog-info.yaml,mkdocs.yml}'` — the validator requires it)
and `scripts/provision-homepage-vault.sh`.

Edits to existing files:

- `base-apps/istio-ingress/gateway.yaml` — `https-homepage` listener
- `base-apps/istio-ingress/authorizationpolicy.yaml` — allow rule for the new host
- `scripts/agent-docs-scope.txt` — add `homepage`
- `terraform/modules/argocd/helm.tf` — enable controller metrics
- 14 app `httproute.yaml` files — `gethomepage.dev/*` annotations

### RBAC

Read-only, and **narrower than upstream's example**: the Traefik and Ingress rules
are dropped because this cluster runs neither.

```yaml
- apiGroups: [""]
  resources: [namespaces, pods, nodes]
  verbs: [get, list]
- apiGroups: [gateway.networking.k8s.io]
  resources: [httproutes, gateways]
  verbs: [get, list]
- apiGroups: [metrics.k8s.io]
  resources: [nodes, pods]
  verbs: [get, list]
```

This is still cluster-wide read on pods and namespaces. That is the one new
privilege this design introduces and it is unavoidable for discovery and pod-health
dots.

### Deployment details that are load-bearing

- **`HOMEPAGE_ALLOWED_HOSTS` is mandatory** since Homepage v1.0. It must contain
  both `home.arigsela.com` and `$(MY_POD_IP):3000` — the latter for the kubelet
  probe. Omitting the hostname rejects every real request.
- **The ConfigMap mounts as individual `subPath` files**, and Kubernetes never
  propagates ConfigMap updates into `subPath` mounts. A config edit therefore syncs
  cleanly and does nothing until the pod restarts. The `checksum/config` pod
  annotation used by `donetick` is **required here, not stylistic**, with the same
  `shasum -a 256 ... | cut -c1-16` recipe in a comment.
- `/app/config/logs` needs an `emptyDir`.
- Verify the image's default UID at implementation and set `securityContext` to
  match; assume non-root `1000:1000` as with `donetick` unless the image differs.

## Tile sourcing

**The rule**, to be reproduced verbatim in `docs.md`:

> Tiles with a widget are defined in `configmap.yaml`. Link-only tiles are defined by
> annotations on their own `httproute.yaml`. Never put a credential in an
> annotation — annotations are plaintext in Git, and Homepage performs
> `{{HOMEPAGE_VAR_*}}` substitution only in config files, never in annotations.

**Static (3):** Argo CD, Grafana, Plex.
**Annotated (14):** coroot, argo-rollouts, argo-workflows, atlantis, backstage, dex,
kagent, kagent-mcp, oncall-agent, oncall-crewai, vault, weather-kitchen, n8n,
donetick.

The `vault.local` and `vault.10.0.1.110` listeners are excluded — LAN-only names
with no public DNS.

**One hostname is served by two HTTPRoutes.** `weather-kitchen.arigsela.com` is
path-split across `weather-kitchen-frontend` and `weather-kitchen-backend`.
Homepage discovers per-HTTPRoute, so annotating both yields two duplicate tiles.
**Annotate only `weather-kitchen-frontend`.** A scan of every `httproute*.yaml`
confirms this is the sole duplicate; `kagent` has two HTTPRoutes but they carry
distinct hostnames (`kagent` and `kagent-mcp`) and are correctly two tiles.

Annotation shape:

```yaml
metadata:
  annotations:
    gethomepage.dev/enabled: "true"
    gethomepage.dev/name: Donetick
    gethomepage.dev/group: Home
    gethomepage.dev/icon: donetick.png
    gethomepage.dev/description: Household chore tracker
    gethomepage.dev/pod-selector: app=donetick
```

`href` is derived from the HTTPRoute's own `hostnames` — this is the drift
protection.

**`pod-selector` is optional and must be determined per app.** Label conventions
here are not uniform — verified against the live cluster:

- `app=<name>` works for atlantis, backstage, dex, n8n, donetick.
- `app=` exists but does **not** match the app name for argo-workflows (`server`),
  oncall-agent (`oncall-agent-api`), oncall-crewai (`crewai-frontend`), and
  weather-kitchen (`weather-kitchen-backend`, and the HTTPRoute for that host is
  owned by `weather-kitchen-frontend` — check which workload the route actually
  targets).
- No `app=` label at all on argo-rollouts, vault, kagent, coroot.
  `app.kubernetes.io/name` covers argo-rollouts and vault.
- **Omit `pod-selector`** for coroot (a ClickHouse cluster whose pods carry neither
  label) and kagent (a namespace of many independently-labelled agent pods, with no
  single selector that means "the kagent UI").

Omitting it costs only the pod-health dot; the tile and link are unaffected. Do not
guess a selector — a wrong one renders a permanently unhealthy tile, which is worse
than no dot at all.

### Grouping

| Group | Tiles |
|---|---|
| GitOps & Delivery | Argo CD*, Argo Rollouts, Atlantis |
| Automation | Argo Workflows, n8n |
| Observability | Grafana*, Coroot |
| Platform | Backstage, Vault, Dex |
| AI & Agents | Kagent, Kagent MCP, Oncall, Oncall CrewAI |
| Home | Plex*, Chores, Weather Kitchen |

`*` = static, widget-bearing. A full-width `resources` widget (cluster + node
CPU/memory) sits above the groups in `widgets.yaml`; metrics-server is present and
running, verified 1/1.

Icons resolve against [dashboard-icons](https://github.com/homarr-labs/dashboard-icons).
Argo CD, Grafana, Vault, n8n, Backstage, Plex, and Atlantis are expected to resolve;
**Kagent, Coroot, Donetick, Oncall CrewAI, and Weather Kitchen are not** and use
Material Design fallbacks (`mdi-<name>`). Confirm each against the icon set at
implementation rather than assuming.

## Widgets and secrets

Vault path `k8s-secrets/homepage`, read by a `homepage` k8s-auth role bound to the
`default` ServiceAccount in the `homepage` namespace (role name matches namespace,
per repo convention). Provisioned by a new idempotent
`scripts/provision-homepage-vault.sh` mirroring `provision-donetick-vault.sh`,
run via `kubectl cp` into `vault-0`.

| Vault key | Env var | Consumer |
|---|---|---|
| `plex-token` | `HOMEPAGE_VAR_PLEX_TOKEN` | Plex widget |
| `grafana-token` | `HOMEPAGE_VAR_GRAFANA_TOKEN` | Grafana tile (`customapi`) |

**Grafana uses a service-account token via `customapi`, not the built-in `grafana`
widget.** *(Revised during implementation — see below.)* Homepage's `grafana` widget
speaks only basic auth, and `base-apps/logging/grafana-deployment.yaml` sets
`GF_AUTH_BASIC_ENABLED=false` and `GF_AUTH_DISABLE_LOGIN_FORM=true` deliberately, so
GitHub OAuth is the only interactive path in. That file's own comment states the
consequence: *"this ends admin API access by username/password. Anything scripted
against Grafana's API needs a service account token instead."* Verified empirically:
basic auth with a `glsa_` token returns 401; the same token as a Bearer header
returns 200.

The tile therefore uses `customapi` against
`/api/alertmanager/grafana/api/v2/alerts` with `format: size`, showing the count of
**currently firing alerts** — the actionable number, rather than a dashboard count
that never changes.

Argo CD needs no credential under this design.

### Argo CD metrics

`argocd_app_info` is not currently in Prometheus: the controller metrics Service
does not exist and no Service in `argo-cd` carries a scrape annotation (both
verified against the live cluster).

Terraform change in `terraform/modules/argocd/helm.tf`, added through the
`settings` values map that is already `yamlencode`d — **not** as a `set` block,
because annotation keys contain dots that helm's `set` syntax requires escaping
(cf. the existing `server.config.exec\\.enabled` line):

```yaml
controller:
  metrics:
    enabled: true
    service:
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8082"
```

**No Prometheus config change is needed.** The existing `kubernetes-service-endpoints`
job keeps any Service annotated `prometheus.io/scrape: "true"` and honors
`prometheus.io/port`; the relabel rules were read to confirm this.

Widget:

```yaml
widget:
  type: prometheusmetric
  url: http://prometheus.logging.svc.cluster.local:9090
  metrics:
    - label: Apps
      query: count(argocd_app_info)
    - label: Synced
      query: count(argocd_app_info{sync_status="Synced"})
    - label: OutOfSync
      query: count(argocd_app_info{sync_status="OutOfSync"})
    - label: Degraded
      query: count(argocd_app_info{health_status="Degraded"})
```

Confirm the `sync_status` / `health_status` label names against live metric output
before finalizing the queries.

`terraform apply` rolls the Argo CD application-controller. That is safe — it is a
reconciler holding no in-flight user state — but Argo CD briefly stops syncing, so
it should not share a window with other changes.

### Plex reachability (external dependency)

Plex runs inside WSL2, which sits behind a NAT'd virtual switch, so it is not
reachable from cluster pods by default. Preferred fix is **WSL2 mirrored networking**
(`networkingMode=mirrored` in `.wslconfig`; Windows 11 22H2+ / WSL 2.0+), which makes
WSL services reachable at the Windows host's LAN IP with no port forwarding and no
drift across reboots.

Fallback for Windows 10 is `netsh interface portproxy` plus a firewall rule, but the
WSL IP changes on every boot, so it needs a scheduled task to re-apply or the widget
silently dies.

Either way the Windows box needs a **DHCP reservation** — the Plex URL is a static IP
in `configmap.yaml`.

Blast radius is small and worth stating: if Plex is unreachable the tile still
renders and the link still works; only the stats go blank. The link works from the
browser regardless, since only the widget requires pod-to-Plex reachability.

## Exposure

1. **Route 53 A record** for `home.arigsela.com` — manual; there is no external-dns
   in this repo.
2. `certificate.yaml` → `homepage-tls` via `letsencrypt-route53`.
3. `reference-grant.yaml` → Gateway may read the secret.
4. `https-homepage` listener in `gateway.yaml`.
5. Allow rule in `authorizationpolicy.yaml` for `home.arigsela.com` and
   `home.arigsela.com:*`, copying the source IP list used by the other restricted
   hosts.
6. `httproute.yaml` with `sectionName: https-homepage`.

Steps 4 and 5 must land in the same commit. The AuthorizationPolicy is
deny-by-default, so a listener without a matching rule fails closed — a 403 that
presents as a routing bug. The listener will sit unprogrammed briefly while ACME
issues; this self-heals and is expected. One new certificate is comfortable against
the 50/week per-domain limit.

## Failure modes (for `runbook.md`)

| Symptom | Check | Fix |
|---|---|---|
| Blank page / request rejected | `HOMEPAGE_ALLOWED_HOSTS` in the pod env | Add `home.arigsela.com`. Most likely first-boot failure. |
| Edited `configmap.yaml`, Argo synced, nothing changed | Pod age vs commit time | Bump `checksum/config`. subPath mounts never receive ConfigMap updates. |
| A new app's tile is missing | Annotations on its `httproute.yaml` | Add/fix `gethomepage.dev/enabled: "true"`. Annotation changes are live — no restart needed, unlike `configmap.yaml`. |
| Plex stats blank, link works | Curl `:32400` from a pod | WSL2 forwarding down, or the Windows DHCP lease moved. |
| Argo CD tile blank | Query `argocd_app_info` in Prometheus | Metrics not enabled, or SD has not re-scraped. |
| Grafana tile 401 | Viewer user in Grafana | Recreate user, update Vault, restart pod. |
| 403 on every host | Source IP vs allow-list | ISP address moved off the allow-listed range. Pre-existing for all apps; the dashboard is just where it shows first. |

## Validation

CI (`.github/workflows/validate.yaml`) covers the mechanical layer: yamllint 1.35.1,
kubeconform, `pytest tests/agent-docs/`, `validate-agent-docs.py`,
`pytest tests/catalog-refs/`, `validate-catalog-refs.py`, and
`gen-techdocs.py --check`. Existing apps already use Gateway API and ESO CRDs, so
kubeconform schema resolution is a solved problem here.

CI cannot verify the things most likely to be wrong. These are explicit manual steps
in the implementation plan:

1. Discovery returns the expected 14 tiles.
2. Each of the 4 widgets renders real numbers, not placeholders or errors.
3. The allow-list admits the operator and rejects an off-list source.
4. `argocd_app_info` returns data in Prometheus **before** the widget is configured.

## Rollout order

Sequenced so each step is verifiable before anything depends on it.

| Step | Work | Verifiable by |
|---|---|---|
| A | Vault role, policy, secrets | `vault kv get k8s-secrets/homepage` |
| B | Terraform: Argo CD controller metrics | `argocd_app_info` returns data in Prometheus |
| C | Route 53 A record | `dig home.arigsela.com` |
| D | `base-apps/homepage/` + gateway listener + authz rule | Dashboard loads over TLS |
| E | Annotate the 14 HTTPRoutes | Tiles appear without a restart |
| F | WSL2 networking + DHCP reservation | `curl <win-ip>:32400` from a pod |
| G | Docs contract, scope file, generators | CI green |

B and F are independent of the rest and may run in parallel. **E is where the value
appears** — before it the dashboard is nearly empty, which is expected, not broken.

## Risks

| Risk | Mitigation |
|---|---|
| Cluster-wide read on pods/namespaces | Narrowed vs upstream; read-only; documented as the one new privilege. Kyverno policies should be checked for anything that would reject the ClusterRoleBinding. |
| Homepage has no auth | Restricted allow-list; no admin-grade credentials in its env (hence the Grafana Viewer user). |
| Plex fix lives outside Git | Documented in `runbook.md` with the exact `.wslconfig` change; failure is graceful and non-blocking. |
| The two-source tile rule is subtle | Stated verbatim in `docs.md`; the failure mode of getting it wrong (a token in an annotation) is called out explicitly. |
| 14 files annotated in one PR | Each is additive and independent; Argo syncs them per-app, so a bad annotation degrades one tile rather than the dashboard. |
