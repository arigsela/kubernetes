---
app: vault
catalog_entity: vault
kind: runbook
namespace: vault
last_reviewed: 2026-07-08
status: current
tags: [secrets, stateful, kv-v2]
sources:
  - base-apps/vault/statefulsets.yaml
  - base-apps/vault/services.yaml
  - base-apps/vault/configmaps.yaml
---

# vault — Runbook

## Failure modes
### Symptom: many apps' ExternalSecrets stop syncing at once
- **Check:** `kubectl -n vault get pods` and seal status (`kubectl -n vault exec vault-0 -- vault status`). A sealed or down Vault breaks all ESO syncs.
- **Fix:** Vault is configured for AWS KMS auto-unseal (`configmaps.yaml`'s `seal.awskms` stanza, region `us-east-2`) — under normal restarts it unseals itself within seconds, no manual key entry needed. If it stays sealed, the KMS call is failing: check the `vault-kms-credentials` Secret exists and is valid (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION`/`VAULT_AWSKMS_SEAL_KEY_ID` env vars in `statefulsets.yaml`), and that the pod can reach AWS KMS in `us-east-2`. Only fall back to manual `vault operator unseal` with Shamir/recovery keys if KMS itself is unrecoverable (see `docs/vault-auto-unseal-plan.md` for background).

### Symptom: Vault pod won't start after being deleted/recreated (e.g. fresh PVC)
- **Check:** whether the `vault-kms-credentials` Secret exists in the `vault` namespace. There is no `secret-store.yaml`/`external-secret.yaml` in `base-apps/vault/` — this Secret is not synced by External Secrets here (avoids the chicken-and-egg of Vault fetching its own unseal creds from itself), so it must be created out-of-band before the pod comes up healthy.
- **Fix:** (re)create the `vault-kms-credentials` Secret with valid AWS credentials for the `vault-auto-unseal` KMS key (see `terraform/roots/asela-cluster/vault-kms.tf`), then let the pod restart.

### Symptom: one namespace's ExternalSecrets fail but others work
- **Check:** that namespace's `SecretStore` role vs the Vault Kubernetes-auth role/policy (Vault's auth-delegator access is granted via `cluster_role_bindings.yaml`).
- **Fix:** align the Vault role name with the namespace and confirm the policy grants the `k8s-secrets` KV v2 path.

## How-to
### Deploy / update
Edit manifests here and PR; Argo CD syncs on merge. The StatefulSet uses `updateStrategy: OnDelete`, so template changes (image, env, resources) only take effect after the pod is manually deleted (`kubectl -n vault delete pod vault-0`) — plan for the brief unseal/reconnect window this causes.

### Restart safely
Deleting the Vault pod re-seals it momentarily; with AWS KMS configured it should auto-unseal on the new pod within seconds. Verify with `kubectl -n vault exec vault-0 -- vault status` (expect `Sealed: false`, `Seal Type: awskms`) before assuming recovery is complete.
