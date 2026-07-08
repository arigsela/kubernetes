---
app: cert-manager
catalog_entity: cert-manager
kind: runbook
namespace: cert-manager
last_reviewed: 2026-07-08
status: current
tags: [tls, certificates, route53]
sources:
  - base-apps/cert-manager/letsencrypt-prod.yaml
  - base-apps/cert-manager/letsencrypt-route53.yaml
  - base-apps/cert-manager/external-secret.yaml
---

# cert-manager — Runbook

## Failure modes
### Symptom: a Certificate stays in `pending`/not Ready
- **Check:** `kubectl describe certificate <name> -n <ns>`, then follow the CertificateRequest -> Order -> Challenge chain. First confirm which `issuerRef` (ClusterIssuer) the Certificate uses — the fix path differs by challenge type.
- **Fix (issuer is `letsencrypt-route53`, DNS-01):** confirm the ExternalSecret is healthy: `kubectl -n cert-manager get externalsecret route53-credentials`. If the AWS credentials at Vault path `cert-manager/route53` are stale or invalid, correct the Vault value so the solver can create the Route 53 DNS record.
- **Fix (issuer is `letsencrypt-prod`/`letsencrypt-staging`, HTTP-01):** these have no AWS/Route 53 dependency — confirm the `nginx` Ingress for the domain is actually reachable from the internet, since the ACME server must reach it directly to complete the HTTP-01 challenge.

### Symptom: renewals failing / cert expiring soon
- **Check:** `kubectl describe clusterissuer <name>` for issuer-level errors; for `letsencrypt-route53` also check IAM permissions on the credentials backing `route53-credentials`.
- **Fix:** delete the failing `CertificateRequest` (or the `Certificate`, if needed) so cert-manager retries. For DNS-01 failures, rotate the Route 53 credentials first (see below).

## How-to
### Add a new certificate
Reference `letsencrypt-prod` (HTTP-01, requires a working `nginx` Ingress for the domain) or `letsencrypt-route53` (DNS-01, no ingress required) as the `issuerRef` on your `Certificate`/Ingress. Test HTTP-01 domains against `letsencrypt-staging` first. There is no staging issuer for the Route 53/DNS-01 path, so testing that path hits production ACME directly.

### Rotate Route 53 credentials
Update the `access-key-id` / `secret-access-key` values at Vault path `cert-manager/route53`. The `route53-credentials` ExternalSecret (`refreshInterval: 1h`) re-syncs the target Secret automatically. Re-trigger any DNS-01 challenges that were stuck on the old credentials by deleting the affected `CertificateRequest`.
