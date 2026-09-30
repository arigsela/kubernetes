---
type: "Kubernetes App Guide"
title: "cert-manager"
description: "TLS via Let's Encrypt: jetstack chart plus the single letsencrypt-route53 (DNS-01) ClusterIssuer; one Certificate per host"
app: cert-manager
catalog_entity: cert-manager
kind: docs
namespace: cert-manager
last_reviewed: 2026-09-30
status: current
tags: [tls, certificates, route53]
sources:
  - base-apps/cert-manager.yaml
  - base-apps/cert-manager-config.yaml
  - base-apps/cert-manager/letsencrypt-route53.yaml
  - base-apps/cert-manager/external-secret.yaml
  - base-apps/cert-manager/secret-store.yaml
---

# cert-manager

## What it is
Automated TLS certificate management via Let's Encrypt. One cluster-scoped `ClusterIssuer` exists, `letsencrypt-route53`, which solves **DNS-01** against AWS Route 53 and registers the ACME account under `admin@arigsela.com`. The HTTP-01 issuers (`letsencrypt-prod`, `letsencrypt-staging`) were removed: they solved through the `nginx` ingress class, which went away with the Istio Gateway cutover.

## Architecture & data flow
Two Argo CD Applications:
- `cert-manager` (`base-apps/cert-manager.yaml`) installs the jetstack Helm chart (`https://charts.jetstack.io`, version pinned in `targetRevision`) with `installCRDs: "true"`. The controller, webhook and cainjector are all pinned to the control-plane node (`node.kubernetes.io/workload: infrastructure` plus the control-plane toleration).
- `cert-manager-config` (`base-apps/cert-manager-config.yaml`) syncs this directory, `base-apps/cert-manager/` (the issuer, SecretStore and ExternalSecret).

- `letsencrypt-route53.yaml` is a production-only DNS-01 issuer (there is no staging equivalent) that solves via the Route 53 API in `region: us-east-1`.
- The Route 53 DNS-01 solver authenticates with AWS credentials read from the `route53-credentials` Secret. That Secret is populated by `external-secret.yaml` (ExternalSecret `route53-credentials`, `refreshInterval: 1h`), which pulls from Vault path `cert-manager/route53` (properties `access-key-id` / `secret-access-key`) through the `vault-backend` SecretStore (`secret-store.yaml`: Vault server `http://vault.vault.svc.cluster.local:8200`, KV v2 mount `k8s-secrets`, Kubernetes-auth role `cert-manager`).
- Every certificate is an explicit `Certificate` in its app's directory (`base-apps/<app>/certificate.yaml`), issuing into a Secret in the app's namespace. The Istio Gateway (`base-apps/istio-ingress/gateway.yaml`) serves that Secret cross-namespace, which the app's `ReferenceGrant` permits.

## Where config lives
- Chart version and values: `base-apps/cert-manager.yaml`.
- Issuer: `letsencrypt-route53.yaml` (synced by `base-apps/cert-manager-config.yaml`).
- Route 53 credentials: `external-secret.yaml` (ExternalSecret) + `secret-store.yaml` (SecretStore, Vault backend).
- Certificates: `base-apps/<app>/certificate.yaml`, one per host.

## Gotchas & tribal knowledge
- **Never let an Ingress or Gateway annotation own a Certificate.** cert-manager's ingress-shim makes the Certificate a child of the Ingress, so deleting the Ingress garbage-collects it while the Secret survives unrenewed. That is how 14 hosts silently stopped renewing after the nginx Ingresses were removed (fixed in #575). Declare a standalone `Certificate` instead.
- There is no staging issuer. Every issuance hits the production Let's Encrypt server, so watch rate limits.
- DNS-01 issuance depends on the `route53-credentials` ExternalSecret being healthy — if the Vault value at `cert-manager/route53` is stale, or the `cert-manager` Vault role/SecretStore is broken, Route 53 challenges silently stall in `pending`.
- **The New App scaffolding still emits the removed setup** (SPEC T76): `templates/new-app/skeleton-ingress/` renders an nginx `Ingress` with `cert-manager.io/cluster-issuer: letsencrypt-prod`, and so does `base-apps/crossplane-compositions/composition-application.yaml` (the `Application` composition). Neither nginx nor that issuer exists, so an app created that way gets no route and no certificate. Until those are fixed, hand-write the `certificate.yaml` / HTTPRoute / `ReferenceGrant` / Gateway listener / allow-list rule (runbook how-to).
- cert-manager adopts an existing Secret when a new `Certificate`'s `secretName`, `dnsNames` and issuer match it (and its `cert-manager.io/certificate-name` annotation matches the Certificate name) — no reissue until the normal renewal time.
