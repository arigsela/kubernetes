---
type: "Kubernetes App Runbook"
title: "donetick — Runbook"
description: "Operational runbook for donetick: failure modes, checks, and fixes."
app: donetick
catalog_entity: donetick
kind: runbook
namespace: donetick
last_reviewed: 2026-08-03
status: current
tags: [go, postgres, chores, self-hosted]
sources:
  - base-apps/donetick/deployments.yaml
  - base-apps/donetick/configmap.yaml
  - base-apps/donetick/db-external-secret.yaml
  - base-apps/postgresql/donetick-database.yaml
  - base-apps/postgresql/external-secrets-donetick.yaml
---

# donetick — Runbook

## First-time provisioning

The manifests do not create Vault material. Before the first sync can succeed:

1. Run `scripts/provision-donetick-vault.sh` inside the `vault-0` pod. It writes
   both Vault keys, both policies, and both Kubernetes-auth roles, and is
   idempotent — a re-run preserves existing secrets rather than rotating them:

   ```bash
   kubectl -n vault cp scripts/provision-donetick-vault.sh vault-0:/tmp/prov.sh
   kubectl -n vault exec -it vault-0 -- sh
   export VAULT_TOKEN=<root-or-admin-token>
   sh /tmp/prov.sh
   unset VAULT_TOKEN; rm /tmp/prov.sh; exit
   ```

2. Add `donetick.arigsela.com` to Route 53 pointing at the ingress address, the
   same record shape as the other hosts.

Order matters only in that the Certificate cannot issue until DNS resolves, and
the Deployment will crash-loop until both ExternalSecrets have synced. Both
converge on their own once the prerequisites exist.

## Failure modes

### Symptom: pod crash-loops immediately, log ends at a JWT panic

```
panic: JWT secret must be at least 32 characters
```

- **Check:** `kubectl -n donetick get secret donetick-secrets -o jsonpath='{.data.jwt-secret}' | base64 -d | wc -c`
- **Fix:** the Vault value is short or empty. Rewrite `k8s-secrets/donetick`
  with 32+ characters and wait for the refresh (1h) or force it with
  `kubectl -n donetick annotate externalsecret donetick-secrets force-sync=$(date +%s) --overwrite`.

### Symptom: pod starts, then logs repeated `failed to open database`

donetick retries the connection 30 times at 500ms before giving up, so this
window is about 15 seconds of warnings before the process exits.

- **Check:** does the role exist and does the password match?
  ```bash
  kubectl -n postgresql get externalsecret donetick-db-credentials
  kubectl -n donetick   get externalsecret donetick-db-credentials
  kubectl -n postgresql get database donetick -o yaml | yq '.status'
  ```
- **Fix:** the two ExternalSecrets read the same Vault property; if one is
  `SecretSyncedError` they have diverged. Resync both. If
  `Secret/donetick-db-credentials` in `postgresql` is not type
  `kubernetes.io/basic-auth`, CNPG ignored it and created the role with no
  password — the operator logs nothing useful here, so check the type directly:
  `kubectl -n postgresql get secret donetick-db-credentials -o jsonpath='{.type}'`

### Symptom: `Database/donetick` stuck, database never created

- **Check:** `kubectl -n postgresql get database donetick -o jsonpath='{.status}'`
- **Fix:** almost always the `donetick` role does not exist yet, because its
  ExternalSecret has not synced and CNPG will not create a role without its
  password. Fix the secret; the operator retries and converges. Do not create the
  database by hand — an undeclared database is exactly the state `chores_tracker`
  is in, and it does not survive a cluster rebuild.

### Symptom: HTTPS fails / browser reports no certificate

- **Check:** `kubectl -n donetick get certificate donetick-tls` then the
  CertificateRequest and Order beneath it.
- **Fix:** confirm the issuer is `letsencrypt-route53`. If anything ever points
  this at `letsencrypt-prod`, it will never complete — that ClusterIssuer solves
  HTTP-01 through `ingress.class: nginx`, and there is no nginx ingress
  controller in this cluster since the Istio cutover. Do not delete and recreate
  the Certificate in a loop while debugging; Let's Encrypt allows 50 issuances
  per registered domain per week and arigsela.com has approached that ceiling
  before.

### Symptom: reachable from home, times out from a phone

Working as configured, not a fault. The AuthorizationPolicy allow-lists four
source IPs and carrier NAT is not among them. See `docs.md`.

### Symptom: config change had no effect

- **Check:** `kubectl -n donetick get pod -o jsonpath='{.items[0].metadata.annotations.checksum/config}'`
- **Fix:** donetick reads its config once at startup. Bump `checksum/config` in
  `deployments.yaml` (`shasum -a 256 base-apps/donetick/configmap.yaml | cut -c1-16`)
  or `kubectl -n donetick rollout restart deploy/donetick`.

## How-to

### Deploy / update

GitOps only. Bump `image:` in `base-apps/donetick/deployments.yaml` to a pinned
tag — never `latest` — and merge to `main`. The new pod runs GORM `AutoMigrate`
on boot; `strategy: Recreate` guarantees the old pod is gone before the migrating
pod starts, so two schema versions never serve at once.

Before a version bump, confirm the release notes do not require a manual
migration step, and take a backup (below) — CNPG's scheduled backup runs at
02:00 UTC, which may be many hours stale.

### Back up / restore

Backups are the CNPG cluster's, not this app's. `base-apps/postgresql/cnpg-scheduled-backup.yaml`
covers every database in `postgresql-cluster` including this one. For an
on-demand backup before a risky change, create a `Backup` resource against
`postgresql-cluster`; see the postgresql runbook.

### Rotate the DB password

```bash
vault kv put k8s-secrets/donetick-db db-password="$(openssl rand -base64 32 | tr -d '\n' | cut -c1-32)"
```

Then force-sync both ExternalSecrets (`donetick` and `postgresql` namespaces).
CNPG applies `ALTER ROLE` from the postgresql-side secret; the app picks up the
new value on its next pod restart. There is a window where the role has the new
password and the running pod still holds the old one — restart the Deployment
after the ExternalSecret shows `SecretSynced`.

### Rotate the JWT secret

Rewrite `k8s-secrets/donetick` `jwt-secret`, force-sync, restart the Deployment.
Every session and refresh token is invalidated; everyone is logged out. Do it
deliberately, not as a reflex.

### Close signup after the first account

Set `is_user_creation_disabled: true` in `configmap.yaml`, bump `checksum/config`
in `deployments.yaml`, merge. Verify at least one account exists first — there is
no bootstrap admin and no CLI to create one.

### Recover a password with no email configured

There is no SMTP relay. Trigger the reset in the UI, then read the URL from the
pod log (`log_raw_url: true` in `configmap.yaml`):

```bash
kubectl -n donetick logs deploy/donetick | grep -i reset
```
