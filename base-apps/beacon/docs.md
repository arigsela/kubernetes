---
type: "Kubernetes App Guide"
title: "Beacon"
description: "Multi-tenant uptime monitor and public status page (app repo arigsela/beacon, private): the platform's customer zero"
app: beacon
catalog_entity: beacon-db
kind: docs
namespace: beacon
last_reviewed: 2026-10-18
status: draft
tags: [uptime, multi-tenant, ambient, fastapi]
sources:
  - base-apps/beacon.yaml
  - base-apps/beacon/namespace.yaml
  - base-apps/beacon/deployments.yaml
  - base-apps/beacon/cronjobs.yaml
  - base-apps/beacon/jobs.yaml
  - base-apps/beacon/services.yaml
  - base-apps/beacon/configmaps.yaml
  - base-apps/beacon/waypoint.yaml
  - base-apps/beacon/waypoint-telemetry.yaml
  - base-apps/beacon/authorization-policy.yaml
  - base-apps/beacon/network-policies.yaml
  - base-apps/tracing/collector.yaml
  - base-apps/beacon/httproute.yaml
  - base-apps/beacon/certificate.yaml
  - base-apps/beacon/secret-stores.yaml
  - base-apps/beacon/external-secrets.yaml
  - base-apps/beacon/serviceaccounts.yaml
  - base-apps/postgresql/beacon-database.yaml
  - base-apps/postgresql/init-beacon-roles.yaml
  - base-apps/postgresql/external-secrets-beacon-db.yaml
  - base-apps/postgresql/cnpg-cluster.yaml
  - base-apps/beacon-aws-infrastructure/ecr-push.yaml
  - base-apps/dex/configmap.yaml
  - scripts/provision-beacon-vault.sh
  - tests/beacon/test_manifests.py
  - tests/beacon/test_homelab_seed.py
---

# Beacon

## What it is
A multi-tenant uptime monitor with a public status page per tenant, built as the platform's
"customer zero": it exists to prove what the platform claims (a service with no tracing code
traced by policy, a tenant provisioned by one PR, an injected fault diagnosed by an agent).
The code, the images and CI live in the private repo `arigsela/beacon`, whose `SPEC.md` is
the source of truth; every comment here cites it as `beacon §T.n` / `beacon §V.n`, never a
bare `§T.n` (that would be this repo's own SPEC.md).

| Workload | Kind | What it does |
|---|---|---|
| `beacon-web` | Deployment | The SPA (nginx, static). Logs in against Dex as public client `beacon` (PKCE); logout is local. |
| `beacon-api` | Deployment | REST API (`/api/v1/*`) and the public status JSON (`/public/v1/status/<slug>`). Verifies Dex tokens; tenant membership lives in its own database. |
| `beacon-scheduler` | CronJob, every minute | Enqueues due checks into the `jobs` table, each row carrying the run's `traceparent`. |
| `beacon-scheduler-daily` | CronJob, 03:17 | Retention (30 days) and per-monitor daily roll-ups. |
| `beacon-checker` | Deployment | Claims jobs (`FOR UPDATE SKIP LOCKED`), probes HTTP/TCP targets through the SSRF guard, opens and closes incidents, hands each one to the notifier. |
| `beacon-notifier` | Deployment | `POST /internal/v1/notify` from the checker only; stores one delivery per channel, then delivers webhooks (HMAC-signed), Slack and email with retries and a dead-letter after 8 attempts. **No OpenTelemetry code at all**, by design (beacon §V.9). |
| `beacon-smoke` | PreSync Job | Runs every image on a node before each sync (beacon §V.18). |
| `beacon-migrate` | Sync hook Job | Alembic migrations as `beacon_owner`, before the Deployments. |
| `beacon-seed-homelab` | PostSync Job | Applies the `homelab` tenant (`beacon-homelab-seed`): beacon monitors every host on the shared Gateway (beacon §T.17). Adding a Gateway host means adding its monitor there; `tests/beacon/test_homelab_seed.py` enforces it. |

`beacon-mcp` is built and smoke-run but not deployed until beacon §T.30.

## Traffic
- **North-south:** `beacon.arigsela.com` (HTTPRoute `beacon`): `/api/*` and `/public/*` go to
  `beacon-api`, everything else to `beacon-web`. Straight to the Services: the namespace's
  waypoint is bypassed, and must stay bypassed. Never put `istio.io/ingress-use-waypoint` on
  the namespace or a Service (istio#60780 drops spans, beacon §V.8). The host is IP
  allow-listed (`base-apps/istio-ingress/authorizationpolicy.yaml`) until beacon's WAF work.
- **East-west:** the namespace is ambient with waypoint `beacon-waypoint`. The only east-west
  call is checker → notifier, which the waypoint admits for the checker's principal only
  (`authorization-policy.yaml`). NetworkPolicies are the L3 belt: default-deny ingress, and
  every allow lists HBONE 15008 next to the app port (beacon §V.31).
- **Egress:** the checker and notifier connect to tenant-supplied addresses, so their egress
  refuses private, CGNAT, loopback, link-local and cluster ranges at L3, under the
  application's SSRF guard. The home WAN address is refused by the guard only (it rotates;
  the guard resolves it live from `SSRF_SELF_HOSTS`). The one in-cluster exception is the
  trace collector (`otel-collector` in `tracing`, TCP 4318 only, beacon §V.41).

## Tracing
api, checker and both scheduler CronJobs export over OTLP/HTTP to
`otel-collector.tracing.svc.cluster.local:4318` with an explicit sampler (100% of their own
root traces; the collector samples, beacon §V.43). The collector scrubs URLs before anything
is stored (beacon §V.42). See `base-apps/tracing/docs.md`. The ingress Gateway traces every
request (`base-apps/istio-ingress/telemetry.yaml`), and `waypoint-telemetry.yaml` traces the
waypoint, so checker -> notifier shows a waypoint span between the two (beacon §V.6). Every log line carries
`trace_id=<32 hex>`, which Grafana links to the trace (beacon §T.43). The notifier carries no
OTel variable: it is traced only through the platform (beacon §V.9, beacon §T.22).

## Data
- Database `beacon` on the shared CNPG `postgresql-cluster` (`base-apps/postgresql/`). Row-
  level security on every tenant table, enforced even for the table owner; the only
  cross-tenant paths are named `SECURITY DEFINER` functions owned by a role nobody can log
  in as (beacon §V.1, beacon §V.24, beacon §V.34).
- Roles come from `init-beacon-roles.yaml` in the postgresql app, which runs this release's
  `beacon-api bootstrap-roles` as the CNPG superuser (`enableSuperuserAccess`, added for
  this). `beacon_app` (runtime, CONNECTION LIMIT 20, `statement_timeout` 5s) and
  `beacon_owner` (migrations, LIMIT 3): 23 of the cluster's 100 connections.
- Secrets (Vault, `scripts/provision-beacon-vault.sh`): `k8s-secrets/beacon` holds the
  AES-GCM key that seals webhook secrets in the database; `k8s-secrets/beacon-db` holds both
  role passwords. Nothing secret is in git.
- Fault flags: ConfigMap `beacon-flags` (flagd JSON). Every flag defaults to off; a fault is
  a commit, its removal a `git revert` (beacon §V.13).

## Images
Six IMMUTABLE ECR repos (`beacon-api`, `-web`, `-scheduler`, `-checker`, `-notifier`, `-mcp`,
us-east-2), pushed only by `arigsela/beacon`'s release workflow from a `v*` tag on `main` via
the GitHub OIDC role in `base-apps/beacon-aws-infrastructure/`. Pinned here by digest, all six
from one release, in `base-apps/beacon/` and in `base-apps/postgresql/init-beacon-roles.yaml`.
Promotion is a PR: [runbook](runbook.md), "Promote a release". `linux/amd64`, glibc,
x86-64-v2 only: the nodes are Xeon E5-2670 (AVX, no AVX2).

## Known limits
- A notification is lost if the notifier is unreachable for longer than the checker's three
  quick retries (about 3.5s); the incident itself is recorded.
- An incident opens on the first failed check (no flap damping).
- No SMTP relay is configured, so email deliveries dead-letter.
- Single replicas everywhere, and one waypoint.
