---
type: "Kubernetes App Guide"
title: "Backstage"
description: "Internal developer portal / software catalog (Backstage, shared PostgreSQL, Vault, kubernetes-ingestor)"
app: backstage
catalog_entity: backstage
kind: docs
namespace: backstage
last_reviewed: 2026-09-30
status: current
tags: [backstage, developer-portal, catalog, kubernetes-ingestor]
sources:
  - base-apps/backstage/deployments.yaml
  - base-apps/backstage/configmaps.yaml
  - base-apps/backstage/external-secrets.yaml
  - base-apps/backstage/secret-store.yaml
  - base-apps/backstage/rbac.yaml
  - base-apps/backstage/httproute.yaml
  - base-apps/backstage/certificate.yaml
  - base-apps/backstage/reference-grant.yaml
  - base-apps/backstage/services.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - templates/new-app/template.yaml
  - scripts/gen-techdocs.py
---

# backstage

## What it is
The internal developer portal / software catalog (Backstage), running a custom
image, `backstage-portal`, from ECR (tag in `deployments.yaml`, `v1.4.14` at
review; `imagePullPolicy: Always`). The image is built from the portal repo,
`arigsela/backstage` (checked out at `~/git/backstage`). It is the platform's
software catalog UI, scaffolder and TechDocs site, and (per the RBAC below) also
ingests live cluster/Crossplane resources and renders kagent agent detail cards.

Recent portal releases, per the bump commits on `deployments.yaml`: a home page
launchpad at `/` (v1.4.8), removal of the `smoketestapp` XRD (whose template
the ingestor generated) and of the old Node.js/application/decommission/kagent
example templates (v1.4.10), and the **New Application** scaffolder template (v1.4.11, name
availability check v1.4.12; the template itself lives in this repo at
`templates/new-app/template.yaml`, see below).

**Backstage's config is not in this directory.** `app-config.yaml` and
`app-config.production.yaml` (catalog locations, the TeraSky
`kubernetes-ingestor` config, MCP Actions backend, TechDocs, Kubernetes plugin
clusters) are baked into the image from the portal repo, so changing them means
a portal build and a tag bump here. The backend starts with both files, and
Backstage config layering merges **objects** but **replaces arrays**:
`app-config.production.yaml` has its own `catalog.locations` (and
`kubernetes.customResources`), so an entry added only to `app-config.yaml` is
silently dropped in production. Add array entries to **both** files.

## Architecture & data flow
A single-replica `Deployment` (`deployments.yaml`, port `7007`) on the
`node.kubernetes.io/workload: application` nodes, running non-root (UID/GID
1000). Probes hit `/healthcheck` (readiness after 30s, liveness after 60s);
resources are 500m/512Mi requested, 2 CPU/1Gi limit, raised 2026-09-25 after
sustained CPU throttling. A ClusterIP `Service` (`services.yaml`, `80` ->
`7007`) is the backend of the HTTPRoute below. The pod sets no
`serviceAccountName`, so it runs as the namespace's `default` ServiceAccount.

Config is split between a `ConfigMap` (`backstage-config`, `configmaps.yaml`)
and a Vault-backed `Secret` (`backstage-secrets`, `external-secrets.yaml`),
both wired in via `envFrom`:
- **Database**: `POSTGRES_HOST=postgresql.postgresql.svc.cluster.local`,
  `POSTGRES_PORT=5432` (`configmaps.yaml`) — the shared PostgreSQL instance
  (see `base-apps/postgresql`). Credentials (`POSTGRES_USER`,
  `POSTGRES_PASSWORD`) come from Vault.
- **Vault**: `VAULT_ADDR=http://vault.vault.svc.cluster.local:8200`
  (`configmaps.yaml`) is used by the `vault:setup` scaffolder action, which
  authenticates with a **static** `VAULT_TOKEN` (below) that expires; see the
  runbook. The `SecretStore` `vault-backend` (`secret-store.yaml`) resolves the
  `backstage-secrets` `ExternalSecret` from the `k8s-secrets` KV v2 mount using
  Kubernetes auth, role `backstage`, as the namespace's `default` ServiceAccount.
- **Secrets from Vault** (`external-secrets.yaml`, key `backstage`): Postgres
  creds, a GitHub token + GitHub OAuth client id/secret (catalog
  ingestion/auth), a Kubernetes cluster URL + service account token (for the
  Kubernetes/kubernetes-ingestor plugins), AWS access keys (ECR scaffolder
  actions `aws:ecr:create`/`aws:ecr:build-push`, region `us-east-2` per
  `AWS_DEFAULT_REGION`), a Vault token (the `vault:setup` scaffolder action),
  and an `MCP_TOKEN` (static bearer token the MCP Actions backend accepts on
  `/api/mcp-actions/v1/catalog`; kagent's `backstage-catalog` MCP server and
  `homelab-agent` each hold a copy).

## Exposure
`backstage.arigsela.com` is the Gateway API `HTTPRoute` `backstage`
(`httproute.yaml`, listener `https-backstage` on the shared `main` Gateway in
`base-apps/istio-ingress/`) → `backstage:80`. TLS comes from `certificate.yaml`
(`backstage-tls`, issuer `letsencrypt-route53`, DNS-01), which the Gateway reads
through `reference-grant.yaml`. Access is IP-restricted by the host's rule in
`base-apps/istio-ingress/authorizationpolicy.yaml`; sign-in is GitHub OAuth.

## RBAC / cluster ingestion
The Kubernetes plugin and `kubernetes-ingestor` talk to the API server with the
static `K8S_SERVICE_ACCOUNT_TOKEN` from Vault (`authProvider: serviceAccount` in
the portal's app-config), not the pod's own identity. `rbac.yaml` creates a
`backstage` `ServiceAccount` bound to three `ClusterRole`s; they take effect
through that token only if it belongs to this SA:
- `backstage-read-only` — get/list/watch on pods (+ `pods/log`), services,
  configmaps, namespaces, limit ranges, resource quotas, deployments/replica
  sets/stateful sets/daemon sets, HPAs, ingresses, jobs and cronjobs, plus
  get/list on `metrics.k8s.io` pods/nodes (fills the CPU/Memory columns on an
  entity's Kubernetes tab). It has no Gateway API kinds, so HTTPRoutes don't
  appear there.
- `backstage-crossplane-read` — read on CRDs, Crossplane core APIs (XRDs,
  Compositions, Functions, packages), this platform's own `platform.arigsela.com`
  XRs, managed resources (`postgresql.cnpg.io`, `s3.aws.upbound.io`,
  `iam.aws.upbound.io`), External Secrets Operator objects
  (`secretstores`/`pushsecrets`/`externalsecrets`) and
  `protection.crossplane.io` — what the TeraSky `kubernetes-ingestor` needs to
  discover XRDs/Compositions and walk composed resources into catalog entities.
- `backstage-kagent-read` — read on `kagent.dev` `agents`/`modelconfigs`/
  `remotemcpservers`. The kagent entity-page card fetches the live `Agent` CRD
  through the Kubernetes plugin proxy at render time, because the kagent
  controller drops multi-line annotations, so the annotation-based card
  originally tried couldn't work. Without this role the card shows "Could not
  load agent details".

## TechDocs
Backstage renders each in-scope app's docs as TechDocs (the "Docs" tab). The
source is not `docs.md`/`runbook.md` directly: `scripts/gen-techdocs.py`
generates a mirror per app (`mkdocs.yml`, `docs/index.md`, `docs/runbook.md`)
and the `backstage.io/techdocs-ref: dir:.` annotation in `catalog-info.yaml`,
and CI runs it with `--check`. The portal builds the sites in-process
(`techdocs.builder: local`, `generator.runIn: local`), fetching the tree from
GitHub. `mkdocs.yml` isn't a Kubernetes manifest, which is why every app's Argo
CD Application excludes `{catalog-info.yaml,mkdocs.yml}`.

## New Application template
`templates/new-app/template.yaml` (registered in the portal's catalog
locations) scaffolds a complete `base-apps/<app>/` directory, including the
agent-docs files, and opens a PR. When the app needs secrets it first runs
`vault:setup` ("Provision Vault role + secrets"), which creates the Vault
policy, kubernetes-auth role and placeholder secrets; that step comes **before**
"Open PR", so a Vault failure means no PR. The optional "Expose via nginx
Ingress" step still renders an nginx `Ingress` with `letsencrypt-prod`
(`templates/new-app/skeleton-ingress/`), which no longer works since the
Gateway API cutover: leave it off and add an HTTPRoute, Certificate,
ReferenceGrant and allow-list rule by hand (pattern: this app's own files).

## Where config lives
- Runtime env: `configmaps.yaml` (Postgres host/port, AWS region, Vault addr).
- Secrets: `external-secrets.yaml` + `secret-store.yaml` (Vault, key
  `backstage`, `k8s-secrets` KV v2 path).
- RBAC for cluster/Crossplane/kagent ingestion: `rbac.yaml`.
- Exposure: `httproute.yaml`, `certificate.yaml`, `reference-grant.yaml`, and
  the host's rule in `base-apps/istio-ingress/authorizationpolicy.yaml`.
- Portal config (catalog, plugins, auth, TechDocs): the `arigsela/backstage`
  repo, baked into the image.
- Catalog wiring for other apps: each `base-apps/<app>/catalog-info.yaml` is
  the entity Backstage's catalog providers ingest — see
  `templates/agent-docs/README.md` for the contract.
