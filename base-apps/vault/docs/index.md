---
type: "Kubernetes App Guide"
title: "Vault"
description: "In-cluster secret backend (KV v2)"
app: vault
catalog_entity: vault
kind: docs
namespace: vault
last_reviewed: 2026-07-08
status: current
tags: [secrets, stateful, kv-v2]
sources:
  - base-apps/vault/statefulsets.yaml
  - base-apps/vault/services.yaml
  - base-apps/vault/configmaps.yaml
---

# vault

## What it is
In-cluster HashiCorp Vault (`hashicorp/vault:1.18.1`, Helm chart `vault-0.29.1`): the secret backend for the whole platform. Other apps' `ExternalSecret`/`SecretStore` resources resolve values from a KV v2 mount at path `k8s-secrets` using Vault's Kubernetes auth method (see e.g. `base-apps/postgresql/secret-store.yaml`, which points at `http://vault.vault.svc.cluster.local:8200` with `path: "k8s-secrets"`, `version: "v2"`).

## Architecture & data flow
Runs as a single-replica StatefulSet (`statefulsets.yaml`, `replicas: 1`) in the `vault` namespace. Storage is **file-based** (`storage.file` at `/vault/data`, backed by a 1Gi PVC via `volumeClaimTemplates`) — this is a single-node Vault, not a Raft/integrated-storage HA cluster, despite the `vault-internal` headless-style service (`services.yaml`) that exists for the Helm chart's clustering machinery.

Two Services exist (`services.yaml`): `vault` (ClusterIP, ports `8200`/`8201`) is the one other namespaces target — `vault.vault.svc.cluster.local:8200` — and `vault-internal` (`publishNotReadyAddresses: true`) is the StatefulSet's governing service. The listener has TLS disabled (`tls_disable: 1` in `configmaps.yaml`), so traffic on 8200 inside the cluster is plaintext HTTP.

**Seal**: Vault auto-unseals via AWS KMS (`seal.awskms`, region `us-east-2`, `configmaps.yaml`) — the StatefulSet injects `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION`/`VAULT_AWSKMS_SEAL_KEY_ID` from a `vault-kms-credentials` Secret (`statefulsets.yaml`). This replaced manual Shamir unsealing (background/history in `docs/vault-auto-unseal-plan.md`; the KMS key itself is provisioned in `terraform/roots/asela-cluster/vault-kms.tf`). Note the `vault-kms-credentials` Secret is **not** synced by an ExternalSecret in this directory — there is no `secret-store.yaml`/`external-secret.yaml` here (chicken-and-egg: Vault can't pull its own unseal credentials from itself), so it must exist by other means before Vault can start cleanly.

## Where config lives
- Server config (listener, storage, seal stanza): `configmaps.yaml`.
- Workload: `statefulsets.yaml` (single replica, `updateStrategy: OnDelete` — pods are not auto-recreated on template change).
- Access: `service_accounts.yaml` (ServiceAccount `vault`), `cluster_role_bindings.yaml` (binds `vault` SA to cluster role `system:auth-delegator`, required for the Kubernetes auth method's TokenReview calls).
- External exposure: `ingress.yaml` — nginx `Ingress` `vault-internal` fronting the `vault` Service on port 8200 at hosts `vault.local` and `vault.10.0.1.110` (internal hostnames, `ssl-redirect: "false"`).

## Gotchas & tribal knowledge
- Vault sealing (or KMS unreachability) blocks every downstream `ExternalSecret`; a cluster-wide "secrets not syncing" symptom usually traces back here.
- This is a single-replica, file-storage Vault — there is no automatic failover. Losing the `vault-data` PVC or the pod for an extended period is a real outage, not just a blip.
- `updateStrategy: OnDelete` on the StatefulSet means changes to the pod template do **not** roll out until the pod is manually deleted.
- Vault roles referenced by other namespaces' `SecretStore`s are expected to match those namespaces' names for ESO access.
