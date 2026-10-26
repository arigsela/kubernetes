---
type: "Kubernetes App Guide"
title: "Backstage"
description: "Internal developer portal / software catalog (Backstage, shared PostgreSQL, Vault, kubernetes-ingestor)"
app: backstage
catalog_entity: backstage
kind: docs
namespace: backstage
last_reviewed: 2026-10-26
status: stable
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
image, `backstage-portal`, from ECR (tag in `deployments.yaml`, `v1.6.1` since
2026-10-26; `imagePullPolicy: Always`). The image is built from the portal repo,
`arigsela/backstage` (checked out at `~/git/backstage`). It is the platform's
software catalog UI, scaffolder and TechDocs site, and (per the RBAC below) also
ingests live cluster/Crossplane resources and renders kagent agent detail cards.

Recent portal releases, per the bump commits on `deployments.yaml`: a home page
launchpad at `/` (v1.4.8), removal of the `smoketestapp` XRD (whose template
the ingestor generated) and of the old Node.js/application/decommission/kagent
example templates (v1.4.10), the **New Application** scaffolder template (v1.4.11, name
availability check v1.4.12; the template itself lives in this repo at
`templates/new-app/template.yaml`, see below), kagent 1.0 support (v1.5.0,
`arigsela/backstage#41`: agent entities, the agent card and the A2A client),
the scorecard plus the `readiness` MCP server (v1.6.0, `arigsela/backstage#44`;
see **Scorecard** below), and v1.6.1 (`arigsela/backstage#45`), which drops the
scorecard's file check after it stalled the portal.

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
  `/api/mcp-actions/v1/catalog` and `/api/mcp-actions/v1/readiness`; kagent's
  `backstage-catalog` MCP server holds a copy in Vault key
  `kagent-backstage-mcp`).

## Scorecard
Since v1.6.0 the portal runs the Red Hat Developer Hub Scorecard plugin at
**4.2.0** (the last release whose core dependencies fit the portal's Backstage
1.52; newer ones wait for a Backstage upgrade). Each component's **Scorecard**
tab shows its metrics with a success/warning/error status, from two providers:
- **`github.openPRs`**: open pull requests in the repo named by
  `github.com/project-slug`. Hourly.
- **`dependabot.alerts{Critical,High,Medium,Low}`**: open Dependabot alerts by
  severity. An entity opts in with the annotation `github.com/dependabot: 'true'`
  (that exact string), and the repo must have Dependabot alerts turned on.
  Hourly.

Metrics are collected on that schedule and stored, starting 1 minute after the
pod starts; until then an entity's metrics read `[]`.

**No file check.** v1.6.0 also shipped the `filecheck` provider (`runbook.md` /
`docs.md` in each entity's `backstage.io/source-location` folder). Its first run,
5 minutes after startup, downloaded this repo's ~105 MB archive once per entity,
stalled the backend until the liveness probe killed it (exit 137, not an OOM),
and would have retried every 15 minutes. v1.6.1 removes it; the portal repo's
contract script fails if it comes back. The `/production-readiness` skill
checks both files from this repo directly.

Do not repoint `backstage.io/source-location` at an app's own repo: TechDocs
resolves `backstage.io/techdocs-ref: dir:.` against it, so that would break the
app's Docs tab.

The data is read over REST at
`GET /api/scorecard/metrics/catalog/<kind>/<namespace>/<name>` (the
`/production-readiness` skill in `arigsela/claude-agents` does this), and over
MCP at `/api/mcp-actions/v1/readiness`. That server serves the scorecard's
read-only `get-entity-metrics` and `list-metrics` plus `get-catalog-entity` and
`query-catalog-entities`; the `catalog` server is unchanged. The portal repo's
`scripts/check-scorecard-contract.sh` checks the metrics, both tool lists and,
with `ENTITY_REF`, one entity's values.

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
- `backstage-kagent-read` — read on kagent 1.0's `api.kagent.dev` `agents`/
  `agenttemplates`/`modelconfigs`/`remotemcpservers`. Two readers depend on it.
  The `kubernetes-ingestor` creates the agent catalog entities from the
  `Agent` objects themselves (`agents.api.kagent.dev/v1alpha3`, through the
  portal's `kubernetesIngestor.components.customWorkloadTypes`): kagent 1.0 has
  no per-agent Deployment for it to find, so each `Agent` carries the
  `terasky.backstage.io/add-to-catalog` and `component-type: kagent-agent`
  annotations. The entity-page card fetches the live `Agent` and its
  `AgentTemplate` (model, MCP bindings, approvals, sub-agents) through the
  Kubernetes plugin proxy at render time. Without this role there are no agent
  entities and the card shows "Could not load agent details".

The portal also *calls* agents, without any RBAC: the scaffolder's skill
suggestions (`kagent-suggest` → `skill-suggester`) and the `kagent:agent:invoke`
action send A2A v1 `SendMessage` to the kagent controller's gateway,
`http://kagent-controller.kagent.svc.cluster.local:8083/agents/<ns>/<name>`, as
`X-User-Id: backstage-portal`. kagent 0.10's per-agent Services and the
`agents.platform.ai/a2a-endpoint` annotation contract are gone.

The ingestor config, the agent card and the A2A client above are portal code,
moved to kagent 1.0 in `arigsela/backstage#41` and shipped in portal `v1.5.0`
(2026-10-11). An older portal image speaks kagent 0.10 and finds no agents.

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
