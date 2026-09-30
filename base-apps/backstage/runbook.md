---
type: "Kubernetes App Runbook"
title: "Backstage — Runbook"
description: "Operational runbook for Backstage: failure modes, checks, and fixes."
app: backstage
catalog_entity: backstage
kind: runbook
namespace: backstage
last_reviewed: 2026-09-30
status: current
tags: [backstage, developer-portal, catalog, kubernetes-ingestor]
sources:
  - base-apps/backstage/deployments.yaml
  - base-apps/backstage/external-secrets.yaml
  - base-apps/backstage/secret-store.yaml
  - base-apps/backstage/rbac.yaml
  - base-apps/backstage/httproute.yaml
  - base-apps/backstage/certificate.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - templates/new-app/template.yaml
  - scripts/gen-techdocs.py
---

# backstage — Runbook

## Failure modes
### Symptom: pod CrashLoopBackOff / fails readiness on startup
- **Check:** `kubectl -n backstage get pods` and `kubectl -n backstage logs deploy/backstage`; also `kubectl -n backstage get externalsecret,secret backstage-secrets` to confirm the `ExternalSecret` synced (the `Deployment` uses `envFrom.secretRef: backstage-secrets`, so a missing/stale Secret means Postgres, GitHub, AWS, Vault, and MCP env vars are all absent).
- **Fix:** if the `ExternalSecret` is not Ready, check the `vault-backend` `SecretStore` (`secret-store.yaml`) — role `backstage`, as the namespace's `default` ServiceAccount, against Vault at `vault.vault.svc.cluster.local:8200`, key `backstage`. If Vault itself is sealed/unreachable, see `base-apps/vault/runbook.md`. Once Vault resolves, ESO recreates the Secret (`refreshInterval: 1h`); the pod will still need a restart to pick up new env values (`kubectl -n backstage rollout restart deploy/backstage`).

### Symptom: New Application wizard fails at "Provision Vault role + secrets" with a Vault 403
- **Check:** the scaffolder task log shows `403` with `permission denied` / `invalid token`. The `vault:setup` action authenticates with a **static, periodic** `VAULT_TOKEN` (Vault key `backstage`, property `vault-token`, delivered through `backstage-secrets`), and that token expires roughly yearly. The step runs before "Open PR" in `templates/new-app/template.yaml`, so no PR is opened either.
- **Fix:** mint a new token and roll it out. This needs a current Vault root/admin token over a port-forward (`kubectl -n vault port-forward svc/vault 8200:8200`); never paste token values into Git, tickets or chat.
  1. Make sure the policy exists (idempotent):
     ```sh
     vault policy write backstage-scaffolder - <<'EOF'
     path "sys/policies/acl/*"     { capabilities = ["create", "update"] }
     path "auth/kubernetes/role/*" { capabilities = ["create", "update"] }
     path "k8s-secrets/data/*"     { capabilities = ["create", "update"] }
     path "k8s-secrets/metadata/*" { capabilities = ["read"] }
     EOF
     ```
  2. Mint and store it in one go, using **patch** (not put) so the other `backstage` properties survive:
     `vault kv patch -mount=k8s-secrets backstage vault-token="$(vault token create -policy=backstage-scaffolder -period=8760h -orphan -field=token)"`
  3. Sync, **then** restart, in that order: `kubectl -n backstage annotate externalsecret backstage-secrets force-sync=$(date +%s) --overwrite`, wait for it to report synced, then `kubectl -n backstage rollout restart deploy/backstage`.
  4. Verify the live pod has the new token, comparing hashes rather than values: `kubectl -n backstage exec deploy/backstage -- printenv VAULT_TOKEN | shasum -a 256` against `kubectl -n backstage get secret backstage-secrets -o jsonpath='{.data.VAULT_TOKEN}' | base64 -d | shasum -a 256`. If they differ, the restart beat the ESO sync; restart again.
- **Durable fix (not done):** switch `vault:setup` (in the portal repo) to Kubernetes auth, as ESO does, so there is no static token to expire.

### Symptom: a new catalog location, template or plugin setting works locally but not in production
- **Check:** whether the entry was added only to `app-config.yaml` in the portal repo (`arigsela/backstage`). The backend loads `app-config.yaml` then `app-config.production.yaml`; objects merge but arrays are **replaced**, so the production file's `catalog.locations` (or `kubernetes.customResources`) silently wins.
- **Fix:** add the entry to both files, build a new portal image, and bump the tag in `deployments.yaml`.

### Symptom: Crossplane/XR resources tab shows nothing, or kubernetes-ingestor logs 403 Forbidden
- **Check:** `kubectl -n backstage logs deploy/backstage | grep -i forbidden` and confirm the RBAC bindings exist: `kubectl get clusterrolebinding backstage-crossplane-read backstage-read-only backstage-kagent-read -o wide` (all three bind the `backstage` ServiceAccount, `rbac.yaml`). The plugins use the static `K8S_SERVICE_ACCOUNT_TOKEN` from Vault, so the bindings only help if that token belongs to the `backstage` SA.
- **Fix:** if a binding or its `ClusterRole` is missing/out of date for a new resource type the ingestor now needs to walk (e.g. a new managed-resource API group), open a PR adding the `apiGroups`/`resources` to the relevant `ClusterRole` in `base-apps/backstage/rbac.yaml`. A kagent entity card saying "Could not load agent details" is the same failure for `backstage-kagent-read`.

### Symptom: catalog entity page 404s / new `base-apps/<app>/catalog-info.yaml` doesn't show up
- **Check:** confirm the entity's `catalog-info.yaml` is valid and its Argo CD Application excludes it from sync (`spec.source.directory.exclude: '{catalog-info.yaml,mkdocs.yml}'`, per `templates/agent-docs/README.md`) — the file must exist in the repo but not be applied as a Kubernetes manifest.
- **Fix:** the catalog provider config lives in the portal image's app-config, not in this directory — if the provider itself needs reconfiguring, that requires a new image build/tag bump in `deployments.yaml` (and see the array-replace symptom above).

### Symptom: an app's "Docs" tab is missing or shows stale content
- **Check:** TechDocs builds from the generated mirror (`base-apps/<app>/mkdocs.yml`, `docs/index.md`, `docs/runbook.md`), not from `docs.md`/`runbook.md`. Run `python3 scripts/gen-techdocs.py --repo-root . --check`; drift means someone edited the canonical files without regenerating. Also confirm the entity carries `backstage.io/techdocs-ref: dir:.`.
- **Fix:** run `python3 scripts/gen-techdocs.py --repo-root .` and commit the mirror with the change. Never edit the mirror by hand.

### Symptom: `backstage.arigsela.com` returns 403 or doesn't load
- **Check:** a 403 `RBAC: access denied` is the Gateway allow-list (`base-apps/istio-ingress/authorizationpolicy.yaml`), not Backstage. Otherwise check the route and cert: `kubectl -n backstage get httproute backstage -o yaml` (accepted on listener `https-backstage`?) and `kubectl -n backstage get certificate backstage-tls`.
- **Fix:** allow-list and WAN-IP problems are fixed in `istio-ingress` / `wan-ip-monitor` (see their runbooks). A missing `reference-grant.yaml` leaves the listener silently certless.

## How-to
### Deploy / update
Edit manifests in this directory (or bump the `image:` tag in `deployments.yaml` after a portal build) and open a PR; Argo CD syncs on merge to `main`.

### Rotate a Vault secret (e.g. GitHub token, AWS keys, MCP token)
Update the value under Vault key `backstage` (property matching the field in `external-secrets.yaml`, e.g. `github-token`, `aws-access-key-id`, `mcp-token`) with `vault kv patch`, so the other properties survive; ESO re-syncs the `backstage-secrets` Secret within the 1h `refreshInterval`. Restart the Deployment to pick up the new env values immediately (`kubectl -n backstage rollout restart deploy/backstage`), after the sync. Rotating `mcp-token` also means updating the copies kagent holds (`kagent-backstage-mcp` and `homelab-agent` keys, see `base-apps/kagent/runbook.md`).
