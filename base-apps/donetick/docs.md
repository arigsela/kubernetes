---
type: "Kubernetes App Guide"
title: "donetick"
description: "Self-hosted household chore and task tracker (Go + React), backed by the CNPG Postgres cluster."
app: donetick
catalog_entity: donetick
kind: docs
namespace: donetick
last_reviewed: 2026-08-03
status: current
tags: [go, postgres, chores, self-hosted]
sources:
  - base-apps/donetick/deployments.yaml
  - base-apps/donetick/configmap.yaml
  - base-apps/donetick/external-secrets.yaml
  - base-apps/donetick/db-external-secret.yaml
  - base-apps/donetick/certificate.yaml
  - base-apps/donetick/httproute.yaml
  - base-apps/postgresql/donetick-database.yaml
  - base-apps/postgresql/external-secrets-donetick.yaml
---

# donetick

## What it is

[donetick](https://github.com/donetick/donetick) is a self-hosted chore and task
tracker for households: recurring chores, assignment across a "circle" of users,
natural-language due dates, and a mobile-friendly web UI. AGPLv3, upstream
publishes multi-arch images (`amd64`, `arm64`, `arm/v7`).

It is deployed here as the intended successor to the homegrown
[chores-tracker](https://github.com/arigsela/chores-tracker) (FastAPI + MySQL).
Both are in the `chores-tracker` Backstage system. chores-tracker's database and
`CHORES_TRACKER_INTEGRATION.md` are untouched by this app and are pending
retirement — see the Gotchas section.

## Architecture & data flow

A single stateless `Deployment`, one replica, one container. The binary serves
both the JSON API and the compiled React frontend on port 2021; there is no
separate frontend workload and no sidecar.

Request path: `chores.arigsela.com` → the shared `main` Gateway in
`istio-ingress` (listener `https-chores`) → `HTTPRoute` → `Service/donetick`
→ pod. The hostname is the one chores-tracker already owned; donetick took it
over on 2026-08-03, which is why the TLS Secret is named `donetick-tls` while the
listener and certificate are named for `chores`. TLS terminates at the Gateway using `donetick-tls`, a Secret in this
namespace reached across namespaces by `reference-grant.yaml`.

All state is in Postgres — the `donetick` database inside the CloudNativePG
cluster `postgresql-cluster`, not the plain `postgresql` Deployment. That choice
is the reason there is no PVC here: the CNPG cluster is the only Postgres in this
cluster with backups (daily 02:00 UTC to S3, 30d retention). The pod itself holds
nothing that survives a restart.

Schema is applied by GORM `AutoMigrate` at startup (`database.migration: true`),
so a version bump migrates on the first boot of the new image. This is why the
Deployment uses `strategy: Recreate` — see Gotchas.

## Where config lives

| What | Where |
|---|---|
| Non-secret config (`selfhosted.yaml`) | `configmap.yaml`, mounted over the image's `/config` |
| JWT signing key | Vault `k8s-secrets/donetick` → `jwt-secret` → `external-secrets.yaml` |
| DB password | Vault `k8s-secrets/donetick-db` → `db-password` |
| DB role + database | `base-apps/postgresql/cnpg-cluster.yaml` (`spec.managed.roles`) and `base-apps/postgresql/donetick-database.yaml` |
| TLS certificate | `certificate.yaml` (ClusterIssuer `letsencrypt-route53`) |
| Who can reach it | `base-apps/istio-ingress/authorizationpolicy.yaml` |

The DB password is read from **one** Vault key by **two** namespaces, through the
`donetick-db` Vault role bound to the `eso-donetick-db` ServiceAccount in both
`donetick` and `postgresql`. The app needs it to authenticate; CloudNativePG
needs it to provision the role with that same password. Rotating means editing
Vault once — never either Kubernetes Secret.

## Gotchas & tribal knowledge

- **Not reachable from mobile data.** The Gateway AuthorizationPolicy restricts
  this host to four source IPs. A phone on LTE is denied at the mesh, which reads
  as a hang or a TLS-level failure in the app, not a login error. This is the
  deliberate posture; the alternative considered was public-with-app-auth, as
  grafana does.
- **`serve_swagger` is off.** Upstream defaults it on and the Swagger UI is
  unauthenticated wherever it is served.
- **First-boot signup window.** `is_user_creation_disabled: false` in
  `configmap.yaml` exists so the first account can be created. There is no
  bootstrap admin and no CLI to make one. Flip it to `true` after registering,
  and understand that flipping it back is the only recovery if you disable signup
  with no accounts present.
- **The image's own HEALTHCHECK probes a path that does not exist.** The
  Dockerfile hits `/health`; the binary only registers `/api/v1/health`. Harmless
  under Docker, fatal if copied into a Kubernetes probe. The probes here use the
  real path.
- **`sslmode=disable` to Postgres, hardcoded.** `internal/database.NewDatabase`
  builds its DSN with `sslmode=disable TimeZone=Asia/Shanghai` and neither is
  configurable. The connection is in-cluster only. The timezone in the DSN
  affects how the driver interprets bare timestamps; donetick stores UTC, and the
  `TZ` env var on the pod is what governs displayed and scheduled times.
- **Config is read once, at startup.** Editing `configmap.yaml` without bumping
  the `checksum/config` annotation syncs a new ConfigMap that nothing reads.
- **No email.** There is no SMTP relay in this cluster, so password reset cannot
  send a link. `log_raw_url: true` prints the reset URL to the pod log instead;
  that is the documented recovery path in the runbook.
- **chores-tracker is not retired by this app.** Its `chores_tracker` database and
  `chores_user` role still live inside `postgresql-cluster`, undeclared in Git
  (see the comment in `cnpg-cluster.yaml`). Nothing here touches them. Retiring
  them is a separate, deliberate change once data is migrated.
