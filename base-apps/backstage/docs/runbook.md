---
app: backstage
catalog_entity: backstage
kind: runbook
namespace: backstage
last_reviewed: 2026-07-10
status: current
tags: [backstage, developer-portal, catalog, kubernetes-ingestor]
sources:
  - base-apps/backstage/deployments.yaml
  - base-apps/backstage/external-secrets.yaml
  - base-apps/backstage/secret-store.yaml
  - base-apps/backstage/rbac.yaml
---

# backstage — Runbook

## Failure modes
### Symptom: pod CrashLoopBackOff / fails readiness on startup
- **Check:** `kubectl -n backstage get pods` and `kubectl -n backstage logs deploy/backstage`; also `kubectl -n backstage get externalsecret,secret backstage-secrets` to confirm the `ExternalSecret` synced (the `Deployment` uses `envFrom.secretRef: backstage-secrets`, so a missing/stale Secret means Postgres, GitHub, AWS, Vault, and MCP env vars are all absent).
- **Fix:** if the `ExternalSecret` is not Ready, check the `vault-backend` `SecretStore` (`secret-store.yaml`) — role `backstage` against Vault at `vault.vault.svc.cluster.local:8200`, key `backstage`. If Vault itself is sealed/unreachable, see `base-apps/vault/runbook.md`. Once Vault resolves, ESO recreates the Secret (`refreshInterval: 1h`); the pod will still need a restart to pick up new env values (`kubectl -n backstage rollout restart deploy/backstage`).

### Symptom: Crossplane/XR resources tab shows nothing, or kubernetes-ingestor logs 403 Forbidden
- **Check:** `kubectl -n backstage logs deploy/backstage | grep -i forbidden` and confirm the RBAC bindings exist: `kubectl get clusterrolebinding backstage-crossplane-read backstage-read-only backstage-kagent-read -o wide` (all three bind the `backstage` ServiceAccount, `rbac.yaml`).
- **Fix:** if a binding or its `ClusterRole` is missing/out of date for a new resource type the ingestor now needs to walk (e.g. a new managed-resource API group), open a PR adding the `apiGroups`/`resources` to the relevant `ClusterRole` in `base-apps/backstage/rbac.yaml`.

### Symptom: catalog entity page 404s / new `base-apps/<app>/catalog-info.yaml` doesn't show up
- **Check:** confirm the entity's `catalog-info.yaml` is valid and its Argo CD Application excludes it from sync (`spec.source.directory.exclude` covering `catalog-info.yaml`, per `templates/agent-docs/README.md`) — the file must exist in the repo but not be applied as a Kubernetes manifest.
- **Fix:** the catalog provider config lives in the baked-in `app-config.yaml` (inside the `backstage-portal` image), not in this directory — if the provider itself needs reconfiguring, that requires a new image build/tag bump in `deployments.yaml`, not a manifest change here.

## How-to
### Deploy / update
Edit manifests in this directory (or bump the `image:` tag in `deployments.yaml`) and open a PR; Argo CD syncs on merge to `main`.

### Rotate a Vault secret (e.g. GitHub token, AWS keys, MCP token)
Update the value under Vault key `backstage` (property matching the field in `external-secrets.yaml`, e.g. `github-token`, `aws-access-key-id`, `mcp-token`); ESO re-syncs the `backstage-secrets` Secret within the 1h `refreshInterval`. Restart the Deployment to pick up the new env values immediately: `kubectl -n backstage rollout restart deploy/backstage`.
