---
type: "Kubernetes App Runbook"
title: "homepage runbook"
description: "Operational runbook for homepage: failure modes, checks, and fixes."
app: homepage
catalog_entity: homepage
kind: runbook
namespace: homepage
last_reviewed: 2026-09-30
status: stable
tags: [dashboard, gitops, self-hosted]
sources:
  - base-apps/homepage/configmap.yaml
  - base-apps/homepage/deployments.yaml
  - base-apps/homepage/external-secrets.yaml
  - base-apps/homepage/secret-store.yaml
  - base-apps/homepage/httproute.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - terraform/roots/asela-cluster/argocd.tf
  - scripts/provision-homepage-vault.sh
  - scripts/provision-homepage-media-vault.sh
---

# homepage runbook

## First-time provisioning

The manifests do not create Vault material. Before the first sync can
succeed, run `scripts/provision-homepage-vault.sh` inside the `vault-0` pod —
idempotent, writes `plex-token` and `grafana-token` under `k8s-secrets/homepage`
plus the matching policy and Kubernetes-auth role, and leaves existing values
untouched on a re-run:

```bash
kubectl -n vault cp scripts/provision-homepage-vault.sh vault-0:/tmp/prov.sh
kubectl -n vault exec -it vault-0 -- sh
export VAULT_TOKEN=<root-or-admin-token>
PLEX_TOKEN=... GRAFANA_TOKEN=glsa_... sh /tmp/prov.sh
unset VAULT_TOKEN; rm /tmp/prov.sh; exit
```

`GRAFANA_TOKEN` must be a Grafana **service-account token with the Viewer
role**, not an admin credential — see the "No authentication of its own"
section in `docs.md` for why. `PLEX_TOKEN` is Plex's own server access token
(`X-Plex-Token`), obtainable from an authenticated Plex Web session.

Then run `scripts/provision-homepage-media-vault.sh` for the Media widgets. It
`vault kv patch`es five more properties into the same secret
(`qbittorrent-username`, `qbittorrent-password`, `sonarr-key`, `radarr-key`,
`seerr-key`) and refuses to run if `k8s-secrets/homepage` does not exist yet.
Its header shows both ways to run it (port-forward + OIDC login, or inside
`vault-0`):

```bash
QBITTORRENT_USERNAME=... QBITTORRENT_PASSWORD=... \
SONARR_KEY=... RADARR_KEY=... SEERR_KEY=... \
  sh scripts/provision-homepage-media-vault.sh
```

**Both scripts are required before the pod can start.** Every
`HOMEPAGE_VAR_*` env var in `deployments.yaml` is a non-optional
`secretKeyRef`, so if any of the seven properties is missing the pod sits in
`CreateContainerConfigError` — not a blank widget.

## Failure modes

| Symptom | Check | Fix |
|---|---|---|
| Blank page, or requests rejected outright | `kubectl -n homepage exec deploy/homepage -- printenv HOMEPAGE_ALLOWED_HOSTS` | Must contain `home.arigsela.com` and `$(MY_POD_IP):3000`. Homepage rejects any request whose `Host` header isn't listed here; this is the most likely first-boot failure and reads as a generic blank/error page, not an auth error. |
| Edited `configmap.yaml`, Argo synced Healthy, nothing changed | Pod `AGE` (`kubectl -n homepage get pods`) versus the commit time | `configmap.yaml` is mounted via `subPath`, and Kubernetes never propagates ConfigMap updates into subPath mounts. Bump `checksum/config` in `deployments.yaml` (`shasum -a 256 base-apps/homepage/configmap.yaml \| cut -c1-16`) and re-sync, or `kubectl -n homepage rollout restart deploy/homepage`. |
| A new app's tile is missing from the dashboard | The six `gethomepage.dev/*` annotations on that app's own `httproute.yaml` | Typo in a key, or `enabled` missing/not `"true"`. Also confirm `gethomepage.dev/group` exactly matches a key under `layout:` in `configmap.yaml`'s `settings.yaml` — a mismatch doesn't error, it just renders into an unconfigured row that's easy to miss. No pod restart is needed either way; annotations are read live. |
| Two tiles show up for one app | Whether two `HTTPRoute`s in that app's namespace(s) share the same hostname | Annotate only the tile-facing route. This is exactly why `base-apps/weather-kitchen-backend/httproute.yaml` is deliberately **unannotated** — it shares `weather-kitchen.arigsela.com` with `weather-kitchen-frontend`, which carries the annotations. |
| Pod stuck in `CreateContainerConfigError` | `kubectl -n homepage describe pod -l app=homepage` (names the missing key) and `kubectl -n homepage get externalsecret homepage-secrets` | A property is missing from `k8s-secrets/homepage` — usually the media keys, if `scripts/provision-homepage-media-vault.sh` never ran. Run it (see First-time provisioning); the pod starts once the ExternalSecret re-syncs. |
| Plex (or any Media) tile stats blank, but the `href` link still works from a browser | `kubectl run plex-probe --rm -it --restart=Never --image=curlimages/curl:8.10.1 -- curl -m5 http://10.0.1.200:32401/identity` | If this times out from inside the cluster but Plex is reachable from your own LAN device, WSL2 mirrored networking is off (check `%UserProfile%\.wslconfig` on the Windows host after any Windows/WSL update) or the DHCP lease for the WSL2 host moved off `10.0.1.200` — every Media widget goes blank together. Neither is fixable from Git — see docs.md. If only one Media widget is blank, its key or login in Vault is stale (see "Rotate a credential"). |
| Argo CD tile blank, or all counters read `0` | `count(argocd_app_info)` against Prometheus (`kubectl -n logging port-forward svc/prometheus 9090:9090`, then query) | Zero results means `controller.metrics.enabled` in `terraform/roots/asela-cluster/argocd.tf` was never applied (Atlantis PR not merged/applied) or was reverted, **or** the `argocd-application-controller-metrics` Service lost its `prometheus.io/scrape` annotation and Prometheus stopped scraping it. This is a Terraform/Atlantis problem, not a homepage or Argo CD sync problem — `kubectl -n argo-cd get app homepage` can show Healthy while this tile is empty. |
| Grafana tile errors (not just blank) | `curl -H "Authorization: Bearer <token>" https://grafana.arigsela.com/api/alertmanager/grafana/api/v2/alerts` | `401`/`403` means the service-account token was revoked or the service account deleted in Grafana's UI — Grafana tokens don't auto-expire unless one was set. Mint a new Viewer-role token and follow "Rotate a credential" below. |
| A group appears on both tabs | Its entry under `layout:` in `settings.yaml` | It has no `tab:`. Homepage shows a group without one on every tab. Add `tab: Infra` or `tab: Home` (bookmark groups too), bump `checksum/config`. |
| `403` on every host, not just `home.arigsela.com` | Your current public source IP against the `arigsela.com/wan-ip` annotation in `base-apps/istio-ingress/authorizationpolicy.yaml` | Your ISP reassigned your address. This affects every restricted hostname behind the Gateway — homepage is just the one you happen to be looking at. `wan-ip-monitor` detects it (every 12 h), fixes Route 53 and opens a PR that **replaces** the old `/32` (never append — the old address goes back to the ISP pool); merging that PR is the fix. Nothing in this app. See the istio-ingress runbook. |

## Rotate a credential (any widget token or key)

1. Write the new value to Vault with `patch` (never `put` — it replaces the
   whole secret and would drop the other six properties):
   ```bash
   vault kv patch k8s-secrets/homepage plex-token="<new-token>"
   # or grafana-token, sonarr-key, radarr-key, seerr-key,
   #    qbittorrent-username / qbittorrent-password
   ```
   Re-running `scripts/provision-homepage-media-vault.sh` with new values also
   rotates the five media properties.
2. The `ExternalSecret` refreshes on its own within an hour (`refreshInterval:
   1h` in `external-secrets.yaml`), or force it immediately:
   ```bash
   kubectl -n homepage annotate externalsecret homepage-secrets force-sync=$(date +%s) --overwrite
   ```
3. **Restart the Deployment.** Homepage reads its `HOMEPAGE_VAR_*` env vars
   once at process start — updating the underlying Kubernetes `Secret` does
   not make the running container pick up the new value:
   ```bash
   kubectl -n homepage rollout restart deploy/homepage
   ```

Order matters: force-sync before restarting, or the new pod starts holding
the stale value and you repeat the restart for nothing.

## Deploy / update

GitOps only. Bump `image:` in `base-apps/homepage/deployments.yaml` to a
pinned tag — never `latest` — and merge to `main`. Because `strategy:
Recreate`, there is a brief full outage of the dashboard during the swap;
nothing else in the cluster depends on homepage being up, so this is
low-risk to do at any time.

Before a version bump, skim the upstream release notes for widget or config
schema changes — `services.yaml`/`widgets.yaml`/`settings.yaml` shape has
changed across major versions before, and a schema-incompatible ConfigMap
after a bump can render as a blank page with no obvious pod-log error.
