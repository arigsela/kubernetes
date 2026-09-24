---
type: "Kubernetes App Runbook"
title: "cert-manager — Runbook"
description: "Operational runbook for cert-manager: failure modes, checks, and fixes."
app: cert-manager
catalog_entity: cert-manager
kind: runbook
namespace: cert-manager
last_reviewed: 2026-09-24
status: current
tags: [tls, certificates, route53]
sources:
  - base-apps/cert-manager/letsencrypt-route53.yaml
  - base-apps/cert-manager/external-secret.yaml
---

# cert-manager — Runbook

## Failure modes
### Symptom: a Certificate stays in `pending`/not Ready
- **Check:** `kubectl describe certificate <name> -n <ns>`, then follow the CertificateRequest -> Order -> Challenge chain.
- **Fix:** confirm the ExternalSecret is healthy: `kubectl -n cert-manager get externalsecret route53-credentials`. If the AWS credentials at Vault path `cert-manager/route53` are stale or invalid, correct the Vault value so the solver can create the Route 53 DNS record.
- **Fix (issuerRef is `letsencrypt-prod`/`letsencrypt-staging`):** those issuers no longer exist. Point the Certificate at `letsencrypt-route53`.

### Symptom: renewals failing / cert expiring soon
- **Check:** `kubectl describe clusterissuer letsencrypt-route53` for issuer-level errors, and the IAM permissions on the credentials backing `route53-credentials`.
- **Check:** that a `Certificate` exists for the Secret at all — `kubectl get certificates -A`. A TLS Secret with no Certificate is never renewed (see the ingress-shim gotcha in docs.md).
- **Fix:** delete the failing `CertificateRequest` (or the `Certificate`, if needed) so cert-manager retries. For DNS-01 failures, rotate the Route 53 credentials first (see below).

## How-to
### Add a certificate for a new host
Add `base-apps/<app>/certificate.yaml` with `issuerRef: {name: letsencrypt-route53, kind: ClusterIssuer}` and a `secretName` in the app's namespace (copy `base-apps/homepage/certificate.yaml`). To serve it from the Gateway, add a listener in `base-apps/istio-ingress/gateway.yaml` whose `certificateRefs` names that Secret and namespace, and a `ReferenceGrant` in the app's namespace (copy `base-apps/homepage/reference-grant.yaml`). There is no staging issuer, so every test hits production ACME directly.

### Rotate Route 53 credentials
Update the `access-key-id` / `secret-access-key` values at Vault path `cert-manager/route53`. The `route53-credentials` ExternalSecret (`refreshInterval: 1h`) re-syncs the target Secret automatically. Re-trigger any DNS-01 challenges that were stuck on the old credentials by deleting the affected `CertificateRequest`.
