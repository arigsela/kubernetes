---
type: "Kubernetes App Guide"
title: "Vault"
description: "In-cluster secret backend (KV v2), KMS auto-unseal, UI at vault.arigsela.com with OIDC login via Dex"
app: vault
catalog_entity: vault
kind: docs
namespace: vault
last_reviewed: 2026-09-30
status: stable
tags: [secrets, stateful, kv-v2]
sources:
  - base-apps/vault/statefulsets.yaml
  - base-apps/vault/services.yaml
  - base-apps/vault/configmaps.yaml
  - base-apps/vault/httproute.yaml
  - base-apps/vault/httproute-internal.yaml
  - base-apps/vault/certificate.yaml
  - base-apps/vault/reference-grant.yaml
  - terraform/roots/asela-cluster/vault-kms.tf
  - base-apps/dex/configmap.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - scripts/vault-backup.sh
  - scripts/vault-restore.sh
---

# vault

## What it is
In-cluster HashiCorp Vault (`hashicorp/vault:1.18.1`, Helm chart `vault-0.29.1`): the secret backend for the whole platform. Other apps' `ExternalSecret`/`SecretStore` resources resolve values from a KV v2 mount at path `k8s-secrets` using Vault's Kubernetes auth method (see e.g. `base-apps/postgresql/secret-store.yaml`, which points at `http://vault.vault.svc.cluster.local:8200` with `path: "k8s-secrets"`, `version: "v2"`).

## Architecture & data flow
Runs as a single-replica StatefulSet (`statefulsets.yaml`, `replicas: 1`) in the `vault` namespace. Storage is **file-based** (`storage.file` at `/vault/data`, backed by a 1Gi local-path PVC via `volumeClaimTemplates`) — this is a single-node Vault, not a Raft/integrated-storage HA cluster, despite the `vault-internal` headless-style service (`services.yaml`) that exists for the Helm chart's clustering machinery.

**Placement**: `vault-0` is pinned to `k3s-worker-01` by `nodeSelector: kubernetes.io/hostname` (`statefulsets.yaml`; the comment there explains the move off the control plane). The local-path PV is node-bound to the same host, so the pod cannot run anywhere else.

Two Services exist (`services.yaml`): `vault` (ClusterIP, ports `8200`/`8201`) is the one other namespaces target — `vault.vault.svc.cluster.local:8200` — and `vault-internal` (`publishNotReadyAddresses: true`) is the StatefulSet's governing service. The listener has TLS disabled (`tls_disable: 1` in `configmaps.yaml`), so traffic on 8200 inside the cluster is plaintext HTTP.

**Seal**: Vault auto-unseals via AWS KMS (`seal.awskms`, region `us-east-2`, `configmaps.yaml`) — the StatefulSet injects `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION`/`VAULT_AWSKMS_SEAL_KEY_ID` from the `vault-kms-credentials` Secret (`statefulsets.yaml`). That Secret, the KMS key (`alias/vault-auto-unseal`), and the `vault-kms-user` IAM user + access key are all **Terraform-managed** in `terraform/roots/asela-cluster/vault-kms.tf` (`kubernetes_secret.vault_kms_credentials`) — deliberately not an ExternalSecret, since Vault can't pull its own unseal credentials from itself. The key has `prevent_destroy = true`: Vault's storage is ciphertext under it, so it is effectively part of every backup (see Gotchas).

**Access**: humans reach the UI at `https://vault.arigsela.com` and log in with OIDC via Dex. The Dex side is the static client `vault` in `base-apps/dex/configmap.yaml` (redirect URIs for the UI hostnames and the CLI helper on `localhost:8250`); the Vault side (the `oidc` auth method and its roles) is configured inside Vault, not in git.

## Where config lives
- Server config (listener, storage, seal stanza, `ui: true`): `configmaps.yaml`.
- Workload: `statefulsets.yaml` (single replica, `updateStrategy: OnDelete`, node pin, resources). Requests/limits were set on 2026-09-25 (previously BestEffort); `resizePolicy` is `NotRequired` for cpu and memory, so an in-place resize doesn't restart the container. The sizing rationale is in the manifest comment.
- Access: `service_accounts.yaml` (ServiceAccount `vault`), `cluster_role_bindings.yaml` (binds `vault` SA to cluster role `system:auth-delegator`, required for the Kubernetes auth method's TokenReview calls).
- Public HTTPS UI: `httproute.yaml` (HTTPRoute `vault` on the shared `main` Gateway, listener `https-vault`, host `vault.arigsela.com`), `certificate.yaml` (issuer `letsencrypt-route53`, DNS-01) and `reference-grant.yaml` (lets the Gateway read the TLS Secret). TLS terminates at the Gateway; the hop to `vault:8200` is plain HTTP.
- LAN hostnames: `httproute-internal.yaml` (HTTPRoute `vault-internal`, listeners `http-vault-local`/`http-vault-ip`, hosts `vault.local` and `vault.10.0.1.110`, plaintext, no certificate).
- Source-IP allow-lists for both routes: the `vault.arigsela.com` and `vault.local` rules in `base-apps/istio-ingress/authorizationpolicy.yaml`. The Coraza WAF (`base-apps/istio-waf/`) does not inspect Vault's hosts.
- KMS key, IAM user, `vault-kms-credentials` Secret: `terraform/roots/asela-cluster/vault-kms.tf` (applied by Atlantis on the open PR).
- Backup/restore: `scripts/vault-backup.sh`, `scripts/vault-restore.sh`; cluster-wide procedure in `recovery/CLUSTER-RECOVERY.md`.

## Gotchas & tribal knowledge
- Vault sealing (or KMS unreachability) blocks every downstream `ExternalSecret`; a cluster-wide "secrets not syncing" symptom usually traces back here.
- This is a single-replica, file-storage Vault — there is no automatic failover. Losing the `vault-data` PVC or the pod for an extended period is a real outage, not just a blip.
- **`vault-0` is tied to `k3s-worker-01`.** Draining or losing that node leaves the pod `Pending` until the node is back; it can't reschedule elsewhere (node-pinned selector plus node-bound local-path PV). Expect every ESO sync to stall for that window.
- `updateStrategy: OnDelete` on the StatefulSet means changes to the pod template do **not** roll out until the pod is manually deleted.
- **The KMS key is the backup.** Backups of `/vault/data` are ciphertext and useless without `alias/vault-auto-unseal`; the Shamir recovery keys don't decrypt storage. Never remove `prevent_destroy` from `vault-kms.tf`.
- The file backend has no consistent-snapshot API. `vault-backup.sh` defaults to a **cold** copy (scales Vault to 0, copies, scales back and checks unseal), so every backup is a planned Vault outage.
- The `vault.local` allow-list rule deliberately includes `10.0.1.0/24` and deliberately leaves out the remote /32s used on other hosts. Host-header routing needs no DNS, so without that rule the plaintext API would be reachable from the internet (the comment in `authorizationpolicy.yaml` has the history).
- Vault roles referenced by other namespaces' `SecretStore`s are expected to match those namespaces' names for ESO access.
