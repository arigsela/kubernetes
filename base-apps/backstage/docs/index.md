---
type: "Kubernetes App Guide"
title: "Backstage"
description: "Internal developer portal / software catalog (Backstage, shared PostgreSQL, Vault, kubernetes-ingestor)"
app: backstage
catalog_entity: backstage
kind: docs
namespace: backstage
last_reviewed: 2026-07-10
status: current
tags: [backstage, developer-portal, catalog, kubernetes-ingestor]
sources:
  - base-apps/backstage/deployments.yaml
  - base-apps/backstage/configmaps.yaml
  - base-apps/backstage/external-secrets.yaml
  - base-apps/backstage/secret-store.yaml
  - base-apps/backstage/rbac.yaml
  - base-apps/backstage/nginx-ingress.yaml
  - base-apps/backstage/services.yaml
---

# backstage

## What it is
The internal developer portal / software catalog (Backstage), running a custom
image `852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.4.5`
(`deployments.yaml`). It is the platform's software catalog UI and scaffolder,
and (per the RBAC below) also ingests live cluster/Crossplane resources and
renders kagent agent detail cards. Note: Backstage's `app-config.yaml` (catalog
providers, the TeraSky `kubernetes-ingestor` plugin config, MCP Actions
backend) is baked into the container image, not present as a manifest in this
directory — this doc only covers what the Kubernetes manifests and env
actually show.

## Architecture & data flow
A single-replica `Deployment` (`deployments.yaml`, port `7007`) fronted by a
ClusterIP `Service` (`services.yaml`, port `80` -> `7007`) and an nginx
`Ingress` (`nginx-ingress.yaml`) terminating TLS at `backstage.arigsela.com`
(cert via `letsencrypt-prod`, source IPs restricted by
`nginx.ingress.kubernetes.io/whitelist-source-range`).

Config is split between a `ConfigMap` (`backstage-config`, `configmaps.yaml`)
and a Vault-backed `Secret` (`backstage-secrets`, `external-secrets.yaml`),
both wired in via `envFrom`:
- **Database**: `POSTGRES_HOST=postgresql.postgresql.svc.cluster.local`,
  `POSTGRES_PORT=5432` (`configmaps.yaml`) — the shared PostgreSQL instance
  (see `base-apps/postgresql`). Credentials (`POSTGRES_USER`,
  `POSTGRES_PASSWORD`) come from Vault.
- **Vault**: `VAULT_ADDR=http://vault.vault.svc.cluster.local:8200`
  (`configmaps.yaml`) is used by the `vault:setup` scaffolder action; the
  `SecretStore` `vault-backend` (`secret-store.yaml`) resolves the
  `backstage-secrets` `ExternalSecret` from the `k8s-secrets` KV v2 mount
  using the Kubernetes auth method (role `backstage`).
- **Secrets from Vault** (`external-secrets.yaml`, key `backstage`): Postgres
  creds, a GitHub token + GitHub OAuth client id/secret (catalog
  ingestion/auth), a Kubernetes cluster URL + service account token (for the
  Kubernetes/kubernetes-ingestor plugins), AWS access keys (ECR scaffolder
  actions `aws:ecr:create`/`aws:ecr:build-push`, region `us-east-2` per
  `AWS_DEFAULT_REGION`), a Vault token (the `vault:setup` scaffolder action),
  and an `MCP_TOKEN` (static bearer token the MCP Actions backend accepts on
  `/api/mcp-actions/v1/catalog`, shared with kagent).

## RBAC / cluster ingestion
The `backstage` `ServiceAccount` (`rbac.yaml`) is bound to three
`ClusterRole`s:
- `backstage-read-only` — read-only on core workload/network objects (pods,
  services, deployments, ingresses, jobs, HPAs, etc.) for the Kubernetes
  plugin.
- `backstage-crossplane-read` — read on CRDs, Crossplane core APIs (XRDs,
  Compositions, Functions), this platform's own `platform.arigsela.com` XRs,
  managed resources (`postgresql.cnpg.io`, `s3.aws.upbound.io`,
  `iam.aws.upbound.io`), and External Secrets Operator objects
  (`secretstores`/`pushsecrets`/`externalsecrets`) — this is what the TeraSky
  `kubernetes-ingestor` plugin needs to discover XRDs/Compositions and walk
  composed resources into catalog entities.
- `backstage-kagent-read` — read on `kagent.dev` `agents`/`modelconfigs`/
  `remotemcpservers`, used by the Backstage entity-page card that fetches the
  live kagent `Agent` CRD through the Kubernetes plugin proxy.

## Where config lives
- Runtime env: `configmaps.yaml` (Postgres host/port, AWS region, Vault addr).
- Secrets: `external-secrets.yaml` + `secret-store.yaml` (Vault, key
  `backstage`, `k8s-secrets` KV v2 path).
- RBAC for cluster/Crossplane/kagent ingestion: `rbac.yaml`.
- Exposure: `services.yaml` (ClusterIP `80`->`7007`) + `nginx-ingress.yaml`
  (`backstage.arigsela.com`).
- Catalog wiring for other apps: each `base-apps/<app>/catalog-info.yaml` is
  the entity Backstage's catalog providers ingest — see
  `templates/agent-docs/README.md` for the contract.
