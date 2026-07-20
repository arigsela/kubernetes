---
type: "Kubernetes App Guide"
title: "cert-manager"
description: "TLS via Let's Encrypt (HTTP-01 via nginx; Route 53 DNS-01 issuer)"
app: cert-manager
catalog_entity: cert-manager
kind: docs
namespace: cert-manager
last_reviewed: 2026-07-08
status: current
tags: [tls, certificates, route53]
sources:
  - base-apps/cert-manager/letsencrypt-prod.yaml
  - base-apps/cert-manager/letsencrypt-staging.yaml
  - base-apps/cert-manager/letsencrypt-route53.yaml
  - base-apps/cert-manager/external-secret.yaml
  - base-apps/cert-manager/secret-store.yaml
---

# cert-manager

## What it is
Automated TLS certificate management via Let's Encrypt. Three cluster-scoped `ClusterIssuer`s exist, and they use **two different challenge types**, not one: `letsencrypt-prod` and `letsencrypt-staging` solve via **HTTP-01** through the `nginx` ingress class, while `letsencrypt-route53` solves via **DNS-01** against AWS Route 53. All three register the ACME account under `admin@arigsela.com`.

## Architecture & data flow
- `letsencrypt-prod.yaml` (production ACME endpoint) and `letsencrypt-staging.yaml` (staging ACME endpoint) are HTTP-01 issuers that solve challenges via `ingress.class: nginx`. They do not use Route 53 or any AWS credentials.
- `letsencrypt-route53.yaml` is a separate, production-only DNS-01 issuer (there is no staging equivalent for the Route 53 path) that solves via the Route 53 API in `region: us-east-1`.
- The Route 53 DNS-01 solver authenticates with AWS credentials read from the `route53-credentials` Secret. That Secret is populated by `external-secret.yaml` (ExternalSecret `route53-credentials`, `refreshInterval: 1h`), which pulls from Vault path `cert-manager/route53` (properties `access-key-id` / `secret-access-key`) through the `vault-backend` SecretStore (`secret-store.yaml`: Vault server `http://vault.vault.svc.cluster.local:8200`, KV v2 mount `k8s-secrets`, Kubernetes-auth role `cert-manager`).
- cert-manager watches `Certificate`/`Ingress` resources cluster-wide and provisions Secrets holding the issued cert/key pairs.

## Where config lives
- HTTP-01 issuers: `letsencrypt-prod.yaml`, `letsencrypt-staging.yaml`.
- DNS-01 (Route 53) issuer: `letsencrypt-route53.yaml`.
- Route 53 credentials: `external-secret.yaml` (ExternalSecret) + `secret-store.yaml` (SecretStore, Vault backend).

## Gotchas & tribal knowledge
- Do not assume `letsencrypt-prod`/`letsencrypt-staging` can validate domains without live ingress traffic — they are HTTP-01 and need the `nginx` Ingress for the domain to be reachable from the ACME server. Only `letsencrypt-route53` does DNS-01.
- There is no Route 53 *staging* issuer. Testing against `letsencrypt-route53` hits the production Let's Encrypt server directly, so watch rate limits.
- DNS-01 issuance depends on the `route53-credentials` ExternalSecret being healthy — if the Vault value at `cert-manager/route53` is stale, or the `cert-manager` Vault role/SecretStore is broken, Route 53 challenges silently stall in `pending`.
- Use `letsencrypt-staging` while testing HTTP-01-validated domains to avoid Let's Encrypt rate limits; there is no equivalent safety net for the DNS-01/Route 53 path.
