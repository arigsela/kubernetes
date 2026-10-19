---
type: "Kubernetes App Runbook"
title: "Beacon — Runbook"
description: "Operational runbook for beacon: release promotion, first deploy, smoke and migration failures, login, notifications, secrets."
app: beacon
catalog_entity: beacon-db
kind: runbook
namespace: beacon
last_reviewed: 2026-10-19
status: draft
tags: [uptime, runbook]
sources:
  - base-apps/beacon/jobs.yaml
  - base-apps/beacon/instrumentation.yaml
  - base-apps/beacon/deployments.yaml
  - base-apps/beacon/cronjobs.yaml
  - base-apps/beacon/authorization-policy.yaml
  - base-apps/beacon/network-policies.yaml
  - base-apps/postgresql/init-beacon-roles.yaml
  - scripts/provision-beacon-vault.sh
  - scripts/provision-beacon-tenants-vault.sh
---

# Beacon — Runbook

Everything here is a Git change. Never `kubectl apply/edit/patch/delete` (Argo CD reverts or
prunes it); the read-only commands below only inspect.

## How-to

### Promote a release (beacon §T.41)
Renovate does not watch beacon's ECR repos, so a new release reaches the cluster only through
this PR.

1. In `arigsela/beacon`, a `v*` tag on `main` runs the release workflow. Its step summary
   lists six lines `852893458518.dkr.ecr.us-east-2.amazonaws.com/beacon-<svc>:<tag>@sha256:<digest>`.
   A re-run of an already-pushed tag skips the push and prints the same digests.
2. On a branch here, replace **every** beacon image reference with the new line for its
   service. They are in `base-apps/beacon/deployments.yaml`, `cronjobs.yaml`, `jobs.yaml`
   (the smoke Job lists all six) and `base-apps/postgresql/init-beacon-roles.yaml`.
   `tests/beacon/test_manifests.py` fails if any reference is unpinned, from another
   release, or missing from the smoke Job.
   Also the four in `IMAGES` in `base-apps/crossplane-compositions/composition-tenant.yaml`
   (api, scheduler, checker, notifier): every enterprise tenant runs the same release
   (beacon §V.53, `tests/beacon/test_tenants.py`). Then re-render the tenants' goldens,
   `UPDATE_GOLDENS=1 python -m pytest tests/composition/` (crossplane CLI, see
   `tests/composition/README.md`): the diff is the new images and the new init Job name.
3. Run `python -m pytest tests/beacon/ -q`, open the PR with a title citing `beacon §T.41`
   and the tag, and merge it after review. Each tenant then runs its own `beacon-init-<digest>`
   Job (roles, migrations) and its workers move to the release once that Job succeeded.
4. Argo CD runs `beacon-smoke` first. If it fails, nothing rolls (next section).
   Then `beacon-migrate`, then the Deployments and CronJobs.

Rollback is `git revert` of that PR. ECR keeps the last 10 tagged beacon images, so a
revert's digests are still pullable.

A failed sync keeps retrying **its own revision** (five retries, about 25 minutes) before
Argo CD takes a newer commit, so a revert or fix merged meanwhile waits for it (seen during
beacon §B.10). To skip the wait, terminate the running operation in the Argo CD UI; auto-sync
then picks up the new commit.

### First deploy (once)
1. `scripts/provision-beacon-vault.sh`, BEFORE this app's first sync (a SecretStore with no
   Vault role fails the sync; the postgresql hook skips without the passwords).
2. Merge the deploy PR. Argo CD syncs `postgresql` (superuser access, roles, database) and
   `beacon` (smoke, migrate, workloads) independently; `beacon` retries until the roles and
   the database exist.
3. The listener, allow-list and DNS come after (beacon §T.16).

### Provision the enterprise-tenant Vault roles (once, beacon §T.42)
`scripts/provision-beacon-tenants-vault.sh`, once, BEFORE the beacon §T.45 PR merges and before
the first `XTenant`: both add SecretStores whose roles it writes, and a SecretStore with no
Vault role fails its app's sync. It writes two policies (one templated per tenant namespace, one
read-only for beacon-api) and two roles, and no secret values:

- `beacon-tenant` (SA `eso-tenant`, namespaces `tenant-*`): each tenant namespace reads and
  writes only `k8s-secrets/tenants/<its namespace>/*`. ESO generates and pushes its secrets there.
- `beacon-tenants` (SA `eso-beacon-tenants` in `beacon`): beacon-api lists `k8s-secrets/tenants/`
  and reads only `tenants/<ns>/api` (DSN + signing key), never a tenant's DB passwords.

Re-running it changes nothing. There is no per-tenant Vault step (beacon §V.52).

### Inject or clear a fault
Change one `defaultVariant` in `configmaps.yaml` (`beacon-flags`) in a PR; clear it with
`git revert`. Pods read the file live (directory mount), no restart.

### Rotate the database passwords
`scripts/provision-beacon-vault.sh --rotate-db`, then re-run `init-beacon-roles` with a PR
that bumps `beacon.arigsela.com/rerun-init-beacon-roles` on
`base-apps/postgresql/external-secrets-beacon-db.yaml` (hooks run only inside a sync, and
only a desired-state change starts one: a hook's own annotations or a `.md` edit do not,
beacon §B.8), then restart the beacon pods once ESO has refreshed (≤ 1h): until then they
hold the old password. The signing key is never rotated this way (see "Webhook secrets unreadable").

## Failure modes

### Symptom: the beacon sync is stuck or failed at PreSync (`beacon-smoke`)
- **Check:** `kubectl -n beacon get pods -l app=beacon-smoke`, then
  `kubectl -n beacon logs job/beacon-smoke --all-containers --prefix`.
- **ImagePullBackOff on the first sync:** the namespace has no `ecr-registry` secret yet.
  `kube-system/ecr-credentials-sync` adds it within 15 minutes; the pod then pulls.
- **A container exited 132 (SIGILL):** that image carries code built for AVX2
  (x86-64-v3), which these Xeon E5-2670 nodes lack. Do not promote it; fix the build in
  `arigsela/beacon` (its CI's Sandy Bridge emulation should have caught it: file it there
  as a bug).
- **`notifier-no-otel` failed:** an OpenTelemetry distribution reached the notifier image
  (beacon §V.9). Same: do not promote.

### Symptom: the beacon sync stops before `beacon-migrate`, nothing rolls out
- **Check:** `kubectl -n argo-cd get application beacon -o jsonpath='{.status.operationState.syncResult.resources}'`
  for a resource whose `hookPhase` is `Failed`.
- A Degraded object in an earlier wave stops the whole sync. Objects whose health another
  app decides (HTTPRoute, Certificate, ReferenceGrant) are in the last wave for that reason
  (beacon §B.9); keep anything like them there.

### Symptom: `beacon-migrate` fails, Deployments never appear
- **Check:** `kubectl -n beacon logs job/beacon-migrate`.
- `role "beacon_owner" does not exist` / `database "beacon" does not exist`: the postgresql
  app has not run `init-beacon-roles` yet, or it skipped (bump
  `beacon.arigsela.com/rerun-init-beacon-roles` to re-run it). Check
  `kubectl -n postgresql get database beacon -o jsonpath='{.status}'` and the postgresql
  app's last sync. The beacon app retries five times over about 25 minutes.
- `password authentication failed`: Vault and Postgres disagree. A postgresql sync re-runs
  `init-beacon-roles`, which re-applies the Vault passwords.

### Symptom: the postgresql app is Degraded and its sync "failed"
Usually not beacon: the `agent-audit-risk` CronJob exits 1 on purpose when its daily run finds
a high-risk agent session, which marks the app Degraded until a clean run, and Argo stops the
sync before wave 1. `init-beacon-roles` runs in wave 0 for that reason (beacon §B.7), so
beacon's roles are still created. Check:
`kubectl -n postgresql get databases.postgresql.cnpg.io beacon -o jsonpath='{.status}'`
(`applied: true` once the roles exist).

### Symptom: `init-beacon-roles` logs "skipping"
`beacon-db-credentials` has no passwords: run `scripts/provision-beacon-vault.sh`, then bump
`beacon.arigsela.com/rerun-init-beacon-roles` (see "Rotate the database passwords").

### Symptom: the notifier is not traced (no `beacon-notifier` spans)
- **Check:** `kubectl -n beacon get pod -l app=beacon-notifier -o jsonpath='{.items[0].spec.initContainers[*].name}'`
  must list `opentelemetry-auto-instrumentation-python`.
- **Missing:** the operator's webhook failed open when the pod was created (operator down or
  not ready). Check `kubectl -n opentelemetry-operator-system get pods`; once it is Ready, the
  next rollout of the notifier (any change to its Deployment, e.g. a promotion) instruments it.
- **Present, still no spans:** the notifier's egress to the collector (`probe-egress`, port
  4318), then the collector (`base-apps/tracing/runbook.md`).
- `beacon-smoke`'s `notifier-otel-layer` container failing means the operator's Python layer
  does not load under the notifier's Python: do not roll that layer out (pin the previous one).

### Symptom: login loops or fails at Dex
- `invalid client` at Dex: the `beacon` client is missing from Dex's running config. Dex
  only reloads when `checksum/config` changes (`base-apps/dex/runbook.md`).
- The browser console shows a CORS error on `dex.arigsela.com/token`: `web.allowedOrigins`
  in `base-apps/dex/configmap.yaml` must list `https://beacon.arigsela.com`.
- API answers 401 with a fresh login: `DEX_ISSUER`/`DEX_CLIENT_ID` on `beacon-api` must be
  `https://dex.arigsela.com` / `beacon`; the API fetches Dex's keys over the public host.

### Symptom: incidents open but nobody is notified
- **Check:** checker logs for the hand-off (`kubectl -n beacon logs deploy/beacon-checker`).
  `403` from the notifier is the waypoint's AuthorizationPolicy: the caller is not
  `beacon-checker`'s ServiceAccount. Connection refused or timeouts: the waypoint is down
  (`kubectl -n beacon get pods -l gateway.networking.k8s.io/gateway-name=beacon-waypoint`).
- After three quick retries the checker gives up on that notification; the incident stays
  recorded (a known limit). Deliveries already stored retry from the database.
- Email: there is no SMTP relay, so email deliveries dead-letter by design until
  `smtp-url` is provisioned.

### Symptom: webhook secrets unreadable after a Vault restore
Webhook channel secrets are AES-GCM-sealed with `k8s-secrets/beacon` `signing-key`. If Vault
comes back without it (a fresh provision generates a new key), every stored webhook secret
is unreadable and those deliveries fail. Restore Vault from its backup
(`base-apps/vault/runbook.md`); otherwise tenants must recreate their webhook channels.

### Symptom: "too many connections" on the shared cluster
beacon's roles are capped at 23 of 100 (`CONNECTION LIMIT`); beacon cannot be the cause of
the cluster-wide limit. If beacon itself is refused, check pool sizes (`BEACON_DB_POOL_MAX`)
times replicas against `beacon_app`'s limit of 20.
